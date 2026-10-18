"""Secure DuckDB Engine — TEE内加密数据库 + 字段级脱敏视图 + SM4 列级加密。

SS-04 §3.1 沙箱内加密数据库实现。
数据在 TEE 内存中以明文存在，TEE 外以密文存在。
支持：内存模式 / tmpfs落盘 / 持久加密DB 三种方案。

集成 SM4-SIV（确定性加密，支持等值查询）和 SM4-GCM（随机加密，最高安全性）。
支持多数据源导入：JSON/Parquet/CSV/PostgreSQL/ClickHouse/MinIO。
"""
import json
import re
import uuid
import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

try:
    import duckdb
    _HAS_DUCKDB = True
except ImportError:
    _HAS_DUCKDB = False
    logger.warning("duckdb not installed — SecureDuckDBEngine is unavailable")


@dataclass
class MaskRule:
    """Field-level masking rule."""
    field_name: str
    mask_type: str  # HASH | REDACT | GENERALIZE | PASSTHROUGH
    top_k: int = 10  # for GENERALIZE_TOP_K


@dataclass
class QueryResult:
    """SQL query execution result."""
    success: bool
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    duration_ms: int = 0
    error: str | None = None
    truncated: bool = False


@dataclass
class TableInfo:
    """Registered table metadata."""
    table_name: str
    source: str  # product_id or file path
    row_count: int
    column_count: int
    columns: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 合约策略状态机 — 数据出域门的 fail-closed 前提（A48）
#
# 「这个会话有没有治理合约」由 API 层（app/api/sandbox_db.py）在建好引擎、执行
# 任何一条 SQL 之前解析并登记在引擎上；引擎只忠实记录，绝不「没策略就放行」：
#   unset         引擎由调用方自建、从未经过策略解析（进程内/测试用法）→ 不施加策略
#   not_required  已解析：会话没有治理合约 → 合法无策略路径，放行
#   required      已解析：会话受合约治理，但策略尚未挂上 → 拒绝执行任何查询
#   attached      已挂编译后的策略 → 逐条按策略判定
# 状态只能单向推进：已 attached 的引擎不得被 not_required/required 回填，否则并发
# 解析会把已经挂上的门重新打开。
# ---------------------------------------------------------------------------
POLICY_UNSET = "unset"
POLICY_NOT_REQUIRED = "not_required"
POLICY_REQUIRED = "required"
POLICY_ATTACHED = "attached"

POLICY_MISSING_REASON = (
    "contract policy is required for this session but was never attached "
    "(fail-closed: refusing to run SQL without the governing policy)"
)

# 合约 allowed_operations 词表里「在沙箱里跑一条只读 SQL」的等价说法。与
# app/api/sandbox_sessions._operation_allowed_by_contract 对 structured_query 的
# 映射同源：合约写 read / query / analyze 都表示放行数据读取；train / build /
# export / api_call 不在其列 —— 只准训练数据的合约不能把明细查出去。
CONTRACT_READ_OPERATIONS = ("read", "query", "analyze")

# 导入路径白名单（A50）：json_path / parquet_path 来自请求体，属于调用者可控输入。
# 平台自己写数据只落在下面这些目录里，因此它们就是导入根；/app/.env、
# /proc/self/environ 之类的路径一律拒绝 —— 那等于「中权限任意文件读 + /query 导出」。
DEFAULT_IMPORT_ROOTS: tuple[str, ...] = (
    "/tmp/cds-uploads",      # data_pipeline 上传根
    "/tmp/cds-sandbox",      # L3 会话工作区（含 data-load / input / output）
    "/tmp/cds-sandbox-l0",   # L0 会话工作区
    "/tmp/cds-storage",      # 对象存储本地回退
    "/tmp/cds-pipeline",     # 非结构化流水线
    "/tmp/cds-duckdb",       # 本引擎的 tmpfs 目录
)
IMPORT_ROOTS_ENV = "CDS_SANDBOX_IMPORT_ROOTS"  # os.pathsep 分隔的**追加**根（部署/测试）
MAX_IMPORT_BYTES = 64 * 1024 * 1024  # 单个导入文件的读入上限（旧实现无上限全量缓冲）


def allowed_import_roots() -> list[Path]:
    """导入根白名单：平台自有数据目录 + 环境变量追加根 + Cube 结果目录。

    解析失败（权限/挂载异常）的根直接丢弃：宁可少一个可用根，不能多一个越界根。
    """
    raw = list(DEFAULT_IMPORT_ROOTS)
    raw += [p for p in os.environ.get(IMPORT_ROOTS_ENV, "").split(os.pathsep) if p.strip()]
    try:
        from app.core.config import get_settings
        cube_dir = get_settings().CUBE_RESULTS_DIR
        if cube_dir:
            raw.append(cube_dir)
    except Exception as e:  # 配置读不到就退回默认根（更严，不是更松）
        logger.debug("[secure-duckdb] CUBE_RESULTS_DIR unavailable for import roots: %s", e)

    roots: list[Path] = []
    for item in raw:
        try:
            roots.append(Path(item).resolve())
        except OSError:
            continue
    return roots


class SecureDuckDBEngine:
    """TEE 内的 DuckDB 实例，对外提供安全 SQL 执行接口。

    三种运行模式：
    1. memory: 全内存，不落盘（L1 TEE / 小数据集）
    2. tmpfs: tmpfs 落盘（L2/L3，加密 tmpfs）
    3. persistent: 持久加密 DB（数商开发沙箱）
    """

    def __init__(self, session_id: str, mode: str = "memory",
                 db_path: str | None = None, dek: bytes | None = None):
        if not _HAS_DUCKDB:
            raise RuntimeError("duckdb not installed")

        self.session_id = session_id
        self.mode = mode
        self._mask_rules: dict[str, list[MaskRule]] = {}  # table -> rules
        self._tables: dict[str, TableInfo] = {}
        self._max_rows = 10000  # Result set limit
        self._db_path: str | None = None
        self._dek = dek  # SM4-GCM key for disk-level encryption
        self._created_at = datetime.now(timezone.utc)
        self._policy = None  # PolicyBundle for query-time policy enforcement (P1-3)
        self._policy_state = POLICY_UNSET  # A48 fail-closed 执行门，见模块常量
        self._policy_note = ""
        self._sandbox_level = "L3"  # Overridden by session context
        # A56: 会话归属随引擎生命周期走（旧的 _engine_owners 平行字典没人清扫）。
        self.owner_user_id: str | None = None
        self.max_import_bytes = MAX_IMPORT_BYTES

        if mode == "memory":
            self._conn = duckdb.connect(":memory:")
        elif mode == "tmpfs":
            path = db_path or f"/tmp/cds-duckdb/{session_id}/sandbox.db"
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._db_path = path
            # If encrypted file exists and we have a DEK, decrypt before opening
            enc_path = Path(path + ".enc")
            if dek and enc_path.exists():
                _decrypt_db_file(enc_path, Path(path), dek)
            self._conn = duckdb.connect(path)
        elif mode == "persistent":
            if not db_path:
                raise ValueError("db_path required for persistent mode")
            self._conn = duckdb.connect(db_path)
        else:
            raise ValueError(f"Invalid mode: {mode}. Use memory/tmpfs/persistent")

        # Register SM3 UDF for 国密 compliant hashing (replaces md5)
        self._has_sm3_udf = False
        self._register_sm3_udf()

    def _register_sm3_udf(self):
        """Register SM3 hash as a DuckDB UDF — 国密 GM/T standard替代 md5.

        Falls back to built-in sha256() if numpy is unavailable (required by DuckDB UDFs).
        """
        try:
            from app.services.crypto_service import crypto_service
            def sm3_hash(text: str | None) -> str | None:
                if text is None:
                    return None
                return crypto_service.sm3_hash(text.encode("utf-8"))
            self._conn.create_function("sm3", sm3_hash)
            self._has_sm3_udf = True
        except Exception as e:
            logger.info(f"[secure-duckdb] SM3 UDF unavailable ({e}), masking uses built-in sha256()")

    @property
    def policy_state(self) -> str:
        """当前策略状态：unset / not_required / required / attached（可观测，便于诚实披露）。"""
        return self._policy_state

    @property
    def policy_contract_id(self) -> str | None:
        """已挂策略所属的合约 ID；未挂策略时为 None。"""
        return getattr(self._policy, "contract_id", None) if self._policy is not None else None

    def set_policy(self, policy_bundle) -> None:
        """挂接编译后的合约策略；此后每条 SQL 都受它约束（P1-3）。

        传 None 过去过去等价于「策略缺失即放行」，现在直接拒绝：无策略要么是
        not_required（显式登记），要么是 required（拒绝执行），不存在第三种。
        """
        if policy_bundle is None:
            raise ValueError(
                "set_policy(None) would open the execution gate — "
                "call mark_policy_not_required() instead when the session has no contract"
            )
        self._policy = policy_bundle
        self._policy_state = POLICY_ATTACHED
        self._policy_note = ""

    def mark_policy_not_required(self, reason: str = "") -> None:
        """登记「本会话无治理合约」—— 合法无策略路径，与「策略缺失」不是一回事（A48）。"""
        if self._policy_state in (POLICY_UNSET, POLICY_NOT_REQUIRED):
            self._policy_state = POLICY_NOT_REQUIRED
            self._policy_note = reason

    def require_contract_policy(self) -> None:
        """登记「本会话受合约治理」：策略挂上之前拒绝执行任何查询（fail-closed，A48）。"""
        if self._policy_state != POLICY_ATTACHED:
            self._policy_state = POLICY_REQUIRED

    def set_sandbox_level(self, sandbox_level: str | None) -> None:
        """用会话上下文覆盖策略判定用的沙箱等级（合约 sandbox_levels 判定用）。"""
        if sandbox_level:
            self._sandbox_level = sandbox_level

    def _check_policy(self, sql: str, row_count: int) -> tuple[bool, str]:
        """Check query against active policy. Returns (allowed, reason).

        A48：这里曾经是 `if not self._policy: return True` —— 合约策略没挂上就
        直接放行，整个合约限制链路等于不存在。现在会话被登记为「受合约治理」而
        策略缺席 ⇒ 拒绝执行。
        """
        if self._policy is None:
            if self._policy_state == POLICY_REQUIRED:
                return False, POLICY_MISSING_REASON
            return True, "No policy set"

        from app.services.policy_compiler import policy_evaluator
        base = {
            "sandbox_level": self._sandbox_level,
            "row_count": row_count,
            "field": self._extract_select_fields(sql),
        }
        result = {"allowed": False, "reason": "policy evaluation returned no decision"}
        for operation in CONTRACT_READ_OPERATIONS:
            result = policy_evaluator.evaluate(self._policy, {**base, "operation": operation})
            if result["allowed"]:
                return True, result["reason"]
        return False, result["reason"]

    @staticmethod
    def _extract_select_fields(sql: str) -> str:
        """Extract first field name from SELECT clause for policy ACL check."""
        import re
        match = re.search(r"SELECT\s+(?:DISTINCT\s+)?(.+?)\s+FROM", sql, re.IGNORECASE | re.DOTALL)
        if match:
            fields_str = match.group(1).strip()
            if fields_str == "*":
                return "*"
            first_field = fields_str.split(",")[0].strip().split(".")[-1].strip().strip('"').strip("'")
            return first_field
        return "*"

    def register_table(self, table_name: str, data: list[dict], source: str = "upload") -> TableInfo:
        """Register a list of dicts as a DuckDB table.

        Args:
            table_name: Sanitized table name
            data: List of row dicts
            source: Data source identifier
        """
        safe_name = self._sanitize_identifier(table_name)
        if not data:
            raise ValueError("Cannot register empty dataset")

        # Extract columns from first row
        columns = list(data[0].keys())
        safe_columns = [self._sanitize_identifier(c) for c in columns]

        # Create table using DuckDB's Python relation API
        self._conn.execute(f"DROP TABLE IF EXISTS {safe_name}")
        # Build CREATE TABLE with explicit columns
        col_defs = ", ".join(f'"{c}" VARCHAR' for c in safe_columns)
        self._conn.execute(f"CREATE TABLE {safe_name} ({col_defs})")

        # Insert data row by row using executemany
        placeholders = ", ".join(["?"] * len(safe_columns))
        insert_sql = f"INSERT INTO {safe_name} VALUES ({placeholders})"
        rows = [tuple(str(row.get(c, "")) for c in columns) for row in data]
        self._conn.executemany(insert_sql, rows)

        info = TableInfo(
            table_name=safe_name,
            source=source,
            row_count=len(data),
            column_count=len(columns),
            columns=[{"name": c, "safe_name": sc} for c, sc in zip(columns, safe_columns)],
        )
        self._tables[safe_name] = info
        self._audit_table_event("table_create", safe_name, row_count=len(data), source=source)
        return info

    def register_csv(
        self,
        table_name: str,
        csv_path: str,
        encrypted_columns: dict[str, str] | None = None,
        dek: bytes | None = None,
        source: str = "csv",
    ) -> TableInfo:
        """Register a CSV file as a DuckDB table with optional column encryption."""
        safe_name = self._sanitize_identifier(table_name)

        if encrypted_columns:
            temp_name = f"_temp_{safe_name}"
            self._conn.execute(f"CREATE TABLE {temp_name} AS SELECT * FROM read_csv_auto(?)", [csv_path])
            result = self._conn.execute(f"SELECT * FROM {temp_name}").fetchall()
            col_names = [desc[0] for desc in self._conn.execute(f"DESCRIBE {temp_name}").fetchall()]
            self._conn.execute(f"DROP TABLE {temp_name}")
            data = [dict(zip(col_names, row)) for row in result]
            return self.register_table_encrypted(
                table_name, data, encrypted_columns, dek, source=source
            )

        self._conn.execute(f"CREATE TABLE IF NOT EXISTS {safe_name} AS SELECT * FROM read_csv_auto(?)", [csv_path])

        count = self._conn.execute(f"SELECT COUNT(*) FROM {safe_name}").fetchone()[0]
        desc = self._conn.execute(f"DESCRIBE {safe_name}").fetchall()

        info = TableInfo(
            table_name=safe_name,
            source=source,
            row_count=count,
            column_count=len(desc),
            columns=[{"name": r[0], "type": r[1]} for r in desc],
        )
        self._tables[safe_name] = info
        self._audit_table_event("table_create", safe_name, row_count=count, source=source)
        return info

    def set_mask_rules(self, table_name: str, rules: list[MaskRule]):
        """Set field-level masking rules for a table."""
        safe_name = self._sanitize_identifier(table_name)
        self._mask_rules[safe_name] = rules

    def create_masked_view(self, table_name: str, view_name: str | None = None) -> str:
        """Create a masked view of a table based on mask rules.

        Returns the view name.
        """
        safe_table = self._sanitize_identifier(table_name)
        safe_view = self._sanitize_identifier(view_name or f"{safe_table}_masked")

        rules = self._mask_rules.get(safe_table, [])
        if not rules:
            # No rules — create passthrough view
            self._conn.execute(f"CREATE OR REPLACE VIEW {safe_view} AS SELECT * FROM {safe_table}")
            return safe_view

        # Build SELECT expressions with masking
        desc = self._conn.execute(f"DESCRIBE {safe_table}").fetchall()
        field_exprs = []
        rule_map = {r.field_name: r for r in rules}

        for col_info in desc:
            col_name = col_info[0]
            rule = rule_map.get(col_name)
            if not rule or rule.mask_type == "PASSTHROUGH":
                field_exprs.append(col_name)
            elif rule.mask_type == "HASH":
                hash_fn = "sm3" if self._has_sm3_udf else "sha256"
                field_exprs.append(f"{hash_fn}(CAST({col_name} AS VARCHAR)) AS {col_name}")
            elif rule.mask_type == "REDACT":
                field_exprs.append(f"NULL AS {col_name}")
            elif rule.mask_type == "GENERALIZE":
                field_exprs.append(
                    f"CASE WHEN {col_name} IN "
                    f"(SELECT {col_name} FROM {safe_table} "
                    f"GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT {rule.top_k}) "
                    f"THEN {col_name} ELSE 'Other' END AS {col_name}"
                )
            else:
                field_exprs.append(col_name)

        select_clause = ", ".join(field_exprs)
        self._conn.execute(f"CREATE OR REPLACE VIEW {safe_view} AS SELECT {select_clause} FROM {safe_table}")
        return safe_view

    def execute_query(self, sql: str, limit: int | None = None) -> QueryResult:
        """Execute a SQL query with safety constraints.

        - Read-only (no DDL/DML on raw tables)
        - Result set limited to prevent memory exhaustion
        - SQL injection prevention via identifier sanitization
        - Activity audit logging via sandbox_audit
        """
        max_rows = limit or self._max_rows
        start = datetime.now()

        try:
            # Enforce read-only: reject dangerous statements
            sql_upper = sql.strip().upper()
            dangerous = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "TRUNCATE", "ATTACH", "INSTALL"]
            for kw in dangerous:
                if sql_upper.startswith(kw):
                    return QueryResult(success=False, error=f"Operation not allowed: {kw}")

            # Policy enforcement (P1-3)
            allowed, reason = self._check_policy(sql, max_rows)
            if not allowed:
                # 拦截必须留痕：被策略挡掉的查询与被放行的查询走同一套数据访问审计
                duration = int((datetime.now() - start).total_seconds() * 1000)
                self._audit_query(sql, 0, duration, success=False, error=f"Policy denied: {reason}")
                return QueryResult(success=False, error=f"Policy denied: {reason}", duration_ms=duration)

            # Execute with limit
            result = self._conn.execute(sql).fetchmany(max_rows + 1)
            duration = int((datetime.now() - start).total_seconds() * 1000)

            if not result:
                return QueryResult(success=True, columns=[], rows=[], row_count=0, duration_ms=duration)

            # Get column names
            columns = [desc[0] for desc in self._conn.execute(sql).description]

            truncated = len(result) > max_rows
            rows = [list(row) for row in result[:max_rows]]

            # Audit log: data access event
            self._audit_query(sql, len(rows), duration, success=True)

            return QueryResult(
                success=True,
                columns=columns,
                rows=rows,
                row_count=len(rows),
                duration_ms=duration,
                truncated=truncated,
            )
        except Exception as e:
            duration = int((datetime.now() - start).total_seconds() * 1000)
            self._audit_query(sql, 0, duration, success=False, error=str(e))
            return QueryResult(success=False, error=str(e), duration_ms=duration)

    def _audit_query(self, sql: str, row_count: int, duration_ms: int,
                     success: bool = True, error: str | None = None):
        """Log a data access audit event for a SQL query."""
        try:
            from app.services.sandbox_audit import SandboxAuditLogger, assess_query_risk
            audit = SandboxAuditLogger()
            detail = {"sql": sql[:2000], "session_id": self.session_id, "success": success}
            if error:
                detail["error"] = error
            risk = assess_query_risk(sql)
            audit.log_data_access(
                "query", detail,
                rows_affected=row_count,
                risk_level=risk,
            )
            audit.close()
        except Exception:
            pass  # Audit failure must not break query execution

    def _audit_table_event(self, event_type: str, table_name: str,
                           row_count: int = 0, source: str = "", error: str | None = None,
                           detail_extra: dict | None = None):
        """Log a data access audit event for table operations."""
        try:
            from app.services.sandbox_audit import SandboxAuditLogger
            audit = SandboxAuditLogger()
            detail = {"table": table_name, "session_id": self.session_id, "source": source}
            if error:
                detail["error"] = error
            if detail_extra:
                detail.update(detail_extra)
            risk = "high" if event_type == "table_drop" else "low"
            audit.log_data_access(
                event_type, detail,
                rows_affected=row_count,
                risk_level=risk,
            )
            audit.close()
        except Exception:
            pass

    def list_tables(self) -> list[TableInfo]:
        """List registered tables."""
        return list(self._tables.values())

    def get_table_info(self, table_name: str) -> TableInfo | None:
        return self._tables.get(self._sanitize_identifier(table_name))

    def close(self):
        """Close the DuckDB connection. Encrypts at rest if DEK provided."""
        if self._conn:
            self._conn.close()
            self._conn = None
        if self.mode == "tmpfs" and self._db_path:
            db_file = Path(self._db_path)
            if self._dek and db_file.exists():
                # Encrypt .db → .db.enc (plaintext wiped inside _encrypt_db_file)
                _encrypt_db_file(db_file, self._dek)
                # Wipe WAL/shm sidecars
                from app.services.sandbox_security import secure_wipe_file
                for suffix in (".wal", ".tmp"):
                    sidecar = db_file.with_suffix(suffix)
                    if sidecar.exists():
                        secure_wipe_file(sidecar)
            else:
                _secure_wipe_db(self._db_path)

    @staticmethod
    def _sanitize_identifier(name: str) -> str:
        """Sanitize SQL identifier to prevent injection."""
        return re.sub(r"[^a-zA-Z0-9_]", "_", name)

    # === SM4 Column Encryption Integration ===

    def register_table_encrypted(
        self,
        table_name: str,
        data: list[dict],
        encrypted_columns: dict[str, str] | None = None,
        dek: bytes | None = None,
        key_id: str = "sandbox-default",
        source: str = "upload",
    ) -> TableInfo:
        """Register a table with SM4 column-level encryption.

        Args:
            table_name: Table name
            data: List of row dicts (plaintext)
            encrypted_columns: Dict of {column_name: "sm4-siv"|"sm4-gcm"} specifying
                              which columns to encrypt and with which mode
            dek: 16-byte SM4 key. If None, generates a random DEK.
            key_id: Key identifier for audit trail
            source: Data source identifier

        Returns:
            TableInfo with encryption metadata
        """
        if not data:
            raise ValueError("Cannot register empty dataset")

        if dek is None:
            dek = os.urandom(16)

        encrypted_columns = encrypted_columns or {}
        columns = list(data[0].keys())
        safe_name = self._sanitize_identifier(table_name)

        # Prepare column definitions
        col_defs = []
        for col in columns:
            safe_col = self._sanitize_identifier(col)
            if col in encrypted_columns:
                # Encrypted columns stored as BLOB
                col_defs.append(f'"{safe_col}" BLOB')
            else:
                col_defs.append(f'"{safe_col}" VARCHAR')

        # Create table
        self._conn.execute(f"DROP TABLE IF EXISTS {safe_name}")
        self._conn.execute(f"CREATE TABLE {safe_name} ({', '.join(col_defs)})")

        # Encrypt sensitive columns and insert
        from app.services.column_encryption import ColumnEncryption, EncryptionMode
        cipher = ColumnEncryption(dek=dek, key_id=key_id)

        placeholders = ", ".join(["?"] * len(columns))
        insert_sql = f"INSERT INTO {safe_name} VALUES ({placeholders})"

        encrypted_rows = []
        for row in data:
            values = []
            for col in columns:
                val = str(row.get(col, ""))
                if col in encrypted_columns:
                    mode = EncryptionMode(encrypted_columns[col])
                    if mode == EncryptionMode.DETERMINISTIC:
                        encrypted = cipher.encrypt_deterministic(val, col)
                    else:
                        encrypted = cipher.encrypt_randomized(val, col)
                    values.append(encrypted.ciphertext)
                else:
                    values.append(val)
            encrypted_rows.append(tuple(values))

        self._conn.executemany(insert_sql, encrypted_rows)

        # Track encryption metadata
        info = TableInfo(
            table_name=safe_name,
            source=source,
            row_count=len(data),
            column_count=len(columns),
            columns=[{"name": c, "safe_name": self._sanitize_identifier(c)} for c in columns],
        )
        info.columns_meta = {
            "encrypted_columns": encrypted_columns,
            "key_id": key_id,
        }
        self._tables[safe_name] = info

        # Store DEK for query-time decryption
        if not hasattr(self, '_table_deks'):
            self._table_deks: dict[str, tuple[bytes, str]] = {}
        self._table_deks[safe_name] = (dek, key_id)

        self._audit_table_event("table_create", safe_name, row_count=len(data),
                                source="encrypted_upload",
                                detail_extra={"encrypted_columns": list(encrypted_columns.keys())})
        logger.info(f"[secure-duckdb] Registered encrypted table {safe_name}: "
                    f"{len(encrypted_columns)} encrypted columns, {len(data)} rows")
        return info

    def _confine_import_path(self, raw_path: str, *, kind: str) -> Path:
        """把导入路径关进平台导入根（A50）。

        请求体里的 json_path / parquet_path 是调用者可控输入：旧实现直接
        open() / read_parquet(?)，等于任何能调 /import/* 的角色（DATA_PROVIDER 就
        够）都能把 /app/.env、/proc/self/environ 这类文件读成一张表，再用 /query
        把内容导出。

        规则：拒绝 glob 通配符（read_parquet 的 glob = 目录枚举）；resolve() 之后再
        判定（符号链接指向根外也算越界）；必须落在 allowed_import_roots() 内。
        越界 ⇒ PermissionError，由端点映射成 403。
        """
        raw = (raw_path or "").strip()
        if not raw:
            raise ValueError(f"{kind} is required")
        if any(ch in raw for ch in "*?["):
            self._audit_import_denied(kind, raw, "glob wildcard")
            raise PermissionError(f"{kind} must not contain glob wildcards")
        try:
            resolved = Path(raw).resolve()
        except OSError as e:
            self._audit_import_denied(kind, raw, f"resolve failed: {e}")
            raise PermissionError(f"{kind} could not be resolved: {e}") from e
        roots = allowed_import_roots()
        if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
            self._audit_import_denied(kind, raw, "outside import root")
            logger.warning(
                "[secure-duckdb] session=%s refused %s outside the import roots: %s",
                self.session_id, kind, raw,
            )
            raise PermissionError(
                f"{kind} must live inside a platform import root "
                f"(allowed: {', '.join(str(r) for r in roots)})"
            )
        return resolved

    def _audit_import_denied(self, kind: str, raw_path: str, reason: str) -> None:
        """越界导入尝试同样进数据访问审计。"""
        self._audit_table_event(
            "import_denied", f"<{kind}>",
            detail_extra={"path": raw_path[:500], "reason": reason},
        )

    def register_json(
        self,
        table_name: str,
        json_path: str,
        encrypted_columns: dict[str, str] | None = None,
        dek: bytes | None = None,
        source: str = "json",
    ) -> TableInfo:
        """Import a JSON file into DuckDB with optional column encryption.

        Supports both JSON array and newline-delimited JSON (NDJSON).

        A50: 路径先过 _confine_import_path（只允许平台导入根），再限幅读入。
        """
        safe_name = self._sanitize_identifier(table_name)

        # Read JSON data（A50：越界路径 ⇒ PermissionError；整文件读入有上限）
        safe_path = self._confine_import_path(json_path, kind="json_path")
        with safe_path.open(encoding="utf-8") as f:
            content = f.read(self.max_import_bytes + 1).strip()
        if len(content) > self.max_import_bytes:
            raise ValueError(
                f"JSON file exceeds the {self.max_import_bytes} byte import limit: {safe_path}"
            )

        if content.startswith("["):
            data = json.loads(content)
        else:
            # NDJSON
            data = [json.loads(line) for line in content.splitlines() if line.strip()]

        if not data:
            raise ValueError(f"Empty JSON file: {json_path}")

        if encrypted_columns:
            return self.register_table_encrypted(
                table_name, data, encrypted_columns, dek, source=source
            )

        # Non-encrypted path: use DuckDB's native JSON reader
        self._conn.execute(f"DROP TABLE IF EXISTS {safe_name}")
        self._conn.execute(
            f"CREATE TABLE {safe_name} AS SELECT * FROM read_json_auto(?)",
            [str(safe_path)],
        )

        count = self._conn.execute(f"SELECT COUNT(*) FROM {safe_name}").fetchone()[0]
        desc = self._conn.execute(f"DESCRIBE {safe_name}").fetchall()

        info = TableInfo(
            table_name=safe_name,
            source=source,
            row_count=count,
            column_count=len(desc),
            columns=[{"name": r[0], "type": r[1]} for r in desc],
        )
        self._tables[safe_name] = info
        self._audit_table_event("import", safe_name, row_count=count, source=source)
        return info

    def register_parquet(
        self,
        table_name: str,
        parquet_path: str,
        encrypted_columns: dict[str, str] | None = None,
        dek: bytes | None = None,
        source: str = "parquet",
    ) -> TableInfo:
        """Import a Parquet file into DuckDB with optional column encryption.

        A50: parquet_path 与 json_path 同形 —— 越界或带 glob 一律拒绝（read_parquet
        的通配符等于目录枚举）。
        """
        safe_name = self._sanitize_identifier(table_name)
        safe_path = self._confine_import_path(parquet_path, kind="parquet_path")
        parquet_path = str(safe_path)

        if encrypted_columns:
            # Load via DuckDB, then encrypt columns
            temp_name = f"_temp_{safe_name}"
            self._conn.execute(f"CREATE TABLE {temp_name} AS SELECT * FROM read_parquet(?)", [parquet_path])
            result = self._conn.execute(f"SELECT * FROM {temp_name}").fetchall()
            col_names = [desc[0] for desc in self._conn.execute(f"DESCRIBE {temp_name}").fetchall()]
            self._conn.execute(f"DROP TABLE {temp_name}")

            data = [dict(zip(col_names, row)) for row in result]
            return self.register_table_encrypted(
                table_name, data, encrypted_columns, dek, source=source
            )

        # Non-encrypted path
        self._conn.execute(f"DROP TABLE IF EXISTS {safe_name}")
        self._conn.execute(
            f"CREATE TABLE {safe_name} AS SELECT * FROM read_parquet(?)",
            [parquet_path],
        )

        count = self._conn.execute(f"SELECT COUNT(*) FROM {safe_name}").fetchone()[0]
        desc = self._conn.execute(f"DESCRIBE {safe_name}").fetchall()

        info = TableInfo(
            table_name=safe_name,
            source=source,
            row_count=count,
            column_count=len(desc),
            columns=[{"name": r[0], "type": r[1]} for r in desc],
        )
        self._tables[safe_name] = info
        self._audit_table_event("import", safe_name, row_count=count, source=source)
        return info

    def register_from_postgres(
        self,
        table_name: str,
        pg_connstr: str,
        pg_table: str,
        encrypted_columns: dict[str, str] | None = None,
        dek: bytes | None = None,
        source: str = "postgresql",
    ) -> TableInfo:
        """Import a PostgreSQL table via postgres_scanner extension.

        Args:
            table_name: Target DuckDB table name
            pg_connstr: PostgreSQL connection string
            pg_table: Source table name (schema.table)
            encrypted_columns: Columns to encrypt after import
            dek: SM4 key for encryption
            source: Data source identifier
        """
        safe_name = self._sanitize_identifier(table_name)

        # Install and load postgres_scanner
        try:
            self._conn.execute("INSTALL postgres_scanner")
        except Exception:
            pass  # Already installed
        self._conn.execute("LOAD postgres_scanner")

        if encrypted_columns:
            # Import to temp table, then encrypt
            temp_name = f"_temp_{safe_name}"
            self._conn.execute(
                f"CREATE TABLE {temp_name} AS SELECT * FROM postgres_scan(?, ?)",
                [pg_connstr, pg_table],
            )
            result = self._conn.execute(f"SELECT * FROM {temp_name}").fetchall()
            col_names = [desc[0] for desc in self._conn.execute(f"DESCRIBE {temp_name}").fetchall()]
            self._conn.execute(f"DROP TABLE {temp_name}")

            data = [dict(zip(col_names, row)) for row in result]
            return self.register_table_encrypted(
                table_name, data, encrypted_columns, dek, source=source
            )

        # Non-encrypted: create view directly
        self._conn.execute(f"DROP TABLE IF EXISTS {safe_name}")
        self._conn.execute(
            f"CREATE TABLE {safe_name} AS SELECT * FROM postgres_scan(?, ?)",
            [pg_connstr, pg_table],
        )

        count = self._conn.execute(f"SELECT COUNT(*) FROM {safe_name}").fetchone()[0]
        desc = self._conn.execute(f"DESCRIBE {safe_name}").fetchall()

        info = TableInfo(
            table_name=safe_name,
            source=source,
            row_count=count,
            column_count=len(desc),
            columns=[{"name": r[0], "type": r[1]} for r in desc],
        )
        self._tables[safe_name] = info
        self._audit_table_event("import", safe_name, row_count=count, source="postgres")
        return info

    def register_from_s3(
        self,
        table_name: str,
        s3_url: str,
        s3_key_id: str = "",
        s3_secret: str = "",
        file_format: str = "parquet",
        encrypted_columns: dict[str, str] | None = None,
        dek: bytes | None = None,
        source: str = "s3",
    ) -> TableInfo:
        """Import data from S3/MinIO via httpfs extension.

        Args:
            table_name: Target DuckDB table name
            s3_url: S3 object URL (e.g., s3://bucket/path/file.parquet)
            s3_key_id: S3 access key
            s3_secret: S3 secret key
            file_format: "parquet", "csv", or "json"
            encrypted_columns: Columns to encrypt after import
            dek: SM4 key for encryption
            source: Data source identifier
        """
        safe_name = self._sanitize_identifier(table_name)

        # Install and load httpfs
        try:
            self._conn.execute("INSTALL httpfs")
        except Exception:
            pass
        self._conn.execute("LOAD httpfs")

        # Configure S3 credentials (escape single quotes to prevent injection)
        if s3_key_id:
            safe_key = s3_key_id.replace("'", "''")
            safe_secret = s3_secret.replace("'", "''")
            self._conn.execute(f"SET s3_access_key_id = '{safe_key}'")
            self._conn.execute(f"SET s3_secret_access_key = '{safe_secret}'")

        # Read based on format (escape single quotes to prevent SQL injection)
        safe_url = s3_url.replace("'", "''")
        if file_format == "parquet":
            read_fn = f"read_parquet('{safe_url}')"
        elif file_format == "csv":
            read_fn = f"read_csv_auto('{safe_url}')"
        elif file_format == "json":
            read_fn = f"read_json_auto('{safe_url}')"
        else:
            raise ValueError(f"Unsupported format: {file_format}")

        if encrypted_columns:
            temp_name = f"_temp_{safe_name}"
            self._conn.execute(f"CREATE TABLE {temp_name} AS SELECT * FROM {read_fn}")
            result = self._conn.execute(f"SELECT * FROM {temp_name}").fetchall()
            col_names = [desc[0] for desc in self._conn.execute(f"DESCRIBE {temp_name}").fetchall()]
            self._conn.execute(f"DROP TABLE {temp_name}")

            data = [dict(zip(col_names, row)) for row in result]
            return self.register_table_encrypted(
                table_name, data, encrypted_columns, dek, source=source
            )

        self._conn.execute(f"DROP TABLE IF EXISTS {safe_name}")
        self._conn.execute(f"CREATE TABLE {safe_name} AS SELECT * FROM {read_fn}")

        count = self._conn.execute(f"SELECT COUNT(*) FROM {safe_name}").fetchone()[0]
        desc = self._conn.execute(f"DESCRIBE {safe_name}").fetchall()

        info = TableInfo(
            table_name=safe_name,
            source=source,
            row_count=count,
            column_count=len(desc),
            columns=[{"name": r[0], "type": r[1]} for r in desc],
        )
        self._tables[safe_name] = info
        self._audit_table_event("import", safe_name, row_count=count, source="s3")
        return info

    def execute_query_decrypted(self, sql: str, limit: int | None = None) -> QueryResult:
        """Execute a query and decrypt encrypted columns in results.

        Automatically detects encrypted columns and decrypts them using
        the stored DEK.
        """
        result = self.execute_query(sql, limit)
        if not result.success:
            return result

        if not hasattr(self, '_table_deks') or not self._table_deks:
            return result

        # Find which tables are referenced in the query
        sql_upper = sql.upper()
        encrypted_tables = []
        for table_name in self._table_deks:
            if table_name.upper() in sql_upper:
                encrypted_tables.append(table_name)

        if not encrypted_tables:
            return result

        # Get encryption metadata for referenced tables
        encrypted_col_map = {}  # col_name -> (dek, key_id, mode)
        for table_name in encrypted_tables:
            info = self._tables.get(table_name)
            if not info or not hasattr(info, 'columns_meta'):
                continue
            meta = info.columns_meta
            dek, key_id = self._table_deks[table_name]
            for col, mode_str in meta.get("encrypted_columns", {}).items():
                encrypted_col_map[col] = (dek, key_id, mode_str)

        if not encrypted_col_map:
            return result

        # Decrypt matching columns in result rows
        from app.services.column_encryption import ColumnEncryption, EncryptionMode, EncryptedColumn

        decrypted_rows = []
        for row in result.rows:
            new_row = []
            for i, col_name in enumerate(result.columns):
                if col_name in encrypted_col_map and row[i] is not None:
                    dek, key_id, mode_str = encrypted_col_map[col_name]
                    cipher = ColumnEncryption(dek=dek, key_id=key_id)
                    try:
                        # Wrap raw bytes from DuckDB BLOB into EncryptedColumn
                        raw = bytes(row[i]) if isinstance(row[i], (bytearray, memoryview)) else row[i]
                        enc_obj = EncryptedColumn(
                            ciphertext=raw,
                            mode=EncryptionMode(mode_str),
                            column_name=col_name,
                            key_id=key_id,
                        )
                        decrypted = cipher.decrypt_column(enc_obj)
                        new_row.append(decrypted)
                    except Exception as e:
                        logger.warning(f"Decryption failed for {col_name}: {e}")
                        new_row.append("[DECRYPT_ERROR]")
                else:
                    new_row.append(row[i])
            decrypted_rows.append(new_row)

        result.rows = decrypted_rows
        return result

    def get_encryption_metadata(self, table_name: str) -> dict | None:
        """Get encryption metadata for a table."""
        safe_name = self._sanitize_identifier(table_name)
        info = self._tables.get(safe_name)
        if not info or not hasattr(info, 'columns_meta'):
            return None
        return info.columns_meta

    def get_dek_hex(self, table_name: str) -> str | None:
        """Get the DEK hex string for a table (for sandbox injection via env var)."""
        safe_name = self._sanitize_identifier(table_name)
        if not hasattr(self, '_table_deks') or safe_name not in self._table_deks:
            return None
        dek, _ = self._table_deks[safe_name]
        return dek.hex()

    def get_all_encryption_configs(self) -> dict[str, dict]:
        """Get encryption configs for all encrypted tables (for sandbox injection).

        Returns:
            Dict of table_name -> {"dek_hex": str, "encrypted_columns": dict, "key_id": str}
        """
        if not hasattr(self, '_table_deks'):
            return {}
        configs = {}
        for table_name, (dek, key_id) in self._table_deks.items():
            info = self._tables.get(table_name)
            if info and hasattr(info, 'columns_meta'):
                meta = info.columns_meta
                configs[table_name] = {
                    "dek_hex": dek.hex(),
                    "encrypted_columns": meta.get("encrypted_columns", {}),
                    "key_id": key_id,
                }
        return configs

    def attach_encrypted_db(self, db_alias: str, db_path: str, dek_hex: str) -> None:
        """Attach an encrypted DuckDB database file.

        Uses DuckDB's PRAGMA key for encrypted database access.
        Requires DuckDB >= 0.9 with encrypted DB support.

        Args:
            db_alias: Alias for the attached database
            db_path: Path to the encrypted .duckdb file
            dek_hex: 16-byte DEK as hex string for decryption
        """
        # Validate DEK hex format (must be 32 hex chars = 16 bytes)
        if not re.fullmatch(r"[0-9a-fA-F]{32}", dek_hex):
            raise ValueError(f"dek_hex must be 32 hex characters (16 bytes), got {len(dek_hex)}")
        safe_alias = self._sanitize_identifier(db_alias)
        safe_path = db_path.replace("'", "''")
        # DuckDB encrypted DB uses PRAGMA key before ATTACH
        self._conn.execute(f"PRAGMA key = '{dek_hex}'")
        self._conn.execute(f"ATTACH '{safe_path}' AS {safe_alias}")
        logger.info(f"[secure-duckdb] Attached encrypted DB '{safe_alias}' from {db_path}")

    def detach_db(self, db_alias: str) -> None:
        """Detach a previously attached database."""
        safe_alias = self._sanitize_identifier(db_alias)
        self._conn.execute(f"DETACH {safe_alias}")

    def register_from_postgres_sandbox(
        self,
        table_name: str,
        pg_connstr: str,
        pg_table: str,
        encrypted_columns: dict[str, str] | None = None,
        dek: bytes | None = None,
        network_allowed: bool = False,
        source: str = "postgresql",
    ) -> TableInfo:
        """Import a PostgreSQL table with sandbox network policy awareness.

        Args:
            table_name: Target DuckDB table name
            pg_connstr: PostgreSQL connection string
            pg_table: Source table name (schema.table)
            encrypted_columns: Columns to encrypt after import
            dek: SM4 key for encryption
            network_allowed: If False, raises error (sandbox network policy must allow)
            source: Data source identifier
        """
        if not network_allowed:
            raise PermissionError(
                "PostgreSQL import requires network access. "
                "Update sandbox network policy to allowlist mode with the target host."
            )
        return self.register_from_postgres(
            table_name, pg_connstr, pg_table,
            encrypted_columns=encrypted_columns, dek=dek, source=source,
        )

    def register_from_s3_sandbox(
        self,
        table_name: str,
        s3_url: str,
        s3_key_id: str = "",
        s3_secret: str = "",
        file_format: str = "parquet",
        encrypted_columns: dict[str, str] | None = None,
        dek: bytes | None = None,
        network_allowed: bool = False,
        source: str = "s3",
    ) -> TableInfo:
        """Import data from S3/MinIO with sandbox network policy awareness.

        Args:
            table_name: Target DuckDB table name
            s3_url: S3 object URL
            s3_key_id: S3 access key
            s3_secret: S3 secret key
            file_format: "parquet", "csv", or "json"
            encrypted_columns: Columns to encrypt after import
            dek: SM4 key for encryption
            network_allowed: If False, raises error (sandbox network policy must allow)
            source: Data source identifier
        """
        if not network_allowed:
            raise PermissionError(
                "S3/MinIO import requires network access. "
                "Update sandbox network policy to allowlist mode with the target endpoint."
            )
        return self.register_from_s3(
            table_name, s3_url,
            s3_key_id=s3_key_id, s3_secret=s3_secret,
            file_format=file_format,
            encrypted_columns=encrypted_columns, dek=dek, source=source,
        )


# === Module-level TTL cleanup ===

def _secure_wipe_db(db_path: str) -> None:
    """Securely wipe a DuckDB database file and its WAL/shm sidecars."""
    from app.services.sandbox_security import secure_wipe_file
    p = Path(db_path)
    for suffix in ("", ".wal", ".tmp"):
        target = p.with_suffix(suffix) if suffix else p
        if target.exists():
            secure_wipe_file(target)
    # Also wipe .enc file if present
    enc = Path(db_path + ".enc")
    if enc.exists():
        secure_wipe_file(enc)
    # Remove the parent directory if empty
    try:
        p.parent.rmdir()
    except OSError:
        pass  # Not empty, leave it


def _encrypt_db_file(db_path: Path, dek: bytes) -> Path:
    """Encrypt a DuckDB .db file to .db.enc using SM4-GCM.

    Format: [nonce(12)][tag(16)][ciphertext]
    Returns path to encrypted file.
    """
    from app.services.sandbox_security import encrypt_workspace_file
    encrypt_workspace_file(db_path, dek)
    # Rename .db → .db.enc
    enc_path = Path(str(db_path) + ".enc")
    db_path.rename(enc_path)
    return enc_path


def _decrypt_db_file(enc_path: Path, db_path: Path, dek: bytes) -> None:
    """Decrypt a .db.enc file back to .db using SM4-GCM."""
    from app.services.sandbox_security import decrypt_workspace_file
    plaintext = decrypt_workspace_file(enc_path, dek)
    db_path.write_bytes(plaintext)


def cleanup_expired_engines(engines: dict[str, SecureDuckDBEngine],
                            max_age_seconds: int = 3600) -> int:
    """Clean up expired DuckDB engines. Returns count of cleaned sessions.

    A56: 会话归属（engine.owner_user_id）与明文表、_table_deks 一起随引擎消失，
    所以清掉这一个字典就够了 —— 调用方（app/api/sandbox_db.py）现在每个端点入口
    都扫一次，不再只挂在 /query 上；「只建表不查询」的会话不会再把引擎永久留在内存里。
    """
    now = datetime.now(timezone.utc)
    expired = []
    for sid, engine in engines.items():
        age = (now - engine._created_at).total_seconds()
        if age > max_age_seconds:
            expired.append(sid)
    for sid in expired:
        engines[sid].close()
        del engines[sid]
        logger.info(f"[secure-duckdb] Cleaned expired session {sid}")
    return len(expired)
