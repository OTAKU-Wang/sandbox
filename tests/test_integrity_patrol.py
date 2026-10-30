"""P2-5（D-e2e-18，ss-19 §10.1-5）：MinIO 一致性巡检的行为门.

对账面 = `data_resources.status=ready` × 存储对象存在性。本文件钉：
1. `stat_presence` 诚实三态分类学（ok / missing / uncheckable，混存引用形态
   全吃；MinIO 不可达必须 uncheckable——禁冒认 ok，也禁误判 missing）；
2. `patrol_cycle` 只碰 ready、只写 integrity_* 两列、审计留痕、不 commit；
3. 调度双门默认关（janitor 同型）+ interval 语义；手动触发不受双门约束；
4. API 面：OPERATOR+/ADMIN 才能读台账/触发，data_provider 403；
5. 链侧：0034 恒单头+祖先形（链头永不钉死）+ upgrade/downgrade 真往返。
"""
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.script import ScriptDirectory
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

REPO = Path(__file__).resolve().parents[1]
REVISION = "0034_integrity_patrol_columns"


# --------------------------------------------------------------------------
# 链侧：恒单头 + 祖先形（a170/a186b 同款：历史快照不当契约）
# --------------------------------------------------------------------------

def test_chain_has_exactly_one_head_and_0034_stays_on_the_linear_trunk():
    from alembic.config import Config

    script = ScriptDirectory.from_config(Config(str(REPO / "alembic.ini")))
    heads = script.get_heads()
    assert len(heads) == 1, f"全链必须恒单头（分叉才多头），实际 {heads}"
    ancestors = {rev.revision for rev in script.walk_revisions(head=heads[0])}
    assert REVISION in ancestors, (
        f"{REVISION} 不在 head {heads[0]} 的祖先历史里：已发布历史被改写/抹除"
    )
    assert script.get_revision(REVISION).down_revision == "0033_ocr_webhook_index_convergence"


def _alembic(target: str, db: Path) -> None:
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy")}
    env.update(NO_PROXY="*", CDS_DATABASE_URL=f"sqlite+aiosqlite:///{db}")
    subprocess.run(
        [sys.executable, "-m", "alembic", *target.split()],
        check=True, cwd=REPO, env=env, capture_output=True, timeout=300,
    )


def _resource_columns(db: Path) -> dict[str, tuple[str, int, str]]:
    with sqlite3.connect(db) as conn:
        return {r[1]: (r[2], r[3], r[4] or "") for r in conn.execute(
            "PRAGMA table_info(data_resources)")}


def test_0034_roundtrip_columns_default_uncheckable(tmp_path):
    db = tmp_path / "p25_roundtrip.db"
    _alembic(f"upgrade {REVISION}", db)
    cols = _resource_columns(db)
    assert cols["integrity_status"][1] == 1, "integrity_status 必须 NOT NULL"
    assert "uncheckable" in cols["integrity_status"][2].lower(), (
        "DDL 默认值必须是 'uncheckable'：未巡检行禁冒认 ok"
    )
    assert "integrity_checked_at" in cols, "0034 的巡检时刻列缺失"
    _alembic("downgrade -1", db)
    cols = _resource_columns(db)
    assert "integrity_status" not in cols and "integrity_checked_at" not in cols, (
        "downgrade 必须忠实删列"
    )


# --------------------------------------------------------------------------
# stat_presence：诚实三态分类学（真逻辑，不打桩分类本身）
# --------------------------------------------------------------------------

def test_stat_presence_empty_reference_is_missing():
    from app.services.storage_service import storage_service

    state, reason = storage_service.stat_presence(None)
    assert state == "missing" and reason


def test_stat_presence_local_fallback_pair(tmp_path, monkeypatch):
    from app.services.storage_service import storage_service

    monkeypatch.setattr(storage_service, "_local_dir", tmp_path)
    f = tmp_path / "legacy.csv"
    f.write_bytes(b"x")
    assert storage_service.stat_presence(str(f))[0] == "ok"
    f.unlink()
    state, reason = storage_service.stat_presence(str(f))
    assert state == "missing" and "legacy.csv" in reason


def test_stat_presence_minio_unreachable_is_uncheckable_not_ok(monkeypatch):
    """铁律用例：客户端不可用时禁冒认 ok，也禁把「查不出」误报成「丢了」。"""
    from app.services.storage_service import storage_service

    monkeypatch.setattr(storage_service, "_get_minio_client", lambda: None)
    state, reason = storage_service.stat_presence("minio://cds-data/some/obj.csv")
    assert state == "uncheckable", f"MinIO 不可达必须 uncheckable，得到 {state}"


def test_stat_presence_no_such_key_is_missing(monkeypatch):
    """结构化 NoSuchKey 形状（真 SDK S3Error 必带 code/status；本机无 minio 包
    也能钉住分类学——stat_presence 只吃形状不吃 SDK 类，见其注释）。"""
    from app.services.storage_service import storage_service

    class _NoSuchKey(Exception):
        code = "NoSuchKey"
        status = 404

    class _C:
        def stat_object(self, bucket, name):
            raise _NoSuchKey()

    monkeypatch.setattr(storage_service, "_get_minio_client", lambda: _C())
    state, reason = storage_service.stat_presence("minio://cds-data/gone.bin")
    assert state == "missing" and "NoSuchKey" in reason


def test_stat_presence_other_errors_are_uncheckable(monkeypatch):
    from app.services.storage_service import storage_service

    class _C:
        def stat_object(self, bucket, name):
            raise ConnectionError("connection refused")

    monkeypatch.setattr(storage_service, "_get_minio_client", lambda: _C())
    state, _ = storage_service.stat_presence("minio://cds-data/x.bin")
    assert state == "uncheckable"


def test_stat_presence_ok(monkeypatch):
    from app.services.storage_service import storage_service

    class _C:
        def stat_object(self, bucket, name):
            assert bucket == "cds-data" and name == "present.bin"
            return object()

    monkeypatch.setattr(storage_service, "_get_minio_client", lambda: _C())
    assert storage_service.stat_presence("minio://cds-data/present.bin") == ("ok", None)


def test_stat_presence_wrong_bucket_is_uncheckable(monkeypatch):
    from app.services.storage_service import storage_service

    monkeypatch.setattr(storage_service, "_get_minio_client", lambda: object())
    state, _ = storage_service.stat_presence("minio://other-bucket/x")
    assert state == "uncheckable", "陌生桶不可对当前桶下 any 结论"


# --------------------------------------------------------------------------
# patrol_cycle / maybe_run_patrol_cycle
# --------------------------------------------------------------------------

@pytest_asyncio.fixture
async def patrol_provider(db_session: AsyncSession):
    from app.models.user import User
    from app.services.auth_service import hash_password

    u = User(username=f"patrolprov_{uuid.uuid4().hex[:8]}",
             email=f"patrolprov_{uuid.uuid4().hex[:8]}@example.com",
             hashed_password=hash_password("x" * 12), role="data_provider")
    db_session.add(u)
    await db_session.flush()
    return u


def _mk_resource(provider_id, name, status="ready", storage_path="minio://cds-data/o.csv"):
    from app.models.data_resource import DataResource

    return DataResource(provider_id=provider_id, name=name, status=status,
                        storage_path=storage_path)


def _fake_storage(present: set[str]):
    """返回 (patched stat_presence)，按 storage_path 是否在 present 集合分 ok/missing。"""

    def _stat(ref):
        if ref in present:
            return "ok", None
        return "missing", "fake NoSuchKey"

    return _stat


async def test_new_ready_row_starts_uncheckable(db_session, patrol_provider):
    """默认值诚实：新建 ready 行未巡检前不得是 ok。"""
    r = _mk_resource(patrol_provider.id, "fresh")
    db_session.add(r)
    await db_session.flush()
    assert r.integrity_status == "uncheckable"
    assert r.integrity_checked_at is None


async def test_patrol_classifies_and_labels_and_audits(db_session, patrol_provider, monkeypatch):
    from app.models.audit_log import AuditLog
    from app.services import integrity_patrol as ip
    from app.services.storage_service import storage_service

    ok_res = _mk_resource(patrol_provider.id, "still-here")
    lost_res = _mk_resource(patrol_provider.id, "evicted", storage_path="minio://cds-data/lost.bin")
    bad_res = _mk_resource(patrol_provider.id, "no-ref", storage_path=None)
    db_session.add_all([ok_res, lost_res, bad_res])
    await db_session.flush()

    def _stat(ref):
        if not ref:
            return "missing", "no ref"
        return ("ok", None) if ref.endswith("o.csv") else ("missing", "NoSuchKey")

    monkeypatch.setattr(storage_service, "stat_presence", _stat)
    out = await ip.patrol_cycle(db_session, triggered_by="test")
    assert (out["checked"], out["ok"], out["missing"]) == (3, 1, 2)
    assert out["uncheckable"] == 0
    assert str(lost_res.id) in out["missing_ids"] and str(ok_res.id) not in out["missing_ids"]
    await db_session.flush()
    assert ok_res.integrity_status == "ok" and ok_res.integrity_checked_at is not None
    assert lost_res.integrity_status == "missing"
    assert bad_res.integrity_status == "missing", "ready 却没有引用本身就是不一致"

    logs = (await db_session.execute(
        select(AuditLog).where(AuditLog.action == "integrity.patrol"))).scalars().all()
    assert len(logs) == 1, "一轮巡检一枚审计（唯一漏斗）"
    assert logs[0].detail["missing"] == 2 and logs[0].detail["triggered_by"] == "test"


async def test_unreachable_storage_labels_uncheckable_never_ok(db_session, patrol_provider, monkeypatch):
    """端到端诚实档：存储不可达 → 库面 uncheckable + 审计计数走 uncheckable，
    integrity_status 全表不得出现 ok。"""
    from app.services import integrity_patrol as ip
    from app.services.storage_service import storage_service

    db_session.add(_mk_resource(patrol_provider.id, "blind-1"))
    db_session.add(_mk_resource(patrol_provider.id, "blind-2"))
    await db_session.flush()
    monkeypatch.setattr(storage_service, "stat_presence",
                        lambda ref: ("uncheckable", "MinIO 不可达"))
    out = await ip.patrol_cycle(db_session, triggered_by="test")
    assert (out["ok"], out["missing"], out["uncheckable"]) == (0, 0, 2)
    await db_session.flush()
    rows = (await db_session.execute(
        select(ip.DataResource).where(ip.DataResource.name.in_(["blind-1", "blind-2"]))
    )).scalars().all()
    assert {r.integrity_status for r in rows} == {"uncheckable"}
    assert all(r.integrity_checked_at is not None for r in rows)


async def test_patrol_only_touches_ready_rows(db_session, patrol_provider, monkeypatch):
    from app.services import integrity_patrol as ip
    from app.services.storage_service import storage_service

    up = _mk_resource(patrol_provider.id, "mid-upload", status="uploading")
    fa = _mk_resource(patrol_provider.id, "broken", status="failed")
    rd = _mk_resource(patrol_provider.id, "ready-ok")
    db_session.add_all([up, fa, rd])
    await db_session.flush()
    monkeypatch.setattr(storage_service, "stat_presence", lambda ref: ("ok", None))
    out = await ip.patrol_cycle(db_session, triggered_by="test")
    assert out["checked"] == 1 and out["ok"] == 1
    await db_session.flush()
    assert up.integrity_checked_at is None and fa.integrity_checked_at is None
    assert rd.integrity_checked_at is not None


async def test_patrol_uses_unchecked_first_batching(db_session, patrol_provider, monkeypatch):
    """批次公平性：未巡检/最旧者优先，批满即停——大表不会饿死新行。"""
    from app.core.config import get_settings
    from app.services import integrity_patrol as ip
    from app.services.storage_service import storage_service

    for i in range(3):
        db_session.add(_mk_resource(patrol_provider.id, f"q-{i}"))
    await db_session.flush()
    monkeypatch.setattr(get_settings(), "INTEGRITY_PATROL_BATCH_SIZE", 2)
    checked: list[str] = []
    monkeypatch.setattr(storage_service, "stat_presence",
                        lambda ref: checked.append(ref) or ("ok", None))
    out = await ip.patrol_cycle(db_session, triggered_by="test")
    assert out["checked"] == 2 and len(checked) == 2


# ---- 调度双门（janitor 同型，默认全关）----

async def test_scheduler_double_gate_defaults_off(db_session, monkeypatch):
    from app.core.config import get_settings
    from app.services import integrity_patrol as ip

    calls: list[int] = []

    async def _fake(db, **kw):
        calls.append(1)
        return {"checked": 1}

    monkeypatch.setattr(ip, "_last_patrol_monotonic", None)
    monkeypatch.setattr(ip, "patrol_cycle", _fake)
    # 主闸默认 false → 直接空转
    assert get_settings().INTEGRITY_PATROL_ENABLED is False
    assert await ip.maybe_run_patrol_cycle(db_session) == {}
    assert not calls


async def test_scheduler_interval_gate_zero_means_manual_only(db_session, monkeypatch):
    from app.core.config import get_settings
    from app.services import integrity_patrol as ip

    async def _fake(db, **kw):
        calls.append(1)
        return {"checked": 1}

    calls: list[int] = []
    monkeypatch.setattr(ip, "_last_patrol_monotonic", None)
    monkeypatch.setattr(ip, "patrol_cycle", _fake)
    monkeypatch.setattr(get_settings(), "INTEGRITY_PATROL_ENABLED", True)
    monkeypatch.setattr(get_settings(), "INTEGRITY_PATROL_INTERVAL_SECONDS", 0)
    assert await ip.maybe_run_patrol_cycle(db_session) == {}
    assert not calls, "interval=0 = 只留手动面，调度不得抢跑"


async def test_scheduler_runs_then_honours_interval(db_session, monkeypatch):
    import time as _t
    from app.core.config import get_settings
    from app.services import integrity_patrol as ip

    ran: list[int] = []

    async def _fake(db, **kw):
        ran.append(1)
        return {"checked": 7}

    monkeypatch.setattr(ip, "_last_patrol_monotonic", None)
    monkeypatch.setattr(ip, "patrol_cycle", _fake)
    monkeypatch.setattr(get_settings(), "INTEGRITY_PATROL_ENABLED", True)
    monkeypatch.setattr(get_settings(), "INTEGRITY_PATROL_INTERVAL_SECONDS", 3600)
    out = await ip.maybe_run_patrol_cycle(db_session)
    assert out == {"checked": 7} and len(ran) == 1
    assert await ip.maybe_run_patrol_cycle(db_session) == {}, "间隔内二次 sweep 不得重跑"
    # 时间旅行到间隔之后 → 到期补跑
    monkeypatch.setattr(ip, "_last_patrol_monotonic", _t.monotonic() - 3601)
    assert (await ip.maybe_run_patrol_cycle(db_session))["checked"] == 7
    assert len(ran) == 2


def test_sweep_skeleton_ride_present():
    """定时化必须真接在 janitor/scheduler 骨架上（文本钉：搭载点 + 双门读取）。"""
    src = (REPO / "app/services/session_lifecycle.py").read_text(encoding="utf-8")
    assert "maybe_run_patrol_cycle" in src
    assert "Integrity patrol failed" in src, "失败必须记日志不许静默"


# --------------------------------------------------------------------------
# API 面
# --------------------------------------------------------------------------

async def test_api_denies_non_operator(client: AsyncClient, auth_headers):
    r = await client.get("/api/v1/integrity/data-resources", headers=auth_headers)
    assert r.status_code == 403
    r = await client.post("/api/v1/integrity/patrol", headers=auth_headers)
    assert r.status_code == 403


async def test_api_operator_reads_and_triggers(client, db_session, operator_headers, patrol_provider, monkeypatch):
    from app.services.storage_service import storage_service

    db_session.add(_mk_resource(patrol_provider.id, "api-ready"))
    await db_session.flush()
    monkeypatch.setattr(storage_service, "stat_presence", lambda ref: ("ok", None))

    r = await client.get("/api/v1/integrity/data-resources", headers=operator_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("items", "total", "page", "page_size", "summary"):
        assert k in body, "列表信封约定 {items,total,page,page_size} + 诚实汇总"
    assert body["summary"]["never_checked"] >= 1
    assert body["items"][0]["integrity_status"] == "uncheckable"

    r = await client.post("/api/v1/integrity/patrol", headers=operator_headers)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] >= 1

    r = await client.get("/api/v1/integrity/data-resources",
                         params={"integrity_status": "ok"}, headers=operator_headers)
    assert r.status_code == 200 and r.json()["total"] >= 1

    r = await client.get("/api/v1/integrity/data-resources",
                         params={"integrity_status": "bogus"}, headers=operator_headers)
    assert r.status_code == 400


async def test_api_manual_trigger_not_gated_by_scheduler_switch(client, db_session, admin_headers, patrol_provider, monkeypatch):
    """手动触发 = 显式人工意图：调度主闸关着也要能跑（否则端点是死门面）。"""
    from app.core.config import get_settings
    from app.services.storage_service import storage_service

    assert get_settings().INTEGRITY_PATROL_ENABLED is False
    db_session.add(_mk_resource(patrol_provider.id, "manual-only"))
    await db_session.flush()
    monkeypatch.setattr(storage_service, "stat_presence", lambda ref: ("missing", "NoSuchKey"))
    r = await client.post("/api/v1/integrity/patrol", headers=admin_headers)
    assert r.status_code == 200 and r.json()["missing"] >= 1


# --------------------------------------------------------------------------
# 部署面：新开关六副本默认 false（janitor 同款纪律；middleware 无 api 环境不在列）
# --------------------------------------------------------------------------

def test_integrity_patrol_switch_off_in_every_deploy_copy():
    import yaml

    checks = {
        "docker-compose.prod.yml": lambda t: 'CDS_INTEGRITY_PATROL_ENABLED: "false"' in t,
        "docker-compose.yml": lambda t: 'CDS_INTEGRITY_PATROL_ENABLED: "false"' in t,
        "k8s/cds-app.yaml": lambda t: 'CDS_INTEGRITY_PATROL_ENABLED: "false"' in t,
        "deploy/e2e-remote/remote_deploy.sh": lambda t: '-e CDS_INTEGRITY_PATROL_ENABLED="false" \\' in t,
        "deploy/local/local_deploy.sh": lambda t: '-e CDS_INTEGRITY_PATROL_ENABLED="false" \\' in t,
    }
    for f, pred in checks.items():
        assert pred((REPO / f).read_text(encoding="utf-8")), f"{f} 缺默认 false 钉"
    vals = yaml.safe_load((REPO / "helm/cds/values.yaml").read_text(encoding="utf-8"))
    assert vals["api"]["env"].get("CDS_INTEGRITY_PATROL_ENABLED") == "false"
