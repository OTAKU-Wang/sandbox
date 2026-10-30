"""A81（medium）— 一条形态异常的 egress 行不能把整批审计打成 500，也不许被洗成正常审计事件。

台账（`docs/ocr-review-2026-10-18/ocr-verified-findings.md` §A81）：`EgressEntry.record`
是裸 `dict`（`app/api/cube_egress.py:20`，无任何结构约束），而下游
`cube_egress_audit.map_egress_record()` 假设具体形态（`sandbox.get(...)`、
`",".join(secret_ids)`）。改前批循环里对 `map_egress_record` **没有任何 try** ⇒
一条 `{"sandbox": "sbx-1"}` 或 `{"credentials": {"secret_ids": 7}}` 在批末
`await db.commit()` 之前抛出 ⇒ 整个 POST /internal/audit/egress 冒 500，同批其它正常行
连它们的审计行一起丢；节点尾器只在 2xx 才推进水位
（`deploy/cube-node/audit_tailer.py:388-392`），于是一条 poison 行永久阻塞该文件的审计流。

本文件钉的口径（父会话路由决定，不是"能过就行"）：
1. 坏行逐条兜住，批次继续（同批好行必须照常入库）；
2. **必须披露**：WARNING 带完整出处（node_id/file_name/offset/line_no）与原因，
   并有 `cds_cube_egress_lines_total{result="malformed"}` 计数——不许静默丢弃；
3. **不许洗白**：坏行不得降级成一条看起来正常的审计事件（把 `secret_ids=7` 拼成
   `encryption_key_id="7"` 入库就是洗白），所以坏行一条审计行都不留；
4. 取舍（写进代码注释与本文件）：`_claim_dedup_key` 先于 mapping，坏行的幂等键**被永久
   消费** ⇒ 重投只计 duplicate、不再重复告警。理由：`map_egress_record` 是纯函数，
   重试不可能得到不同结果；不消费键只会把一条已知坏行变成无上限的告警循环。原始行不丢
   （仍在节点自己的 JSONL 里，WARNING 给了定位它的全部坐标），恢复是运维的显式动作。

响应体形状 `{received, duplicates, stored}` 被 `tests/test_cube_audit_schema.py:95,116` 与
`tests/test_ocr_a120_egress_dedup_race.py` 用等值断言钉死，本轨不得改它们 ⇒
对运维的披露面只能是日志 + 指标（见 test_response_shape_is_unchanged）。
"""
from __future__ import annotations

import logging
import re
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import render_metrics
from app.models.audit_log import AuditLog
from app.models.cube_egress_dedup import CubeEgressDedup
from app.services.cube_egress_audit import ingest_egress_batch, map_egress_record

NODE = "node-a81"
FILE = "access.jsonl#ino-a81"
LOGGER = "app.services.cube_egress_audit"


def _rec(**overrides: Any) -> dict[str, Any]:
    """One upstream-shaped line (SS-10 §2.7 / CubeEgress/lua/audit.lua)."""
    record: dict[str, Any] = {
        "ts": "2026-10-20T09:00:00Z",
        "request_id": "req-a81",
        "event": "http_request",
        "sandbox": {"src_ip": "10.0.0.5", "policy_id": "pol-9"},
        "conn": {"original_dst_ip": "93.184.216.34", "original_dst_port": 443},
        "tls": {"sni": "api.example.com", "cipher": "SM4-GCM", "version": "TLSv1.3"},
        "http": {
            "method": "POST",
            "host": "api.example.com",
            "path": "/v1/data",
            "status": 200,
            "req_bytes": 42,
            "resp_bytes": 1337,
            "user_agent": "cube-sandbox/0.7",
        },
        "policy": {"matched_rule": "rule-7", "decision": "allow", "duration_us": 2500},
        "credentials": {"injected": ["Authorization"], "secret_ids": ["cred-1", "cred-2"]},
        "security": None,
    }
    record.update(overrides)
    return record


def _entry(offset: int, line_no: int, record: dict[str, Any]) -> dict[str, Any]:
    return {"offset": offset, "line_no": line_no, "record": record}


def _counter(result: str) -> int:
    """Read cds_cube_egress_lines_total{result=...} through the public exposition."""
    text = render_metrics().decode()
    hit = re.search(
        r'^cds_cube_egress_lines_total\{result="%s"\} (\d+)' % result, text, re.MULTILINE
    )
    return int(hit.group(1)) if hit else 0


async def _audit_rows(db: AsyncSession) -> list[AuditLog]:
    return list((await db.execute(select(AuditLog))).scalars().all())


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


# ─── 1. 批次存活：坏行只丢自己，同批好行照常入库并带披露 ──────────────────────


@pytest.mark.asyncio
async def test_malformed_line_does_not_kill_the_batch(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
):
    entries = [
        _entry(100, 1, _rec(request_id="req-good-1")),
        _entry(200, 2, _rec(sandbox="sbx-1")),  # 台账点名的形态：section 是 str
        _entry(300, 3, _rec(request_id="req-good-2")),
    ]
    before = _counter("malformed")

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = await ingest_egress_batch(db_session, NODE, FILE, entries)

    assert result == {"received": 3, "duplicates": 0, "stored": 2}, (
        f"坏行把同批好行带下了，或批次仍然抛错：{result}"
    )
    rows = await _audit_rows(db_session)
    assert len(rows) == 2, [r.action for r in rows]
    assert {r.detail["egress_request_id"] for r in rows} == {"req-good-1", "req-good-2"}

    assert _counter("malformed") - before == 1, "坏行没有计入 malformed 计数（静默丢弃）"

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING and LOGGER in r.name]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    msg = warnings[0].getMessage()
    # 出处四元组 + 原因（哪个字段、期望什么形态）都要能从日志里读到
    for needle in (NODE, FILE, "200", "sandbox"):
        assert needle in msg, f"WARNING 缺少 {needle}：{msg}"


# ─── 2. 不许洗白：坏行一条审计行都不留 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_non_list_secret_ids_is_rejected_not_laundered(db_session: AsyncSession):
    before = _counter("malformed")

    result = await ingest_egress_batch(
        db_session, NODE, FILE, [_entry(100, 1, _rec(credentials={"secret_ids": 7}))]
    )

    assert result == {"received": 1, "duplicates": 0, "stored": 0}
    rows = await _audit_rows(db_session)
    assert rows == [], f"形态异常的行被当成正常审计事件入库了：{[r.action for r in rows]}"
    assert _counter("malformed") - before == 1


@pytest.mark.asyncio
async def test_unanticipated_scalar_drift_is_still_contained(db_session: AsyncSession):
    """`duration_us` 不是 dict/secret_ids 那两类已知形态，走的是兜底 tuple。

    `"fast" // 1000` 抛 TypeError：改前同样把整批带下，改后必须是"少一条 + 计数 + 告警"。
    """
    before = _counter("malformed")

    result = await ingest_egress_batch(
        db_session,
        NODE,
        FILE,
        [
            _entry(100, 1, _rec(policy={"duration_us": "fast"})),
            _entry(200, 2, _rec(request_id="req-good")),
        ],
    )

    assert result == {"received": 2, "duplicates": 0, "stored": 1}
    assert _counter("malformed") - before == 1


# ─── 3. 取舍：坏行的幂等键被永久消费（重投不再重复告警） ──────────────────────


@pytest.mark.asyncio
async def test_malformed_line_consumes_its_dedup_key(db_session: AsyncSession):
    entries = [_entry(500, 7, _rec(policy="allow"))]
    bad_key = (NODE, FILE, 500, 7)

    first = await ingest_egress_batch(db_session, NODE, FILE, entries)
    assert first == {"received": 1, "duplicates": 0, "stored": 0}
    assert bad_key in await _dedup_keys(db_session), (
        "坏行必须占用幂等键：否则尾器每次重投都会再告警一次（无上限告警循环）"
    )

    before = _counter("malformed")
    replay = await ingest_egress_batch(db_session, NODE, FILE, entries)
    assert replay == {"received": 1, "duplicates": 1, "stored": 0}
    assert _counter("malformed") == before, "重投的已知坏行被重复计入 malformed"
    assert await _audit_rows(db_session) == []


# ─── 4. map_egress_record 自己给出带字段名的原因（不是裸 AttributeError） ──────


@pytest.mark.asyncio
async def test_map_egress_record_names_the_offending_field():
    from app.services.cube_egress_audit import MalformedEgressRecord

    assert issubclass(MalformedEgressRecord, ValueError)

    with pytest.raises(ValueError, match="sandbox"):
        map_egress_record(_rec(sandbox="sbx-1"), NODE, FILE, 0, 1)
    with pytest.raises(ValueError, match="credentials.secret_ids"):
        map_egress_record(_rec(credentials={"secret_ids": 7}), NODE, FILE, 0, 1)
    with pytest.raises(ValueError, match="credentials.secret_ids"):
        map_egress_record(_rec(credentials={"secret_ids": [1]}), NODE, FILE, 0, 1)
    # src_ip/user_agent 落的是有类型的列（audit_logs.ip_address / user_agent），
    # 非字符串必须在映射期被拒，不能等到 INSERT 才由 DB 报错把整批带下。
    with pytest.raises(ValueError, match="sandbox.src_ip"):
        map_egress_record(_rec(sandbox={"src_ip": 10}), NODE, FILE, 0, 1)
    with pytest.raises(ValueError, match="http.user_agent"):
        map_egress_record(_rec(http={"user_agent": {"v": 1}}), NODE, FILE, 0, 1)


# ─── 5. 回归护栏：正常行的映射与响应形状都没动 ───────────────────────────────


@pytest.mark.asyncio
async def test_good_record_maps_unchanged(db_session: AsyncSession):
    mapped = map_egress_record(_rec(), NODE, FILE, 10, 3)

    assert mapped["action"] == "cube_egress.access"
    assert mapped["resource_id"] == "rule-7"
    assert mapped["ip_address"] == "10.0.0.5"
    assert mapped["user_agent"] == "cube-sandbox/0.7"
    assert mapped["detail"]["encryption_key_id"] == "cred-1,cred-2"
    assert mapped["detail"]["compute_duration_ms"] == 2
    assert mapped["detail"]["offset"] == 10 and mapped["detail"]["line_no"] == 3

    result = await ingest_egress_batch(db_session, NODE, FILE, [_entry(10, 3, _rec())])
    assert result == {"received": 1, "duplicates": 0, "stored": 1}


@pytest.mark.asyncio
async def test_response_shape_is_unchanged(db_session: AsyncSession):
    """披露走日志+指标，响应体形状由两个邻居文件用等值断言钉死，不得新增键。"""
    result = await ingest_egress_batch(
        db_session, NODE, FILE, [_entry(100, 1, _rec(sandbox="sbx-1"))]
    )
    assert set(result) == {"received", "duplicates", "stored"}
