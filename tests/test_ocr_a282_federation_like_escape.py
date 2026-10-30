"""A282：跨空间镜像的 LIKE 过滤没有转义 `%` / `_`（backend/latent）。

镜像条目在本库里没有独立表，靠**名字前缀** `[remote:<space_id>] <远端名>` 认账（写侧
`catalog_sync._build_remote_product`，读侧三处 `.count()` + 一处列表 + 一处 delete）。
SQL 的 LIKE 不是子串匹配：`%` 吞任意串、`_` 顶掉一个字符。而 space_id 是可写字段——
`app/api/federation.py` 的 `EstablishTrustRequest.space_id` 只有 `min_length=1, max_length=64`，
没有 pattern，`%` 与 `_` 存得进去。于是：

* 读侧：某个空间的镜像数/镜像列表会把**邻居空间**的条目算进来（统计口径错、跨空间串列表）；
* 写侧：`catalog_sync._full_sync` 用同一条式子做 `delete`，一次全量同步会把邻居空间的镜像整批删掉。

修法是一个来源：前缀只在 `catalog_sync` 里拼一次（`remote_product_name` / `remote_name_like`），
元字符整段转义 + 交给 SQLAlchemy 的 `escape=`，通配符只剩我们自己拼上的末尾那个 `%`。
"""
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.data_product import DataProduct
from app.services.catalog_sync import (
    LIKE_ESCAPE,
    catalog_sync,
    escape_like,
    remote_name_like,
    remote_product_name,
)

# 这一对是照着"不转义时必然互相命中"挑的：`a282%` 拼出的模式 `[remote:a282%]%`
# 会正好吞掉邻居 `a282k` 的镜像；`a282_1` 的 `_` 则会顶掉 `a282X1` 里的那个 X。
# 前缀带 a282 是为了躲开同进程里其它模块往 connector 单子里塞过的空间。
SPACE_PLAIN = "a282k"        # 正常邻居空间
SPACE_WILD = "a282%"         # 名字里带通配符的空间（本轮的注入面）
SPACE_UNDER = "a282_1"       # 带 `_` 的空间（`_` 会顶掉一个字符）


async def _mirror(db_session, make_user, space_id: str, entry_name: str) -> DataProduct:
    """按**写侧同一个入口**造一条镜像条目（provider_id 是 users 外键，不能省）。"""
    _headers, provider_id = await make_user(role="data_provider", prefix="a282")
    product = DataProduct(
        name=remote_product_name(space_id, entry_name),
        provider_id=uuid.UUID(provider_id),
        product_type="dataset",
        status="published",
    )
    db_session.add(product)
    await db_session.flush()
    return product


class TestEscapeLike:
    def test_wildcards_are_escaped(self):
        assert escape_like("a%b_c") == "a\\%b\\_c"

    def test_escape_char_itself_is_escaped_first(self):
        # 不先转义反斜杠，后面两步产出的 `\` 会被二次展开，ESCAPE 子句读回来就不是原字符。
        assert escape_like("a\\%b") == "a\\\\\\%b"

    def test_plain_id_is_untouched(self):
        assert escape_like("cds-remote-1") == "cds-remote-1"

    def test_sql_carries_an_explicit_escape_clause(self):
        sql = str(remote_name_like("sp%").compile(compile_kwargs={"literal_binds": False}))
        assert "ESCAPE" in sql.upper()
        assert LIKE_ESCAPE in sql


class TestRemoteNameLikeScoping:
    async def test_plain_space_matches_only_itself(self, db_session, make_user):
        mine = await _mirror(db_session, make_user, SPACE_PLAIN, "本产品")
        other = await _mirror(db_session, make_user, SPACE_WILD, "邻居")
        rows = (await db_session.execute(select(DataProduct).where(remote_name_like(SPACE_PLAIN)))).scalars().all()
        assert [p.id for p in rows] == [mine.id], f"邻居 {other.name!r} 串进来了"

    async def test_percent_in_space_id_does_not_swallow_the_neighbor(self, db_session, make_user):
        """修前必红：`[remote:sp%]%` 当模式用，`[remote:sp1] …` 整个被吞进这个空间。"""
        neighbor = await _mirror(db_session, make_user, SPACE_PLAIN, "邻居的产品")
        mine = await _mirror(db_session, make_user, SPACE_WILD, "自己的产品")
        rows = (await db_session.execute(select(DataProduct).where(remote_name_like(SPACE_WILD)))).scalars().all()
        assert {p.id for p in rows} == {mine.id}
        assert neighbor.id not in {p.id for p in rows}

    async def test_underscore_does_not_match_one_different_char(self, db_session, make_user):
        """`a282_1` 与 `a282X1` 只差一个字符：不转义时 `_` 正好把它顶掉。"""
        lookalike = await _mirror(db_session, make_user, "a282X1", "看着像的产品")
        mine = await _mirror(db_session, make_user, SPACE_UNDER, "自己的产品")
        rows = (await db_session.execute(select(DataProduct).where(remote_name_like(SPACE_UNDER)))).scalars().all()
        assert {p.id for p in rows} == {mine.id}
        assert lookalike.id not in {p.id for p in rows}

    async def test_none_means_all_remote_mirrors(self, db_session, make_user):
        a = await _mirror(db_session, make_user, SPACE_PLAIN, "甲")
        b = await _mirror(db_session, make_user, SPACE_UNDER, "乙")
        _headers, provider_id = await make_user(role="data_provider", prefix="a282-local")
        local = DataProduct(name="本地产品（不是镜像）", provider_id=uuid.UUID(provider_id), product_type="dataset", status="published")
        db_session.add(local)
        await db_session.flush()
        rows = (await db_session.execute(select(DataProduct).where(remote_name_like()))).scalars().all()
        assert {p.id for p in rows} == {a.id, b.id}


class TestFullSyncDelete:
    """伤害真正落地的地方：全量同步的 delete 用的就是同一条式子。"""

    async def test_full_sync_of_a_wildcard_space_keeps_the_neighbor_mirror(self, db_session, make_user):
        neighbor = await _mirror(db_session, make_user, SPACE_PLAIN, "邻居的产品")
        mine = await _mirror(db_session, make_user, SPACE_WILD, "自己的产品")

        added, _updated, removed = await catalog_sync._full_sync(db_session, SPACE_WILD, [])

        assert removed == 1, f"这一轮该只删掉自己那条，实际删了 {removed} 条"
        assert added == 0
        names = {p.name for p in (await db_session.execute(select(DataProduct))).scalars().all()}
        assert neighbor.name in names, "邻居空间的镜像被一次全量同步删掉了"
        assert mine.name not in names


class TestReadSideScoping:
    """两处读侧：镜像列表（`?space_id=`）与同步状态里的 `entry_count`。"""

    async def test_wildcard_space_id_is_actually_storable(self, client, operator_headers):
        """台账成立性的那一半：`%` 真的进得来（schema 只有 min/max_length，没有 pattern），
        所以下面两条不是纸上防御。"""
        resp = await client.post(
            "/api/v1/federation/trust",
            json={
                "space_id": SPACE_WILD,
                "space_name": "通配符空间",
                "endpoint": "https://remote.example.com",
                "trust_level": "verified",
            },
            headers=operator_headers,
        )
        assert resp.status_code in (200, 201), resp.text
        assert resp.json()["remote_space"] == SPACE_WILD

    async def test_entries_endpoint_does_not_leak_the_neighbor_mirror(
        self, client, operator_headers, db_session, make_user,
    ):
        neighbor = await _mirror(db_session, make_user, SPACE_PLAIN, "邻居的产品")
        mine = await _mirror(db_session, make_user, SPACE_WILD, "自己的产品")

        resp = await client.get(
            "/api/v1/federation/catalog/entries", params={"space_id": SPACE_WILD}, headers=operator_headers,
        )
        assert resp.status_code == 200, resp.text
        # 这个端点返回裸数组（不是 {items,…} 信封），针按真实形状来。
        names = [e["product_name"] for e in resp.json()]
        assert names == [mine.name], f"跨空间串了列表：{names}（邻居那条 {neighbor.name!r} 不该出现）"

    async def test_sync_status_entry_count_does_not_count_the_neighbor(
        self, client, operator_headers, db_session, make_user,
    ):
        # 这个端点是遍历「已建立的信任」算 entry_count 的，所以本例自己建一次信任
        # （connector 是进程内单子，但不假设别的模块建过什么）。
        trust = await client.post(
            "/api/v1/federation/trust",
            json={
                "space_id": SPACE_WILD,
                "space_name": "通配符空间",
                "endpoint": "https://remote.example.com",
                "trust_level": "verified",
            },
            headers=operator_headers,
        )
        assert trust.status_code in (200, 201), trust.text
        await _mirror(db_session, make_user, SPACE_PLAIN, "邻居的产品")
        await _mirror(db_session, make_user, SPACE_WILD, "自己的产品")

        resp = await client.get("/api/v1/federation/catalog/sync-status", headers=operator_headers)
        assert resp.status_code == 200, resp.text
        row = next((i for i in resp.json() if i["space_id"] == SPACE_WILD), None)
        assert row is not None, "上一轮建的信任没出现在同步状态里，这条针就空了"
        # 修前这里是 2：邻居的镜像被算进本空间的镜像数。
        assert row["entry_count"] == 1


# ---------------------------------------------------------------------------
# lead 代收（2026-10-30 波次 3 收口）：A282 同族 LIKE 的两处姊妹位核查结论。
# ---------------------------------------------------------------------------

def test_connector_search_like_is_escaped_structurally():
    """connectors.py 的目录搜索 `?q=` 之前把用户词直接嵌进 ilike——`q=%` 命中全表。
    本查询已被 connector 谓词圈死（不是跨租户泄露），但结果集会静默串味，同族同修。
    结构针：必须走 escape_like + 显式 escape="\\"，不许回到裸 f-string。"""
    src = (Path(__file__).resolve().parents[1] / "app" / "api" / "connectors.py").read_text(
        encoding="utf-8"
    )
    seg = src[src.index("if q:"):]
    seg = seg[: seg.index("if product_type:")]
    assert "escape_like(q)" in seg, "q 必须过 escape_like"
    assert 'escape="\\\\"' in seg, "ilike 必须显式声明转义符"
    assert "ilike(f\"%{q}%\")" not in seg, "裸插值回潮即红"


def test_data_products_product_id_predicate_is_uuid_typed_not_wildcard():
    """ledger 曾把 data_products 的 `%"<uuid>"%` 谓词列为同族姊妹位——证伪：
    product_id 全程 uuid.UUID 类型，`%` 进不了这个插值点；且它只是超集过滤器，
    最终判据是 _contract_references_product 的精确比对（漏一行=放行删除，
    所以它宁超勿漏是设计）。本针锁住「类型收口在前」这个前提，别改成 str 直通。"""
    src = (Path(__file__).resolve().parents[1] / "app" / "api" / "data_products.py").read_text(
        encoding="utf-8"
    )
    assert "def _product_reference_predicate(product_id: uuid.UUID)" in src
    assert "product_id: uuid.UUID" in src
    assert 'ilike(f\'%"{product_id}"%\')' in src, "超集过滤器形状别动（判据见函数 docstring）"
