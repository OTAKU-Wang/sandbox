"""RLS Strategy Engine — Row-Level Security policy evaluation.

Provides three policy types for fine-grained data access control:
- Region policy: Filter by geographic region/tenant
- Time policy: Filter by time window (data retention, embargo periods)
- Sensitivity policy: Filter by security level clearance

Policies are evaluated and converted to SQL WHERE clauses for injection
into database queries.

A133 twin-subsystem convergence conclusion (2026-10-30, wave 2 — 可达性先行，
证据在基线工作树上重跑，不抄台账)。（2026-11-03 死代码清理：下述
``db_access_proxy`` / ``pg_rls_manager`` 死半区及各自专属测试已删除，本模块为
该家族仅存实现。）
- 可达性：本模块是 RLS 双子系统中唯一有测试侧用户的一半。app/ 内唯一导入方
  是 ``db_access_proxy.py:17``，而 ``db_access_proxy`` 自身在 app/ 内零导入方
  （只有 ``tests/test_db_access_proxy.py`` / ``tests/test_sprint4_services.py``
  导入它）；``app.main`` 经 AST 反向闭包不可达。⇒ 整个家族 LATENT：今天生产
  不执行任何一行，接线即高。姊妹模块 ``pg_rls_manager.py`` 全仓零导入方
  （连测试都没有），是对照 PG-in-TEE 后端（FG-006 未落地）的死的一半。
- 收敛决定（lead 2026-10-30 拍板）：不做物理合并。本模块是应用层 PDP
  （策略求值 + SQL 改写），pg_rls_manager 是目录级 DDL（角色/GRANT/策略对象），
  两层职责不同；把一个从未执行过的模块并进有测试的一半只会搬运风险。
- A133 修复轮（本轮）：所有 fail-open 改 fail-closed（区域策略缺上下文、
  未知 clearance 生成 ``IN ()``、未知 policy_type 放行、敏感度上限被反转）、
  策略携带的列名一律过 ``_ident()`` 白名单、WHERE 注入改最外层深度 0 定位并
  把既有子句加括号（``OR`` 优先级旁路）、时间窗改 UTC 且支持跨午夜、
  ``request_time`` 真正生效。
- 接线/合并前置（任何路由 import 本模块之前必须完成的，逐条有依据）：
  1. 自由 SQL 的文本改写不可能用字符串手术做安全（深度 0 扫描已消除已知
     旁路，但对注释/引号标识符仍是尽力而为）——接线时换真 SQL 解析器
     （sqlglot 级）或由调用方查询构造器自己带谓词（台账 #21/#27 残留面）；
  2. 敏感度模型只留一个：dict-API 用 int 0-3，RLSPolicy-API 用
     public/…/secret 枚举，同文件双模型必须先统一；
  3. ``_policies`` 是进程内字典，重启即失、多副本不共享——接线前策略必须
     落库并带缓存失效。
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

# Identifier whitelist for policy-supplied column names (A133 #19/#23/#36):
# plain or one-level qualified (`col` / `t.col`). Deliberately stricter than
# PG's grammar — unicode identifiers are policy configuration, not user data,
# and a policy that cannot say what it means must fail at registration time.
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?")


def _ident(name: str, *, what: str = "column") -> str:
    """Validate a policy-supplied SQL identifier; raise on anything else.

    Value-position escaping (`_escape`) cannot protect identifier slots —
    the old code interpolated `region_column` etc. verbatim, so a policy
    whose column was \"region = 'a' OR 1=1 --\" became part of the SQL.
    """
    if not isinstance(name, str) or not _IDENT_RE.fullmatch(name):
        raise ValueError(f"Invalid RLS policy {what}: {name!r}")
    return name


def _ascii_upper_fixed(sql: str) -> str:
    """Upper-case copy with identical indexing (ASCII keywords only).

    ``str.upper()`` can change length for non-ASCII input ('ß' → 'SS'),
    which would desynchronise every index derived from the upper copy.
    """
    return "".join(ch.upper() if ch.isascii() else ch for ch in sql)


def _code_positions(sql_upper: str) -> Iterator[tuple[int, str, int]]:
    """Yield (index, char, bracket_depth) for characters outside '...' strings.

    Best-effort by design (module header, prerequisite 1): single-quote
    literals and bracket depth are tracked because they are what actually
    defeats naive text surgery (\"WHERE\" inside a string, WHERE of an inner
    subquery); comments and double-quoted identifiers are NOT parsed — a
    real SQL parser is the wiring prerequisite, not a TODO hidden here.
    """
    in_str = False
    depth = 0
    i = 0
    n = len(sql_upper)
    while i < n:
        ch = sql_upper[i]
        if in_str:
            if ch == "'":
                if i + 1 < n and sql_upper[i + 1] == "'":
                    i += 2
                    continue
                in_str = False
        else:
            if ch == "'":
                in_str = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            else:
                yield i, ch, depth
        i += 1


def _find_keyword_depth0(sql_upper: str, keyword: str, *, after: int = 0) -> int:
    """First depth-0, word-bounded occurrence of ``keyword`` at/after ``after``.

    ``keyword`` must be upper-case with single spaces (callers normalise
    whitespace first). Returns -1 when absent. A133 #21/#27: keyword
    placement used to be positional text search, which lands inside
    subqueries (rfind takes the INNERMOST WHERE) and inside literals.
    """
    klen = len(keyword)
    for i, ch, depth in _code_positions(sql_upper):
        if depth != 0 or i < after:
            continue
        if ch != keyword[0]:
            continue
        if not sql_upper.startswith(keyword, i):
            continue
        prev_ok = i == 0 or not (sql_upper[i - 1].isalnum() or sql_upper[i - 1] == "_")
        j = i + klen
        next_ok = j >= len(sql_upper) or not (sql_upper[j].isalnum() or sql_upper[j] == "_")
        if prev_ok and next_ok:
            return i
    return -1


# Clause terminators that a WHERE block must close before (in scan order).
_CLAUSE_KEYWORDS = ("GROUP BY", "HAVING", "ORDER BY", "LIMIT")


def inject_condition_outermost(sql: str, condition: str) -> str:
    """AND ``condition`` into the OUTERMOST query of a SELECT (depth 0).

    ONE placement rule for the whole module — the two historical injectors
    disagreed with each other (find vs rfind) and both were wrong
    (A133 #21/#22/#27/#28). Semantics:
      WHERE exists → the existing clause is wrapped in parens and the new
                     condition ANDed after it (an existing ``a = 1 OR b = 2``
                     must not absorb the RLS predicate through precedence);
      no WHERE     → WHERE inserted before the first depth-0 clause keyword
                     (GROUP BY / HAVING / ORDER BY / LIMIT), else appended.
    Whitespace is normalised and a trailing ``;`` is preserved (A133 #22 —
    a newline before WHERE used to produce two WHERE keywords, invalid SQL).
    """
    sql = " ".join(sql.split())
    trailing_semi = sql.endswith(";")
    if trailing_semi:
        sql = sql[:-1].rstrip()
    upper = _ascii_upper_fixed(sql)
    cond = f"({condition})"

    where_idx = _find_keyword_depth0(upper, "WHERE")
    if where_idx >= 0:
        clause_start = where_idx + len("WHERE")
        end = len(sql)
        for kw in _CLAUSE_KEYWORDS:
            idx = _find_keyword_depth0(upper, kw, after=clause_start)
            if 0 <= idx < end:
                end = idx
        clause = sql[clause_start:end].strip()
        rebuilt = (
            sql[:where_idx].rstrip()
            + " WHERE (" + clause + ") AND " + cond
            + (" " + sql[end:] if end < len(sql) else "")
        )
    else:
        insert_at = len(sql)
        for kw in _CLAUSE_KEYWORDS:
            idx = _find_keyword_depth0(upper, kw)
            if 0 <= idx < insert_at:
                insert_at = idx
        rebuilt = (
            sql[:insert_at].rstrip()
            + " WHERE " + cond
            + (" " + sql[insert_at:] if insert_at < len(sql) else "")
        )
    return rebuilt + (";" if trailing_semi else "")


class PolicyType(str, Enum):
    REGION = "region"
    TIME = "time"
    SENSITIVITY = "sensitivity"


class SensitivityLevel(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    SECRET = "secret"

    @classmethod
    def clearance_rank(cls, level: str) -> int:
        """Return numeric rank for comparison (higher = more sensitive)."""
        ranks = {
            cls.PUBLIC.value: 0,
            cls.INTERNAL.value: 1,
            cls.CONFIDENTIAL.value: 2,
            cls.SECRET.value: 3,
        }
        return ranks.get(level, -1)


@dataclass
class RLSPolicy:
    """A single Row-Level Security policy."""
    policy_id: str
    name: str
    policy_type: PolicyType
    enabled: bool = True
    priority: int = 0  # Higher = evaluated first
    conditions: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    # A133 #30: evaluate_sql() advertised a table_name parameter that nothing
    # ever read — policies now carry the table they belong to. None = global.
    table: Optional[str] = None

    def to_sql_condition(self, context: dict[str, Any]) -> str | None:
        """Generate SQL WHERE clause from policy + evaluation context.

        Returns None if policy doesn't apply to the current context.
        An *unparseable* policy type is not "inapplicable": it denies
        (A133 #26 — unknown type used to fall through to "no predicate").
        """
        if not self.enabled:
            return None

        if self.policy_type == PolicyType.REGION:
            return self._region_condition(context)
        elif self.policy_type == PolicyType.TIME:
            return self._time_condition(context)
        elif self.policy_type == PolicyType.SENSITIVITY:
            return self._sensitivity_condition(context)
        # A condition that comes from here is AND-combined onto the caller's
        # SQL, so the only honest fallback for "I don't understand this
        # policy" is deny-all, never None.
        logger.error(
            "Unknown RLS policy type %r on policy %s — denying (returning FALSE)",
            self.policy_type, self.policy_id,
        )
        return "FALSE"

    def _region_condition(self, context: dict[str, Any]) -> str | None:
        """Generate region-based WHERE clause.

        Conditions:
        - allowed_regions: list of allowed region codes
        - region_column: column name containing the region data

        Fail-closed (A133 #17): an enabled region policy with no allowed list
        or a context without user_region used to return None — i.e. the
        region filter silently vanished. An enabled policy that cannot be
        honoured must deny.
        """
        allowed = self.conditions.get("allowed_regions", [])
        region_column = _ident(self.conditions.get("region_column", "region"))
        user_region = context.get("user_region")

        if not allowed:
            logger.error("Region policy %s has empty allowed_regions — denying", self.policy_id)
            return "FALSE"
        if not user_region:
            logger.error("Region policy %s evaluated without user_region context — denying", self.policy_id)
            return "FALSE"

        if user_region not in allowed:
            # Deny access — return impossible condition
            return "FALSE"

        return f"{region_column} = '{self._escape(user_region)}'"

    def _time_condition(self, context: dict[str, Any]) -> str | None:
        """Generate time-based WHERE clause.

        Conditions:
        - time_column: column name for timestamp filtering
        - min_age_hours: minimum data age (no access to data newer than this)
        - max_age_hours: maximum data age (no access to data older than this)
        - embargo_hours: block access to data within this many hours of creation
        """
        time_column = _ident(self.conditions.get("time_column", "created_at"))
        min_age_hours = self.conditions.get("min_age_hours")
        max_age_hours = self.conditions.get("max_age_hours")
        embargo_hours = self.conditions.get("embargo_hours")

        # A133 #33: the context carried request_time but nothing read it —
        # evaluation was not reproducible per request. Naive times are taken
        # as UTC (repo-wide convention, cf. audit boundaries).
        now = context.get("request_time") or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        conditions = []

        if embargo_hours is not None:
            cutoff = now - timedelta(hours=embargo_hours)
            conditions.append(f"{time_column} <= '{cutoff.isoformat()}'")

        if min_age_hours is not None:
            cutoff = now - timedelta(hours=min_age_hours)
            conditions.append(f"{time_column} <= '{cutoff.isoformat()}'")

        if max_age_hours is not None:
            cutoff = now - timedelta(hours=max_age_hours)
            conditions.append(f"{time_column} >= '{cutoff.isoformat()}'")

        if not conditions:
            return None

        return " AND ".join(conditions)

    def _sensitivity_condition(self, context: dict[str, Any]) -> str | None:
        """Generate sensitivity-level WHERE clause.

        Conditions:
        - sensitivity_column: column name for sensitivity level
        - max_allowed_level: maximum sensitivity level the user can access

        Fixed semantics (A133 #18/#29):
        - the policy ceiling was never applied — a user clearing above it got
          ``None`` ("no filtering") and could read past the cap; the effective
          rank is now ``min(user_rank, max_rank)``;
        - an unrecognised level ranked -1 and produced ``... IN ()`` (invalid
          SQL) or disabled the policy outright; both now deny with FALSE.
        """
        sensitivity_column = _ident(self.conditions.get("sensitivity_column", "security_level"))
        max_level = self.conditions.get("max_allowed_level", "public")
        user_clearance = context.get("user_clearance") or "public"

        max_rank = SensitivityLevel.clearance_rank(max_level)
        user_rank = SensitivityLevel.clearance_rank(user_clearance)

        if max_rank < 0 or user_rank < 0:
            logger.error(
                "Sensitivity policy %s has unrecognised level (max=%r, user=%r) — denying",
                self.policy_id, max_level, user_clearance,
            )
            return "FALSE"

        # The ceiling caps the *data*, the clearance caps the *user* — the
        # rows allowed are those at or below the lower of the two.
        effective_rank = min(user_rank, max_rank)
        allowed_levels = [
            level.value
            for level in SensitivityLevel
            if SensitivityLevel.clearance_rank(level.value) <= effective_rank
        ]
        if not allowed_levels:
            return "FALSE"
        in_clause = ", ".join(f"'{l}'" for l in allowed_levels)
        return f"{sensitivity_column} IN ({in_clause})"

    @staticmethod
    def _escape(value: str) -> str:
        """Basic SQL string escaping."""
        return value.replace("'", "''")


@dataclass
class RLSContext:
    """Evaluation context for RLS policies."""
    user_id: str | None = None
    user_region: str | None = None
    user_clearance: str | None = None
    user_roles: list[str] = field(default_factory=list)
    request_time: datetime | None = None
    session_id: str | None = None


class RLSEngine:
    """Row-Level Security policy engine.

    Manages policies, evaluates them against request context, and generates
    SQL WHERE clauses for injection into database queries.
    """

    def __init__(self):
        self._policies: dict[str, RLSPolicy] = {}

    def add_policy(self, policy: RLSPolicy) -> None:
        """Register a new RLS policy."""
        self._policies[policy.policy_id] = policy
        logger.info(f"Registered RLS policy: {policy.policy_id} ({policy.policy_type.value})")

    def remove_policy(self, policy_id: str) -> bool:
        """Remove an RLS policy (atomic pop — A133 #32: the old check-then-del
        KeyError'd under a second mutator; CPython dict pop is one step)."""
        return self._policies.pop(policy_id, None) is not None

    def get_policy(self, policy_id: str) -> RLSPolicy | None:
        """Get a policy by ID."""
        return self._policies.get(policy_id)

    def list_policies(self, policy_type: PolicyType | None = None) -> list[RLSPolicy]:
        """List all policies, optionally filtered by type."""
        policies = list(self._policies.values())
        if policy_type:
            policies = [p for p in policies if p.policy_type == policy_type]
        return sorted(policies, key=lambda p: p.priority, reverse=True)

    def evaluate_sql(
        self,
        context: RLSContext,
        table_name: str | None = None,
        policy_ids: list[str] | None = None,
    ) -> str | None:
        """Evaluate all applicable policies and return combined SQL WHERE clause.

        Args:
            context: Request context with user info
            table_name: Optional table name for table-specific policies
            policy_ids: Optional list of specific policy IDs to evaluate

        Returns:
            SQL WHERE clause string, or None if no filtering needed.
            Multiple policies are AND-combined.
        """
        eval_context = {
            "user_id": context.user_id,
            "user_region": context.user_region,
            "user_clearance": context.user_clearance,
            "user_roles": context.user_roles,
            "request_time": context.request_time,
            # A133 #33: session_id existed on the context but was never
            # placed here, so no condition could ever key on it.
            "session_id": context.session_id,
        }

        # Select policies to evaluate
        policies = list(self._policies.values())
        if policy_ids:
            policies = [p for p in policies if p.policy_id in policy_ids]
        # A133 #30: table_name was advertised but never read — every
        # registered policy used to AND onto every table. A policy scoped to
        # another table no longer participates; a table-scoped policy still
        # coexists with global (table=None) ones.
        if table_name:
            policies = [p for p in policies if p.table is None or p.table == table_name]

        # Filter to enabled, sorted by priority
        policies = [p for p in policies if p.enabled]
        policies.sort(key=lambda p: p.priority, reverse=True)

        conditions = []
        for policy in policies:
            condition = policy.to_sql_condition(eval_context)
            if condition:
                if condition == "FALSE":
                    return "FALSE"  # Deny all — short circuit
                conditions.append(f"({condition})")

        if not conditions:
            return None

        return " AND ".join(conditions)

    def inject_rls_where(self, sql: str, rls_where: str) -> str:
        """Inject RLS WHERE clause into a SQL query.

        Delegates to the module's single depth-0 injector (see
        ``inject_condition_outermost`` for the placement semantics —
        A133 #21/#22/#27/#28). Text surgery is best-effort: the module
        header's prerequisite 1 (real SQL parser) governs wiring.
        """
        if not rls_where:
            return sql
        return inject_condition_outermost(sql, rls_where)

    def validate_policy(self, policy: RLSPolicy) -> list[str]:
        """Validate a policy configuration. Returns list of errors.

        Now actually gates what the generated SQL interpolates (A133 #36):
        column identifiers and the numeric window parameters, so a hostile
        or typo'd policy is rejected at registration instead of at query
        time.
        """
        errors: list[str] = []

        if not policy.policy_id:
            errors.append("policy_id is required")
        if not policy.name:
            errors.append("name is required")
        if policy.table is not None:
            try:
                _ident(policy.table, what="table")
            except ValueError as e:
                errors.append(str(e))

        def _check_column(key: str) -> None:
            if key in policy.conditions:
                try:
                    _ident(policy.conditions[key])
                except ValueError as e:
                    errors.append(str(e))

        def _check_hours(*keys: str) -> None:
            for key in keys:
                val = policy.conditions.get(key)
                if val is None:
                    continue
                if not isinstance(val, (int, float)) or isinstance(val, bool) or val < 0:
                    errors.append(f"{key} must be a non-negative number, got {val!r}")

        if policy.policy_type == PolicyType.REGION:
            if "allowed_regions" not in policy.conditions:
                errors.append("Region policy requires 'allowed_regions' condition")
            else:
                allowed = policy.conditions["allowed_regions"]
                if (
                    not isinstance(allowed, (list, tuple))
                    or not allowed
                    or not all(isinstance(r, str) and r for r in allowed)
                ):
                    errors.append("allowed_regions must be a non-empty list of region codes")
            _check_column("region_column")
        elif policy.policy_type == PolicyType.TIME:
            if not any(
                k in policy.conditions
                for k in ("min_age_hours", "max_age_hours", "embargo_hours")
            ):
                errors.append("Time policy requires at least one of: min_age_hours, max_age_hours, embargo_hours")
            _check_column("time_column")
            _check_hours("min_age_hours", "max_age_hours", "embargo_hours")
        elif policy.policy_type == PolicyType.SENSITIVITY:
            if "max_allowed_level" not in policy.conditions:
                errors.append("Sensitivity policy requires 'max_allowed_level' condition")
            else:
                max_level = policy.conditions["max_allowed_level"]
                # A133 #36: `__members__.values()` only worked while the enum
                # subclassed str — compare explicit values instead.
                if max_level not in {m.value for m in SensitivityLevel}:
                    errors.append(f"Invalid sensitivity level: {max_level}")
            _check_column("sensitivity_column")

        return errors

    # ── Row-level evaluation API (simplified) ──────────────────

    def evaluate(
        self,
        policy: dict[str, Any],
        row: dict[str, Any],
        user_region: str | None = None,
        user_clearance: int | None = None,
    ) -> bool:
        """Evaluate a single policy against a row (row-level access control).

        Args:
            policy: Policy dict with 'type' and type-specific fields
            row: Data row to evaluate
            user_region: User's region for region policies
            user_clearance: User's clearance level for sensitivity policies

        Returns:
            True if access allowed, False if denied.
        """
        policy_type = policy.get("type", "")

        if policy_type == "region":
            allowed_regions = policy.get("allowed_regions", [])
            if user_region is None or not allowed_regions:
                return False
            # A133 #24: this used to allow any row whose region was merely
            # *somewhere in* allowed_regions — a cn-east user reading cn-east
            # AND cn-east-only lists aside, a user in an allowed region could
            # read every *other* allowed region's rows. The SQL path was
            # always row.region == user.region; the two APIs now agree.
            row_region = row.get("region")
            if not row_region:
                return False
            return user_region in allowed_regions and row_region == user_region

        elif policy_type == "sensitivity":
            if user_clearance is None:
                return False
            # A133 #25: the old predicate compared the *user* against the
            # ceiling (``user_clearance >= max_level``), so a low-clearance
            # user was denied even public-class rows while the ceiling never
            # limited anything. Data rows are gated by min(clearance, ceiling).
            max_level = policy.get("max_level", 0)
            try:
                effective = min(int(user_clearance), int(max_level))
                row_sensitivity = int(row.get("sensitivity", 0))
            except (TypeError, ValueError):
                return False
            return row_sensitivity <= effective

        elif policy_type == "time":
            return self.evaluate_time_policy(policy)

        # A133 #26: unknown / missing policy type used to ALLOW (composite
        # AND-logic turned a typo into a no-op). An unparseable policy denies.
        logger.error("Unknown RLS dict-policy type %r — denying", policy.get("type"))
        return False

    def evaluate_time_policy(
        self,
        policy: dict[str, Any],
        current_hour: int | None = None,
    ) -> bool:
        """Evaluate a time-based policy.

        Args:
            policy: Policy dict with 'start_hour' and 'end_hour'
            current_hour: Current hour (0-23). If None, uses actual current
                hour in UTC (A133 #31 — used to be a shadowed import of a
                *naive server-local* hour while the SQL path ran UTC, so an
                hour-window policy flipped at the UTC offset).

        Returns:
            True if within allowed time window, False otherwise. Windows
            wrap across midnight (start > end means "from start through
            end of day, plus day start to end").
        """
        if current_hour is None:
            current_hour = datetime.now(timezone.utc).hour

        start = policy.get("start_hour", 0)
        end = policy.get("end_hour", 24)

        # Inclusive start, exclusive end; A133 #31: the wrap-around window
        # (22..6) used to be unsatisfiable and denied at every hour.
        if start <= end:
            return start <= current_hour < end
        return not (end <= current_hour < start)

    def evaluate_composite(
        self,
        policies: list[dict[str, Any]],
        row: dict[str, Any],
        user_region: str | None = None,
        user_clearance: int | None = None,
    ) -> bool:
        """Evaluate multiple policies against a row (AND logic).

        Returns True only if ALL policies allow access.
        """
        for policy in policies:
            if not self.evaluate(policy, row, user_region, user_clearance):
                return False
        return True

    def inject_filter(
        self,
        sql: str,
        policy: dict[str, Any],
        user_region: str | None = None,
        user_clearance: int | None = None,
    ) -> str:
        """Inject a WHERE clause into SQL based on policy.

        Returns rewritten SQL with appropriate WHERE clause.

        Hardened inputs (A133 #23): ``column`` passes the identifier
        whitelist and ``user_clearance`` is coerced through ``int`` — both
        arrive from callers without runtime annotation enforcement, and the
        old code interpolated either straight into the statement
        (\"3 OR 1=1\" as a clearance used to read the whole table).
        An unparseable policy type raises instead of silently returning the
        unfiltered SQL (deny-by-default; the row-level sibling ``evaluate``
        returns False for the same shape).
        """
        policy_type = policy.get("type", "")

        if policy_type == "region":
            column = _ident(policy.get("column", "region"))
            if user_region:
                condition = f"{column} = '{self._escape_sql(user_region)}'"
                return inject_condition_outermost(sql, condition)
            return sql

        elif policy_type == "sensitivity":
            column = _ident(policy.get("column", "sensitivity_level"))
            if user_clearance is not None:
                try:
                    clearance = int(user_clearance)
                except (TypeError, ValueError):
                    raise ValueError(
                        f"Invalid user_clearance for sensitivity injection: {user_clearance!r}"
                    ) from None
                condition = f"{column} <= {clearance}"
                return inject_condition_outermost(sql, condition)
            return sql

        raise ValueError(f"Unknown RLS policy type for SQL injection: {policy.get('type')!r}")

    @staticmethod
    def _inject_where(sql: str, condition: str) -> str:
        """Inject a WHERE condition into a SQL query.

        Kept as a thin shim for external callers; it now shares the ONE
        placement rule with ``inject_rls_where`` (A133 #28 — the two helpers
        used different keyword scans AND forgot parentheses, so an existing
        ``a = 1 OR b = 2`` clause let every b=2 row bypass the filter).
        """
        return inject_condition_outermost(sql, condition)

    @staticmethod
    def _escape_sql(value: str) -> str:
        """Basic SQL string escaping."""
        return value.replace("'", "''")


# Singleton
rls_engine = RLSEngine()
