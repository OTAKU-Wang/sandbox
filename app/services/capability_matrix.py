"""PZ-13 (SS-11): sandbox-level capability matrix — the SINGLE authority source.

Data model (spec ss-11-cds-productization.md §4.2):

    {level x capability} -> {state: real|simulated|unsupported|planned,
                             note, evidence?}

plus level metadata (label / positioning / deprecation). Baseline below is
the 2026-10-12 code-verified matrix from spec §2.1; the L2' (``l2c``) column
was written back 2026-10-13 after SS-13 M1+M2 landed (D-e2e-10): the level
enum, node registry and fail-closed create are code-verified, per-VM TEE
(RT-06) and full envd streaming (CB-04) stay planned.

Consumers (four deployment points, keep in sync):
  - API  : ``GET /api/v1/meta/capability-matrix`` (app/api/meta.py)
  - API  : ``/health`` capability section (app/api/health.py)
  - FE/SDK/docs: rendered from this module's output — never hardcode level
    labels in a second place.
"""

from __future__ import annotations

# Capability enum — spec §2.1 matrix rows.
CAPABILITY_SYNC_EXEC = "sync_exec"
CAPABILITY_STREAM_WS = "stream_ws"
CAPABILITY_FILES_SNAPSHOT = "files_snapshot"
CAPABILITY_PAUSE_RESUME = "pause_resume"
CAPABILITY_TEE_PROOF_GATE = "tee_proof_gate"
CAPABILITY_NETWORK_POLICY_HOTSWAP = "network_policy_hotswap"

CAPABILITIES = (
    CAPABILITY_SYNC_EXEC,
    CAPABILITY_STREAM_WS,
    CAPABILITY_FILES_SNAPSHOT,
    CAPABILITY_PAUSE_RESUME,
    CAPABILITY_TEE_PROOF_GATE,
    CAPABILITY_NETWORK_POLICY_HOTSWAP,
)

# Capability state enum — real | simulated | unsupported | planned.
STATE_REAL = "real"
STATE_SIMULATED = "simulated"
STATE_UNSUPPORTED = "unsupported"
STATE_PLANNED = "planned"

STATES = (STATE_REAL, STATE_SIMULATED, STATE_UNSUPPORTED, STATE_PLANNED)

# The planned L2' level (SS-10 L2'; CB-01 adds SandboxLevel.L2C="l2c").
LEVEL_L2C = "l2c"

MATRIX_BASELINE_DATE = "2026-10-12"

# ---------------------------------------------------------------------------
# Level metadata — labels / positioning / deprecation (single source; the
# frontend's two hardcoded level arrays and the login-page static labels are
# superseded by this).
# ---------------------------------------------------------------------------
LEVEL_METADATA: dict[str, dict] = {
    "L0": {
        "label": "L0",
        "positioning": "开发级",
        "description": "cgroup + 输出限额进程隔离；开发调试用，不可写入合约。",
        "deprecated": False,
    },
    "L1": {
        "label": "L1",
        "positioning": "软件机密级",
        "description": "TEE 机密计算路径；无硬件时 software_confidential 诚实降级（FG-001）。",
        "deprecated": False,
    },
    "L2": {
        "label": "L2",
        "positioning": "软件增强(弃用观察)",
        "description": "firecracker 路径；SS-10 后弃用（CDS_LEGACY_L2_ENABLED 观察期），由 L2' 承接。",
        "deprecated": True,
    },
    "l2c": {
        "label": "L2'",
        "positioning": "生产默认(软件就绪，需节点)",
        "description": "Cube MicroVM 数据面 L2'（SS-13 单一路径，M1+M2 已实现 2026-10-13）：会话创建仅接受 l2c；无 ready 节点时 fail-closed 400 RUNTIME_UNAVAILABLE（无 bwrap/进程回退）；per-VM TEE（RT-06）规划中，无硬件时会话 trust_root=none 诚实披露。",
        "deprecated": False,
    },
    "L3": {
        "label": "L3",
        "positioning": "进程隔离",
        "description": "bwrap 命名空间隔离。",
        "deprecated": False,
    },
    "k8s": {
        "label": "k8s",
        "positioning": "Pod隔离(弃用观察)",
        "description": "Kubernetes/K3s Pod 隔离；SS-10 后弃用，存量用户观察期验证（FG-004）。",
        "deprecated": True,
    },
}

LEVEL_ORDER = ("L0", "L1", "L2", "l2c", "L3", "k8s")

# ---------------------------------------------------------------------------
# A55: 档位强度序 —— 承载供数的会话可用档位（唯一权威，由强到弱）
#
# 与 workflow_service.pick_test_sandbox_level 的 CR BLOCKER-1 阶梯同源：会话一旦
# 装载真实供数，运行面只能是 l2c（Cube MicroVM，SS-13 单路径）或 L3（bwrap），
# 下限 L3。L0 是本表自己写明「开发调试用，不可写入合约」的进程隔离；L1 无硬件时是
# software_confidential 诚实降级（FG-001）；L2/k8s 在 SS-10 后弃用且受 legacy 门控。
# 这条下限是**平台不变量**，不由合约数据决定：一纸只放行 L0 的合约不得把密态供数跑到
# L0 上去 —— 违规时按 SS-13 拒绝（LEGACY_LEVEL_RETIRED），绝不静默换成更强的档，
# 也绝不因为「合约同意了」就放行更弱的档。
# ---------------------------------------------------------------------------
SUPPLY_DATA_LEVELS: tuple[str, ...] = (LEVEL_L2C, "L3")


def supply_data_level_rank(level: str) -> int | None:
    """强度序里的名次（0 = 最强）；``None`` = 该档不得承载供数。

    只回答「这个档位能不能装真实供数、在可装档位里排第几」，不给 L0/L1/L2 之间
    编造强弱关系 —— 它们一律是「不可承载」，不是「更弱一档」。
    """
    return SUPPLY_DATA_LEVELS.index(level) if level in SUPPLY_DATA_LEVELS else None


def can_carry_supply_data(level: str) -> bool:
    """该档位是否允许承载解密后的供数（A55 判据；调用方不得另抄一份档位表）。"""
    return supply_data_level_rank(level) is not None

# ---------------------------------------------------------------------------
# Capability matrix — {level x capability} -> {state, note, evidence?}
# Baseline: 2026-10-12 code-verified (spec ss-11 §2.1).
# ---------------------------------------------------------------------------
_MATRIX: dict[str, dict[str, dict]] = {
    "L0": {
        CAPABILITY_SYNC_EXEC: {
            "state": STATE_REAL,
            "note": "同步执行真实可用（cgroup 进程沙箱）。",
            "evidence": "session_stream.py:162-165 / sandbox_sessions.py execute",
        },
        CAPABILITY_STREAM_WS: {
            "state": STATE_REAL,
            "note": "WS 流式执行支持（L0/k8s 白名单）。",
            "evidence": "session_stream.py:162-165",
        },
        CAPABILITY_FILES_SNAPSHOT: {
            "state": STATE_REAL,
            "note": "文件上传/下载 + 快照/回滚支持。",
            "evidence": "sandbox_sessions.py:1500-1655, :1834-1903",
        },
        CAPABILITY_PAUSE_RESUME: {
            "state": STATE_REAL,
            "note": "暂停/恢复/续期支持。",
            "evidence": "sandbox_sessions.py:1834-1903",
        },
        CAPABILITY_TEE_PROOF_GATE: {
            "state": STATE_UNSUPPORTED,
            "note": "不适用：L0 无 TEE 证明门禁（n/a）。",
        },
        CAPABILITY_NETWORK_POLICY_HOTSWAP: {
            "state": STATE_REAL,
            "note": "网络策略热更新支持（running-only）。",
            "evidence": "app/api/network_policy.py",
        },
    },
    "L1": {
        CAPABILITY_SYNC_EXEC: {
            "state": STATE_REAL,
            "note": "同步执行真实可用；L1 TEE 无硬件时走 software_confidential 模拟路径，is_simulation 显式披露（FG-001）。",
            "evidence": "kms_service.py:219-303 / tee_simulator (MR registry)",
        },
        CAPABILITY_STREAM_WS: {
            "state": STATE_UNSUPPORTED,
            "note": "流式执行不支持：STREAM_UNSUPPORTED（WS 白名单仅 L0/k8s）。",
            "evidence": "session_stream.py:162-165",
        },
        CAPABILITY_FILES_SNAPSHOT: {
            "state": STATE_UNSUPPORTED,
            "note": "文件/快照端点对 L1 返回 400。",
            "evidence": "sandbox_sessions.py:1500-1655",
        },
        CAPABILITY_PAUSE_RESUME: {
            "state": STATE_REAL,
            "note": "暂停/恢复/续期支持。",
            "evidence": "sandbox_sessions.py:1834-1903",
        },
        CAPABILITY_TEE_PROOF_GATE: {
            "state": STATE_REAL,
            "note": "创建时 TEE 证明门禁 required（KMS 证明白名单校验）。",
            "evidence": "sandbox_sessions.py create (attestation gate)",
        },
        CAPABILITY_NETWORK_POLICY_HOTSWAP: {
            "state": STATE_REAL,
            "note": "网络策略热更新支持（running-only）。",
            "evidence": "app/api/network_policy.py",
        },
    },
    "L2": {
        CAPABILITY_SYNC_EXEC: {
            "state": STATE_REAL,
            "note": "同步执行真实可用；firecracker 路径含模拟阶梯（simulation ladder，诚实披露），级别已弃用观察。",
            "evidence": "firecracker_runtime.py (sim mode, T12 boot disclosure)",
        },
        CAPABILITY_STREAM_WS: {
            "state": STATE_UNSUPPORTED,
            "note": "流式执行不支持：STREAM_UNSUPPORTED（WS 白名单仅 L0/k8s）。",
            "evidence": "session_stream.py:162-165",
        },
        CAPABILITY_FILES_SNAPSHOT: {
            "state": STATE_UNSUPPORTED,
            "note": "文件/快照端点对 L2 返回 400。",
            "evidence": "sandbox_sessions.py:1500-1655",
        },
        CAPABILITY_PAUSE_RESUME: {
            "state": STATE_REAL,
            "note": "暂停/恢复/续期支持。",
            "evidence": "sandbox_sessions.py:1834-1903",
        },
        CAPABILITY_TEE_PROOF_GATE: {
            "state": STATE_REAL,
            "note": "创建时 TEE 证明门禁 required（KMS 证明白名单校验）。",
            "evidence": "sandbox_sessions.py create (attestation gate)",
        },
        CAPABILITY_NETWORK_POLICY_HOTSWAP: {
            "state": STATE_REAL,
            "note": "网络策略热更新支持（running-only）。",
            "evidence": "app/api/network_policy.py",
        },
    },
    # L2' (SS-13 single-path production default) — written back 2026-10-13
    # (D-e2e-10) after SS-13 M1+M2 landed: code-verified cells only; RT-06
    # (per-VM TEE) and CB-04 (envd streaming) stay planned.
    LEVEL_L2C: {
        CAPABILITY_SYNC_EXEC: {
            "state": STATE_REAL,
            "note": "SS-13 已实现：Cube MicroVM 同步执行（gRPC Exec）；无 ready 节点时创建 fail-closed 400 RUNTIME_UNAVAILABLE（无 bwrap/进程回退）。",
            "evidence": "cube_runtime_adapter.py:480 / sandbox_sessions.py:396-428",
        },
        CAPABILITY_STREAM_WS: {
            "state": STATE_UNSUPPORTED,
            "note": "Day-1 spike：envd 不可达 → 诚实 STREAM_UNSUPPORTED；完整 envd 流式 CB-04 落地（SS-13 spec §2.7）。",
            "evidence": "cube_runtime_adapter.py:582-606",
        },
        CAPABILITY_FILES_SNAPSHOT: {
            "state": STATE_REAL,
            "note": "文件上传/下载 + 快照/回滚：服务端会话文件服务（level-agnostic）。",
            "evidence": "sandbox_sessions.py:1500-1655, :1834-1903",
        },
        CAPABILITY_PAUSE_RESUME: {
            "state": STATE_REAL,
            "note": "暂停/恢复/续期：状态机门禁（level-agnostic，执行期间门禁生效）；cube pause-snapshot 槽位预留（spec §2.6）。",
            "evidence": "sandbox_sessions.py:1923-1990",
        },
        CAPABILITY_TEE_PROOF_GATE: {
            "state": STATE_PLANNED,
            "note": "per-VM TEE（RT-06）未落地：无 TEE 硬件时会话 trust_root=none 诚实披露；RT-06 后回写本单元格。",
        },
        CAPABILITY_NETWORK_POLICY_HOTSWAP: {
            "state": STATE_PLANNED,
            "note": "egress 策略包 provision 时注入（cube_egress.py / _load_policy_package）；运行中热更路径待节点验收后回写。",
        },
    },
    "L3": {
        CAPABILITY_SYNC_EXEC: {
            "state": STATE_REAL,
            "note": "同步执行真实可用（bwrap 命名空间隔离）。",
        },
        CAPABILITY_STREAM_WS: {
            "state": STATE_UNSUPPORTED,
            "note": "流式执行不支持：STREAM_UNSUPPORTED（含 bwrap L3；旧注释 'L0 (bwrap)' 系命名错误，以枚举为准）。",
            "evidence": "session_stream.py:162-165",
        },
        CAPABILITY_FILES_SNAPSHOT: {
            "state": STATE_REAL,
            "note": "文件上传/下载 + 快照/回滚支持。",
            "evidence": "sandbox_sessions.py:1500-1655, :1834-1903",
        },
        CAPABILITY_PAUSE_RESUME: {
            "state": STATE_REAL,
            "note": "暂停/恢复/续期支持。",
            "evidence": "sandbox_sessions.py:1834-1903",
        },
        CAPABILITY_TEE_PROOF_GATE: {
            "state": STATE_UNSUPPORTED,
            "note": "不适用：L3 无 TEE 证明门禁（n/a）。",
        },
        CAPABILITY_NETWORK_POLICY_HOTSWAP: {
            "state": STATE_REAL,
            "note": "网络策略热更新支持（running-only）。",
            "evidence": "app/api/network_policy.py",
        },
    },
    "k8s": {
        CAPABILITY_SYNC_EXEC: {
            "state": STATE_REAL,
            "note": "同步执行真实可用（K8s/K3s Pod）。",
        },
        CAPABILITY_STREAM_WS: {
            "state": STATE_REAL,
            "note": "WS 流式执行支持（L0/k8s 白名单，python client exec WebSocket）。",
            "evidence": "session_stream.py:162-165",
        },
        CAPABILITY_FILES_SNAPSHOT: {
            "state": STATE_UNSUPPORTED,
            "note": "文件/快照不支持：共享卷 RWX 边界下走只读共享卷（ROX）。",
            "evidence": "app/api/shared_volumes.py",
        },
        CAPABILITY_PAUSE_RESUME: {
            "state": STATE_REAL,
            "note": "暂停/恢复/续期支持。",
            "evidence": "sandbox_sessions.py:1834-1903",
        },
        CAPABILITY_TEE_PROOF_GATE: {
            "state": STATE_UNSUPPORTED,
            "note": "不适用：k8s 无 TEE 证明门禁（n/a）。",
        },
        CAPABILITY_NETWORK_POLICY_HOTSWAP: {
            "state": STATE_REAL,
            "note": "网络策略热更新支持（running-only）。",
            "evidence": "app/api/network_policy.py",
        },
    },
}


def _validate() -> None:
    """Self-check the matrix shape at import time (fail fast, fail loud)."""
    assert tuple(_MATRIX) == LEVEL_ORDER, "matrix levels must match LEVEL_ORDER"
    for level, caps in _MATRIX.items():
        assert tuple(caps) == CAPABILITIES, f"{level}: capabilities incomplete"
        for cap, cell in caps.items():
            assert cell["state"] in STATES, f"{level}/{cap}: bad state {cell['state']!r}"
            assert cell.get("note"), f"{level}/{cap}: missing note"


_validate()


def dataplane_status() -> dict:
    """SS-13 交付档：本机 MicroVM 数据面是否可用——区分「完整档」与「控制面档」。

    判据是**唯一的**：``CUBE_DATAPLANE_ENABLED`` 关掉时 ``l2c`` 适配器根本不注册，
    任何执行入口都会得到 400 ``RUNTIME_UNAVAILABLE`` / ``reason=dataplane_disabled``（SS-13 不降级）。
    这里只陈述事实，**不改变任何执行行为**；控制面能力（目录/契约/审批/审计/交付）与本开关无关。

    注：``enabled=true`` 仅代表开关打开且适配器已注册；节点实际不可达时执行仍会失败，
    那条路径由 ``RUNTIME_UNAVAILABLE`` 自己披露，不在此掩盖。
    """
    from app.core.config import get_settings

    enabled = bool(get_settings().CUBE_DATAPLANE_ENABLED)
    return {
        "enabled": enabled,
        "mode": "full" if enabled else "control_plane_only",
        "reason": None if enabled else "dataplane_disabled",
        "note": (
            "MicroVM 数据面就绪，可执行沙箱计算" if enabled else
            "未启用 MicroVM 数据面（需 /dev/kvm 或已透传 vmx/svm 的宿主 + Cube 计算节点）："
            "平台按控制面档交付，目录/契约/审批/审计/交付可用，沙箱执行入口不可用"
        ),
    }


# ---------------------------------------------------------------------------
# 部署真实能力披露（2026-10-30 拍板②）：「这一档在**当前这套部署**上到底能不能用」
#
# 与矩阵本身是两件事：矩阵说的是档位的固有能力（与部署无关），这里说的是运行时
# 注册/门禁真值。判据逐条抄自执行侧的真实门禁，绝不另立一套口径：
#   - ``l2c`` 适配器只在 ``CUBE_DATAPLANE_ENABLED`` 后注册（sandbox_runtime.py:2404），
#     且没有 online 节点时创建会话 fail-closed 400 ``RUNTIME_UNAVAILABLE``
#     （sandbox_sessions.py，SS-13 不降级）→ reason ``dataplane_disabled`` / ``no_ready_node``
#     与 ``flow_runtime.py`` 的 REASON_MESSAGES 用同一批词。
#   - 历史档位（L0/L1/L2/L3/k8s）适配器只在 ``SANDBOX_LEGACY_ENABLED`` 或
#     ``LEGACY_L2_ENABLED`` 后注册（sandbox_runtime.py:2391），创建侧同判据
#     （sandbox_sessions.py:330 ``LEGACY_LEVEL_RETIRED``）→ reason ``legacy_level_retired``。
#   - 合约可写档位 = 承载供数下限（``SUPPLY_DATA_LEVELS``，A55）∩ 合约取值域
#     （``app.schemas.contract.VALID_SANDBOX_LEVELS``），两个都已存在的权威表。
#
# **披露能力 ≠ 放宽校验**：这个模块只读，不改任何执行判定；不可满足的请求照旧 400。
# ---------------------------------------------------------------------------

# 门禁镜像 reason：每个词都**逐字取自**执行侧已登记的 ``detail.reason``
# （``dataplane_disabled``/``no_ready_node`` 见 app/api/flow_runtime.py 与
# app/api/sandbox_sessions.py；``legacy_level_retired``/``below_supply_data_floor``
# 见 app/api/sandbox_sessions.py）。tests/test_deployment_sandbox_levels.py 机检
# 每个词在 app/ 源码里真实存在，防止披露侧发明一个执行侧没有的理由。
GATE_MIRROR_REASONS: frozenset[str] = frozenset({
    "dataplane_disabled",
    "no_ready_node",
    "legacy_level_retired",
    "below_supply_data_floor",
})
# 纯披露 reason：描述的是「可用」的来历或取值域事实，执行侧没有对应的拒绝码，
# 所以只可能出现在文案里，不可能被当成放行凭据。
DISCLOSURE_ONLY_REASONS: frozenset[str] = frozenset({
    "legacy_observation_window",
    "not_in_contract_vocabulary",
})


def _legacy_gate_open(settings) -> bool:
    """历史档位观察期开关的真值（与 sandbox_runtime.py:2391 同判据）。"""
    return bool(settings.SANDBOX_LEGACY_ENABLED or settings.LEGACY_L2_ENABLED)


def level_availability(ready_nodes: int) -> list[dict]:
    """每个档位在当前部署上的运行时可用性与合约可写性（按 ``LEVEL_ORDER`` 排序）。

    ``ready_nodes`` 是 online 的 Cube 计算节点数，由调用方（API 层）从节点表查来；
    本模块不碰数据库，保持可单测。
    """
    from app.core.config import get_settings
    from app.schemas.contract import VALID_SANDBOX_LEVELS

    settings = get_settings()
    legacy_open = _legacy_gate_open(settings)
    dataplane_on = bool(settings.CUBE_DATAPLANE_ENABLED)

    rows: list[dict] = []
    for level in LEVEL_ORDER:
        meta = dict(LEVEL_METADATA[level])
        if level == LEVEL_L2C:
            registered = dataplane_on
            if not registered:
                reason: str | None = "dataplane_disabled"
                disclosure = (
                    "需 Cube 计算节点：本部署按控制面档交付（CDS_CUBE_DATAPLANE_ENABLED=false），"
                    "创建会话会返回 400 RUNTIME_UNAVAILABLE（SS-13 单一路径，不降级到进程/bwrap 隔离）"
                )
            elif ready_nodes <= 0:
                reason = "no_ready_node"
                disclosure = (
                    "MicroVM 数据面已启用，但当前没有 online 的 Cube 计算节点，"
                    "创建会话会返回 400 RUNTIME_UNAVAILABLE（请检查节点心跳与 deploy/cube-node/）"
                )
            else:
                reason = None
                disclosure = f"生产默认档位：Cube MicroVM（SS-13 单一路径），当前 online 节点 {ready_nodes} 个"
        else:
            registered = legacy_open
            if not registered:
                reason = "legacy_level_retired"
                disclosure = (
                    "SS-13 起执行面只有 MicroVM 单一路径，该档位已退役："
                    "需 CDS_SANDBOX_LEGACY_ENABLED=true 观察期开关（生产禁止开启），"
                    "否则创建会话返回 400 LEGACY_LEVEL_RETIRED"
                )
            else:
                reason = "legacy_observation_window"
                disclosure = (
                    "开发环境 / 观察期档位：本部署开启了历史档位观察期开关"
                    "（CDS_SANDBOX_LEGACY_ENABLED 或 CDS_LEGACY_L2_ENABLED，生产禁止开启）"
                )

        floor_ok = can_carry_supply_data(level)
        vocabulary_ok = level in VALID_SANDBOX_LEVELS
        if not floor_ok:
            contract_reason: str | None = "below_supply_data_floor"
            contract_note = (
                f"不可写入合约：低于平台供数下限（可承载契约供数的档位只有 {'/'.join(SUPPLY_DATA_LEVELS)}），"
                "装载真实供数的会话在该档位会被 400 拒绝"
            )
        elif not vocabulary_ok:
            contract_reason = "not_in_contract_vocabulary"
            contract_note = "不可写入合约：不在合约取值域（app/schemas/contract.py VALID_SANDBOX_LEVELS）内"
        else:
            contract_reason = None
            contract_note = None

        rows.append({
            **meta,
            "level": level,
            "registered": registered,
            "runtime_available": registered and not (level == LEVEL_L2C and reason == "no_ready_node"),
            "reason": reason,
            "disclosure": disclosure,
            "supply_data_eligible": floor_ok,
            "contract_selectable": floor_ok and vocabulary_ok,
            "contract_reason": contract_reason,
            "contract_note": contract_note,
        })
    return rows


def sandbox_levels_payload(ready_nodes: int) -> dict:
    """``GET /api/v1/meta/sandbox-levels`` 的响应体（只读披露，不含任何放行决定）。"""
    from app.core.config import get_settings

    settings = get_settings()
    rows = level_availability(ready_nodes)
    by_level = {row["level"]: row for row in rows}

    # 推荐档：按 SUPPLY_DATA_LEVELS 的强度序（l2c → L3）取第一个「可写合约且当前可执行」的档；
    # 一个都没有时回落到产品默认 l2c（合约授权是控制面动作，允许先签后部署节点），
    # 但披露字段照实说「当前不可执行」，不粉饰。
    recommended = next(
        (lv for lv in SUPPLY_DATA_LEVELS
         if by_level[lv]["contract_selectable"] and by_level[lv]["runtime_available"]),
        LEVEL_L2C,
    )
    dev_level = settings.DEV_SANDBOX_LEVEL
    dev_row = by_level.get(dev_level)
    return {
        "levels": rows,
        "recommended_level": recommended,
        "recommended_disclosure": by_level[recommended]["disclosure"],
        "supply_data_levels": list(SUPPLY_DATA_LEVELS),
        "dev_sandbox_level": {
            "level": dev_level,
            "known": dev_row is not None,
            "runtime_available": bool(dev_row and dev_row["runtime_available"]),
            "disclosure": dev_row["disclosure"] if dev_row else f"未知档位 {dev_level!r}，不在能力矩阵取值域内",
        },
        "ready_nodes": ready_nodes,
        "dataplane": dataplane_status(),
        "note": (
            "口径：只披露当前部署的真实能力，不改变任何执行判定；"
            "不可满足的请求照旧按 SS-13 fail-closed 400 拒绝，披露能力 ≠ 降级隔离"
        ),
    }


def get_capability_matrix() -> dict:
    """Return the full matrix payload: ``{"matrix": {...}, "levels": {...}}``.

    ``matrix`` is level -> capability -> cell (state/note/evidence?);
    ``levels`` carries per-level metadata (label/positioning/deprecated).
    ``dataplane`` 是本部署的档位声明（SS-13 交付档，前端全局横幅的唯一数据源）。
    """
    return {
        "matrix": {level: dict(_MATRIX[level]) for level in LEVEL_ORDER},
        "levels": {level: dict(LEVEL_METADATA[level]) for level in LEVEL_ORDER},
        "dataplane": dataplane_status(),
    }


def health_capability_section() -> dict:
    """Compact capability block for the additive /health section:
    level list + the l2c (L2') planned flag.

    A9: ``l2c_planned`` is True while the L2' row still has *any* planned
    capability cell (per the row comments: RT-06 per-VM TEE, CB-04 envd
    streaming). The previous ``all(...)`` could never be True on a mixed row —
    dead logic that read "L2' not ready" as "ready"."""
    return {
        "levels": list(LEVEL_ORDER),
        "l2c_planned": any(
            _MATRIX[LEVEL_L2C][cap]["state"] == STATE_PLANNED for cap in CAPABILITIES
        ),
        "baseline": MATRIX_BASELINE_DATE,
    }
