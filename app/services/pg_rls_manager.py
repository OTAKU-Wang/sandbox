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

A133 twin-subsystem convergence conclusion (2026-10-30, wave 2):
- 可达性：全仓零导入方——app/、sdk/、alembic/、scripts/、tests/ 均不导入本
  模块（AST 反向闭包与全文件 grep 双口径复核；连测试都没有）。这是 RLS 双子
  系统里「死」的一半；有测试侧用户的活的一半是 ``rls_engine.py``（经零接线
  的 ``db_access_proxy``）。docs/spec-implementation-analysis.md 称
  db_access_proxy「✅ 已实现」是"代码存在"口径，不代表接线。
- 收敛决定（lead 2026-10-30 拍板）：不做物理合并，本文件保留为 PG-in-TEE
  后端的已标注原型（specs/SS-gap-analysis.md FG-006：未落地）。依据：与
  rls_engine 属不同层（目录级角色/GRANT/策略 DDL vs 应用层策略求值+SQL 改写），
  且把从未执行过的模块并进有测试的模块只会搬运风险；合并的正确时机是
  PG-in-TEE 真落地时，以远程 PG16 语义为准绳重写。
- A133 修复轮（本轮，均为代码级修复）：FORCE RLS 不再锁死应用角色（同表附
  一条 ``TO CURRENT_ROLE`` 管理策略；策略表达式改缺省容忍的
  ``current_setting(..., true)`` 形式）；角色名改 sha256 后缀（旧「清洗+截
  8字符」让不同会话共享 ``sandbox_sessabcd``/``sandbox_default``，退化 id 一
  律拒绝）；DSN 经 ``make_url`` 重建并携带角色口令（旧实现三个缺陷：口令从
  未设置、正则重写把 ``:pass`` 拼成 ``::pass``、基 DSN 无用户名时静默返回
  APP 角色 DSN）；DSN 携带 GUC 的注入面删除，会话上下文改参数化
  ``set_config``；CREATE ROLE 幂等（42710 视为成功）；能力探测只正缓存且
  不再拿 ``row_security`` 当能力位；建策失败/降级如实进 ``RLSStatus.error``；
  清理失败不再丢追踪记录；GRANT 边界钉在 ``sandbox_`` 命名空间且不支持的权
  限如实报错而不是静默吞掉。
- 接线前置（先于任何路由 import 本模块）：
  1. 以上全部只有代码级证据——本仓 CI 无 PostgreSQL，FORCE/42710/
     ``TO CURRENT_ROLE``/GUC 语义必须在远程 PG16 lane 复验后才可接；
  2. 角色生命周期要与 ``pg_roles``/``pg_policies`` 开机对账（进程字典活不过
     重启与多副本，台账 #7），管理策略 ``rls_admin_*`` 属共享资源、不随单会
     话清理删除；
  3. 特权 DDL 需要专用管理连接或 CREATEROLE+表主 app 角色——旧 ``admin_dsn``
     参数从未被使用（台账 #8），已删除；口令目前只活在进程内，多副本前必须
     接真凭据存储。
"""
import hashlib
import logging
import re
import secrets
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# session_ids are UUID-shaped upstream; keep the accepted alphabet equally
# narrow so it can never carry DSN/GUC/identifier payload (A133 #2/#4).
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{6,64}")


@dataclass
class SessionRoleInfo:
    """Metadata for a session-scoped PostgreSQL role."""
    role_name: str
    session_id: str
    created_at: str = ""
    tables_granted: list[str] = field(default_factory=list)
    active: bool = True
    # A133 #3: the role gets a generated password at CREATE time (the old
    # CREATE ROLE set none, so every scram/md5 login with the reused app
    # password failed). The credential lives only in this process dict —
    # see the header's prerequisite 3 before any multi-replica use.
    role_password: str = ""


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

    Privileged DDL (CREATE ROLE / ALTER TABLE … RLS / CREATE POLICY /
    GRANT) runs on the caller-provided session, so the app role must hold
    CREATEROLE plus table ownership. The old ``admin_dsn`` constructor
    parameter was stored and never used (A133 #8) — removed rather than
    half-wired; adding a dedicated admin engine is a wiring decision.
    """

    # Role name prefix and max length for PG identifiers
    _ROLE_PREFIX = "sandbox"
    _MAX_ROLE_LEN = 63  # PostgreSQL NAMEDATALEN - 1

    # Default permissions granted to session roles
    _SAFE_PERMISSIONS = {"SELECT", "INSERT", "UPDATE"}

    def __init__(self):
        """Initialize the PG RLS Manager (see class docstring re: privileges)."""
        self._active_roles: dict[str, SessionRoleInfo] = {}
        self._rls_available: bool | None = None  # positive-cached only
        self._last_error: dict[str, str] = {}

    def _validate_session_id(self, session_id: str) -> str:
        """Reject ids that must never reach a role name, DSN or GUC (A133 #2/#4)."""
        if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
            raise ValueError(
                "session_id must match [A-Za-z0-9_-]{6,64}; refusing degenerate/shared identity"
            )
        return session_id

    def _build_role_name(self, session_id: str) -> str:
        """Build a collision-free PostgreSQL role name from session_id.

        A133 #4: the old ``sanitize + truncate 8`` mapping collided for any
        ids sharing their first alphanumeric 8 chars ('sess-abcdef123456' vs
        'sess-abcdef9999' → same ``sandbox_sessabcd`` → shared RLS filter,
        shared GRANTs, and one session's DROP ROLE evicting the others), and
        every degenerate id collapsed onto one shared ``sandbox_default``.
        A sha256 prefix is injective-enough, fixed-width and identifier-safe.
        """
        sid = self._validate_session_id(session_id)
        suffix = hashlib.sha256(sid.encode("utf-8")).hexdigest()[:16]
        return f"{self._ROLE_PREFIX}_{suffix}"[: self._MAX_ROLE_LEN]

    async def _check_rls_support(self, db: AsyncSession) -> bool:
        """Check whether RLS actually protects this connection.

        A133 #9: ``current_setting('row_security')`` is a USERSET switch, not
        a capability bit — and one transient error used to pin the negative
        for the process lifetime, silently dropping every later session to
        no DB-layer isolation. Now: real capability questions (server ≥ 9.5,
        role without BYPASSRLS), positives cached, negatives re-probed.
        """
        if self._rls_available is True:
            return True

        try:
            result = await db.execute(
                text(
                    "SELECT current_setting('server_version_num')::int >= 90500 "
                    "AS rls_supported, "
                    "COALESCE((SELECT rolbypassrls FROM pg_roles "
                    "WHERE rolname = current_user), false) AS bypass_rls"
                )
            )
            row = result.first()
            available = bool(row is not None and row[0] and not row[1])
            if not available and row is not None:
                logger.warning(
                    "PostgreSQL RLS cannot protect this role (version<9.5 or BYPASSRLS). "
                    "Falling back to application-level session isolation."
                )
            if available:
                self._rls_available = True  # positives only — re-probe failures (A133 #9)
            return available
        except Exception as e:
            logger.warning(f"RLS support check failed (not cached): {e}")
            return False

    @staticmethod
    def _is_duplicate_object(e: Exception) -> bool:
        """True for PostgreSQL 42710 duplicate_object (A133 #12/#16)."""
        orig = getattr(e, "orig", None)
        code = (
            getattr(orig, "sqlstate", None)
            or getattr(orig, "pgcode", None)
            or getattr(orig, "sqlstate_value", None)
        )
        if code == "42710":
            return True
        return "already exists" in str(e)

    def _merge_role_info(self, session_id: str, role_name: str, password: str = "") -> SessionRoleInfo:
        """Get-or-create tracking WITHOUT losing prior grants (A133 #11).

        ``setup_rls_policy`` does not require ``create_session_role`` first,
        so tracking must survive either order; the old wholesale overwrite in
        create_session_role erased tables already recorded (and with them
        the only pointer DROP POLICY had).
        """
        info = self._active_roles.get(session_id)
        if info is None:
            info = SessionRoleInfo(role_name=role_name, session_id=session_id)
            self._active_roles[session_id] = info
        info.role_name = role_name
        info.active = True
        if password:
            info.role_password = password
        return info

    async def create_session_role(
        self, session_id: str, db: AsyncSession
    ) -> str:
        """Create a PostgreSQL role for a sandbox session.

        Creates a LOGIN role with a generated password and minimal
        privileges. The role name is ``sandbox_<sha16>`` (collision-free,
        see _build_role_name). Idempotent: a role that already exists
        (including one this process lost track of after a restart) is
        adopted, not fatalized (A133 #16 — the old pre-check failure fell
        through to CREATE ROLE and died on 42710).

        Returns:
            The created role name.

        Raises:
            RuntimeError: If role creation fails.
        """
        role_name = self._build_role_name(session_id)  # validates session_id

        # Check if role already exists (informational — CREATE tolerates it)
        try:
            result = await db.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
                {"role": role_name},
            )
            if result.first():
                logger.info(f"Session role already exists: {role_name}")
                self._merge_role_info(session_id, role_name)
                return role_name
        except Exception as e:
            # A133 #16: a probe outage must not make creation non-idempotent —
            # fall through; the 42710 catch below is the real idempotence.
            logger.warning(f"Role existence check failed: {e}")

        # A133 #3a: give the role a real credential. token_urlsafe's alphabet
        # (A-Za-z0-9_-) is inert inside single quotes, so quoting is complete.
        role_password = secrets.token_urlsafe(32)
        try:
            safe_role = role_name.replace('"', '""')
            await db.execute(
                text(
                    f'CREATE ROLE "{safe_role}" WITH LOGIN '
                    f"PASSWORD '{role_password}' "
                    f"NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    f"NOINHERIT NOREPLICATION "
                    f"CONNECTION LIMIT 5 "
                    f"VALID UNTIL 'infinity'"
                )
            )
            await db.flush()

            self._merge_role_info(session_id, role_name, password=role_password)
            logger.info(f"Created session role: {role_name}")
            return role_name

        except Exception as e:
            if self._is_duplicate_object(e):
                # A133 #12: interleaved creators — the other one won; adopt.
                logger.info(f"Session role created concurrently, adopting: {role_name}")
                self._merge_role_info(session_id, role_name)
                return role_name
            logger.error(f"Failed to create session role {role_name}: {e}")
            raise RuntimeError(
                f"Role creation failed for session {session_id}: {e}"
            ) from e

    async def setup_rls_policy(
        self, table_name: str, session_id: str, db: AsyncSession
    ) -> bool:
        """Create an RLS policy that filters rows by session_id.

        Fixes baked into the emitted DDL (A133 #1/#10):
        - FORCE ROW LEVEL SECURITY previously bound the table owner too while
          the ONLY policy was ``TO "sandbox_<sid>"`` — after the first call
          the app role (and every other session) saw zero rows and failed
          WITH CHECK. A second policy ``TO CURRENT_ROLE`` (expanded at CREATE
          time to the owning app role) restores the management path; it is
          shared per table and deliberately NOT dropped with a single
          session (header prerequisite 2 owns its lifecycle).
        - ``current_setting('cds.session_id')`` (1-arg) raised on any
          connection without the GUC; the missing-tolerant 2-arg form with an
          unmatchable sentinel keeps fail-closed semantics without erroring.
        - ``DROP POLICY IF EXISTS`` first: the old code was non-idempotent
          and left the table FORCE-RLS'd while reporting "fallback".

        Failures are recorded per session and surfaced through
        ``get_status().error`` (A133 #10 — the field existed but nothing
        ever wrote it).

        Args:
            table_name: The table to apply RLS on.
            session_id: The sandbox session identifier.
            db: AsyncSession for executing SQL.

        Returns:
            True if RLS policy was applied, False if fallback mode.
        """
        rls_ok = await self._check_rls_support(db)
        if not rls_ok:
            self._last_error[session_id] = "RLS unavailable on this server/role; application-level filtering only"
            logger.warning(
                f"RLS not available, skipping policy for {table_name}. "
                "Using application-level filtering."
            )
            return False

        role_name = self._build_role_name(session_id)
        policy_name = f"rls_{role_name}_{table_name}"
        admin_policy_name = f"rls_admin_{table_name}"

        # Sanitize identifiers
        safe_table = table_name.replace('"', '""')
        safe_policy = policy_name.replace('"', '""')
        safe_admin_policy = admin_policy_name.replace('"', '""')
        safe_role = role_name.replace('"', '""')

        # Missing-tolerant read; the sentinel can never equal a real
        # session_id column value, so an unset GUC filters nothing in (denies).
        guc_expr = (
            "coalesce(current_setting('cds.session_id', true), '__cds_no_session__')"
        )

        try:
            # Enable RLS on the table (requires table owner)
            await db.execute(
                text(f'ALTER TABLE "{safe_table}" ENABLE ROW LEVEL SECURITY')
            )

            # Force RLS even for table owner — paired with the admin policy
            # below, otherwise the app role is locked out of its own table.
            await db.execute(
                text(
                    f'ALTER TABLE "{safe_table}" FORCE ROW LEVEL SECURITY'
                )
            )

            # Idempotence (A133 #10): a second call re-creates, not errors.
            await db.execute(
                text(f'DROP POLICY IF EXISTS "{safe_policy}" ON "{safe_table}"')
            )
            await db.execute(
                text(
                    f'DROP POLICY IF EXISTS "{safe_admin_policy}" ON "{safe_table}"'
                )
            )

            # Management policy for the creating (app) role — CURRENT_ROLE
            # expands at CREATE POLICY time, not at query time.
            await db.execute(
                text(
                    f'CREATE POLICY "{safe_admin_policy}" ON "{safe_table}" '
                    f"FOR ALL TO CURRENT_ROLE "
                    f"USING (true) WITH CHECK (true)"
                )
            )

            # Permissive policy for the session role
            await db.execute(
                text(
                    f'CREATE POLICY "{safe_policy}" ON "{safe_table}" '
                    f"FOR ALL "
                    f'TO "{safe_role}" '
                    f"USING (session_id = {guc_expr}) "
                    f"WITH CHECK (session_id = {guc_expr})"
                )
            )
            await db.flush()

            # Track applied policy — setdefault, never conditional-on-existing
            # (A133 #11: untracked policies orphan forever).
            info = self._merge_role_info(session_id, role_name)
            if table_name not in info.tables_granted:
                info.tables_granted.append(table_name)
            self._last_error.pop(session_id, None)

            logger.info(
                f"RLS policy '{safe_policy}' applied on '{safe_table}' "
                f"for role '{safe_role}'"
            )
            return True

        except Exception as e:
            self._last_error[session_id] = f"RLS policy setup failed for {table_name}: {e}"
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

        Boundaries (A133 #13/#14):
        - ``role`` must live in this manager's ``sandbox_`` namespace —
          without that check this method was a general GRANT endpoint for
          any principal in the database;
        - a permission outside the safe DML set raises instead of being
          silently skipped while the method still returned True (a caller
          asking for ["SELECT","DELETE"] used to get True and no DELETE).

        Args:
            role: A ``sandbox_*`` role name produced by create_session_role.
            table_name: The table to grant access to.
            permissions: List of permissions (subset of SELECT/INSERT/UPDATE).
            db: AsyncSession for executing SQL.

        Returns:
            True if all requested grants were executed.

        Raises:
            ValueError: On an unsafe or unsupported permission, or a role
                outside the sandbox namespace.
        """
        unsafe = {"CREATE", "DROP", "ALTER", "TRUNCATE", "GRANT", "ALL"}
        requested = {p.upper().strip() for p in permissions}
        dangerous = requested & unsafe
        if dangerous:
            raise ValueError(
                f"Unsafe permissions requested for session role: {dangerous}. "
                f"Allowed: {self._SAFE_PERMISSIONS}"
            )
        unsupported = requested - self._SAFE_PERMISSIONS
        if unsupported:
            raise ValueError(
                f"Unsupported permissions requested for session role: {unsupported}. "
                f"Allowed: {self._SAFE_PERMISSIONS}"
            )
        if not role.startswith(f"{self._ROLE_PREFIX}_"):
            raise ValueError(
                f"grant_table_access only manages {self._ROLE_PREFIX}_* roles, got {role!r}"
            )

        safe_role = role.replace('"', '""')
        safe_table = table_name.replace('"', '""')

        granted = []
        for perm in sorted(requested):
            try:
                await db.execute(
                    text(
                        f'GRANT {perm} ON "{safe_table}" '
                        f'TO "{safe_role}"'
                    )
                )
                granted.append(perm)
            except Exception as e:
                logger.error(
                    f"Failed to grant {perm} on {table_name} to {role}: {e}"
                )
                raise

        await db.flush()
        logger.info(f"Granted {granted} on {table_name} to {role}")
        return set(granted) == requested

    async def drop_session_role(
        self, session_id: str, db: AsyncSession
    ) -> bool:
        """Drop the session role and revoke all associated policies.

        Cleans up all RLS policies and table grants created for this
        session, then drops the role. The shared ``rls_admin_*`` management
        policies are NOT dropped (they belong to the table/app role, not one
        session — header prerequisite 2).

        A133 #6: a failed DROP ROLE used to still delete the tracking entry,
        orphaning the role and its policies with no way left to enumerate
        them; on error the entry is now kept (deactivated) for a later
        reconcile. A133 #7: tracked tables are revoked individually — the
        blanket revoke only ever covered schema ``public`` while the product
        spec places data in other schemas.

        Args:
            session_id: The sandbox session identifier.
            db: AsyncSession for executing SQL.

        Returns:
            True if cleanup succeeded (best-effort per step).
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
                try:
                    await db.execute(
                        text(
                            f'REVOKE ALL PRIVILEGES ON TABLE "{safe_table}" '
                            f'FROM "{safe_role}"'
                        )
                    )
                except Exception as e:
                    errors.append(f"REVOKE failed on {table_name}: {e}")
                    logger.warning(f"Per-table revocation failed on {table_name}: {e}")

        # Step 2: Sweep the public schema too (best effort; other schemas are
        # covered by the per-table revokes above when tracked — the residual
        # untracked case is the boot-reconcile prerequisite, not something a
        # process dict can promise).
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

        await db.flush()

        # Step 4: Tracking is dropped ONLY when the DB side fully succeeded
        # (A133 #6). Old code: unconditional delete + a dead `.active=False`
        # line on the object that was deleted the next statement.
        if not errors:
            self._active_roles.pop(session_id, None)
            self._last_error.pop(session_id, None)
            logger.info(f"Session role {role_name} dropped for session {session_id}")
            return True

        if info is not None:
            info.active = False  # retained for reconcile; not "active"
        logger.warning(
            f"Session role cleanup for {session_id} had {len(errors)} errors — "
            f"tracking entry retained for reconciliation"
        )
        return False

    def get_connection_string(
        self, session_id: str, base_dsn: str
    ) -> str:
        """Return a DSN with the session-specific role.

        Rebuilt via SQLAlchemy ``make_url`` (A133 #3b/#3c): the old regex
        rewrite (i) silently returned the *app-role* DSN when the base DSN
        carried no username — indistinguishable from a real session DSN,
        handing the caller full application privileges; (ii) doubled the
        colon of ``:pass`` captures, producing a malformed password even on
        the happy path; (iii) never had a real password to give, because
        CREATE ROLE set none. Now the password generated at create time is
        carried, and an unknown credential raises instead of degrading.

        A133 #2: the session context is no longer smuggled through the DSN
        ``options`` string (an unencoded ``session_id`` there terminated the
        parameter at the first space and appended attacker-chosen ``-c`` GUCs
        such as ``row_security=off``). Callers set the context per connection
        with :meth:`set_session_context`, which is parameter-bound.

        Args:
            session_id: The sandbox session identifier.
            base_dsn: The base PostgreSQL DSN
                      (e.g., postgresql+asyncpg://user:pass@host:5432/db).

        Returns:
            Modified DSN string with session role and its credential.

        Raises:
            ValueError: On a degenerate session_id, a base DSN without a
                username, or a session whose role password this process never
                minted (no credential store yet — header prerequisite 3).
        """
        role_name = self._build_role_name(session_id)

        try:
            url = make_url(base_dsn)
        except Exception as e:  # make_url raises on garbage input
            raise ValueError(f"Unparseable base DSN: {e}") from e
        if not url.username:
            raise ValueError(
                "base_dsn carries no username — refusing to hand out the app-role "
                "DSN as a session DSN (the old regex silently did exactly that)"
            )

        info = self._active_roles.get(session_id)
        if info is None or not info.role_password:
            raise ValueError(
                f"No session role credential known for {session_id}: this process "
                "must have created the role (a role adopted after restart has no "
                "password — rotate it via the admin path first)"
            )

        url = url.set(username=role_name, password=info.role_password)
        return url.render_as_string(hide_password=False)

    def get_status(self, session_id: str) -> RLSStatus:
        """Get the RLS status for a session.

        A133 #10: ``RLSStatus.error`` used to be write-never — a caller
        reading status saw a protected session even when every policy
        statement had failed. Setup errors now surface here, and any error
        flips ``fallback_mode``.
        """
        info = self._active_roles.get(session_id)
        error = self._last_error.get(session_id)
        if not info:
            return RLSStatus(
                session_id=session_id,
                fallback_mode=(self._rls_available is False) or error is not None,
                error=error,
            )
        return RLSStatus(
            session_id=session_id,
            role_created=True,
            rls_enabled=bool(info.tables_granted),
            policies_applied=list(info.tables_granted),
            fallback_mode=(self._rls_available is False) or error is not None,
            error=error,
        )

    async def set_session_context(
        self, session_id: str, db: AsyncSession
    ) -> None:
        """Set the session context for RLS policy evaluation.

        Parameter-bound ``set_config`` (A133 #5): ``SET`` cannot take a bind
        parameter, so the old code hand-quoted session_id into the statement;
        ``set_config(name, $1, true)`` binds it properly. ``is_local=true``
        keeps the old SET LOCAL scoping (transaction-scoped). The policy side
        reads the GUC missing-tolerantly, so a pooled connection that lost
        the setting now sees zero rows instead of erroring on an
        ``unrecognized configuration parameter``.

        Args:
            session_id: The sandbox session identifier.
            db: AsyncSession for executing SQL.
        """
        self._validate_session_id(session_id)
        await db.execute(
            text("SELECT set_config('cds.session_id', :sid, true)"),
            {"sid": session_id},
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
