"""W12 + 2026-10-30 拍板: retention janitor — bounded, audited GC with a code-level
real-delete whitelist.

类别模型（2026-10-30 用户拍板「先只对确定安全的类别真删」，见 docs/retention.md）：

- ``REAL_DELETE_WHITELIST`` —— 唯一可能被真删的类别，显式写死在代码常量里：
    * ``alert_records``              告警记录（DB，既有 180d 行为）
    * ``session_workspace_residue``  会话临时文件/工作区残留（/tmp/cds-sandbox*
      等固定根下、整棵子树 mtime 都过期的孤儿目录）
    * ``dev_debug_artifacts``        过期的开发调试产物（供数明文暂存
      /tmp/cds-sandbox/data-load、OCR 流水线工作区 /tmp/cds-pipeline）
- ``NEVER_DELETE_CATEGORIES`` —— 代码层硬拒，**不是配置开关**：
    * ``audit_logs`` / ``merkle_leaves`` —— 审计日志与 Merkle 存证永久保留
      （retention 0 = 永远；export-and-archive only），这是不可被任何配置翻转
      的铁律；``delivery_bundles``（交付物 bundle）与 ``data_resources``
      （原始数据资源）同理。守卫在 ``_require_deletable``：即便运维把
      ``AUDIT_LOG_RETENTION_DAYS`` 等改成正数，执行器也会在发出任何
      SELECT/DELETE 之前 fail-closed。

开关语义：
- ``JANITOR_ENABLED``（默认 false，生产默认关闭）：真删主开关。文件系统类
  别（含其 dry-run）只有它为 true 才被处理；会话清理循环要求它与
  ``RETENTION_JANITOR_ENABLED``（兼容副闸，默认 true）同时为 true 才调度。
- ``JANITOR_CATEGORIES``：白名单子集配置，只能从 ``REAL_DELETE_WHITELIST``
  里选；空 = 全部白名单类别；选集外（含受保护类别）= 启动报错 fail-closed
  （config.validate_security_config 调 parse_janitor_categories）。
- ``RETENTION_JANITOR_DRY_RUN`` 保持默认 true：dry-run 仍是默认行为。

每个周期无论是否删除都写审计记录（``retention.purge``）；受保护类别被尝试
处理时该记录里带 ``protected_refusals``，结果值记 -1（拒删，永不记正数）。
"""
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_ADVISORY_LOCK_KEY = "cds_retention_janitor"

# ── 2026-10-30 拍板：白名单/硬拒类别是代码常量，配置只能收窄不能扩张 ──────────
REAL_DELETE_WHITELIST: frozenset[str] = frozenset(
    {
        "alert_records",
        "session_workspace_residue",
        "dev_debug_artifacts",
    }
)
NEVER_DELETE_CATEGORIES: frozenset[str] = frozenset(
    {
        "audit_logs",
        "merkle_leaves",
        "delivery_bundles",
        "data_resources",
    }
)

# 每个文件系统类别的固定根目录：写死在代码里（不从配置来），否则 janitor
# 会变成「按配置删任意目录」的漏洞。测试通过 monkeypatch 本常量做隔离。
_FS_ROOTS: dict[str, tuple[str, ...]] = {
    "session_workspace_residue": (
        "/tmp/cds-sandbox",     # L3 会话工作区（含 jupyter root）
        "/tmp/cds-sandbox-l0",  # L0 会话工作区
        "/tmp/cds-firecracker",  # firecracker 会话工作区
    ),
    "dev_debug_artifacts": (
        "/tmp/cds-sandbox/data-load",  # dev_sandbox 供数明文暂存（A52/A54）
        "/tmp/cds-pipeline",           # unstructured_pipeline 任务工作区
    ),
}
# residue 扫描跳过的顶层目录名：data-load 归 dev_debug_artifacts 类别所有；
# 任何名字含 audit 的目录一律不碰（纵深防御：即使有人往工作区根写审计痕迹，
# janitor 也绝不删）。
_RESIDUE_SKIP_NAMES = frozenset({"data-load"})


class ProtectedCategoryError(RuntimeError):
    """真删尝试落在受保护/白名单外类别上 —— 代码级硬守卫，不可配置翻转。"""


def _require_deletable(category: str) -> None:
    """执行器硬守卫：白名单外一律拒删，受保护类别给出可读的 fail-closed 报错。

    审计日志/Merkle 存证/交付物 bundle/原始数据资源的「永久保留」铁律落在此处
    ——它先于任何 SELECT/DELETE 执行，且不在任何配置开关的控制之下。
    """
    if category in NEVER_DELETE_CATEGORIES:
        raise ProtectedCategoryError(
            f"REFUSED: {category} is a never-delete category — audit logs, "
            "Merkle leaves, delivery bundles and raw data resources are "
            "retained forever (retention 0 = forever; export-and-archive "
            "only). This guard lives in code; no configuration can flip it."
        )
    if category not in REAL_DELETE_WHITELIST:
        raise ProtectedCategoryError(
            f"REFUSED: {category!r} is not in the real-delete whitelist "
            f"{sorted(REAL_DELETE_WHITELIST)}"
        )


def parse_janitor_categories(raw: str) -> frozenset[str]:
    """Parse CDS_JANITOR_CATEGORIES: empty = full whitelist; anything outside
    REAL_DELETE_WHITELIST (protected categories included) raises ValueError —
    the config-time twin of ``_require_deletable`` (fail-closed at boot via
    config.validate_security_config, and defensively re-checked in purge_cycle).
    """
    tokens = [tok.strip() for tok in (raw or "").split(",") if tok.strip()]
    if not tokens:
        return REAL_DELETE_WHITELIST
    picked = frozenset(tokens)
    outside = sorted(picked - REAL_DELETE_WHITELIST)
    if outside:
        raise ValueError(
            f"JANITOR_CATEGORIES contains categories outside the code "
            f"whitelist: {outside}. Allowed = {sorted(REAL_DELETE_WHITELIST)}. "
            f"Protected {sorted(NEVER_DELETE_CATEGORIES)} can never be deleted "
            "(retention 0 = forever; export-and-archive only)."
        )
    return picked


# ── 文件系统类别 helpers ────────────────────────────────────────────────────
def _dir_subtree_stale(path: Path, cutoff_epoch: float) -> bool:
    """保守判定一棵目录树整体过期：自身与所有子目录的 lstat mtime 都早于
    cutoff。写文件会刷新所在目录的 mtime，所以活跃会话不可能被误判；无法读取
    （权限/竞态）一律按「不过期」处理——宁可留残留，不可误删。"""
    try:
        if path.lstat().st_mtime >= cutoff_epoch:
            return False
        for child in path.iterdir():
            if child.is_dir(follow_symlinks=False) and not _dir_subtree_stale(child, cutoff_epoch):
                return False
        return True
    except OSError:
        return False


def _iter_stale_dirs(directory: Path, cutoff_epoch: float):
    """产出 directory 内可删的过期目录子树：整棵过期则整体产出，否则下探一层
    （tenant 目录 mtime 新但里面某个 session 目录是残留的常见形态）。"""
    if _dir_subtree_stale(directory, cutoff_epoch):
        yield directory
        return
    try:
        children = sorted(directory.iterdir())
    except OSError:
        return
    for child in children:
        try:
            if child.is_symlink() or not child.is_dir(follow_symlinks=False):
                continue  # 只删目录；符号链接永不穿越、也永不代替其目标被删
        except OSError:
            continue
        yield from _iter_stale_dirs(child, cutoff_epoch)


def _iter_fs_deletables(category: str, cutoff_epoch: float):
    for root_s in _FS_ROOTS.get(category, ()):
        root = Path(root_s)
        try:
            if root.is_symlink():
                continue
            resolved_root = root.resolve()
        except OSError:
            continue
        if not resolved_root.is_dir():
            continue
        try:
            children = sorted(resolved_root.iterdir())
        except OSError:
            continue
        for child in children:
            try:
                if child.is_symlink() or not child.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if category == "session_workspace_residue" and (
                child.name in _RESIDUE_SKIP_NAMES or "audit" in child.name.lower()
            ):
                continue
            yield from _iter_stale_dirs(child, cutoff_epoch)


def _purge_fs_category(category: str, cutoff: datetime, batch: int, dry_run: bool) -> int:
    """Delete whole stale directory subtrees under the category's fixed roots.

    Bounded by ``batch`` directories per cycle (the rest is picked up next
    sweep). dry-run counts candidates and removes nothing. Returns the number
    of directories actually removed (0 in dry-run).
    """
    cutoff_epoch = cutoff.timestamp()
    removed = 0
    candidates = 0
    for path in _iter_fs_deletables(category, cutoff_epoch):
        candidates += 1
        if dry_run:
            continue
        if removed >= batch:
            break
        try:
            shutil.rmtree(path)
            removed += 1
        except OSError as e:
            logger.warning("[RetentionJanitor] fs delete failed for %s: %s", path, e)
    if dry_run:
        logger.info(
            "[RetentionJanitor] DRY-RUN %s: %d stale dir subtrees older than %ds would be removed",
            category, candidates, int(cutoff_epoch),
        )
    return removed


# ── DB 类别（既有行为，A130 钉住上报口径与 savepoint 隔离） ─────────────────
async def _purge_table(db: AsyncSession, category: str, model, batch: int, cutoff: datetime) -> int:
    """Delete rows older than the cutoff in id batches (SQLite has no
    DELETE ... LIMIT, so the id set is selected first).

    A130: the reported number is what the DELETE actually removed
    (``rowcount``), not how many ids this pass happened to select — another
    replica may already have removed some of them, and docs/retention.md has
    operators reconcile this figure.

    2026-10-30: the whitelist hard guard runs here first — before any SELECT —
    so a protected table can never be purged regardless of configuration.
    """
    _require_deletable(category)
    total = 0
    while True:
        id_rows = await db.execute(select(model.id).where(model.created_at < cutoff).limit(batch))
        ids = [row[0] for row in id_rows.all()]
        if not ids:
            break
        result = await db.execute(delete(model).where(model.id.in_(ids)))
        deleted = result.rowcount
        total += len(ids) if deleted < 0 else deleted
        if len(ids) < batch:
            break
    return total


async def purge_cycle(db: AsyncSession) -> dict[str, int]:
    """Run one retention pass over all configured categories.

    Returns {category: removed_count}. Conventions:
      * dry-run (default): removed stays 0 and the candidate count is logged;
      * a category that fails is reported as -1 and isolated in its own
        SAVEPOINT (A130: without it, one failed statement aborts the whole
        PostgreSQL transaction — 25P02 — and the remaining tables, the
        advisory unlock and the caller's commit all cascade);
      * a protected (never-delete) category is reported as -1 forever and
        every attempt is recorded in the audit record's
        ``protected_refusals`` — a positive number for one is impossible.

    Filesystem categories are processed only when the real-delete master
    switch ``JANITOR_ENABLED`` is true, so an un-armed deployment never even
    walks /tmp (and direct callers such as legacy tests stay hermetic).
    """
    from app.core.config import get_settings

    settings = get_settings()
    now = datetime.now(timezone.utc)
    active = parse_janitor_categories(settings.JANITOR_CATEGORIES)
    plan = [
        # (category, db_module, db_class, retention_days)
        ("audit_logs", "app.models.audit_log", "AuditLog", settings.AUDIT_LOG_RETENTION_DAYS),
        ("alert_records", "app.models.alert", "AlertRecord", settings.ALERT_RETENTION_DAYS),
        ("merkle_leaves", "app.models.merkle_leaf", "MerkleLeaf", settings.MERKLE_LEAF_RETENTION_DAYS),
        ("session_workspace_residue", None, None, settings.WORKSPACE_RESIDUE_RETENTION_DAYS),
        ("dev_debug_artifacts", None, None, settings.DEV_ARTIFACT_RETENTION_DAYS),
    ]

    results: dict[str, int] = {}
    refused: list[str] = []
    lock_acquired = False
    if db.bind.dialect.name == "postgresql":
        lock_result = await db.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": _ADVISORY_LOCK_KEY}
        )
        lock_acquired = bool(lock_result.scalar())
        if not lock_acquired:
            logger.info("[RetentionJanitor] Another replica holds the janitor lock — skipping cycle")
            return {"lock": 0}

    try:
        for category, module_name, class_name, retention_days in plan:
            try:
                if retention_days <= 0:
                    results[category] = 0
                    continue
                if category in REAL_DELETE_WHITELIST and category not in active:
                    results[category] = 0
                    continue
                if module_name is None:
                    # 文件系统类别：主开关未武装就整体跳过（连扫描都不做）
                    if not settings.JANITOR_ENABLED:
                        results[category] = 0
                        continue
                    cutoff = now - timedelta(days=retention_days)
                    removed = _purge_fs_category(
                        category, cutoff, settings.RETENTION_JANITOR_BATCH,
                        settings.RETENTION_JANITOR_DRY_RUN,
                    )
                    results[category] = removed
                else:
                    cutoff = now - timedelta(days=retention_days)
                    module = __import__(module_name, fromlist=[class_name])
                    model = getattr(module, class_name)
                    async with db.begin_nested():
                        # SQLAlchemy 惰性发 SAVEPOINT：守卫若在语句之前抛错，
                        # savepoint 根本不会物化，A130 钉的 ROLLBACK TO SAVEPOINT
                        # 隔离语句序列就消失了。用不碰任何表的 SELECT 1 先物化
                        # savepoint，再上守卫——受保护表依然零 SELECT/DELETE。
                        await db.execute(text("SELECT 1"))
                        # 硬守卫先行：audit_logs / merkle_leaves 即便被（错误地）
                        # 配置了正的保留天数，也在对该表的任何 SELECT/DELETE 之前拒删。
                        _require_deletable(category)
                        if settings.RETENTION_JANITOR_DRY_RUN:
                            count_result = await db.execute(
                                select(func.count()).select_from(model).where(model.created_at < cutoff)
                            )
                            candidates = count_result.scalar() or 0
                            removed = 0
                            logger.info(
                                "[RetentionJanitor] DRY-RUN %s: %d rows older than %dd would be purged",
                                category, candidates, retention_days,
                            )
                        else:
                            removed = await _purge_table(
                                db, category, model, settings.RETENTION_JANITOR_BATCH, cutoff
                            )
                    results[category] = removed
                if results.get(category):
                    logger.info(
                        "[RetentionJanitor] Purged %d units from %s (retention=%dd)",
                        results[category], category, retention_days,
                    )
            except ProtectedCategoryError as e:
                logger.warning("[RetentionJanitor] %s", e)
                results[category] = -1
                refused.append(category)
            except Exception as e:
                logger.warning("[RetentionJanitor] Category %s purge failed: %s", category, e)
                results[category] = -1
    finally:
        if lock_acquired:
            await db.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": _ADVISORY_LOCK_KEY})

    try:
        from app.services.audit_service import audit_service

        await audit_service.log(
            db,
            action="retention.purge",
            resource_type="retention",
            detail={
                "dry_run": settings.RETENTION_JANITOR_DRY_RUN,
                "results": results,
                "active_categories": sorted(active),
                "protected_refusals": refused,
                "ran_at": now.isoformat(),
            },
        )
    except Exception as e:
        logger.warning("[RetentionJanitor] Purge audit write failed: %s", e)
    return results
