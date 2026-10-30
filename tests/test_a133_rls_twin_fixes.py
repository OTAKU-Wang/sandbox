"""A133 RLS 双子系统修复针（wave 2，2026-10-30）。

潜伏半区（rls_engine 仅经零接线的 db_access_proxy 有测试用户；pg_rls_manager
全仓零导入方）——本文件把修复轮的每条语义变化钉死在代码级，让「接线即高」
的三条必修项（FORCE 角色锁定 / 策略下发与表名拼接 / 策略缺失默认开放）有了
回归防线。PG DDL 语义本身（FORCE/42710/TO CURRENT_ROLE/GUC）仍是代码级证据，
远程 PG16 复验前不得宣称已验证——两个模块头注释同口径。
"""
import pytest

from app.services.rls_engine import (
    RLSEngine,
    RLSPolicy,
    RLSContext,
    PolicyType,
    SensitivityLevel,
    inject_condition_outermost,
)
from app.services.pg_rls_manager import PGRLSManager


# ══════════════════════════════ rls_engine：fail-closed ══════════════════════════════

def _region_policy(**conditions):
    return RLSPolicy(
        policy_id="r1", name="region",
        policy_type=PolicyType.REGION,
        conditions={"allowed_regions": ["CN", "HK"], "region_column": "data_region", **conditions},
    )


class TestFailClosed:
    def test_region_policy_without_allowed_list_denies(self):
        """#17：enabled 区域策略没有白名单 ⇒ FALSE（旧行为 None=过滤消失）。"""
        p = RLSPolicy(policy_id="r", name="r", policy_type=PolicyType.REGION, conditions={})
        assert p.to_sql_condition({"user_region": "CN"}) == "FALSE"

    def test_region_policy_without_user_context_denies(self):
        """#17：enabled 区域策略评估时缺 user_region ⇒ FALSE（旧行为 None）。"""
        assert _region_policy().to_sql_condition({}) == "FALSE"

    def test_unknown_sensitivity_level_denies(self):
        """#18/#29：未知等级不再产出 `IN ()` 废 SQL 或放行，而是 FALSE。"""
        bad_max = RLSPolicy(policy_id="s", name="s", policy_type=PolicyType.SENSITIVITY,
                            conditions={"max_allowed_level": "confidential-ish"})
        assert bad_max.to_sql_condition({"user_clearance": "secret"}) == "FALSE"
        bad_user = RLSPolicy(policy_id="s2", name="s2", policy_type=PolicyType.SENSITIVITY,
                             conditions={"max_allowed_level": "secret"})
        assert bad_user.to_sql_condition({"user_clearance": "unobtanium"}) == "FALSE"

    def test_none_clearance_filters_at_public_not_broken_sql(self):
        """#29：clearance=None 旧代码产出 `security_level IN ()`（语法废）；
        现按默认 public 过滤——可执行且 fail-closed。"""
        p = RLSPolicy(policy_id="s", name="s", policy_type=PolicyType.SENSITIVITY,
                      conditions={"max_allowed_level": "secret"})
        cond = p.to_sql_condition({})
        assert cond == "security_level IN ('public')"

    def test_ceiling_applies_above_clearance(self):
        """#18：用户 clearance 高于上限时上限必须封顶（旧行为 None=不设防）。"""
        p = RLSPolicy(policy_id="s", name="s", policy_type=PolicyType.SENSITIVITY,
                      conditions={"max_allowed_level": "confidential"})
        cond = p.to_sql_condition({"user_clearance": "secret"})
        assert cond is not None and "'secret'" not in cond
        assert "'confidential'" in cond and "'public'" in cond

    def test_unknown_policy_type_denies_sql(self):
        """#26：无法解析的 policy_type ⇒ to_sql_condition 返回 FALSE，
        而不是 None（None 会被 AND 组合器当作"本策略无谓词"直接放行）。"""
        p = RLSPolicy(policy_id="x", name="x", policy_type="bogus")  # dataclass 不设运行时闸
        assert p.to_sql_condition({}) == "FALSE"

    def test_unknown_dict_policy_type_denies_row(self):
        assert RLSEngine().evaluate({"type": "bogus"}, {}, user_region="CN") is False

    def test_remove_policy_idempotent(self):
        """#32：原子 pop；二次删除如实 False，不 KeyError。"""
        e = RLSEngine()
        e.add_policy(_region_policy())
        assert e.remove_policy("r1") is True
        assert e.remove_policy("r1") is False


class TestIdentifierGates:
    """#19/#23/#36：策略携带的标识符是 SQL 拼接位，必须过白名单。"""

    def test_malicious_region_column_raises_not_interpolates(self):
        p = _region_policy(region_column="data_region = 'CN' OR 1=1 --")
        with pytest.raises(ValueError):
            p.to_sql_condition({"user_region": "CN"})

    def test_malicious_column_rejected_at_validation(self):
        e = RLSEngine()
        errors = e.validate_policy(_region_policy(region_column="c; DROP TABLE t --"))
        assert any("region_column" in err or "column" in err for err in errors)

    def test_numeric_time_conditions_validated(self):
        e = RLSEngine()
        p = RLSPolicy(policy_id="t", name="t", policy_type=PolicyType.TIME,
                      conditions={"embargo_hours": "24; "})
        assert any("embargo_hours" in err for err in e.validate_policy(p))

    def test_sensitivity_bad_level_rejected_by_value_set(self):
        """#36：枚举成员判断换成显式 value 集合（str-Enum 解绑后仍有效）。"""
        e = RLSEngine()
        p = RLSPolicy(policy_id="s", name="s", policy_type=PolicyType.SENSITIVITY,
                      conditions={"max_allowed_level": "bogus"})
        assert any("bogus" in err for err in e.validate_policy(p))

    def test_qualified_column_allowed(self):
        p = _region_policy(region_column="t.data_region")
        assert p.to_sql_condition({"user_region": "CN"}).startswith("t.data_region = ")

    def test_inject_filter_clearance_injection_raises(self):
        """#23：user_clearance 无运行时类型闸的历史受害者，现在 int 强制。"""
        e = RLSEngine()
        with pytest.raises(ValueError):
            e.inject_filter("SELECT s FROM t", {"type": "sensitivity"}, user_clearance="3 OR 1=1")
        assert "sensitivity_level <= 3" in e.inject_filter(
            "SELECT s FROM t", {"type": "sensitivity"}, user_clearance="3")

    def test_inject_filter_unknown_type_raises(self):
        """fail-closed：未知类型不得原样返回未过滤 SQL。"""
        e = RLSEngine()
        with pytest.raises(ValueError):
            e.inject_filter("SELECT 1", {"type": "who-knows"})


class TestPlacementSemantics:
    """#21/#22/#27/#28：注入定位的唯一规则。"""

    def test_where_lands_in_outer_query_not_subquery(self):
        """#21：旧 rfind 拿最内层 WHERE——过滤被塞进子查询，外层裸奔。"""
        sql = "SELECT * FROM t WHERE EXISTS (SELECT 1 FROM u WHERE u.id = t.id)"
        out = inject_condition_outermost(sql, "region = 'cn-north'")
        assert out.index("(region = 'cn-north')") > out.index("u.id = t.id)")
        assert "AND (region = 'cn-north')" in out

    def test_existing_or_clause_is_wrapped(self):
        """#28：AND 优先级高于 OR——既有子句不加括号，b=2 的行整类绕过过滤。"""
        out = inject_condition_outermost(
            "SELECT * FROM t WHERE a = 1 OR b = 2", "sec <= 1")
        assert out == "SELECT * FROM t WHERE (a = 1 OR b = 2) AND (sec <= 1)"

    def test_multiline_where_no_second_where(self):
        """#22：WHERE 前有换行时旧 find(" WHERE ") 漏检 ⇒ 注入出第二个 WHERE，
        整条 SQL 语法废（拒绝执行=策略消失）。"""
        out = inject_condition_outermost("SELECT * FROM t\nWHERE a = 1", "x = 'y'")
        assert out.upper().count(" WHERE ") == 1
        assert out == "SELECT * FROM t WHERE (a = 1) AND (x = 'y')"

    def test_where_inside_string_literal_not_a_clause(self):
        out = inject_condition_outermost(
            "SELECT * FROM t WHERE description = 'a WHERE b'", "x = 'y'")
        assert out == "SELECT * FROM t WHERE (description = 'a WHERE b') AND (x = 'y')"

    def test_trailing_semicolon_preserved(self):
        out = inject_condition_outermost("SELECT * FROM t;", "x = 1")
        assert out.endswith("WHERE (x = 1);")

    def test_having_gets_a_where_before_it(self):
        """#27：旧分支组没有 HAVING——`SELECT c FROM t HAVING ...` 的产出是
        `... HAVING (cond) AND count(*) > 2`，策略谓词落进 HAVING，语义报废。"""
        out = inject_condition_outermost(
            "SELECT c FROM t HAVING count(*) > 2", "sec <= 1")
        assert out.index("WHERE (sec <= 1)") < out.index(" HAVING ")

    def test_earliest_clause_wins(self):
        out = inject_condition_outermost(
            "SELECT c FROM t ORDER BY c LIMIT 5", "sec <= 1")
        assert out == "SELECT c FROM t WHERE (sec <= 1) ORDER BY c LIMIT 5"

    def test_rls_and_filter_injectors_agree(self):
        """#28：两个注入器历史各带一套错误规则，现在共享同一原语。"""
        e = RLSEngine()
        sql, cond = "SELECT * FROM t WHERE a = 1 OR b = 2", "x = 1"
        assert e.inject_rls_where(sql, cond) == e._inject_where(sql, cond)


class TestRowLevelApi:
    def test_region_requires_row_equality(self):
        """#24：行在允许区域≠行属于用户区域。cn-east 用户此前可读走白名单
        内任何其他区域的行。"""
        e = RLSEngine()
        policy = {"type": "region", "allowed_regions": ["cn-east", "cn-north"]}
        assert e.evaluate(policy, {"region": "cn-north"}, user_region="cn-east") is False
        assert e.evaluate(policy, {"region": "cn-east"}, user_region="cn-east") is True

    def test_low_clearance_reads_public_rows(self):
        """#25：旧谓词 `user >= max_level` 让低 clearance 连 public 行都读不到，
        同时上限从不封顶数据行——两头都是错的。"""
        e = RLSEngine()
        policy = {"type": "sensitivity", "max_level": 2}
        assert e.evaluate(policy, {"sensitivity": 0}, user_clearance=1) is True
        assert e.evaluate(policy, {"sensitivity": 2}, user_clearance=1) is False

    def test_time_window_wraps_midnight(self):
        """#31：22→6 窗口旧代码在每个钟点都返回 False。"""
        e = RLSEngine()
        policy = {"type": "time", "start_hour": 22, "end_hour": 6}
        assert e.evaluate_time_policy(policy, current_hour=23) is True
        assert e.evaluate_time_policy(policy, current_hour=3) is True
        assert e.evaluate_time_policy(policy, current_hour=12) is False
        # 普通窗口保持 含左不含右
        assert e.evaluate_time_policy({"start_hour": 8, "end_hour": 18}, current_hour=8) is True
        assert e.evaluate_time_policy({"start_hour": 8, "end_hour": 18}, current_hour=18) is False


class TestRequestTimeAndTableScoping:
    def test_request_time_is_honoured(self):
        """#33：context 携带 request_time 但求值从不读它——每次求值不可复现。"""
        from datetime import datetime, timezone, timedelta
        anchor = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
        e = RLSEngine()
        e.add_policy(RLSPolicy(policy_id="t", name="t", policy_type=PolicyType.TIME,
                               conditions={"embargo_hours": 24}))
        cond = e.evaluate_sql(RLSContext(request_time=anchor))
        # evaluate_sql 用括号 AND 组合：(created_at <= '<iso>')
        assert anchor - timedelta(hours=24) == datetime.fromisoformat(
            cond.split("<= '")[1].split("'")[0]
        )

    def test_table_name_parameter_selects_policies(self):
        """#30：table_name 旧参数从未被读——所有策略 AND 到所有表。"""
        e = RLSEngine()
        e.add_policy(RLSPolicy(policy_id="pa", name="a", policy_type=PolicyType.REGION,
                               conditions={"allowed_regions": ["CN"], "region_column": "a_region"},
                               table="t_a"))
        e.add_policy(RLSPolicy(policy_id="pb", name="b", policy_type=PolicyType.REGION,
                               conditions={"allowed_regions": ["CN"], "region_column": "b_region"},
                               table="t_b"))
        where = e.evaluate_sql(RLSContext(user_region="CN"), table_name="t_a")
        assert "a_region" in where and "b_region" not in where


# ══════════════════════════════ pg_rls_manager ══════════════════════════════

class _FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _RecorderSession:
    """AsyncSession stand-in: records every emitted statement (+params)."""

    def __init__(self, support=(True, False), role_exists=False, fail_on=None, fail_cls=None):
        self.statements = []
        self.bound = []
        self.flushed = 0
        self._support = support
        self._role_exists = role_exists
        self._fail_on = fail_on
        self._fail_cls = fail_cls or RuntimeError

    async def execute(self, clause, params=None):
        sql = str(clause)
        self.statements.append(sql)
        self.bound.append(params)
        if self._fail_on and self._fail_on in sql:
            raise self._fail_cls(f"simulated failure at: {self._fail_on}")
        if "server_version_num" in sql:
            return _FakeResult(self._support)
        if "pg_roles" in sql:
            return _FakeResult((1,) if self._role_exists else None)
        return _FakeResult(None)

    async def flush(self):
        self.flushed += 1

    def joined(self):
        return "\n".join(self.statements)


SID = "sess-2026-1030-abcdef"


class TestRoleNaming:
    """#4：旧「清洗+截 8 字符」= 前缀相同的会话共享角色；退化 id 全员共享 sandbox_default。"""

    def test_prefix_sharing_ids_get_different_roles(self):
        m = PGRLSManager()
        a = m._build_role_name("sess-abcdef123456")
        b = m._build_role_name("sess-abcdef9999")
        assert a != b
        assert a.startswith("sandbox_") and len(a) <= 63

    @pytest.mark.parametrize("bad", ["", "ab", "a" * 65, "bad;name", "no spaces", "san'quote"])
    def test_degenerate_ids_rejected_not_defaulted(self, bad):
        with pytest.raises(ValueError):
            PGRLSManager()._build_role_name(bad)


class TestCreateRoleAndDsn:
    @pytest.mark.asyncio
    async def test_create_sets_password_and_idempotent_on_duplicate(self):
        """#3/#16：旧 CREATE ROLE 不设口令（session DSN 全灭），且 42710 直接
        RuntimeError；现在带口令创建、撞名收编。"""
        db = _RecorderSession()
        m = PGRLSManager()
        role = await m.create_session_role(SID, db)
        stmt = db.joined()
        assert "CREATE ROLE" in stmt and "PASSWORD '" in stmt

        dup = _RecorderSession(fail_on="CREATE ROLE",
                               fail_cls=lambda msg: RuntimeError(
                                   f'duplicate object: role "{msg}" already exists'))
        m2 = PGRLSManager()
        role2 = await m2.create_session_role(SID, dup)  # 不抛
        assert role2.startswith("sandbox_")

    def test_dsn_rebuild_carries_credential_and_no_guc(self):
        """#2/#3b/#3c：DSN 不再携带 options GUC（注入面），make_url 重建，
        无用户名基 DSN 如实拒绝而不是把 app 角色 DSN 当会话 DSN 发出去。"""
        import asyncio
        m = PGRLSManager()
        asyncio.run(m.create_session_role(SID, _RecorderSession()))
        dsn = m.get_connection_string(SID, "postgresql+asyncpg://app:s3cr3t@db:5432/cds")
        assert "options=" not in dsn
        assert "app:" not in dsn.split("@")[0].replace(m._build_role_name(SID), "")
        assert m._build_role_name(SID) in dsn
        assert dsn.startswith("postgresql+asyncpg://")
        assert "s3cr3t" not in dsn  # 旧正则把 :pass 原样带着走；现在是生成的角色口令

        with pytest.raises(ValueError):
            m.get_connection_string(SID, "postgresql+asyncpg://db:5432/cds")
        with pytest.raises(ValueError):
            m.get_connection_string("sess-never-created-000000", "postgresql+asyncpg://app:p@db/cds")


class TestPolicyDdl:
    @pytest.mark.asyncio
    async def test_admin_policy_and_tolerant_guc(self):
        """#1/#10：FORCE 不再单策略锁死 app 角色；GUC 缺失不炸查询而是过滤为
        空（fail-closed）；重入先 DROP IF EXISTS。"""
        db = _RecorderSession()
        m = PGRLSManager()
        assert await m.setup_rls_policy("tasks", SID, db) is True
        stmt = db.joined()
        assert "DROP POLICY IF EXISTS" in stmt
        assert "TO CURRENT_ROLE" in stmt and "USING (true)" in stmt
        assert "coalesce(current_setting('cds.session_id', true)" in stmt
        assert "current_setting('cds.session_id')::text" not in stmt

    @pytest.mark.asyncio
    async def test_setup_failure_surfaces_in_status(self):
        """#10：RLSStatus.error 字段旧代码从不写——全失败也报"已保护"。"""
        db = _RecorderSession(fail_on="CREATE POLICY")
        m = PGRLSManager()
        assert await m.setup_rls_policy("tasks", SID, db) is False
        status = m.get_status(SID)
        assert status.error is not None and status.fallback_mode is True

    @pytest.mark.asyncio
    async def test_capability_negative_is_not_pinned_forever(self):
        """#9：一次瞬时故障永久缓存 False；row_security 是用户开关不是能力位。"""
        m = PGRLSManager()
        probe = _RecorderSession(support=(True, False))
        assert await m._check_rls_support(_RecorderSession(support=(False, False))) is False
        assert m._rls_available is None  # 负结果不缓存，稍后可复探
        assert await m._check_rls_support(_RecorderSession(support=(True, True))) is False  # BYPASSRLS
        assert await m._check_rls_support(probe) is True
        assert m._rls_available is True
        assert "server_version_num" in probe.joined()  # 能力问题问的是版本+BYPASSRLS，不是 row_security
        assert "row_security" not in probe.joined()


class TestGrantsAndCleanup:
    @pytest.mark.asyncio
    async def test_grant_boundary_is_sandbox_namespace(self):
        """#13：旧方法可以对任意 DB 主体 GRANT。"""
        db = _RecorderSession()
        m = PGRLSManager()
        with pytest.raises(ValueError):
            await m.grant_table_access("postgres", "tasks", ["SELECT"], db)
        # #14：不支持的权限如实拒绝，不再静默跳过后仍报 True
        with pytest.raises(ValueError):
            await m.grant_table_access(m._build_role_name(SID), "tasks", ["SELECT", "DELETE"], db)
        assert await m.grant_table_access(m._build_role_name(SID), "tasks", ["SELECT", "INSERT"], db) is True

    @pytest.mark.asyncio
    async def test_failed_drop_keeps_tracking_entry(self):
        """#6：DROP ROLE 失败仍删追踪记录 = 角色与策略无主孤儿。"""
        m = PGRLSManager()
        db = _RecorderSession()
        await m.create_session_role(SID, db)
        await m.setup_rls_policy("tasks", SID, db)
        bad = _RecorderSession(fail_on="DROP ROLE")
        assert await m.drop_session_role(SID, bad) is False
        assert SID in m._active_roles and m._active_roles[SID].active is False

        good = _RecorderSession()
        assert await m.drop_session_role(SID, good) is True
        assert SID not in m._active_roles
        assert 'REVOKE ALL PRIVILEGES ON TABLE "tasks"' in good.joined()  # #7 逐表吊销

    @pytest.mark.asyncio
    async def test_tracking_survives_call_order(self):
        """#11：setup 先于 create（都合法）不得丢已记录的表。"""
        m = PGRLSManager()
        db = _RecorderSession()
        await m.setup_rls_policy("tasks", SID, db)
        assert m._active_roles[SID].tables_granted == ["tasks"]
        await m.create_session_role(SID, db)
        assert m._active_roles[SID].tables_granted == ["tasks"]

    @pytest.mark.asyncio
    async def test_set_context_is_parameter_bound(self):
        """#5：SET 不能参数化 ⇒ 手写引号；set_config(name,$1,true) 才是绑定形态。"""
        db = _RecorderSession()
        await PGRLSManager().set_session_context(SID, db)
        assert "set_config('cds.session_id', :sid, true)" in db.joined()
        assert db.bound[-1] == {"sid": SID}
        with pytest.raises(ValueError):
            await PGRLSManager().set_session_context("evil'); SELECT 1; --", db)
