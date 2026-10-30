"""OCR A192e 报告腿：`inspection_to_report()` 必须把第三态 `dp_budget_state` 带出。

A192e 把「预算后端读不到」从谎报 `exhausted(remaining=0.0000)` 改成了 `InspectionResult`
上的第三态（`dp_budget_state="unverified"` + `dp_budget_remaining=None`），但报告面
`inspection_to_report()` 只输出 `dp_applied` —— 而它是 11 个调用点共用的唯一序列化出口。
于是对外仍然分不清三种完全不同的情形：DP 真的计账跑了 / 核实过确实没预算 / 预算根本读不到。
`dp_applied` 一个布尔表达不了「未知」，把它压成 False 就是「没有预算」的读数（A192e 的病）。

判据（本文件钉住）：
1) 未核实读数的报告里 `dp_budget_state == "unverified"` 且 `dp_applied is False`，
   且报告里不出现 exhausted 字样；
2) 反向护栏：`sufficient` / `exhausted` / `unallocated` / `not_applied` 各自的读数
   原样进报告（报告 = 结果的镜像，不是又一个压平的布尔）；
3) owner-output-chain 的不变量：未核实 ⇒ 不计账、不退到「没预算」的免费 DP 旁路
   （`dp_applied` 语义未改，谁改这里就红）。
"""
import contextlib
import uuid

from app.services.dp_budget import BudgetBackendUnavailable, dp_budget_ledger
from app.services.output_inspection import OutputInspector, SandboxMode, dp_engine
from app.services.output_security import inspection_to_report


class _BrokenBudgetBackend:
    """「连得上、但每次读写都失败」的 ε 预算后端：Redis 挂掉/权限被撤的等价物。"""

    def ping(self):
        return True

    @staticmethod
    def _boom(op: str):
        raise BudgetBackendUnavailable(f"injected backend failure on {op}")

    def get(self, key):
        self._boom("GET")

    def set(self, key, value):
        self._boom("SET")

    def eval(self, script, numkeys, *args, **kwargs):
        self._boom("EVAL")


@contextlib.contextmanager
def broken_backend():
    """只在「读不到预算」那一次审查期间挂上坏后端，其余读数须用可核实后端。"""
    dp_budget_ledger.attach_redis(_BrokenBudgetBackend())
    try:
        yield
    finally:
        dp_budget_ledger.attach_redis(None)


def _sid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _rows():
    return [{"name": "a", "amount": 100} for _ in range(5)]


def _inspect(sid: str, **kwargs):
    return OutputInspector().inspect(
        "统计结果：共 3 条记录", "u-1", sid,
        sandbox_mode=SandboxMode.STRUCTURED_QUERY.value, output_rows=_rows(),
        **kwargs,
    )


def test_report_of_unverified_reading_says_unverified():
    """报告腿主判据：读不到预算时报告里是 unverified，且 dp_applied 仍为 False。"""
    sid = _sid("a192e-rep")
    with broken_backend():
        result = _inspect(sid, dp_epsilon=1.0)
    report = inspection_to_report(result)

    assert "dp_budget_state" in report, (
        "报告面缺第三态字段，11 个调用点只能从 findings 文本里猜剩余预算是否可知"
    )
    assert report["dp_budget_state"] == "unverified", report["dp_budget_state"]
    assert report["dp_applied"] is False, (
        "未核实没有计账依据：dp_applied 的语义不许被这次改动改掉"
    )
    assert report["stage_results"]["differential_privacy"] is False
    assert report["passed"] is False, "未核实仍未通过（passed = 阶段相与）"
    # blocked 不参与本判据：BLOCKING_SEVERITIES = {critical}，DP 阶段是 high ⇒
    # 拦法与 A192e 之前一致（靠 passed/阶段 False），本条只钉「第三态能不能被读出来」。
    text = str(report).lower()
    assert "unverified" in text and "exhausted" not in text, (
        f"报告里「无法核实」不得说成「已耗尽」: {text[:400]}"
    )


def test_report_mirrors_every_budget_state():
    """反向护栏：五种读数各自原样进报告，不许被压成一个布尔。"""
    sid_unverified = _sid("a192e-mirror-unv")
    sid_sufficient = _sid("a192e-mirror-ok")
    sid_exhausted = _sid("a192e-mirror-ex")
    sid_unallocated = _sid("a192e-mirror-none")
    sid_not_applied = _sid("a192e-mirror-na")

    dp_engine.init_budget(sid_sufficient, 5.0)
    dp_engine.init_budget(sid_exhausted, 1.0)

    with broken_backend():
        unverified = _inspect(sid_unverified, dp_epsilon=1.0)

    cases = [
        (unverified, "unverified", False),
        (_inspect(sid_sufficient, dp_epsilon=1.0), "sufficient", True),
        (_inspect(sid_exhausted, dp_epsilon=2.0), "exhausted", False),
        (_inspect(sid_unallocated, dp_epsilon=1.0), "unallocated", True),
        (_inspect(sid_not_applied), "not_applied", False),
    ]
    for result, state, dp_applied in cases:
        report = inspection_to_report(result)
        assert report["dp_budget_state"] == state, (state, report["dp_budget_state"])
        assert report["dp_budget_state"] == result.dp_budget_state, (
            "报告是 InspectionResult 的镜像，不是另算一份口径"
        )
        assert report["dp_applied"] is dp_applied, (state, report["dp_applied"])
