"""SS-19 开发环网关：把 JupyterLab 挂到沙箱会话的工作区上。

诚实口径（项目铁律「禁伪造 / 诚实降级」）：
- JupyterLab 跑在一个独立容器里 = **容器隔离**，不是 CubeSandbox MicroVM。会话的真实
  隔离等级只由 ``session.sandbox_level`` 决定，前端必须原样标出来，不得暗示 MicroVM。
- 产物出口一条没变：笔记本只能往 ``<会话工作区>/files/`` 里写，成果包打包采集的就是
  这一个目录，仍要过 T5 出口网关审查 + 水印；``input/`` ``tmp/`` 不在采集范围。
- 上游是**单一共享**的 Jupyter 服务（本机一个容器）。因此本模块按「会话前缀」强制
  限制所有带路径的上游 API（tree / contents / notebooks / edit / terminals / kernels），
  并且只放行经本代理为该会话创建过的 kernel id；容器镜像里 terminals 已关闭。
  已知残留（如实记录，不掩饰）：笔记本里的代码能读自己进程的环境变量，从中拿到共享
  Jupyter 的 token。该 token 只在 compose 内网可达（未映射宿主端口），且我们这一道
  代理仍按前缀限制；另外 L3 那一路共享卷里装着**本机全部会话的工作区**（含各自的
  files/input 供数），前缀限制只约束 URL 层——kernel 里的代码拿到 token 后直接走
  文件系统就能看见这些目录，不受本代理约束。因此开发环只适用于单租户开发道；
  多租户生产必须改成每会话独占 Jupyter 实例（k8s 路径）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jwt as pyjwt

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# 前端与反代共用的公开前缀（与容器里 ServerApp.base_url 逐字一致）。
PUBLIC_PATH = "/api/jupyter"
TICKET_TTL_SECONDS = 30
COOKIE_NAME = "cds_jup_ctx"

# 会话等级 → (沙箱适自己看到的工作区根, 该卷在 Jupyter root_dir 下的相对前缀)。
# 两侧路径必须与 docker-compose.yml 里 cds-api / cds-jupyter 的卷挂载逐字对应：
# L3 那一卷在两个容器里都挂 /tmp/cds-sandbox；L0 那一卷在 api 里是
# /tmp/cds-sandbox-l0（适配器默认值），在 jupyter 里挂到 root 下的 l0/ 子目录，
# 这样 root_dir 以内只有沙箱工作区，不含 /tmp 里其它东西（cds-uploads、
# data-load 里的明文供数）。
_LEVEL_LAYOUT = {
    "L0": ("/tmp/cds-sandbox-l0", "l0"),
    "l0": ("/tmp/cds-sandbox-l0", "l0"),
    "L3": ("/tmp/cds-sandbox", ""),
    "l3": ("/tmp/cds-sandbox", ""),
}

JUPYTER_ROOT_DIR = "/tmp/cds-sandbox"


class JupyterUnavailable(RuntimeError):
    """开发环不可用（未配置 / 会话无工作区 / 该等级没有 Jupyter 视图）。"""


def configured() -> bool:
    return bool((get_settings().JUPYTER_PROXY_BASE_URL or "").strip())


def upstream_base() -> str:
    base = (get_settings().JUPYTER_PROXY_BASE_URL or "").rstrip("/")
    if not base:
        raise JupyterUnavailable("开发环未配置：请设置 CDS_JUPYTER_PROXY_BASE_URL 并启动 cds-jupyter 服务")
    return base


def upstream_token() -> str:
    return get_settings().JUPYTER_UPSTREAM_TOKEN or ""


def layout(level: str) -> tuple[str, str]:
    try:
        return _LEVEL_LAYOUT[level]
    except KeyError:
        raise JupyterUnavailable(
            f"开发环暂不支持沙箱等级 {level!r}（已支持：{', '.join(sorted({k for k in _LEVEL_LAYOUT if k.isupper()}))}）"
        ) from None


def session_files_dir(session: Any) -> Path:
    """会话在宿主上的产物目录（与成果包采集读的是同一个目录）。"""
    from app.services.sandbox_runtime import sandbox_runtime

    if not session.container_id:
        raise JupyterUnavailable("会话尚未分配沙箱容器，无法打开开发环")
    layout(session.sandbox_level)
    workspace = sandbox_runtime.get_workspace(session.container_id, session.sandbox_level)
    if not workspace:
        raise JupyterUnavailable(f"会话 {session.id} 的工作区不在本机（沙箱容器可能已被回收）")
    files_dir = Path(workspace) / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    return files_dir


def url_prefix_for(session: Any) -> str:
    """该会话在 Jupyter root_dir 下对用户暴露的目录（URL 用，带尾斜杠）。

    暴露的是**产物目录 ``<工作区>/files/``**，不是工作区根：笔记本落盘在 files/、
    供数注入在 ``files/input/``、成果包也只采集 ``files/``（见模块 docstring）。
    指到工作区根会让 notebook 单元格里相对路径 ``input/`` 指到工作区那个永远为空的
    ``input/``（SS-19 e2e 实测：面板写“已注入 1 个文件”，kernel 里 `input/*.csv` 为空），
    并且会把 ``.security_config.json`` 这类会话配置文件一并暴到文件树里。
    """
    from app.services.sandbox_runtime import sandbox_runtime

    host_root, url_root = layout(session.sandbox_level)
    workspace = sandbox_runtime.get_workspace(session.container_id, session.sandbox_level)
    if not workspace:
        raise JupyterUnavailable(f"会话 {session.id} 的工作区不在本机")
    rel = Path(str(workspace)).resolve().relative_to(Path(host_root).resolve())
    joined = "/".join([p for p in (url_root, str(rel).replace("\\", "/"), "files") if p])
    return f"{joined}/"


def seed_notebook(files_dir: Path, title: str, content: dict | None) -> str:
    """把平台侧笔记本落盘成 .ipynb（幂等），返回相对 files_dir 的文件名。"""
    name = _notebook_filename(title)
    target = files_dir / name
    if not target.exists():
        doc = content if isinstance(content, dict) and content.get("cells") else _blank_notebook(title)
        doc.setdefault("metadata", {})["title"] = title
        target.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        target.chmod(0o666)
    return name


def _notebook_filename(title: str) -> str:
    from app.core.path_security import sanitize_filename

    safe = sanitize_filename(title) or "notebook"
    return safe if safe.endswith(".ipynb") else f"{safe}.ipynb"


def _blank_notebook(title: str) -> dict:
    return {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    f"# {title}\n",
                    "\n",
                    "本笔记本跑在密态沙箱的**在线开发环**里（容器隔离的 JupyterLab，非 MicroVM）。\n",
                    "- 当前目录就是本会话的产物目录：写在这里的文件才会被成果包采集。\n",
                    "- `input/` 是合约授权数据（只读语义），`output/` 建议放最终交付物。\n",
                    "- 离开沙箱前每个文件都要过 T5 出口网关审查；空产物会被如实标成真空，不会当成通过。\n",
                ],
            },
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["import os, pandas as pd\n", "os.getcwd()"]},
        ],
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


async def inject_contract_data(db, files_dir: Path, contract: Any, resource_ids: list[str] | None = None) -> dict:
    """把合约授权的数据产品解密落到会话工作区的 input/ 下（与 dev_sandbox 同口径）。

    resource_ids 是开发任务的「已选择数据」子集（图 2-2）：None = 全部授权供数，
    空列表 = 不注入任何供数（两态在审计里必须区分开，不能把"未选"当作"全选"）。

    返回 {"written": [文件名], "errors": [原因], "subset": 子集或 None, "excluded": 被子集排除数}。
    空 input/ 必须能说清为什么空：SS-19 e2e 里上传原件因未入卷而丢失，库里仍是 ready，
    注入只会留一行日志，开发者在开发环看到的只是一个“看起来正常”的空目录。
    """
    from sqlalchemy import select

    from app.models.data_product import DataProduct
    from app.models.data_resource import DataResource, ResourceStatus
    from app.services.storage_service import storage_service

    written: list[str] = []
    errors: list[str] = []
    excluded = 0
    subset = None if resource_ids is None else sorted({str(x) for x in resource_ids})
    input_dir = files_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    for pid in list(contract.product_ids or []):
        pres = await db.execute(select(DataProduct).where(DataProduct.id == uuid.UUID(str(pid))))
        product = pres.scalar_one_or_none()
        if not product:
            errors.append(f"数据产品 {pid} 不存在或已删除")
            continue
        if not product.resource_id:
            errors.append(f"数据产品「{product.name}」未关联数据资源")
            continue
        if subset is not None and str(product.resource_id) not in subset:
            excluded += 1
            continue
        rres = await db.execute(select(DataResource).where(DataResource.id == product.resource_id))
        resource = rres.scalar_one_or_none()
        if not resource:
            errors.append(f"数据产品「{product.name}」关联的数据资源 {product.resource_id} 不存在")
            continue
        if resource.status != ResourceStatus.READY.value or not resource.storage_path:
            errors.append(f"数据资源「{resource.name}」当前状态={resource.status}，无可读供数文件")
            continue
        target = input_dir / f"{resource.id}.{resource.format or 'csv'}"
        if target.exists() and target.stat().st_size == (resource.file_size_bytes or -1):
            written.append(target.name)
            continue
        try:
            data = storage_service.download_decrypted(resource.storage_path)
        except Exception as e:
            # 供数失败必须看得见：宁缺不造，不能给开发环一个看起来正常的空目录。
            logger.warning("[jupyter] data inject failed for resource %s: %s", resource.id, e)
            errors.append(f"数据资源「{resource.name}」供数读取失败：{type(e).__name__}: {e}")
            continue
        target.write_bytes(data)
        target.chmod(0o666)
        written.append(target.name)
    return {"written": written, "errors": errors, "subset": subset, "excluded": excluded}


# ── 一次性票据：iframe 带不了 Authorization 头，只能用短时效一次性票据换 Cookie ──

async def mint_ticket(user_id: str, role: str, session_id: str, notebook: str) -> dict:
    settings = get_settings()
    jti = uuid.uuid4().hex
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "sub": user_id,
        "role": role,
        "sid": session_id,
        "nb": notebook,
        "jti": jti,
        "type": "jupyter-ticket",
        "iat": now,
        "exp": now + TICKET_TTL_SECONDS,
    }
    token = pyjwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    try:
        from app.core.redis import get_redis

        redis = await get_redis()
        await redis.set(f"cds:jup-ticket:{jti}", "1", ex=TICKET_TTL_SECONDS)
    except Exception as e:
        # 没有 Redis 就没法保证一次性 —— 宁可不发票据（与 ws-ticket 同一口径）。
        logger.warning("[jupyter] ticket mint refused (Redis unavailable): %s", e)
        raise JupyterUnavailable("开发环票据服务不可用（Redis 未就绪）") from e
    return {"ticket": token, "ttl": TICKET_TTL_SECONDS}


async def consume_ticket(ticket: str) -> dict | None:
    settings = get_settings()
    try:
        payload = pyjwt.decode(ticket, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except pyjwt.PyJWTError:
        return None
    if payload.get("type") != "jupyter-ticket":
        return None
    jti = payload.get("jti")
    if not jti:
        return None
    try:
        from app.core.redis import get_redis

        redis = await get_redis()
        # GETDEL 语义：抢到才算用过；抢不到说明已被使用（或已过期）。
        got = await redis.delete(f"cds:jup-ticket:{jti}")
        if not got:
            return None
    except Exception as e:
        logger.warning("[jupyter] ticket consume blocked (Redis unavailable): %s", e)
        return None
    return payload


def sign_cookie(value: str) -> str:
    key = get_settings().JWT_SECRET_KEY.encode()
    return f"{value}.{hmac.new(key, value.encode(), hashlib.sha256).hexdigest()}"


def read_cookie(raw: str | None) -> dict | None:
    if not raw or "." not in raw:
        return None
    value, _, digest = raw.rpartition(".")
    expect = hmac.new(get_settings().JWT_SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, expect):
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    if int(payload.get("exp", 0)) < int(datetime.now(timezone.utc).timestamp()):
        return None
    return payload


# kernel id → 会话前缀的归属表（共享 Jupyter 下的隔离补偿）。
# CR MAJOR-6：真值放 Redis（与票据同一条连接），跨 API 重启/多副本一致；
# 进程内 dict 只剩「Redis 不可达」时的降级层（单 worker 旧语义，行为不低于旧版）。
# Redis 可达但查无此 kernel → fail-closed（不给未登记 kernel 放行）。
_kernel_owner_local: dict[str, str] = {}
_OWNER_TTL_SECONDS = 86400


def _owner_key(kernel_id: str) -> str:
    return f"cds:jup-kernel:{kernel_id}"


async def claim_kernel(kernel_id: str, prefix: str) -> None:
    _kernel_owner_local[kernel_id] = prefix
    try:
        from app.core.redis import get_redis

        redis = await get_redis()
        await redis.set(_owner_key(kernel_id), prefix, ex=_OWNER_TTL_SECONDS)
    except Exception as e:  # noqa: BLE001 — 降级层已声明，不阻断建 kernel
        logger.warning("[jupyter] kernel 归属写入 Redis 失败，仅存进程内: %s", e)


async def owns_kernel(kernel_id: str, prefix: str) -> bool:
    try:
        from app.core.redis import get_redis

        redis = await get_redis()
        owner = await redis.get(_owner_key(kernel_id))
        if owner is None:
            return False
        if owner != prefix:
            return False
        # 活跃即续期：长会话的 kernel 不因 TTL 静默过期而被 403。
        await redis.expire(_owner_key(kernel_id), _OWNER_TTL_SECONDS)
        return True
    except ConnectionError:
        return _kernel_owner_local.get(kernel_id) == prefix
    except Exception as e:  # noqa: BLE001
        logger.warning("[jupyter] kernel 归属读 Redis 失败，回落进程内: %s", e)
        return _kernel_owner_local.get(kernel_id) == prefix


async def release_kernel(kernel_id: str) -> None:
    _kernel_owner_local.pop(kernel_id, None)
    try:
        from app.core.redis import get_redis

        redis = await get_redis()
        await redis.delete(_owner_key(kernel_id))
    except Exception:  # noqa: BLE001 — 删除失败靠 TTL 自然过期兼容
        pass
