"""Query Rewriter — rewrites SQL to work with encrypted columns.

Converts plaintext column references to their encrypted counterparts:
- DET columns: WHERE col = 'x' → WHERE col_hash = hash('x')
- RAND columns: SELECT col → SELECT col_enc (decrypt in application layer)
- PLAIN columns: no rewriting needed

Also enforces security constraints: SELECT-only, no SELECT *, LIMIT enforcement.
"""
import re
import logging
from dataclasses import dataclass

from app.services.deterministic_sm4 import (
    DeterministicSM4, ColumnEncryptionRegistry, default_registry,
)

logger = logging.getLogger(__name__)


class SecurityError(Exception):
    """Raised when SQL violates security constraints."""
    pass


@dataclass
class RewriteResult:
    """Result of SQL rewriting."""
    sql: str
    params: dict | None
    encrypted_columns: list[str]  # Columns that need app-layer decryption
    hash_columns: list[str]       # Columns replaced with hash lookups


class QueryRewriter:
    """Rewrites SQL queries to work with column-level encrypted storage.

    Security checks:
    - Only SELECT statements allowed
    - No SELECT * (must specify columns)
    - No system catalog access
    - No dangerous operations (INSERT/UPDATE/DELETE/DROP/etc.)
    - Automatic LIMIT enforcement

    Column rewriting:
    - DET columns in WHERE → hash equality check
    - DET/RAND columns in SELECT → encrypted column name (app decrypts results)
    - PLAIN columns → unchanged
    """

    FORBIDDEN_KEYWORDS = [
        "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
        "TRUNCATE", "GRANT", "REVOKE", "COPY", "LOAD", "EXECUTE",
        "pg_read_file", "pg_write_file", "lo_import", "lo_export",
    ]

    def __init__(
        self,
        registry: ColumnEncryptionRegistry = default_registry,
        det_encryptor: DeterministicSM4 | None = None,
        max_output_rows: int = 1000,
    ):
        self.registry = registry
        self.det = det_encryptor
        self.max_output_rows = max_output_rows

    def rewrite(self, sql: str, params: dict | None = None) -> RewriteResult:
        """Rewrite SQL for encrypted column access.

        Args:
            sql: Original SQL from the buyer
            params: Query parameters (for parameterized queries)

        Returns:
            RewriteResult with rewritten SQL and metadata
        """
        sql_clean = sql.strip().rstrip(";")

        # Security checks
        self._check_security(sql_clean)

        encrypted_columns = []
        hash_columns = []

        rewritten = sql_clean

        # Process each registered column
        for col_path, meta in self.registry.all_columns().items():
            table, col = col_path.split(".")

            if meta.level == "det":
                # In WHERE: replace col = 'value' → col_hash = hash('value')
                # Must happen BEFORE SELECT replacement since col name changes
                if self.det and meta.hash_column:
                    where_pattern = rf'({table}\.{col})\s*=\s*[\'"]([^\'"]+)[\'"]'
                    match = re.search(where_pattern, rewritten, re.IGNORECASE)
                    if match:
                        plain_value = match.group(2)
                        hash_value = self.det.hash_for_index(plain_value)
                        rewritten = re.sub(
                            where_pattern,
                            f'{table}.{meta.hash_column} = \'{hash_value}\'',
                            rewritten,
                            flags=re.IGNORECASE,
                        )
                        hash_columns.append(f"{table}.{col}")

                # In SELECT: replace col → col_enc (for app-layer decryption)
                select_pattern = rf'(\b{table}\.){col}(\b(?!\s*(?:_enc|_hash)))'
                if re.search(select_pattern, rewritten, re.IGNORECASE):
                    rewritten = re.sub(
                        select_pattern,
                        rf'\g<1>{meta.enc_column}',
                        rewritten,
                        flags=re.IGNORECASE,
                    )
                    encrypted_columns.append(f"{table}.{col}")

            elif meta.level == "rand":
                # In SELECT: replace col → col_enc (for app-layer decryption)
                select_pattern = rf'(\b{table}\.){col}(\b(?!\s*_enc))'
                if re.search(select_pattern, rewritten, re.IGNORECASE):
                    rewritten = re.sub(
                        select_pattern,
                        rf'\g<1>{meta.enc_column}',
                        rewritten,
                        flags=re.IGNORECASE,
                    )
                    encrypted_columns.append(f"{table}.{col}")

                # RAND columns cannot be used in WHERE — log warning
                where_pattern = rf'WHERE\s+.*?{table}\.{col}\s*='
                if re.search(where_pattern, rewritten, re.IGNORECASE):
                    logger.warning(
                        f"RAND column {table}.{col} used in WHERE — "
                        "will need full scan + app-layer filter"
                    )

        # Ensure LIMIT
        if "LIMIT" not in rewritten.upper():
            rewritten = f"{rewritten} LIMIT {self.max_output_rows}"

        return RewriteResult(
            sql=rewritten,
            params=params,
            encrypted_columns=encrypted_columns,
            hash_columns=hash_columns,
        )

    def _check_security(self, sql: str):
        """Enforce security constraints on SQL."""
        sql_upper = sql.upper().strip()

        # Must be SELECT
        if not sql_upper.startswith("SELECT"):
            raise SecurityError("Only SELECT statements are allowed")

        # No SELECT *
        if re.search(r'SELECT\s+\*', sql_upper):
            raise SecurityError(
                "SELECT * is not allowed — specify columns explicitly"
            )

        # No forbidden keywords
        for kw in self.FORBIDDEN_KEYWORDS:
            # Check as whole word to avoid false positives
            if re.search(rf'\b{kw}\b', sql_upper):
                raise SecurityError(f"Forbidden operation: {kw}")

        # No system catalog access
        if "PG_CATALOG" in sql_upper or "INFORMATION_SCHEMA" in sql_upper:
            raise SecurityError("System catalog access is forbidden")


class ResultDecryptor:
    """Decrypts query result columns using the appropriate encryption method."""

    def __init__(
        self,
        registry: ColumnEncryptionRegistry = default_registry,
        det_encryptor: DeterministicSM4 | None = None,
        rand_encryptor=None,
    ):
        self.registry = registry
        self.det = det_encryptor
        self.rand = rand_encryptor

    def decrypt_rows(
        self,
        rows: list[tuple],
        column_names: list[str],
    ) -> list[tuple]:
        """Decrypt encrypted columns in query results.

        Args:
            rows: Query result rows (list of tuples)
            column_names: Column names corresponding to tuple positions

        Returns:
            Decrypted rows
        """
        if not self.det and not self.rand:
            return rows

        decrypted = []
        for row in rows:
            new_row = list(row)
            for i, col_name in enumerate(column_names):
                if not isinstance(new_row[i], (bytes, bytearray)):
                    continue

                # Find encryption metadata for this column
                meta = self._find_meta_by_enc_column(col_name)
                if not meta:
                    continue

                try:
                    if meta.level == "det" and self.det:
                        new_row[i] = self.det.decrypt(bytes(new_row[i]))
                    elif meta.level == "rand" and self.rand:
                        new_row[i] = self.rand.decrypt(bytes(new_row[i]))
                except Exception as e:
                    logger.warning(f"Failed to decrypt {col_name}: {e}")
                    new_row[i] = "[DECRYPT_ERROR]"

            decrypted.append(tuple(new_row))

        return decrypted

    def _find_meta_by_enc_column(self, enc_col_name: str):
        """Find encryption metadata by the encrypted column name."""
        for _, meta in self.registry.all_columns().items():
            if meta.enc_column == enc_col_name:
                return meta
        return None


# Singletons
query_rewriter = QueryRewriter()
result_decryptor = ResultDecryptor()
