"""Code Scanner — AST-based static analysis for sandbox-submitted code.

Implements SS-03 §5 design: whitelist mode for Python, SELECT-only for SQL.
Python: AST walk checking imports, builtins, and save paths.
SQL: sqlparse statement type validation per sandbox mode.
"""
import ast
import re
import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ScanSeverity(str, Enum):
    FATAL = "FATAL"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class ScanIssue:
    severity: ScanSeverity
    code: str
    message: str
    line: int | None = None


@dataclass
class ScanResult:
    passed: bool
    issues: list[ScanIssue] = field(default_factory=list)

    @classmethod
    def PASS(cls):
        return cls(passed=True)

    @classmethod
    def FAIL(cls, message: str = "", issues: list[ScanIssue] | None = None):
        if message and not issues:
            issues = [ScanIssue(ScanSeverity.FATAL, "SCAN_FAILED", message)]
        return cls(passed=False, issues=issues or [])


class SandboxMode(str, Enum):
    STRUCTURED_QUERY = "structured_query"
    STRUCTURED_MODELING = "structured_modeling"
    STRUCTURED_APP = "structured_app"
    PRODUCT_DEVELOPMENT = "product_development"
    LLM_SFT = "llm_sft"
    LLM_PT = "llm_pretrain"
    VISION_TRAIN = "vision_train"
    MULTIMODAL_TRAIN = "multimodal_train"
    SEMI_ETL = "semi_structured_etl"


# ── Python Security Rules (Pure Whitelist Mode) ────────────────────
# NIST SP 800-53: whitelist-only. Anything not in ALLOWED_IMPORTS is FATAL.
# No separate blacklist needed — the whitelist IS the gate.

ALLOWED_IMPORTS = {
    # Data science
    "numpy", "pandas", "scipy", "sklearn", "xgboost", "lightgbm",
    # ML/AI training
    "torch", "torchvision", "transformers", "datasets", "peft",
    "trl", "accelerate", "deepspeed",
    # Visualization
    "matplotlib", "seaborn", "plotly",
    # Standard library (safe subset)
    "json", "re", "math", "datetime", "collections",
    "typing", "dataclasses", "functools", "itertools",
    "io", "copy", "abc", "enum", "pathlib",
    "struct", "hashlib", "hmac", "secrets",
    "decimal", "fractions", "statistics",
    "string", "textwrap", "unicodedata",
    "uuid", "base64", "binascii",
    # Database (sandbox-internal)
    "sqlalchemy",
    # Differential privacy
    "diffprivlib", "opacus",
    # Multimedia
    "PIL", "cv2", "librosa", "torchaudio",
    # Arrow/Parquet
    "pyarrow", "pyarrow.parquet",
    # DuckDB
    "duckdb",
}

# Dangerous builtins (call → FATAL)
DANGEROUS_BUILTINS = {"exec", "eval", "compile", "__import__", "open", "globals", "locals", "breakpoint"}

# Training modes where save paths must be sandbox-restricted
GPU_MODES = {
    SandboxMode.LLM_SFT, SandboxMode.LLM_PT,
    SandboxMode.VISION_TRAIN, SandboxMode.MULTIMODAL_TRAIN,
}

# Valid sandbox output paths
SANDBOX_OUTPUT_PATHS = {"/sandbox/output/", "/sandbox/checkpoints/"}


class CodeScanner:
    """AST-based static code analyzer for sandbox submissions."""

    def scan(self, code: str, language: str, sandbox_mode: str) -> ScanResult:
        try:
            mode = SandboxMode(sandbox_mode)
        except ValueError:
            return ScanResult.FAIL(f"Unknown sandbox mode: {sandbox_mode}")

        if language == "python":
            return self._scan_python(code, mode)
        elif language == "sql":
            return self._scan_sql(code, mode)
        else:
            return ScanResult.FAIL(f"Unsupported language: {language}")

    def _scan_python(self, code: str, mode: SandboxMode) -> ScanResult:
        issues: list[ScanIssue] = []

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return ScanResult.FAIL(f"Syntax error at line {e.lineno}: {e.msg}")

        for node in ast.walk(tree):
            # ── Import checks (pure whitelist mode) ──
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = self._extract_modules(node)
                for mod in modules:
                    top = mod.split(".")[0]
                    if not top:
                        continue
                    if top not in ALLOWED_IMPORTS:
                        issues.append(ScanIssue(
                            ScanSeverity.FATAL, "NOT_WHITELISTED",
                            f"Module '{top}' is not in the sandbox whitelist", node.lineno,
                        ))

            # ── Dangerous builtin checks ──
            if isinstance(node, ast.Call):
                func_name = self._get_call_name(node)
                if func_name in DANGEROUS_BUILTINS:
                    issues.append(ScanIssue(
                        ScanSeverity.FATAL, "DANGEROUS_BUILTIN",
                        f"Builtin '{func_name}()' is not allowed", node.lineno,
                    ))

            # ── Training mode: save path restrictions ──
            if mode in GPU_MODES:
                if isinstance(node, ast.Call):
                    func_name = self._get_call_name(node)
                    if func_name in ("save", "save_pretrained", "save_checkpoint"):
                        if not self._has_valid_save_path(node):
                            issues.append(ScanIssue(
                                ScanSeverity.FATAL, "INVALID_SAVE_PATH",
                                f"'{func_name}' must target a sandbox output path "
                                f"({', '.join(SANDBOX_OUTPUT_PATHS)})", node.lineno,
                            ))

            # ── Attribute-based dangerous access ──
            if isinstance(node, ast.Attribute):
                if node.attr in ("__subclasses__", "__bases__", "__mro__"):
                    issues.append(ScanIssue(
                        ScanSeverity.FATAL, "DANGEROUS_ATTR",
                        f"Access to '{node.attr}' is not allowed", node.lineno,
                    ))

        fatal = [i for i in issues if i.severity == ScanSeverity.FATAL]
        if fatal:
            return ScanResult(passed=False, issues=issues)
        return ScanResult(passed=True, issues=[i for i in issues if i.severity != ScanSeverity.FATAL])

    def _scan_sql(self, sql: str, mode: SandboxMode) -> ScanResult:
        """SQL validation using regex-based statement detection (no sqlparse dependency)."""
        issues: list[ScanIssue] = []
        sql_upper = sql.strip().upper()

        # Extract statement types
        statements = self._extract_sql_statements(sql)

        for stmt_type, stmt_text in statements:
            if mode == SandboxMode.STRUCTURED_QUERY:
                if stmt_type not in ("SELECT", "WITH", "EXPLAIN"):
                    issues.append(ScanIssue(
                        ScanSeverity.FATAL, "SQL_FORBIDDEN_STMT",
                        f"Only SELECT allowed in query mode, got {stmt_type}",
                    ))

            elif mode == SandboxMode.STRUCTURED_MODELING:
                if stmt_type not in ("SELECT", "WITH", "CREATE", "INSERT", "EXPLAIN", "DROP", "ALTER"):
                    issues.append(ScanIssue(
                        ScanSeverity.FATAL, "SQL_FORBIDDEN_STMT",
                        f"Statement type {stmt_type} not allowed in modeling mode",
                    ))

            # All modes: forbid file I/O
            if "INTO OUTFILE" in stmt_text or "LOAD DATA" in stmt_text:
                issues.append(ScanIssue(
                    ScanSeverity.FATAL, "SQL_FILE_IO",
                    "File I/O in SQL is forbidden",
                ))

            # Forbid system catalog access
            if "PG_" in stmt_text or "INFORMATION_SCHEMA" in stmt_text:
                issues.append(ScanIssue(
                    ScanSeverity.FATAL, "SQL_CATALOG_ACCESS",
                    "System catalog access is not allowed",
                ))

        fatal = [i for i in issues if i.severity == ScanSeverity.FATAL]
        if fatal:
            return ScanResult(passed=False, issues=issues)
        return ScanResult.PASS()

    # ── Helpers ─────────────────────────────────────────────────────

    def _extract_modules(self, node: ast.AST) -> list[str]:
        """Extract module names from Import/ImportFrom nodes."""
        if isinstance(node, ast.Import):
            return [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            return [node.module] if node.module else []
        return []

    def _get_call_name(self, node: ast.Call) -> str:
        """Extract the function name from a Call node."""
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return ""

    def _has_valid_save_path(self, node: ast.Call) -> bool:
        """Check if a save() call targets a valid sandbox path."""
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if any(arg.value.startswith(p) for p in SANDBOX_OUTPUT_PATHS):
                    return True
        for kw in node.keywords:
            if kw.arg in ("path", "save_directory", "output_dir"):
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    if any(kw.value.value.startswith(p) for p in SANDBOX_OUTPUT_PATHS):
                        return True
        return False

    def _extract_sql_statements(self, sql: str) -> list[tuple[str, str]]:
        """Extract (statement_type, statement_text) pairs from SQL."""
        statements = []
        # Split on semicolons, ignoring empty segments
        for segment in sql.split(";"):
            segment = segment.strip()
            if not segment:
                continue
            # First keyword is the statement type
            first_word = segment.split()[0].upper() if segment.split() else ""
            statements.append((first_word, segment.upper()))
        return statements


code_scanner = CodeScanner()
