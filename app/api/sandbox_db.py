"""Sandbox Database API — DuckDB in-memory SQL execution with masked views + SM4 column encryption."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.deps import get_current_user, require_roles
from app.models.user import User, UserRole
from app.services.secure_duckdb import SecureDuckDBEngine, MaskRule, QueryResult, cleanup_expired_engines

router = APIRouter()

# In-memory engine registry (per-session)
_engines: dict[str, SecureDuckDBEngine] = {}


class CreateTableRequest(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    table_name: str = Field(..., min_length=1, max_length=64, pattern="^[a-zA-Z_][a-zA-Z0-9_]*$")
    data: list[dict] = Field(..., min_length=1)


class QueryRequest(BaseModel):
    session_id: str
    sql: str = Field(..., min_length=1, max_length=10000)
    limit: int = Field(default=1000, ge=1, le=10000)


class MaskRuleRequest(BaseModel):
    field_name: str
    mask_type: str = Field(..., pattern="^(HASH|REDACT|GENERALIZE|PASSTHROUGH)$")
    top_k: int = Field(default=10, ge=1, le=1000)


class MaskedViewRequest(BaseModel):
    session_id: str
    table_name: str
    view_name: str | None = None
    rules: list[MaskRuleRequest]


@router.post("/create-table")
async def create_table(
    body: CreateTableRequest,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.DATA_PROVIDER)),
):
    """Create an in-memory DuckDB table from JSON data."""
    session_id = body.session_id

    if session_id not in _engines:
        _engines[session_id] = SecureDuckDBEngine(session_id, mode="memory")

    engine = _engines[session_id]
    try:
        info = engine.register_table(body.table_name, body.data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "session_id": session_id,
        "table_name": info.table_name,
        "row_count": info.row_count,
        "column_count": info.column_count,
    }


@router.post("/query")
async def execute_query(
    body: QueryRequest,
    current_user: User = Depends(get_current_user),
):
    """Execute a SQL query on a sandbox DuckDB instance."""
    # Lazy TTL cleanup: remove expired engines (1h default)
    cleanup_expired_engines(_engines)

    engine = _engines.get(body.session_id)
    if not engine:
        raise HTTPException(status_code=404, detail=f"Session {body.session_id} not found")

    result = engine.execute_query(body.sql, limit=body.limit)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)

    return {
        "columns": result.columns,
        "rows": result.rows,
        "row_count": result.row_count,
        "duration_ms": result.duration_ms,
        "truncated": result.truncated,
    }


@router.post("/masked-view")
async def create_masked_view(
    body: MaskedViewRequest,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Create a masked view with field-level data masking."""
    engine = _engines.get(body.session_id)
    if not engine:
        raise HTTPException(status_code=404, detail=f"Session {body.session_id} not found")

    rules = [MaskRule(field_name=r.field_name, mask_type=r.mask_type, top_k=r.top_k) for r in body.rules]
    engine.set_mask_rules(body.table_name, rules)

    view_name = engine.create_masked_view(body.table_name, body.view_name)
    return {
        "session_id": body.session_id,
        "view_name": view_name,
        "rules_applied": len(rules),
    }


@router.get("/sessions/{session_id}/tables")
async def list_tables(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    """List tables in a sandbox session."""
    engine = _engines.get(session_id)
    if not engine:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    tables = engine.list_tables()
    return [
        {
            "table_name": t.table_name,
            "source": t.source,
            "row_count": t.row_count,
            "column_count": t.column_count,
        }
        for t in tables
    ]


@router.delete("/sessions/{session_id}")
async def close_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    """Close and cleanup a sandbox DuckDB session."""
    engine = _engines.pop(session_id, None)
    if engine:
        engine.close()
    return {"session_id": session_id, "closed": True}


# === SM4 Column Encryption + Multi-Source Import ===

class EncryptedTableRequest(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    table_name: str = Field(..., min_length=1, max_length=64, pattern="^[a-zA-Z_][a-zA-Z0-9_]*$")
    data: list[dict] = Field(..., min_length=1)
    encrypted_columns: dict[str, str] = Field(
        default_factory=dict,
        description="Map of column_name -> 'sm4-siv'|'sm4-gcm'"
    )
    dek_hex: str | None = Field(None, description="16-byte DEK as hex string. Auto-generated if empty.")


class ImportJsonRequest(BaseModel):
    session_id: str
    table_name: str = Field(..., min_length=1, max_length=64, pattern="^[a-zA-Z_][a-zA-Z0-9_]*$")
    json_path: str
    encrypted_columns: dict[str, str] = Field(default_factory=dict)
    dek_hex: str | None = None


class ImportParquetRequest(BaseModel):
    session_id: str
    table_name: str = Field(..., min_length=1, max_length=64, pattern="^[a-zA-Z_][a-zA-Z0-9_]*$")
    parquet_path: str
    encrypted_columns: dict[str, str] = Field(default_factory=dict)
    dek_hex: str | None = None


class ImportPostgresRequest(BaseModel):
    session_id: str
    table_name: str = Field(..., min_length=1, max_length=64, pattern="^[a-zA-Z_][a-zA-Z0-9_]*$")
    pg_connstr: str
    pg_table: str
    encrypted_columns: dict[str, str] = Field(default_factory=dict)
    dek_hex: str | None = None


class ImportS3Request(BaseModel):
    session_id: str
    table_name: str = Field(..., min_length=1, max_length=64, pattern="^[a-zA-Z_][a-zA-Z0-9_]*$")
    s3_url: str
    s3_key_id: str = ""
    s3_secret: str = ""
    file_format: str = Field("parquet", pattern="^(parquet|csv|json)$")
    encrypted_columns: dict[str, str] = Field(default_factory=dict)
    dek_hex: str | None = None


class EncryptedQueryRequest(BaseModel):
    session_id: str
    sql: str = Field(..., min_length=1, max_length=10000)
    limit: int = Field(default=1000, ge=1, le=10000)


def _get_engine(session_id: str) -> SecureDuckDBEngine:
    """Get or create a DuckDB engine for a session.

    P1-3: Auto-loads policy from the session's contract if available.
    """
    if session_id not in _engines:
        engine = SecureDuckDBEngine(session_id, mode="memory")
        # P1-3: Try to load policy from session contract
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            loop.create_task(_attach_policy(session_id, engine))
        except RuntimeError:
            pass
        _engines[session_id] = engine
    return _engines[session_id]


async def _attach_policy(session_id: str, engine: SecureDuckDBEngine) -> None:
    """Attach contract policy to a DuckDB engine (P1-3)."""
    try:
        from app.core.database import async_session
        from app.models.sandbox_session import SandboxSession
        from app.models.contract import Contract
        from app.services.policy_compiler import policy_compiler
        from sqlalchemy import select
        async with async_session() as db:
            result = await db.execute(
                select(SandboxSession).where(SandboxSession.id == uuid.UUID(session_id))
            )
            session = result.scalar_one_or_none()
            if session and session.contract_id:
                contract_result = await db.execute(
                    select(Contract).where(Contract.id == session.contract_id)
                )
                contract = contract_result.scalar_one_or_none()
                if contract:
                    policy_bundle = policy_compiler.compile_contract(contract)
                    engine.set_policy(policy_bundle)
                    if hasattr(session, "sandbox_level"):
                        engine._sandbox_level = session.sandbox_level or "L3"
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"Policy auto-attach skipped for session {session_id}: {e}")


def _parse_dek(dek_hex: str | None) -> bytes | None:
    """Parse DEK from hex string, or return None for auto-generation."""
    if not dek_hex:
        return None
    return bytes.fromhex(dek_hex)


def get_engine(session_id: str) -> SecureDuckDBEngine | None:
    """Get existing engine for a session (no auto-create). Used by sandbox_sessions."""
    return _engines.get(session_id)


def get_encryption_config_for_session(session_id: str) -> dict | None:
    """Get encryption config dict for sandbox injection.

    Returns the first encryption config found (DEK + encrypted columns).
    Used by sandbox_sessions.py to inject SM4 DEK into bwrap sandbox.
    """
    engine = _engines.get(session_id)
    if not engine:
        return None
    configs = engine.get_all_encryption_configs()
    if not configs:
        return None
    # Return the first config (most sessions have one encrypted table)
    first_table = next(iter(configs))
    return configs[first_table]


@router.post("/create-encrypted-table")
async def create_encrypted_table(
    body: EncryptedTableRequest,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.DATA_PROVIDER)),
):
    """Create a DuckDB table with SM4 column-level encryption.

    Sensitive columns are encrypted with SM4-SIV (deterministic, supports equality queries)
    or SM4-GCM (randomized, maximum security). Encrypted columns are stored as BLOB.
    """
    engine = _get_engine(body.session_id)
    dek = _parse_dek(body.dek_hex)

    try:
        info = engine.register_table_encrypted(
            body.table_name, body.data,
            encrypted_columns=body.encrypted_columns,
            dek=dek,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    meta = info.columns_meta if hasattr(info, 'columns_meta') else {}
    return {
        "session_id": body.session_id,
        "table_name": info.table_name,
        "row_count": info.row_count,
        "column_count": info.column_count,
        "encrypted_columns": meta.get("encrypted_columns", {}),
        "key_id": meta.get("key_id", ""),
    }


@router.post("/import/json")
async def import_json(
    body: ImportJsonRequest,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.DATA_PROVIDER)),
):
    """Import a JSON file into DuckDB with optional SM4 column encryption."""
    engine = _get_engine(body.session_id)
    dek = _parse_dek(body.dek_hex)

    try:
        info = engine.register_json(
            body.table_name, body.json_path,
            encrypted_columns=body.encrypted_columns,
            dek=dek,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "session_id": body.session_id,
        "table_name": info.table_name,
        "row_count": info.row_count,
        "column_count": info.column_count,
    }


@router.post("/import/parquet")
async def import_parquet(
    body: ImportParquetRequest,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.DATA_PROVIDER)),
):
    """Import a Parquet file into DuckDB with optional SM4 column encryption."""
    engine = _get_engine(body.session_id)
    dek = _parse_dek(body.dek_hex)

    try:
        info = engine.register_parquet(
            body.table_name, body.parquet_path,
            encrypted_columns=body.encrypted_columns,
            dek=dek,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "session_id": body.session_id,
        "table_name": info.table_name,
        "row_count": info.row_count,
        "column_count": info.column_count,
    }


@router.post("/import/postgres")
async def import_postgres(
    body: ImportPostgresRequest,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Import a PostgreSQL table via postgres_scanner with optional SM4 encryption."""
    engine = _get_engine(body.session_id)
    dek = _parse_dek(body.dek_hex)

    try:
        info = engine.register_from_postgres(
            body.table_name, body.pg_connstr, body.pg_table,
            encrypted_columns=body.encrypted_columns,
            dek=dek,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "session_id": body.session_id,
        "table_name": info.table_name,
        "row_count": info.row_count,
        "column_count": info.column_count,
    }


@router.post("/import/s3")
async def import_s3(
    body: ImportS3Request,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Import data from S3/MinIO via httpfs with optional SM4 encryption."""
    engine = _get_engine(body.session_id)
    dek = _parse_dek(body.dek_hex)

    try:
        info = engine.register_from_s3(
            body.table_name, body.s3_url,
            s3_key_id=body.s3_key_id, s3_secret=body.s3_secret,
            file_format=body.file_format,
            encrypted_columns=body.encrypted_columns,
            dek=dek,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "session_id": body.session_id,
        "table_name": info.table_name,
        "row_count": info.row_count,
        "column_count": info.column_count,
    }


@router.post("/query-decrypted")
async def query_decrypted(
    body: EncryptedQueryRequest,
    current_user: User = Depends(get_current_user),
):
    """Execute a query with automatic decryption of SM4-encrypted columns.

    Transparently decrypts SM4-SIV and SM4-GCM encrypted columns in query results.
    """
    engine = _engines.get(body.session_id)
    if not engine:
        raise HTTPException(status_code=404, detail=f"Session {body.session_id} not found")

    result = engine.execute_query_decrypted(body.sql, limit=body.limit)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)

    return {
        "columns": result.columns,
        "rows": result.rows,
        "row_count": result.row_count,
        "duration_ms": result.duration_ms,
        "truncated": result.truncated,
    }


@router.get("/sessions/{session_id}/encryption-metadata")
async def get_encryption_metadata(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get encryption metadata for all tables in a session."""
    engine = _engines.get(session_id)
    if not engine:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    metadata = {}
    for table in engine.list_tables():
        meta = engine.get_encryption_metadata(table.table_name)
        if meta:
            metadata[table.table_name] = meta

    return {"session_id": session_id, "encrypted_tables": metadata}


@router.get("/sessions/{session_id}/encryption-config")
async def get_encryption_config(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get encryption config for sandbox injection (DEK hex + encrypted column map).

    Used by the sandbox execution layer to inject SM4 keys via environment variables.
    The DEK is never written to disk — it's passed via bwrap --setenv.
    """
    engine = _engines.get(session_id)
    if not engine:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    configs = engine.get_all_encryption_configs()
    return {"session_id": session_id, "encryption_configs": configs}


class ImportPostgresSandboxRequest(BaseModel):
    session_id: str
    table_name: str = Field(..., min_length=1, max_length=64, pattern="^[a-zA-Z_][a-zA-Z0-9_]*$")
    pg_connstr: str
    pg_table: str
    encrypted_columns: dict[str, str] = Field(default_factory=dict)
    dek_hex: str | None = None
    network_allowed: bool = Field(default=False, description="Must be true; sandbox network policy must allow the target host.")


class ImportS3SandboxRequest(BaseModel):
    session_id: str
    table_name: str = Field(..., min_length=1, max_length=64, pattern="^[a-zA-Z_][a-zA-Z0-9_]*$")
    s3_url: str
    s3_key_id: str = ""
    s3_secret: str = ""
    file_format: str = Field("parquet", pattern="^(parquet|csv|json)$")
    encrypted_columns: dict[str, str] = Field(default_factory=dict)
    dek_hex: str | None = None
    network_allowed: bool = Field(default=False, description="Must be true; sandbox network policy must allow the target endpoint.")


@router.post("/import/postgres-sandbox")
async def import_postgres_sandbox(
    body: ImportPostgresSandboxRequest,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Import PostgreSQL data with sandbox network policy enforcement.

    Requires network_allowed=true — the caller must verify that the sandbox
    network policy allows outbound connections to the PostgreSQL host.
    """
    engine = _get_engine(body.session_id)
    dek = _parse_dek(body.dek_hex)

    try:
        info = engine.register_from_postgres_sandbox(
            body.table_name, body.pg_connstr, body.pg_table,
            encrypted_columns=body.encrypted_columns,
            dek=dek,
            network_allowed=body.network_allowed,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "session_id": body.session_id,
        "table_name": info.table_name,
        "row_count": info.row_count,
        "column_count": info.column_count,
    }


@router.post("/import/s3-sandbox")
async def import_s3_sandbox(
    body: ImportS3SandboxRequest,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Import S3/MinIO data with sandbox network policy enforcement.

    Requires network_allowed=true — the caller must verify that the sandbox
    network policy allows outbound connections to the S3 endpoint.
    """
    engine = _get_engine(body.session_id)
    dek = _parse_dek(body.dek_hex)

    try:
        info = engine.register_from_s3_sandbox(
            body.table_name, body.s3_url,
            s3_key_id=body.s3_key_id, s3_secret=body.s3_secret,
            file_format=body.file_format,
            encrypted_columns=body.encrypted_columns,
            dek=dek,
            network_allowed=body.network_allowed,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "session_id": body.session_id,
        "table_name": info.table_name,
        "row_count": info.row_count,
        "column_count": info.column_count,
    }
