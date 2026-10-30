"""W12 · 2026-10-30 拍板: janitor 白名单真删、受保护类别「无论怎么配置都不删」、
集外配置启动拒（fail-closed）——docs/retention.md 的 2026-10-30 章节钉在这里。

覆盖面（每条都对应拍板文档的一句承诺）：
① 白名单类别真删：session_workspace_residue / dev_debug_artifacts 的过期目录
   确实消失，新鲜的不动（文件系统根经 monkeypatch 隔离到 tmp，测试永不碰真 /tmp）；
② 白名单外永不碰：audit_logs / merkle_leaves / delivery_bundles / data_resources
   各钉一条「无论怎么配置都不删」——正的保留天数 + 真删模式 + 默认类别集，
   行必须原样在，结果值恒 -1（拒删），审计事件带 protected_refusals；
③ dry-run 默认：Settings 默认 JANITOR_ENABLED=false、DRY_RUN=true；dry-run 下
   文件系统类别一个目录都不删；主开关关闭时文件系统类别整体跳过（连扫描都不做）；
④ 集外配置启动拒：JANITOR_CATEGORIES 选到受保护/未知类别时
   validate_janitor_config / validate_security_config 直接 ValueError，
   且不受 prod 判定影响（dev/TESTING 也拒）。
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.models.alert import AlertRecord
from app.models.audit_log import AuditLog
from app.models.data_resource import DataResource
from app.models.delivery_bundle import DeliveryBundle
from app.models.merkle_leaf import MerkleLeaf
from app.services import retention_janitor as rj
from app.services.retention_janitor import (
    NEVER_DELETE_CATEGORIES,
    REAL_DELETE_WHITELIST,
    ProtectedCategoryError,
    _require_deletable,
    parse_janitor_categories,
    purge_cycle,
)


@pytest.fixture(autouse=True)
def _janitor_env(monkeypatch, tmp_path):
    """默认姿态：主开关开（本文件测真删路径）、dry-run 开、DB 类别全 0、
    文件系统根目录隔离到 tmp_path —— 测试进程永不 walk 真 /tmp。"""
    settings = get_settings()
    residue_root = tmp_path / "cds-sandbox"
    residue_root.mkdir()
    dev_root = tmp_path / "cds-pipeline"
    dev_root.mkdir()
    monkeypatch.setattr(rj, "_FS_ROOTS", {
        "session_workspace_residue": (str(residue_root),),
        "dev_debug_artifacts": (str(dev_root),),
    })
    for name, value in {
        "JANITOR_ENABLED": True,
        "JANITOR_CATEGORIES": "",
        "RETENTION_JANITOR_DRY_RUN": True,
        "RETENTION_JANITOR_BATCH": 10,
        "AUDIT_LOG_RETENTION_DAYS": 0,
        "MERKLE_LEAF_RETENTION_DAYS": 0,
        "ALERT_RETENTION_DAYS": 0,
        "WORKSPACE_RESIDUE_RETENTION_DAYS": 30,
        "DEV_ARTIFACT_RETENTION_DAYS": 14,
    }.items():
        monkeypatch.setattr(settings, name, value)
    yield settings


def _aged_dir(root: Path, rel: str, age_days: float) -> Path:
    p = root / rel
    p.mkdir(parents=True)
    stamp = (datetime.now(timezone.utc) - timedelta(days=age_days)).timestamp()
    os.utime(p, (stamp, stamp))
    return p


def _age(path: Path, age_days: float) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(days=age_days)).timestamp()
    os.utime(path, (stamp, stamp))


async def _count(db_session, model) -> int:
    result = await db_session.execute(select(func.count()).select_from(model))
    return result.scalar() or 0


# ── ① 白名单类别真删 ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_workspace_residue_real_delete_removes_only_stale(db_session, _janitor_env, tmp_path):
    root = tmp_path / "cds-sandbox"
    alice = root / "alice"
    alice.mkdir()
    _aged_dir(alice, "sess-old", 40)                       # stale session under fresh tenant
    olduser = _aged_dir(root, "olduser", 40)               # whole stale subtree
    _aged_dir(olduser, "sess-deep", 45)                    # 子目录更老 → 整棵算一个删除单元
    _age(olduser, 40)                                      # 父目录必须在子目录之后老化
    _aged_dir(alice, "sess-new", 1)                        # 活跃期内，不动

    _janitor_env.RETENTION_JANITOR_DRY_RUN = False
    results = await purge_cycle(db_session)

    assert results["session_workspace_residue"] == 2
    assert not (alice / "sess-old").exists()
    assert not olduser.exists()
    assert alice.exists() and (alice / "sess-new").exists()


@pytest.mark.asyncio
async def test_dev_debug_artifacts_expired_removed(db_session, _janitor_env, tmp_path):
    dev = tmp_path / "cds-pipeline"
    _aged_dir(dev, "task-old", 30)
    _aged_dir(dev, "task-new", 2)

    _janitor_env.RETENTION_JANITOR_DRY_RUN = False
    results = await purge_cycle(db_session)

    assert results["dev_debug_artifacts"] == 1
    assert not (dev / "task-old").exists()
    assert (dev / "task-new").exists()


@pytest.mark.asyncio
async def test_subsets_only_process_selected_categories(db_session, _janitor_env, tmp_path):
    root = tmp_path / "cds-sandbox"
    _aged_dir(root, "bob/sess-old", 40)
    alert = AlertRecord(
        id=uuid.uuid4(), dedup_key=f"jan-{uuid.uuid4().hex[:8]}", alert_type="t",
        severity="info", status="resolved", message="old",
        created_at=datetime.now(timezone.utc) - timedelta(days=90),
    )
    db_session.add(alert)
    await db_session.flush()

    _janitor_env.JANITOR_CATEGORIES = "alert_records"
    _janitor_env.ALERT_RETENTION_DAYS = 7
    _janitor_env.RETENTION_JANITOR_DRY_RUN = False
    results = await purge_cycle(db_session)

    assert results["alert_records"] == 1
    assert results["session_workspace_residue"] == 0
    assert (root / "bob/sess-old").exists()  # 集内但未被选中 → 不处理


# ── ② 白名单外永不碰（无论怎么配置都不删） ─────────────────────────────────
@pytest.mark.asyncio
async def test_audit_logs_never_deleted_no_matter_the_config(db_session, _janitor_env):
    old = AuditLog(
        id=uuid.uuid4(), action="legacy.event", resource_type="test",
        created_at=datetime.now(timezone.utc) - timedelta(days=3650),
    )
    db_session.add(old)
    await db_session.flush()

    # 把能翻的全翻掉：正的保留天数 + 真删模式 + 主开关开 + 全类别
    _janitor_env.AUDIT_LOG_RETENTION_DAYS = 30
    _janitor_env.RETENTION_JANITOR_DRY_RUN = False
    _janitor_env.JANITOR_ENABLED = True
    _janitor_env.JANITOR_CATEGORIES = ""
    results = await purge_cycle(db_session)

    assert results["audit_logs"] == -1  # 拒删：永远不可能记正数
    remaining = await db_session.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "legacy.event")
    )
    assert (remaining.scalar() or 0) == 1
    audit_rows = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "retention.purge")
        )
    ).scalars().all()
    assert audit_rows, "周期必须写 retention.purge 审计记录"
    assert "audit_logs" in (audit_rows[-1].detail or {}).get("protected_refusals", [])


@pytest.mark.asyncio
async def test_merkle_leaves_never_deleted_no_matter_the_config(db_session, _janitor_env):
    db_session.add(MerkleLeaf(
        id=uuid.uuid4(), batch_id="b-old", leaf_index=0, leaf_hash="ab" * 32,
        created_at=datetime.now(timezone.utc) - timedelta(days=3650),
    ))
    await db_session.flush()

    _janitor_env.MERKLE_LEAF_RETENTION_DAYS = 30
    _janitor_env.RETENTION_JANITOR_DRY_RUN = False
    results = await purge_cycle(db_session)

    assert results["merkle_leaves"] == -1
    assert await _count(db_session, MerkleLeaf) == 1


@pytest.mark.asyncio
async def test_delivery_bundles_survive_no_matter_the_config(db_session, _janitor_env):
    for i in range(2):
        db_session.add(DeliveryBundle(
            id=uuid.uuid4(), contract_id=uuid.uuid4(), object_name=f"delivery/b{i}.zip",
            size_bytes=10, file_count=1,
            created_at=datetime.now(timezone.utc) - timedelta(days=3650),
        ))
    await db_session.flush()

    _janitor_env.RETENTION_JANITOR_DRY_RUN = False
    results = await purge_cycle(db_session)
    assert await _count(db_session, DeliveryBundle) == 2
    assert results.get("delivery_bundles", 0) <= 0, "受保护类别永远不可能记正数"
    # 执行器硬守卫：即便有人直接拿类别调删除路径
    with pytest.raises(ProtectedCategoryError, match="never-delete"):
        _require_deletable("delivery_bundles")
    # 配置面同样买不到：集外启动拒
    with pytest.raises(ValueError, match="JANITOR_CATEGORIES"):
        parse_janitor_categories("delivery_bundles")


@pytest.mark.asyncio
async def test_data_resources_survive_no_matter_the_config(db_session, _janitor_env):
    db_session.add(DataResource(
        id=uuid.uuid4(), provider_id=uuid.uuid4(), name="raw-10y",
        created_at=datetime.now(timezone.utc) - timedelta(days=3650),
    ))
    await db_session.flush()

    _janitor_env.RETENTION_JANITOR_DRY_RUN = False
    await purge_cycle(db_session)
    assert await _count(db_session, DataResource) == 1
    with pytest.raises(ProtectedCategoryError, match="never-delete"):
        _require_deletable("data_resources")
    with pytest.raises(ValueError, match="JANITOR_CATEGORIES"):
        parse_janitor_categories("data_resources")


def test_never_and_whitelist_constants_membership():
    # 常量本身就是契约：四个受保护类别 + 三个白名单类别，互斥。
    assert NEVER_DELETE_CATEGORIES == frozenset(
        {"audit_logs", "merkle_leaves", "delivery_bundles", "data_resources"}
    )
    assert REAL_DELETE_WHITELIST == frozenset(
        {"alert_records", "session_workspace_residue", "dev_debug_artifacts"}
    )
    assert not (NEVER_DELETE_CATEGORIES & REAL_DELETE_WHITELIST)
    _require_deletable("alert_records")  # 白名单内不抛
    with pytest.raises(ProtectedCategoryError):
        _require_deletable("no_such_category")


# ── ③ dry-run 默认 / 主开关 ─────────────────────────────────────────────────
def test_default_settings_master_off_and_dry_run():
    s = Settings()  # 无关键字段 → 全部代码默认值
    assert s.JANITOR_ENABLED is False, "真删主开关生产默认必须关"
    assert s.RETENTION_JANITOR_DRY_RUN is True, "dry-run 仍是默认行为"
    assert s.JANITOR_CATEGORIES == ""
    assert s.AUDIT_LOG_RETENTION_DAYS == 0
    assert s.MERKLE_LEAF_RETENTION_DAYS == 0
    assert s.WORKSPACE_RESIDUE_RETENTION_DAYS > 0
    assert s.DEV_ARTIFACT_RETENTION_DAYS > 0


@pytest.mark.asyncio
async def test_dry_run_default_keeps_all_directories(db_session, _janitor_env, tmp_path):
    root = tmp_path / "cds-sandbox"
    _aged_dir(root, "carol/sess-old", 90)

    results = await purge_cycle(db_session)  # fixture 默认 DRY_RUN=True

    assert results["session_workspace_residue"] == 0
    assert (root / "carol/sess-old").exists()


@pytest.mark.asyncio
async def test_master_switch_off_skips_fs_categories_entirely(db_session, _janitor_env, tmp_path):
    root = tmp_path / "cds-sandbox"
    _aged_dir(root, "dave/sess-old", 900)

    _janitor_env.JANITOR_ENABLED = False
    _janitor_env.RETENTION_JANITOR_DRY_RUN = False
    results = await purge_cycle(db_session)

    assert results["session_workspace_residue"] == 0
    assert (root / "dave/sess-old").exists(), "主开关未武装时文件系统类别连删都不许发生"


# ── 文件系统卫生：符号链接 / 审计命名 / 散文件 ──────────────────────────────
@pytest.mark.asyncio
async def test_symlinks_audit_named_and_loose_files_never_touched(
    db_session, _janitor_env, tmp_path
):
    root = tmp_path / "cds-sandbox"
    external = tmp_path / "outside-audit-store"
    external.mkdir()
    _age(external, 400)
    os.symlink(external, root / "link-out")
    audit_like = _aged_dir(root, "session-audit-trail", 400)
    stray = root / "stray.txt"
    stray.write_text("x")
    _age(stray, 400)

    _janitor_env.RETENTION_JANITOR_DRY_RUN = False
    results = await purge_cycle(db_session)

    assert results["session_workspace_residue"] == 0
    assert audit_like.exists(), "名字含 audit 的目录永不删（纵深防御）"
    assert external.exists() and (root / "link-out").is_symlink(), "符号链接永不代替目标被删"
    assert stray.exists(), "散文件不在文件系统类别的删除范围"


# ── ④ 集外配置启动拒（fail-closed，任何环境） ───────────────────────────────
@pytest.mark.parametrize("raw", [
    "audit_logs",
    "merkle_leaves",
    "delivery_bundles",
    "data_resources",
    "alert_records,not_a_real_category",
])
def test_out_of_set_categories_reject_at_boot(raw):
    s = Settings(JWT_SECRET_KEY="k" * 40, JANITOR_CATEGORIES=raw)
    with pytest.raises(ValueError, match="JANITOR_CATEGORIES"):
        s.validate_janitor_config()
    # validate_security_config 也拒，且发生在 is_prod 早退之前（dev/TESTING 同样）
    with pytest.raises(ValueError, match="JANITOR_CATEGORIES"):
        s.validate_security_config()


def test_in_set_subsets_parse_clean():
    assert parse_janitor_categories("") == REAL_DELETE_WHITELIST
    assert parse_janitor_categories(" alert_records , dev_debug_artifacts ") == frozenset(
        {"alert_records", "dev_debug_artifacts"}
    )
    s = Settings(JWT_SECRET_KEY="k" * 40, JANITOR_CATEGORIES="alert_records")
    assert s.validate_janitor_config() == []  # 子集合法且未武装真删 → 无告警无异常
