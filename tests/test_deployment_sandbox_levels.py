"""2026-10-30 拍板② —— 「当前部署实际可用的沙箱档位 + 逐项披露」只读端点。

背景（取证 specs/ss-19-full-platform-docker-e2e.md §10.1-4）：合约表单的「允许沙箱级别」
在前端被硬编码成 disabled 的 l2c，而 dev compose 把 ``CDS_DEV_SANDBOX_LEVEL`` 设成 ``L3``
（docker-compose.yml:213）→ 本机一开调试环必 400。修法是**如实披露部署真实能力**，
让表单按披露渲染，而不是把前端选项写死。

本文件钉三件事：
1. 端点形状与逐项披露（每个档位都要有可展示的 ``disclosure``，不能只回一个布尔）；
2. **披露与执行判定必须一致**（披露说不可用的档位，创建会话照旧 400；
   SS-13 铁律：披露能力 ≠ 降级隔离，一个都不放宽）；
3. 词表机检：披露用到的 ``reason`` 只能来自两个已声明的集合，且其中每个「门禁镜像词」
   在 ``app/`` 源码里真实存在 —— 防止披露侧发明一个执行侧没有的理由。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.core.config import get_settings
from app.models.sandbox_node import NodeStatus, SandboxNode
from app.schemas.contract import VALID_SANDBOX_LEVELS, ContractCreate
from app.services.capability_matrix import (
    DISCLOSURE_ONLY_REASONS,
    GATE_MIRROR_REASONS,
    LEVEL_ORDER,
    SUPPLY_DATA_LEVELS,
)

URL = "/api/v1/meta/sandbox-levels"
APP_ROOT = Path(__file__).resolve().parents[1] / "app"

ROW_KEYS = {
    "level", "label", "positioning", "description", "deprecated",
    "registered", "runtime_available", "reason", "disclosure",
    "supply_data_eligible", "contract_selectable", "contract_reason", "contract_note",
}
PAYLOAD_KEYS = {
    "levels", "recommended_level", "recommended_disclosure", "supply_data_levels",
    "dev_sandbox_level", "ready_nodes", "dataplane", "note",
    # compliance disclosure_fields()，与 /meta/capability-matrix 同一批平台口径
    "signing_backend", "aead_algorithm", "trust_root", "chain_anchoring", "sandbox_engine",
}


def _by_level(payload: dict) -> dict[str, dict]:
    return {row["level"]: row for row in payload["levels"]}


def _set_flags(monkeypatch, **flags) -> None:
    """在**已缓存**的 Settings 上打补丁（get_settings 是 lru_cache，不改环境变量）。"""
    settings = get_settings()
    for key, value in flags.items():
        monkeypatch.setattr(settings, key, value)


@pytest.mark.asyncio
async def test_endpoint_shape_and_per_level_disclosure(client: AsyncClient, auth_headers: dict):
    resp = await client.get(URL, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert set(data.keys()) == PAYLOAD_KEYS, data.keys()
    assert [row["level"] for row in data["levels"]] == list(LEVEL_ORDER)
    for row in data["levels"]:
        assert set(row.keys()) == ROW_KEYS, row
        # 披露必须是能直接渲染的人话，不是空串也不是把 reason 码当文案糊上去
        assert isinstance(row["disclosure"], str) and len(row["disclosure"]) >= 8, row
        assert row["reason"] in (GATE_MIRROR_REASONS | DISCLOSURE_ONLY_REASONS | {None}), row
        # 不可用的档位必须说清楚为什么不可用
        if not row["runtime_available"]:
            assert row["reason"] is not None, row

    # 供数下限的档位表来自唯一权威，不是这里另抄一份
    assert data["supply_data_levels"] == list(SUPPLY_DATA_LEVELS)
    assert data["note"]
    assert data["dataplane"]["mode"] in {"full", "control_plane_only"}
    assert isinstance(data["ready_nodes"], int) and data["ready_nodes"] >= 0
    assert data["recommended_level"] in LEVEL_ORDER
    assert data["recommended_disclosure"]


@pytest.mark.asyncio
async def test_contract_options_never_wider_than_backend_vocabulary(
    client: AsyncClient, auth_headers: dict
):
    """披露给合约表单的档位，必须是后端**本来就接受**的档位（只强不弱）。

    直接拿真实的 pydantic 校验器验一遍：任何被标成 ``contract_selectable`` 的档位
    都要能通过 ``ContractCreate`` 的取值域校验；反过来，取值域外的档位（L0）
    绝不能被披露成可写。
    """
    resp = await client.get(URL, headers=auth_headers)
    data = resp.json()
    selectable = [row["level"] for row in data["levels"] if row["contract_selectable"]]

    assert set(selectable) <= set(VALID_SANDBOX_LEVELS), selectable
    assert set(selectable) <= set(SUPPLY_DATA_LEVELS), selectable
    for level in selectable:
        ContractCreate(
            contract_type="data_query",
            buyer_id="00000000-0000-0000-0000-000000000001",
            product_ids=["00000000-0000-0000-0000-000000000002"],
            title="shape-check",
            allowed_sandbox_levels=level,
        )

    l0 = _by_level(data)["L0"]
    assert l0["contract_selectable"] is False
    assert l0["contract_reason"] in {"below_supply_data_floor", "not_in_contract_vocabulary"}
    assert l0["contract_note"]


@pytest.mark.asyncio
async def test_control_plane_only_shape_discloses_dataplane_disabled(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    _set_flags(monkeypatch, CUBE_DATAPLANE_ENABLED=False, SANDBOX_LEGACY_ENABLED=False,
               LEGACY_L2_ENABLED=False)
    resp = await client.get(URL, headers=auth_headers)
    data = resp.json()
    rows = _by_level(data)

    l2c = rows["l2c"]
    assert l2c["runtime_available"] is False
    assert l2c["reason"] == "dataplane_disabled"
    assert "Cube" in l2c["disclosure"], l2c
    # 控制面档下没有退役的档位被说成可用
    for level in ("L0", "L1", "L2", "L3", "k8s"):
        assert rows[level]["runtime_available"] is False, level
        assert rows[level]["reason"] == "legacy_level_retired", level
        assert rows[level]["registered"] is False, level
    # 一个可执行档位都没有时，推荐档回落到产品默认（合约是控制面动作），
    # 但披露照实说当前不可执行 —— 不粉饰成可用。
    assert data["recommended_level"] == "l2c"
    assert rows["l2c"]["runtime_available"] is False


@pytest.mark.asyncio
async def test_dev_compose_shape_defaults_to_the_level_the_dev_env_actually_uses(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """D-e2e-17 的正解：dev compose 形态（数据面关 + 观察期开关开 + DEV_SANDBOX_LEVEL=L3）

    披露必须推荐 L3，且把「这是开发/观察期档位」写在披露里；合约表单照此渲染，
    本机调试环就不再是「表单只能选 l2c、运行时只会用 L3」的死局。
    """
    _set_flags(monkeypatch, CUBE_DATAPLANE_ENABLED=False, SANDBOX_LEGACY_ENABLED=True,
               LEGACY_L2_ENABLED=True, DEV_SANDBOX_LEVEL="L3")
    resp = await client.get(URL, headers=auth_headers)
    data = resp.json()
    rows = _by_level(data)

    assert rows["l2c"]["runtime_available"] is False
    assert rows["l2c"]["reason"] == "dataplane_disabled"
    assert rows["L3"]["runtime_available"] is True
    assert rows["L3"]["contract_selectable"] is True
    assert rows["L3"]["reason"] == "legacy_observation_window"
    assert any(word in rows["L3"]["disclosure"] for word in ("开发", "观察期")), rows["L3"]
    assert data["recommended_level"] == "L3"
    dev = data["dev_sandbox_level"]
    assert dev == {"level": "L3", "known": True, "runtime_available": True,
                   "disclosure": dev["disclosure"]}
    assert "观察期" in dev["disclosure"] or "生产禁止" in dev["disclosure"]


@pytest.mark.asyncio
async def test_no_ready_node_is_disclosed_as_such(
    client: AsyncClient, auth_headers: dict, db_session, monkeypatch
):
    """数据面开着但一个 online 节点都没有：披露必须是 no_ready_node 而不是「可用」。"""
    _set_flags(monkeypatch, CUBE_DATAPLANE_ENABLED=True, SANDBOX_LEGACY_ENABLED=True,
               LEGACY_L2_ENABLED=True, DEV_SANDBOX_LEVEL="L3")
    await db_session.execute(update(SandboxNode).values(status=NodeStatus.OFFLINE.value))
    await db_session.flush()

    resp = await client.get(URL, headers=auth_headers)
    data = resp.json()
    rows = _by_level(data)

    assert data["ready_nodes"] == 0
    assert rows["l2c"]["registered"] is True
    assert rows["l2c"]["runtime_available"] is False
    assert rows["l2c"]["reason"] == "no_ready_node"
    assert "节点" in rows["l2c"]["disclosure"]
    # 推荐档跳到当前真能跑的档位（观察期开关下的 L3），不是继续推一个跑不起来的 l2c
    assert data["recommended_level"] == "L3"


@pytest.mark.asyncio
async def test_disclosure_agrees_with_enforcement_no_widening(
    client: AsyncClient, auth_headers: dict, publish_product, monkeypatch
):
    """SS-13 铁律：披露说「不可用」的档位，创建会话照旧 400 —— 一个字都没放宽。

    同一次请求里对账：披露的布尔与执行的判定必须一致。
    """
    # 1) 观察期开关关掉 → 披露说 L3 不可用，创建 L3 会话仍然 400 LEGACY_LEVEL_RETIRED
    _set_flags(monkeypatch, SANDBOX_LEGACY_ENABLED=False, LEGACY_L2_ENABLED=False)
    disclosed = _by_level((await client.get(URL, headers=auth_headers)).json())
    assert disclosed["L3"]["runtime_available"] is False
    assert disclosed["L3"]["reason"] == "legacy_level_retired"

    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": await _published_product(client, auth_headers, publish_product),
              "sandbox_level": "L3"},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == "LEGACY_LEVEL_RETIRED"
    assert body["detail"]["reason"] == disclosed["L3"]["reason"]

    # 2) 数据面关掉 → 披露说 l2c 不可用，创建 l2c 会话仍然 400 RUNTIME_UNAVAILABLE
    _set_flags(monkeypatch, CUBE_DATAPLANE_ENABLED=False)
    disclosed = _by_level((await client.get(URL, headers=auth_headers)).json())
    assert disclosed["l2c"]["runtime_available"] is False
    assert disclosed["l2c"]["reason"] == "dataplane_disabled"

    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": await _published_product(client, auth_headers, publish_product),
              "sandbox_level": "l2c"},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "RUNTIME_UNAVAILABLE"


async def _published_product(client: AsyncClient, auth_headers: dict, publish_product) -> str:
    import uuid

    resp = await client.post(
        "/api/v1/data-products",
        json={"name": f"SBL-{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    product_id = resp.json()["id"]
    await publish_product(product_id)
    return product_id


@pytest.mark.asyncio
async def test_endpoint_requires_auth(client: AsyncClient):
    assert (await client.get(URL)).status_code in (401, 403)


def test_gate_mirror_vocabulary_really_exists_in_app_sources():
    """机检：披露词表里每个「门禁镜像词」都必须在 ``app/`` 源码里真实存在。

    与 tests/test_audit_query_filters.py 对 ``AUDIT_FAILURE_MARKERS`` 的做法同风格：
    披露侧不许发明一个执行侧从来没返回过的 reason，否则前端会展示一条永远不可能
    发生的解释。``DISCLOSURE_ONLY_REASONS`` 是显式声明的例外（只写文案，不对应拒绝）。
    """
    sources = "\n".join(p.read_text(encoding="utf-8") for p in APP_ROOT.rglob("*.py"))
    for word in GATE_MIRROR_REASONS:
        assert f'"{word}"' in sources, f"reason {word!r} 在 app/ 里没有真实出处"
    # 两个集合不得重叠：重叠意味着某个词既是拒绝又是纯披露，词表就失去意义了
    assert not (GATE_MIRROR_REASONS & DISCLOSURE_ONLY_REASONS)


def test_disclosure_module_does_not_touch_enforcement():
    """披露模块自己不得出现任何放行/降级动作（只读披露的结构性约束）。"""
    text = (APP_ROOT / "services" / "capability_matrix.py").read_text(encoding="utf-8")
    body = text.split("部署真实能力披露", 1)[1]
    for forbidden in ("raise ", "commit(", "update(", "provision"):
        assert forbidden not in body, f"披露代码里出现了 {forbidden!r}"
