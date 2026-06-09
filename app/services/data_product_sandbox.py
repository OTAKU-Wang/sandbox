"""Data Product Development Sandbox — provides isolated environments for data providers to develop data products.

Supports three development modes:
- Structured: SQL queries, data modeling, feature engineering
- Unstructured: Image/document/media processing, LLM training data prep
- Semi-structured: JSON/XML/CSV/Log ETL pipelines

Session state is stored in Redis for multi-worker persistence.
"""
import uuid
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field

from app.models.sandbox_session import SandboxLevel, SessionStatus
from app.core.path_security import safe_resolve_within, sanitize_filename
from app.services.sandbox_runtime import SandboxRuntime
from app.services.data_processing import (
    UnstructuredDataProcessor,
    SemiStructuredDataProcessor,
    ProcessingResult,
)
from app.services.storage_service import StorageService
from app.services.kms_service import KMSService

logger = logging.getLogger(__name__)

_REDIS_KEY_PREFIX = "dev_sandbox:session:"


class DevMode:
    STRUCTURED = "structured"
    UNSTRUCTURED = "unstructured"
    SEMI_STRUCTURED = "semi_structured"


@dataclass
class DevSandboxConfig:
    """Configuration for a data product development sandbox."""
    mode: str
    sandbox_level: str = SandboxLevel.L3.value
    max_duration_seconds: int = 7200
    max_input_files: int = 100
    max_output_size_mb: int = 500
    allowed_operations: list[str] = field(default_factory=lambda: ["read", "transform", "analyze"])
    dp_epsilon_budget: float | None = None


@dataclass
class DevSandboxSession:
    """Represents an active development sandbox session."""
    session_id: str
    config: DevSandboxConfig
    user_id: str | None = None
    container_id: str | None = None
    status: str = SessionStatus.PENDING.value
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    input_files: list[str] = field(default_factory=list)
    output_files: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def _session_to_json(session: DevSandboxSession) -> str:
    """Serialize DevSandboxSession to JSON for Redis storage."""
    return json.dumps({
        "session_id": session.session_id,
        "config": {
            "mode": session.config.mode,
            "sandbox_level": session.config.sandbox_level,
            "max_duration_seconds": session.config.max_duration_seconds,
            "max_input_files": session.config.max_input_files,
            "max_output_size_mb": session.config.max_output_size_mb,
            "allowed_operations": session.config.allowed_operations,
            "dp_epsilon_budget": session.config.dp_epsilon_budget,
        },
        "user_id": session.user_id,
        "container_id": session.container_id,
        "status": session.status,
        "created_at": session.created_at.isoformat(),
        "input_files": session.input_files,
        "output_files": session.output_files,
        "metadata": session.metadata,
    }, ensure_ascii=False)


def _session_from_json(data: str) -> DevSandboxSession:
    """Deserialize DevSandboxSession from JSON."""
    d = json.loads(data)
    config = DevSandboxConfig(
        mode=d["config"]["mode"],
        sandbox_level=d["config"].get("sandbox_level", SandboxLevel.L3.value),
        max_duration_seconds=d["config"].get("max_duration_seconds", 7200),
        max_input_files=d["config"].get("max_input_files", 100),
        max_output_size_mb=d["config"].get("max_output_size_mb", 500),
        allowed_operations=d["config"].get("allowed_operations", ["read", "transform", "analyze"]),
        dp_epsilon_budget=d["config"].get("dp_epsilon_budget"),
    )
    return DevSandboxSession(
        session_id=d["session_id"],
        config=config,
        user_id=d.get("user_id"),
        container_id=d.get("container_id"),
        status=d.get("status", SessionStatus.PENDING.value),
        created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else datetime.now(timezone.utc),
        input_files=d.get("input_files", []),
        output_files=d.get("output_files", []),
        metadata=d.get("metadata", {}),
    )


class DataProductDevSandbox:
    """Manages data product development sandbox sessions.

    Session state is stored in Redis for cross-worker persistence.
    Each session is stored as JSON under key ``dev_sandbox:session:{session_id}``
    with a TTL matching the session's max_duration_seconds.

    Falls back to an in-memory dict when Redis is unavailable (e.g. tests).
    """

    _memory_sessions: dict[str, DevSandboxSession] = {}  # Fallback store

    def __init__(self):
        self.runtime = SandboxRuntime()
        self.unstructured_proc = UnstructuredDataProcessor()
        self.semi_structured_proc = SemiStructuredDataProcessor()

    async def _get_redis(self):
        from app.core.redis import get_redis
        return await get_redis()

    def create_session(self, config: DevSandboxConfig, data_path: str | None = None, user_id: str | None = None) -> DevSandboxSession:
        """Create a new development sandbox session.

        Note: synchronous because runtime.provision() is sync.
        Redis write is done via a fire-and-forget background save.
        """
        import asyncio
        session_id = str(uuid.uuid4())
        session = DevSandboxSession(session_id=session_id, config=config, user_id=user_id)

        # Provision sandbox
        result = self.runtime.provision(
            session_id=uuid.UUID(session_id),
            level=config.sandbox_level,
            data_path=data_path or "",
            timeout=config.max_duration_seconds,
        )

        session.container_id = result.get("container_id")
        session.status = result.get("status", SessionStatus.FAILED.value)

        # Always save to in-memory first (instant availability)
        self._memory_sessions[session_id] = session

        # Also persist to Redis in background if event loop available
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._save_session(session))
        except RuntimeError:
            pass  # No event loop — in-memory only

        return session

    async def _save_session(self, session: DevSandboxSession) -> None:
        """Persist session to Redis with TTL. Always updates in-memory too."""
        self._memory_sessions[session.session_id] = session
        try:
            redis = await self._get_redis()
            key = f"{_REDIS_KEY_PREFIX}{session.session_id}"
            ttl = session.config.max_duration_seconds
            await redis.setex(key, ttl, _session_to_json(session))
        except Exception as e:
            logger.warning(f"Redis unavailable, using in-memory store: {e}")

    async def get_session(self, session_id: str) -> DevSandboxSession | None:
        """Get a session by ID from Redis (or in-memory fallback)."""
        try:
            redis = await self._get_redis()
            data = await redis.get(f"{_REDIS_KEY_PREFIX}{session_id}")
            if data:
                return _session_from_json(data)
        except Exception:
            pass
        return self._memory_sessions.get(session_id)

    async def list_sessions(self) -> list[DevSandboxSession]:
        """List all active sessions from Redis (or in-memory fallback)."""
        try:
            redis = await self._get_redis()
            sessions = []
            cursor = 0
            while True:
                cursor, keys = await redis.scan(cursor, match=f"{_REDIS_KEY_PREFIX}*", count=100)
                for key in keys:
                    data = await redis.get(key)
                    if data:
                        try:
                            sessions.append(_session_from_json(data))
                        except (json.JSONDecodeError, KeyError):
                            pass
                if cursor == 0:
                    break
            return sessions
        except Exception:
            return list(self._memory_sessions.values())

    async def upload_data(self, session_id: str, file_path: str) -> ProcessingResult:
        """Upload and process input data for the development session."""
        session = await self.get_session(session_id)
        if not session:
            return ProcessingResult(success=False, records_processed=0, error="Session not found")

        if len(session.input_files) >= session.config.max_input_files:
            return ProcessingResult(success=False, records_processed=0, error="Input file limit reached")

        mode = session.config.mode
        output_dir = str(Path("/tmp/cds-sandbox") / session_id / "input")

        if mode == DevMode.STRUCTURED:
            result = self._upload_structured(file_path, output_dir, session)
        elif mode == DevMode.UNSTRUCTURED:
            result = self._upload_unstructured(file_path, output_dir, session)
        elif mode == DevMode.SEMI_STRUCTURED:
            result = self._upload_semi_structured(file_path, output_dir, session)
        else:
            return ProcessingResult(success=False, records_processed=0, error=f"Unknown mode: {mode}")

        # Persist updated session state
        await self._save_session(session)
        return result

    async def execute_code(self, session_id: str, code: str, language: str = "python") -> dict:
        """Execute code in the development sandbox."""
        session = await self.get_session(session_id)
        if not session:
            return {"output": "Session not found", "exit_code": -1, "duration_ms": 0}

        if session.status != SessionStatus.RUNNING.value:
            return {"output": f"Session not running (status: {session.status})", "exit_code": -1, "duration_ms": 0}

        return await self.runtime.execute(session.container_id, code, language)

    async def get_output(self, session_id: str, output_name: str) -> ProcessingResult:
        """Retrieve output from the development sandbox."""
        session = await self.get_session(session_id)
        if not session:
            return ProcessingResult(success=False, records_processed=0, error="Session not found")

        safe_name = sanitize_filename(output_name)
        base_dir = Path("/tmp/cds-sandbox") / session_id / "output"
        output_path = safe_resolve_within(base_dir, safe_name)
        if not output_path.exists():
            return ProcessingResult(success=False, records_processed=0, error=f"Output not found: {output_name}")

        session.output_files.append(str(output_path))
        await self._save_session(session)
        return ProcessingResult(
            success=True,
            records_processed=1,
            output_path=str(output_path),
            metadata={"filename": output_name, "size_bytes": output_path.stat().st_size},
        )

    async def terminate_session(self, session_id: str) -> bool:
        """Terminate a development sandbox session."""
        session = await self.get_session(session_id)
        if not session:
            return False

        if session.container_id:
            self.runtime.terminate(session.container_id)

        session.status = SessionStatus.TERMINATED.value
        await self._save_session(session)
        return True

    def _upload_structured(self, file_path: str, output_dir: str, session: DevSandboxSession) -> ProcessingResult:
        """Process structured data (CSV/DB) for development."""
        path = Path(file_path)
        if not path.exists():
            return ProcessingResult(success=False, records_processed=0, error="File not found")

        ext = path.suffix.lower()
        if ext == ".csv":
            result = self.semi_structured_proc.process(file_path, output_dir)
        elif ext == ".json":
            result = self.semi_structured_proc.process(file_path, output_dir)
        else:
            return ProcessingResult(success=False, records_processed=0, error=f"Unsupported structured format: {ext}")

        if result.success:
            session.input_files.append(file_path)
            session.metadata.setdefault("input_schemas", []).append(result.metadata)

        return result

    def _upload_unstructured(self, file_path: str, output_dir: str, session: DevSandboxSession) -> ProcessingResult:
        """Process unstructured data (image/document/media) for development."""
        result = self.unstructured_proc.process(file_path, output_dir)

        if result.success:
            session.input_files.append(file_path)
            session.metadata.setdefault("input_metadata", []).append(result.metadata)

        return result

    def _upload_semi_structured(self, file_path: str, output_dir: str, session: DevSandboxSession) -> ProcessingResult:
        """Process semi-structured data (JSON/XML/CSV/Log) for development."""
        result = self.semi_structured_proc.process(file_path, output_dir)

        if result.success:
            session.input_files.append(file_path)
            session.metadata.setdefault("input_schemas", []).append(result.metadata)

        return result


class StructuredDevTools:
    """Tools for structured data development in sandbox."""

    @staticmethod
    def generate_sql_template(table_name: str, columns: list[dict]) -> str:
        """Generate a SQL template for data exploration."""
        col_defs = ", ".join(f"{c['name']} {c.get('type', 'TEXT')}" for c in columns)
        select_cols = ", ".join(c["name"] for c in columns)
        return f"""-- Data Product Development Sandbox
-- Table: {table_name}
-- Columns: {col_defs}

-- Sample query
SELECT {select_cols}
FROM {table_name}
LIMIT 100;

-- Aggregation example
SELECT COUNT(*) as total_rows
FROM {table_name};
"""

    @staticmethod
    def generate_pandas_template(csv_path: str) -> str:
        """Generate a pandas template for CSV analysis."""
        return f"""import pandas as pd

# Load data
df = pd.read_csv("{csv_path}")

# Basic exploration
print(f"Shape: {{df.shape}}")
print(f"Columns: {{list(df.columns)}}")
print(df.head())
print(df.describe())

# Check for missing values
print(f"Missing values:\\n{{df.isnull().sum()}}")
"""


class UnstructuredDevTools:
    """Tools for unstructured data development in sandbox."""

    @staticmethod
    def generate_image_batch_template(input_dir: str, output_dir: str) -> str:
        """Generate template for batch image processing."""
        return f"""from pathlib import Path
import json

input_dir = Path("{input_dir}")
output_dir = Path("{output_dir}")
output_dir.mkdir(parents=True, exist_ok=True)

results = []
for img_path in input_dir.glob("*"):
    if img_path.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".tiff"):
        # Process each image
        result = {{
            "filename": img_path.name,
            "size_bytes": img_path.stat().st_size,
        }}
        results.append(result)

# Save processing report
report_path = output_dir / "processing_report.json"
report_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
print(f"Processed {{len(results)}} images. Report: {{report_path}}")
"""

    @staticmethod
    def generate_document_extract_template(input_dir: str, output_dir: str) -> str:
        """Generate template for document text extraction."""
        return f"""from pathlib import Path
import subprocess
import json

input_dir = Path("{input_dir}")
output_dir = Path("{output_dir}")
output_dir.mkdir(parents=True, exist_ok=True)

results = []
for doc_path in input_dir.glob("*.pdf"):
    try:
        result = subprocess.run(
            ["pdftotext", str(doc_path), "-"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            text = result.stdout
            out_file = output_dir / f"{{doc_path.stem}}.txt"
            out_file.write_text(text)
            results.append({{"file": doc_path.name, "chars": len(text)}})
    except Exception as e:
        results.append({{"file": doc_path.name, "error": str(e)}})

print(f"Extracted {{len(results)}} documents")
"""


class SemiStructuredDevTools:
    """Tools for semi-structured data development in sandbox."""

    @staticmethod
    def generate_json_etl_template(input_path: str, output_path: str) -> str:
        """Generate template for JSON ETL pipeline."""
        return f"""import json
from pathlib import Path

# Load
data = json.loads(Path("{input_path}").read_text())

# Transform
if isinstance(data, list):
    # Flatten nested structures
    flattened = []
    for item in data:
        flat_item = {{}}
        for k, v in item.items():
            if isinstance(v, dict):
                for sk, sv in v.items():
                    flat_item[f"{{k}}_{{sk}}"] = sv
            elif isinstance(v, list):
                flat_item[k] = json.dumps(v)
            else:
                flat_item[k] = v
        flattened.append(flat_item)
    output = flattened
else:
    output = data

# Save
Path("{output_path}").write_text(json.dumps(output, indent=2, ensure_ascii=False))
print(f"Transformed {{len(output) if isinstance(output, list) else 1}} records")
"""

    @staticmethod
    def generate_csv_transform_template(input_path: str, output_path: str) -> str:
        """Generate template for CSV transformation."""
        return f"""import csv
import json
from pathlib import Path

# Read
rows = []
with open("{input_path}", "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        rows.append(row)

print(f"Loaded {{len(rows)}} rows")
print(f"Columns: {{list(rows[0].keys()) if rows else []}}")

# Transform (example: filter, clean, aggregate)
transformed = rows  # Apply your transformations here

# Save
with open("{output_path}", "w", newline="", encoding="utf-8") as f:
    if transformed:
        writer = csv.DictWriter(f, fieldnames=transformed[0].keys())
        writer.writeheader()
        writer.writerows(transformed)

print(f"Saved {{len(transformed)}} rows to {output_path}")
"""


# Singleton instances
dev_sandbox = DataProductDevSandbox()
structured_tools = StructuredDevTools()
unstructured_tools = UnstructuredDevTools()
semi_structured_tools = SemiStructuredDevTools()
