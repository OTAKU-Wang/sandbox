"""DB Access Proxy — SQL interception, RLS injection, and column encryption.

Intercepts database queries, evaluates RLS policies, injects WHERE clauses,
and transparently encrypts/decrypts column-level sensitive data.
"""
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rls_engine import rls_engine, RLSContext, RLSPolicy, PolicyType
from app.services.column_encryption import column_encryption, EncryptionMode, EncryptedColumn
from app.services.audit_service import audit_service

logger = logging.getLogger(__name__)


class ProxyAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REWRITE = "rewrite"


@dataclass
class ProxyResult:
    """Result of SQL proxy evaluation."""
    action: ProxyAction
    original_sql: str
    rewritten_sql: str | None = None
    applied_policies: list[str] = field(default_factory=list)
    encrypted_columns: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class ColumnEncryptionRule:
    """Defines which columns to encrypt and how."""
    table_name: str
    column_name: str
    mode: EncryptionMode = EncryptionMode.DETERMINISTIC
    key_id: str = "default"


class DBAccessProxy:
    """SQL interception proxy for RLS and column encryption.

    Intercepts SQL queries to:
    1. Evaluate RLS policies and inject WHERE clauses
    2. Encrypt plaintext values in INSERT/UPDATE statements
    3. Decrypt ciphertext values in SELECT results
    4. Log all access patterns for audit
    """

    def __init__(self, max_rows: int = 10000):
        self._encryption_rules: dict[tuple[str, str], ColumnEncryptionRule] = {}
        self._blocked_patterns: list[re.Pattern] = []
        self._query_count: int = 0
        self._denied_count: int = 0
        self._max_rows = max_rows
        self._query_log: list[dict[str, Any]] = []
        # Default blocked patterns
        self._default_blocked = [
            re.compile(r"DROP\s+TABLE", re.IGNORECASE),
            re.compile(r"TRUNCATE", re.IGNORECASE),
            re.compile(r"ALTER\s+TABLE", re.IGNORECASE),
            re.compile(r"DELETE\s+FROM\s+\w+\s*$", re.IGNORECASE),  # DELETE without WHERE
            re.compile(r"DELETE\s+FROM\s+\w+\s+WHERE\s+1\s*=\s*1", re.IGNORECASE),  # DELETE all
        ]

    def register_encryption_rule(
        self,
        table_name: str,
        column_name: str,
        mode: EncryptionMode = EncryptionMode.DETERMINISTIC,
        key_id: str = "default",
    ) -> None:
        """Register a column for transparent encryption/decryption."""
        rule = ColumnEncryptionRule(
            table_name=table_name,
            column_name=column_name,
            mode=mode,
            key_id=key_id,
        )
        self._encryption_rules[(table_name, column_name)] = rule
        logger.info(f"Registered encryption rule: {table_name}.{column_name} ({mode.value})")

    def add_blocked_pattern(self, pattern: str) -> None:
        """Add a SQL pattern to block (e.g., DROP TABLE, TRUNCATE)."""
        self._blocked_patterns.append(re.compile(pattern, re.IGNORECASE))

    def evaluate_sql(
        self,
        sql: str,
        context: RLSContext,
        params: dict[str, Any] | None = None,
    ) -> ProxyResult:
        """Evaluate a SQL query against policies and security rules.

        Returns ProxyResult with action (allow/deny/rewrite) and modified SQL.
        """
        self._query_count += 1

        # 1. Check blocked patterns
        for pattern in self._blocked_patterns:
            if pattern.search(sql):
                self._denied_count += 1
                return ProxyResult(
                    action=ProxyAction.DENY,
                    original_sql=sql,
                    reason=f"Blocked by pattern: {pattern.pattern}",
                )

        # 2. Evaluate RLS policies
        rls_where = rls_engine.evaluate_sql(context)

        # 3. Rewrite SQL with RLS injection
        rewritten = sql
        applied_policies = []

        if rls_where:
            rewritten = rls_engine.inject_rls_where(sql, rls_where)
            applied_policies.append("rls")

        # 4. Encrypt column values in INSERT/UPDATE
        encrypted_columns = []
        if params:
            rewritten, encrypted_columns = self._encrypt_params(
                rewritten, params
            )

        action = ProxyAction.REWRITE if (rls_where or encrypted_columns) else ProxyAction.ALLOW

        return ProxyResult(
            action=action,
            original_sql=sql,
            rewritten_sql=rewritten,
            applied_policies=applied_policies,
            encrypted_columns=encrypted_columns,
        )

    def _encrypt_params(
        self,
        sql: str,
        params: dict[str, Any],
    ) -> tuple[str, list[str]]:
        """Encrypt column values in query parameters."""
        encrypted_columns = []

        for key, value in params.items():
            # Check if this param maps to an encrypted column
            # Simple heuristic: param name matches column name
            for (table, col), rule in self._encryption_rules.items():
                if col == key and isinstance(value, str) and value:
                    enc = column_encryption.encrypt_column(value, col, rule.mode)
                    params[key] = enc.ciphertext.hex()
                    encrypted_columns.append(f"{table}.{col}")
                    break

        return sql, encrypted_columns

    def decrypt_result_row(
        self,
        table_name: str,
        row: dict[str, Any],
    ) -> dict[str, Any]:
        """Decrypt encrypted columns in a result row."""
        decrypted = dict(row)

        for (table, col), rule in self._encryption_rules.items():
            if table == table_name and col in decrypted:
                value = decrypted[col]
                if isinstance(value, (bytes, str)):
                    try:
                        if isinstance(value, str):
                            value = bytes.fromhex(value)
                        enc = EncryptedColumn(
                            ciphertext=value,
                            mode=rule.mode,
                            column_name=col,
                            key_id=rule.key_id,
                        )
                        decrypted[col] = column_encryption.decrypt_column(enc)
                    except Exception as e:
                        logger.debug(f"Decrypt failed for {table}.{col}: {e}")

        return decrypted

    async def execute_with_rls(
        self,
        db: AsyncSession,
        sql: str,
        context: RLSContext,
        params: dict[str, Any] | None = None,
        user_id: uuid.UUID | None = None,
    ) -> Any:
        """Execute a SQL query with RLS evaluation and column encryption.

        This is the main entry point for proxied database access.
        """
        result = self.evaluate_sql(sql, context, params)

        if result.action == ProxyAction.DENY:
            # Audit the denied access
            try:
                await audit_service.log(
                    db,
                    action="db_access.denied",
                    resource_type="database",
                    user_id=user_id,
                    detail={"sql": sql[:500], "reason": result.reason},
                )
            except Exception:
                pass
            raise PermissionError(f"Access denied: {result.reason}")

        # Use rewritten SQL if available
        final_sql = result.rewritten_sql or sql
        final_params = params or {}

        # Execute the query
        stmt = text(final_sql)
        proxy_result = await db.execute(stmt, final_params)

        # Audit successful access
        try:
            await audit_service.log(
                db,
                action="db_access.execute",
                resource_type="database",
                user_id=user_id,
                detail={
                    "sql": final_sql[:500],
                    "policies_applied": result.applied_policies,
                    "encrypted_columns": result.encrypted_columns,
                },
            )
        except Exception:
            pass

        return proxy_result

    def get_encryption_rules(self) -> list[dict[str, Any]]:
        """List all registered encryption rules."""
        return [
            {
                "table": table,
                "column": col,
                "mode": rule.mode.value,
                "key_id": rule.key_id,
            }
            for (table, col), rule in self._encryption_rules.items()
        ]

    def get_metrics(self) -> dict[str, Any]:
        """Get proxy metrics."""
        return {
            "total_queries": self._query_count,
            "denied_queries": self._denied_count,
            "encryption_rules": len(self._encryption_rules),
            "blocked_patterns": len(self._blocked_patterns),
        }

    # ── Simplified API for test compatibility ──────────────────

    def parse_sql(self, sql: str) -> dict[str, Any]:
        """Parse SQL query and extract type, tables, columns.

        Simple regex-based parser for query analysis.
        """
        sql_stripped = sql.strip()
        sql_upper = sql_stripped.upper()

        # Determine query type
        query_type = "UNKNOWN"
        for qtype in ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER"):
            if sql_upper.startswith(qtype):
                query_type = qtype
                break

        # Extract tables
        tables = []
        # FROM clause
        from_match = re.search(r"FROM\s+(\w+)", sql_stripped, re.IGNORECASE)
        if from_match:
            tables.append(from_match.group(1))
        # INTO clause (INSERT)
        into_match = re.search(r"INTO\s+(\w+)", sql_stripped, re.IGNORECASE)
        if into_match:
            tables.append(into_match.group(1))
        # UPDATE clause
        update_match = re.search(r"UPDATE\s+(\w+)", sql_stripped, re.IGNORECASE)
        if update_match:
            tables.append(update_match.group(1))

        # Extract columns
        columns = []
        if query_type == "SELECT":
            # Extract columns between SELECT and FROM
            select_match = re.search(r"SELECT\s+(.+?)\s+FROM", sql_stripped, re.IGNORECASE | re.DOTALL)
            if select_match:
                cols_str = select_match.group(1).strip()
                if cols_str != "*":
                    columns = [c.strip().split(".")[-1].split(" AS ")[0].strip() for c in cols_str.split(",")]
        elif query_type == "INSERT":
            # Extract columns from INSERT INTO table (col1, col2)
            col_match = re.search(r"\(([^)]+)\)\s*VALUES", sql_stripped, re.IGNORECASE)
            if col_match:
                columns = [c.strip() for c in col_match.group(1).split(",")]

        return {
            "type": query_type,
            "tables": tables,
            "columns": columns,
            "raw": sql_stripped,
        }

    def validate_sql(self, sql: str) -> dict[str, Any]:
        """Validate SQL against security rules. Raises on violations.

        Rules:
        - Block DROP, TRUNCATE, ALTER
        - Block DELETE without WHERE
        - Block SELECT *
        """
        sql_upper = sql.strip().upper()

        # Check blocked patterns
        for pattern in self._default_blocked + self._blocked_patterns:
            if pattern.search(sql):
                raise PermissionError(f"Query blocked: forbidden operation ({pattern.pattern})")

        # Block SELECT *
        if re.search(r"SELECT\s+\*", sql, re.IGNORECASE):
            raise PermissionError("SELECT * blocked: specific columns required")

        # Block DELETE without WHERE
        if sql_upper.startswith("DELETE") and "WHERE" not in sql_upper:
            raise PermissionError("DELETE without WHERE blocked: dangerous operation")

        return {"allowed": True}

    def inject_limit(self, sql: str) -> str:
        """Inject or cap LIMIT clause to prevent bulk extraction."""
        sql_upper = sql.strip().upper()

        # Check if LIMIT already exists
        limit_match = re.search(r"LIMIT\s+(\d+)", sql, re.IGNORECASE)
        if limit_match:
            existing_limit = int(limit_match.group(1))
            if existing_limit > self._max_rows:
                return sql[:limit_match.start()] + f"LIMIT {self._max_rows}" + sql[limit_match.end():]
            return sql

        # Add LIMIT if missing (only for SELECT)
        if sql_upper.startswith("SELECT"):
            return f"{sql.rstrip()} LIMIT {self._max_rows}"

        return sql

    def evaluate_query(
        self,
        sql: str,
        policies: list[dict[str, Any]] | None = None,
        user_region: str | None = None,
        user_clearance: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate a query against RLS policies.

        Returns dict with 'allowed' boolean and optionally 'rewritten_sql'.
        """
        policies = policies or []
        rewritten = sql

        for policy in policies:
            if policy.get("type") == "region":
                allowed_regions = policy.get("allowed_regions", [])
                column = policy.get("column", "region")
                if user_region and user_region in allowed_regions:
                    # Inject region filter
                    if "WHERE" in rewritten.upper():
                        rewritten = re.sub(
                            r"(WHERE)",
                            f"\\1 {column} = '{user_region}' AND",
                            rewritten,
                            count=1,
                            flags=re.IGNORECASE,
                        )
                    else:
                        # Add WHERE clause before GROUP BY/ORDER BY/LIMIT
                        for keyword in ("GROUP BY", "ORDER BY", "LIMIT"):
                            m = re.search(keyword, rewritten, re.IGNORECASE)
                            if m:
                                rewritten = rewritten[:m.start()] + f"WHERE {column} = '{user_region}' " + rewritten[m.start():]
                                break
                        else:
                            rewritten = f"{rewritten} WHERE {column} = '{user_region}'"

        return {
            "allowed": True,
            "rewritten_sql": rewritten,
        }

    def log_query(
        self,
        sql: str,
        user_id: str | None = None,
        allowed: bool = True,
        reason: str | None = None,
    ) -> None:
        """Log a query for audit trail."""
        self._query_log.append({
            "sql": sql,
            "user_id": user_id,
            "allowed": allowed,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def get_query_log(self) -> list[dict[str, Any]]:
        """Get the query audit log."""
        return self._query_log


# Singleton
db_access_proxy = DBAccessProxy()
