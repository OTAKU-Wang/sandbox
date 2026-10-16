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
    level list + the l2c (L2') planned flag."""
    return {
        "levels": list(LEVEL_ORDER),
        "l2c_planned": all(
            _MATRIX[LEVEL_L2C][cap]["state"] == STATE_PLANNED for cap in CAPABILITIES
        ),
        "baseline": MATRIX_BASELINE_DATE,
    }
