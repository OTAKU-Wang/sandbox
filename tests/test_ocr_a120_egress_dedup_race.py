"""A120（high，本轨只做 cube_egress 半边）— 唯一约束上的 check-then-act 会把整批审计带下。

`cube_egress_dedup` 有 `UniqueConstraint(node_id, file_name, offset, line_no)`
（`app/models/cube_egress_dedup.py:24-27`），而写入方 `ingest_egress_batch()` 是"先 SELECT
查重 → 再 db.add → 批末 db.commit()"：尾器至少一次投递，两个副本/两批重投之间那个 SELECT
看不到对手刚提交的行， INSERT 就在 flush/commit 里抛 `IntegrityError`，全仓没有任何捕获
（改前 `grep IntegrityError app/services/cube_egress_audit.py` 0 命中）⇒ 整个 POST
/internal/audit/egress 冒 500，**同一批里其它正常行连同它们的审计行一起丢失**，尾器还会重投
同一批把故障拖长。alert 半边（`alert_center._insert_or_merge`）已由 poolA 用 SAVEPOINT 修掉，
本文件把同一修法钉在 egress 半边。

竞态怎么注入（不用 sleep、不用第二连接）：`_RacingSession` 是一个转发代理，只在第一次
dedup 预查时"撒谎"——先把赢家的行真实写进库（同一事务），再强制让这一次预查回答"没有"，
这正是"对手在预查之后、INSERT 之前提交"的窗口。SQLite 的唯一约束在语句级生效，所以后面的
INSERT 必然撞约束——与真 PG 上的表现同构。
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.cube_egress_dedup import CubeEgressDedup
from app.services.cube_egress_audit import ingest_egress_batch

NODE = "node-a120"
FILE = "cube-egress.a120.jsonl"


class _EmptyResult:
    """Stand-in for "the pre-check saw nothing" — the race window itself."""

    def first(self):
        return None

    def scalar(self):
        return None

    def all(self):
        return []

    def scalars(self):
        return self


class _RacingSession:
    """把除"第一次 dedup 预查"以外的所有调用原样转发给真 session。

    race_key 的那次预查：先把赢家行 INSERT 进库（模拟并发副本刚刚提交），再返回空结果，
    于是被调方的 check-then-act 一定走到 INSERT 并撞上 UNIQUE 约束。
    lie_always=True 时后续每次 dedup 预查都说"没有"——用来验证兜底不会把
    「约束冲突但赢家行根本不可见」这类真异常也吞掉。
    """

    def __init__(
        self,
        db: AsyncSession,
        race_key: tuple[str, str, int, int],
        *,
        lie_always: bool = False,
    ):
        self._db = db
        self._race_key = race_key
        self._lie_always = lie_always
        self._fired = False
        self.injected = 0

    def __getattr__(self, name):
        return getattr(self._db, name)

    @staticmethod
    def _is_dedup_lookup(statement) -> bool:
        try:
            return CubeEgressDedup.__table__ in [
                froms for froms in statement.get_final_froms()
            ]
        except Exception:
            return False

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        if self._is_dedup_lookup(statement) and (
            self._lie_always or not self._fired
        ):
            if not self._fired:
                self._fired = True
                node_id, file_name, offset, line_no = self._race_key
                await self._db.execute(
                    text(
                        'INSERT INTO cube_egress_dedup '
                        '(id, node_id, file_name, "offset", line_no) '
                        "VALUES (:id, :node_id, :file_name, :offset, :line_no)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "node_id": node_id,
                        "file_name": file_name,
                        "offset": offset,
                        "line_no": line_no,
                    },
                )
                self.injected += 1
            return _EmptyResult()
        return await self._db.execute(statement, *args, **kwargs)


def _racing(
    db: AsyncSession,
    race_key: tuple[str, str, int, int],
    *,
    lie_always: bool = False,
) -> Any:
    """Typed escape hatch: the proxy satisfies the AsyncSession contract duck-typed."""
    return _RacingSession(db, race_key, lie_always=lie_always)


def _entry(offset: int, line_no: int, request_id: str) -> dict[str, Any]:
    return {
        "offset": offset,
        "line_no": line_no,
        "record": {
            "ts": "2026-10-20T08:00:00Z",
            "request_id": request_id,
            "event": "http_request",
            "sandbox": {"policy_id": "pol-1", "src_ip": "10.0.0.9"},
            "conn": {"original_dst_ip": "1.2.3.4", "original_dst_port": 443},
            "tls": {"sni": "api.example.com", "cipher": "SM4-GCM", "version": "TLS1.3"},
            "http": {
                "host": "api.example.com",
                "method": "GET",
                "path": "/v1/items",
                "status": 200,
                "req_bytes": 12,
                "resp_bytes": 340,
            },
            "policy": {"decision": "allow", "matched_rule": "r-1", "duration_us": 1500},
            "credentials": {"secret_ids": ["sk-1"], "injected": ["authorization"]},
            "security": {},
        },
    }


async def _dedup_keys(db: AsyncSession) -> set[tuple[str, str, int, int]]:
    rows = await db.execute(
        select(
            CubeEgressDedup.node_id,
            CubeEgressDedup.file_name,
            CubeEgressDedup.offset,
            CubeEgressDedup.line_no,
        )
    )
    return {tuple(r) for r in rows.all()}


async def _audit_actions(db: AsyncSession) -> list[str]:
    rows = await db.execute(select(AuditLog.action).order_by(AuditLog.created_at))
    return [r[0] for r in rows.all()]


@pytest.mark.asyncio
async def test_dedup_race_loses_one_line_not_the_whole_batch(
    db_session: AsyncSession,
):
    racing_key = (NODE, FILE, 100, 1)
    db = _racing(db_session, racing_key)

    result = await ingest_egress_batch(
        db,
        NODE,
        FILE,
        [_entry(100, 1, "req-race"), _entry(240, 2, "req-fresh")],
    )

    assert result["received"] == 2
    assert db.injected == 1, "注入器没生效，本条就是空跑"
    assert result["duplicates"] == 1, f"竞态行没被计成重复：{result}"
    assert result["stored"] == 1, f"同批的新鲜行跟着丢了：{result}"
    assert result == {"received": 2, "duplicates": 1, "stored": 1}

    keys = await _dedup_keys(db_session)
    assert keys == {racing_key, (NODE, FILE, 240, 2)}, "dedup 表应当是赢家行 + 新鲜行"
    actions = await _audit_actions(db_session)
    assert actions == ["cube_egress.access"], (
        "输掉竞态的那一行必须一个审计行都不留（否则同一行被计两次）；"
        f"实际={actions}"
    )


@pytest.mark.asyncio
async def test_plain_duplicate_batch_still_counts_duplicates(
    db_session: AsyncSession,
):
    first = await ingest_egress_batch(
        db_session, NODE, FILE, [_entry(100, 1, "req-a"), _entry(240, 2, "req-b")]
    )
    assert first == {"received": 2, "duplicates": 0, "stored": 2}

    again = await ingest_egress_batch(
        db_session, NODE, FILE, [_entry(100, 1, "req-a"), _entry(300, 3, "req-c")]
    )
    assert again == {"received": 2, "duplicates": 1, "stored": 1}
    assert len(await _dedup_keys(db_session)) == 3
    assert await _audit_actions(db_session) == [
        "cube_egress.access",
        "cube_egress.access",
        "cube_egress.access",
    ]


@pytest.mark.asyncio
async def test_conflict_without_a_visible_winner_is_not_swallowed(
    db_session: AsyncSession,
):
    db = _racing(db_session, (NODE, FILE, 100, 1), lie_always=True)

    with pytest.raises(IntegrityError):
        await ingest_egress_batch(db, NODE, FILE, [_entry(100, 1, "req-x")])
