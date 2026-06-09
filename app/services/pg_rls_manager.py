"""PostgreSQL RLS Manager -- degraded mode for TEE isolation.

When SGX is not available, provides session-level isolation using
PostgreSQL Row Level Security (RLS) policies instead of TEE-based
database isolation.

Degraded mode security model:
- Each sandbox session gets a dedicated PostgreSQL role
- RLS policies filter rows by session_id column
- Roles have minimal privileges (no DDL, no cross-session access)
- All session roles are dropped on session termination
- Graceful fallback to application-level filtering if RLS unavailable
"""
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class SessionRoleInfo:
    """Metadata for a session-scoped PostgreSQL role."""
    role_name: str
    session_id: str
    created_at: str = ""
    tables_granted: list[str] = field(default_factory=list)
    active: bool = True


@dataclass
class RLSStatus:
    """Status of RLS degraded mode for a session."""
    session_id: str
    role_created: bool = False
    rls_enabled: bool = False
    policies_applied: list[str] = field(default_factory=list)
    fallback_mode: bool = False
    error: str | None = None


class PGRLSManager:
    """PostgreSQL RLS manager for degraded-mode session isolation.

    When TEE/SGX is unavailable, this manager creates per-session
    PostgreSQL roles with RLS policies to enforce row-level data
    isolation at the database layer.

    Usage:
        manager = PGRLSManager()
        role = await manager.create_session_role(session_id, db)
        await manager.setup_rls_policy("my_table", session_id, db)
        conn_str = manager.get_connection_string(session_id, base_dsn)
    """

    # Role name prefix and max length for PG identifiers
    _ROLE_PREFIX = "sandbox"
    _MAX_ROLE_LEN = 63  # PostgreSQL NAMEDATALEN - 1

    # Default permissions granted to session roles
    _SAFE_PERMISSIONS = {"SELECT", "INSERT", "UPDATE"}

    def __init__(self, admin_dsn: str | None = None):
        """Initialize the PG RLS Manager.

        Args:
            admin_dsn: Admin DSN for creating roles. If None, uses the
                       session's own connection (requires SUPERUSER or
                       CREATEROLE privilege on the app role).
        """
        self._admin_dsn = admin_dsn
        self._active_roles: dict[str, SessionRoleInfo] = {}
        self._rls_available: bool | None = None  # Lazy-checked

    def _sanitize_session_id(self, session_id: str) -> str:
        """Sanitize session_id for use as a PG role name component."""
        # Keep only alphanumeric and underscore, truncate to 8 chars
        clean = re.sub(r"[^a-zA-Z0-9_]", "", session_id)
        return clean[:8] if clean else "default"

    def _build_role_name(self, session_id: str) -> str:
        """Build a PostgreSQL role name from session_id."""
        suffix = self._sanitize_session_id(session_id)
        role = f"{self._ROLE_PREFIX}_{suffix}"
        # Truncate to PG limit
        return role[: self._MAX_ROLE_LEN]

    async def _check_rls_support(self, db: AsyncSession) -> bool:
        """Check if the PostgreSQL server supports RLS.

        Returns True if RLS is available, False otherwise.
        Caches the result after first check.
        """
        if self._rls_available is not None:
            return self._rls_available

        try:
            result = await db.execute(
                text("SELECT current_setting('row_security') AS rls")
            )
            row = result.first()
            self._rls_available = row is not None and row[0] == "on"
        except Exception as e:
            logger.warning(f"RLS support check failed: {e}")
            self._rls_available = False

        if not self._rls_available:
            logger.warning(
                "PostgreSQL RLS is not available. "
                "Falling back to application-level session isolation."
            )
        return self._rls_available

    async def create_session_role(
        self, session_id: str, db: AsyncSession
    ) -> str:
        """Create a PostgreSQL role for a sandbox session.

        Creates a LOGIN role with limited permissions. The role name
        follows the pattern: sandbox_{session_id[:8]}

        Args:
            session_id: The sandbox session identifier.
            db: AsyncSession for executing SQL.

        Returns:
            The created role name.

        Raises:
            RuntimeError: If role creation fails.
        """
        role_name = self._build_role_name(session_id)

        # Check if role already exists
        try:
            result = await db.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
                {"role": role_name},
            )
            if result.first():
                logger.info(f"Session role already exists: {role_name}")
                self._active_roles[session_id] = SessionRoleInfo(
                    role_name=role_name, session_id=session_id, active=True
                )
                return role_name
        except Exception as e:
            logger.warning(f"Role existence check failed: {e}")

        # Create the role
        try:
            # Use quoted identifier to prevent injection
            safe_role = role_name.replace('"', '""')
            await db.execute(
                text(
                    f'CREATE ROLE "{safe_role}" WITH LOGIN '
                    f"NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    f"NOINHERIT NOREPLICATION "
                    f"CONNECTION LIMIT 5 "
                    f"VALID UNTIL 'infinity'"
                )
            )
            await db.flush()

            self._active_roles[session_id] = SessionRoleInfo(
                role_name=role_name, session_id=session_id, active=True
            )
            logger.info(f"Created session role: {role_name}")
            return role_name

        except Exception as e:
            logger.error(f"Failed to create session role {role_name}: {e}")
            raise RuntimeError(
                f"Role creation failed for session {session_id}: {e}"
            ) from e

    async def setup_rls_policy(
        self, table_name: str, session_id: str, db: AsyncSession
    ) -> bool:
        """Create an RLS policy that filters rows by session_id.

        Enables RLS on the table and creates a policy that restricts
        access to rows where session_id matches the current session's role.

        The policy uses current_setting('cds.session_id') to identify the
        session at query time.

        Args:
            table_name: The table to apply RLS on.
            session_id: The sandbox session identifier.
            db: AsyncSession for executing SQL.

        Returns:
            True if RLS policy was applied, False if fallback mode.
        """
        rls_ok = await self._check_rls_support(db)
        if not rls_ok:
            logger.warning(
                f"RLS not available, skipping policy for {table_name}. "
                "Using application-level filtering."
            )
            return False

        role_name = self._build_role_name(session_id)
        policy_name = f"rls_{role_name}_{table_name}"

        # Sanitize identifiers
        safe_table = table_name.replace('"', '""')
        safe_policy = policy_name.replace('"', '""')
        safe_role = role_name.replace('"', '""')

        try:
            # Enable RLS on the table (requires table owner)
            await db.execute(
                text(f'ALTER TABLE "{safe_table}" ENABLE ROW LEVEL SECURITY')
            )

            # Force RLS even for table owner
            await db.execute(
                text(
                    f'ALTER TABLE "{safe_table}" FORCE ROW LEVEL SECURITY'
                )
            )

            # Create permissive policy for the session role
            # Uses current_setting to get session context at runtime
            await db.execute(
                text(
                    f'CREATE POLICY "{safe_policy}" ON "{safe_table}" '
                    f"FOR ALL "
                    f'TO "{safe_role}" '
                    f"USING (session_id = current_setting('cds.session_id')::text) "
                    f"WITH CHECK (session_id = current_setting('cds.session_id')::text)"
                )
            )
            await db.flush()

            # Track applied policy
            info = self._active_roles.get(session_id)
            if info:
                info.tables_granted.append(table_name)

            logger.info(
                f"RLS policy '{safe_policy}' applied on '{safe_table}' "
                f"for role '{safe_role}'"
            )
            return True

        except Exception as e:
            logger.warning(
                f"RLS policy setup failed for {table_name}: {e}. "
                "Falling back to application-level filtering."
            )
            return False

    async def grant_table_access(
        self,
        role: str,
        table_name: str,
        permissions: list[str],
        db: AsyncSession,
    ) -> bool:
        """Grant specific permissions on a table to a session role.

        Args:
            role: The PostgreSQL role name.
            table_name: The table to grant access to.
            permissions: List of permissions (e.g., ['SELECT', 'INSERT']).
            db: AsyncSession for executing SQL.

        Returns:
            True if grants succeeded.

        Raises:
            ValueError: If an unsafe permission is requested.
        """
        # Validate permissions - only allow safe DML operations
        unsafe = {"CREATE", "DROP", "ALTER", "TRUNCATE", "GRANT", "ALL"}
        requested = {p.upper().strip() for p in permissions}
        dangerous = requested & unsafe
        if dangerous:
            raise ValueError(
                f"Unsafe permissions requested for session role: {dangerous}. "
                f"Allowed: {self._SAFE_PERMISSIONS}"
            )

        safe_role = role.replace('"', '""')
        safe_table = table_name.replace('"', '""')

        granted = []
        for perm in requested:
            perm_upper = perm.upper()
            if perm_upper not in self._SAFE_PERMISSIONS:
                logger.warning(
                    f"Skipping unsupported permission '{perm}' for role {role}"
                )
                continue
            try:
                await db.execute(
                    text(
                        f'GRANT {perm_upper} ON "{safe_table}" '
                        f'TO "{safe_role}"'
                    )
                )
                granted.append(perm_upper)
            except Exception as e:
                logger.error(
                    f"Failed to grant {perm} on {table_name} to {role}: {e}"
                )
                raise

        await db.flush()
        logger.info(f"Granted {granted} on {table_name} to {role}")
        return True

    async def drop_session_role(
        self, session_id: str, db: AsyncSession
    ) -> bool:
        """Drop the session role and revoke all associated policies.

        Cleans up all RLS policies and table grants created for this
        session, then drops the role.

        Args:
            session_id: The sandbox session identifier.
            db: AsyncSession for executing SQL.

        Returns:
            True if cleanup succeeded (best-effort).
        """
        role_name = self._build_role_name(session_id)
        safe_role = role_name.replace('"', '""')
        errors: list[str] = []

        info = self._active_roles.get(session_id)

        # Step 1: Drop RLS policies on granted tables
        if info and info.tables_granted:
            for table_name in info.tables_granted:
                policy_name = f"rls_{role_name}_{table_name}"
                safe_policy = policy_name.replace('"', '""')
                safe_table = table_name.replace('"', '""')
                try:
                    await db.execute(
                        text(
                            f'DROP POLICY IF EXISTS "{safe_policy}" '
                            f'ON "{safe_table}"'
                        )
                    )
                except Exception as e:
                    errors.append(
                        f"Failed to drop policy {policy_name}: {e}"
                    )
                    logger.warning(
                        f"Policy cleanup failed for {policy_name}: {e}"
                    )

        # Step 2: Revoke all privileges
        try:
            await db.execute(
                text(f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM \"{safe_role}\"")
            )
        except Exception as e:
            errors.append(f"REVOKE ALL failed: {e}")
            logger.warning(f"Privilege revocation failed: {e}")

        # Step 3: Drop the role
        try:
            await db.execute(text(f'DROP ROLE IF EXISTS "{safe_role}"'))
        except Exception as e:
            errors.append(f"DROP ROLE failed: {e}")
            logger.error(f"Failed to drop role {role_name}: {e}")

        # Step 4: Clean up tracking
        if session_id in self._active_roles:
            self._active_roles[session_id].active = False
            del self._active_roles[session_id]

        await db.flush()

        if errors:
            logger.warning(
                f"Session role cleanup for {session_id} had {len(errors)} errors"
            )
            return False

        logger.info(f"Session role {role_name} dropped for session {session_id}")
        return True

    def get_connection_string(
        self, session_id: str, base_dsn: str
    ) -> str:
        """Return a DSN with the session-specific role.

        Replaces the username in the base DSN with the session role name
        and adds cds.session_id as a runtime parameter.

        Args:
            session_id: The sandbox session identifier.
            base_dsn: The base PostgreSQL DSN
                      (e.g., postgresql+asyncpg://user:pass@host:5432/db).

        Returns:
            Modified DSN string with session role and session context.
        """
        role_name = self._build_role_name(session_id)

        # Parse and replace the username in the DSN
        # Pattern: postgresql[+driver]://[user[:pass]@]host[:port][/db][?params]
        dsn = base_dsn

        # Replace username portion
        # Match: ://user@ or ://user:pass@
        dsn = re.sub(
            r"://([^:@]+)(:[^@]*)?@",
            f"://{role_name}:\\2@",
            dsn,
            count=1,
        )

        # Add session context as a runtime parameter
        separator = "&" if "?" in dsn else "?"
        dsn += f"{separator}options=-c%20cds.session_id%3D{session_id}"

        return dsn

    def get_status(self, session_id: str) -> RLSStatus:
        """Get the RLS status for a session."""
        info = self._active_roles.get(session_id)
        if not info:
            return RLSStatus(
                session_id=session_id,
                fallback_mode=self._rls_available is False,
            )
        return RLSStatus(
            session_id=session_id,
            role_created=True,
            rls_enabled=bool(info.tables_granted),
            policies_applied=list(info.tables_granted),
            fallback_mode=self._rls_available is False,
        )

    async def set_session_context(
        self, session_id: str, db: AsyncSession
    ) -> None:
        """Set the session context for RLS policy evaluation.

        Sets the cds.session_id PostgreSQL configuration parameter
        on the current connection so RLS policies can use it.

        Args:
            session_id: The sandbox session identifier.
            db: AsyncSession for executing SQL.
        """
        safe_id = session_id.replace("'", "''")
        await db.execute(
            text(f"SET LOCAL cds.session_id = '{safe_id}'")
        )

    @property
    def active_sessions(self) -> list[str]:
        """List session IDs with active roles."""
        return [
            sid
            for sid, info in self._active_roles.items()
            if info.active
        ]


# Singleton (no admin_dsn -- uses app connection by default)
pg_rls_manager = PGRLSManager()
