"""SS-19 开发环反代：把 JupyterLab 挂在 `/api/jupyter` 下，鉴权用我们自己的身份。

浏览器 iframe 带不了 Authorization 头，所以走一次性票据 → 签名 Cookie（见
``app.services.jupyter_gateway``）。上游令牌由本模块注入，不下发浏览器。

前缀强制：一个 Jupyter 服务承载多个会话，所有带路径的上游 API 都必须落在本会话
的目录前缀内；kernel 只放行经本代理为该会话创建的 id；terminals / shutdown /
metrics 一律 403。这是共享服务下的隔离补偿，不是完整多租户隔离（生产按会话独占实例）。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, WebSocket
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_business_user
from app.models.user import User
from app.services import jupyter_gateway as jg
from app.services.audit_service import audit_service

logger = logging.getLogger(__name__)

router = APIRouter()

COOKIE_TTL_SECONDS = 8 * 3600
# 与会话数据无关、可以放行的上游路径（静态资源、kernel 规格、i18n 等）。
# CR MAJOR-3：workspaces 不再放行——它存的是 JupyterLab 布局态（打开的文件路径、
# kernel id），列表无作用域会漏出别人的会话目录名；拿不到时 Lab 回落内存布局，
# 不影响开发环功能。
_OPEN_EXACT = {"", "lab", "doc", "tree", "login", "api/status", "api/specs", "api/config",
               "api/session/properties", "api/translations", "api/me", "api/csrf", "manifest.json",
               "api/specs/kernelspecs"}
_OPEN_PREFIX = ("static/", "api/translations/", "api/specs/kernelspecs", "api/specs/kernels/")
_DENY_PREFIX = ("api/terminals", "api/shutdown", "api/metrics", "api/api.yaml", "api/proxy")


def _prefix_ok(prefix: str, path: str) -> bool:
    """path 是否落在会话前缀内（前缀带尾斜杠；空 path 视为越界）。

    先自己过一遍目录语义：带 .. 直接拒，不让 Jupyter 去消化相对路径——
    否则 `files/../s-2/x.csv` 这种写法就绕过了前缀。
    """
    if not path:
        return False
    parts = path.lstrip("/").replace("\\", "/").split("/")
    if any(p == ".." for p in parts):
        return False
    normalized = "/".join(p for p in parts if p not in ("", "."))
    root = prefix.rstrip("/")
    return bool(root) and (normalized == root or normalized.startswith(prefix))


def _check(path: str, prefix: str) -> None:
    if any(path.startswith(p) for p in _DENY_PREFIX):
        raise HTTPException(status_code=403, detail="开发环不开放该上游接口")
    if path in _OPEN_EXACT or any(path.startswith(p) for p in _OPEN_PREFIX):
        return
    if path == "api/contents":
        # 上游实测：这个 Jupyter 镜像吐掉 `?path=`，带不带都返回**根目录**清单
        # （会漏出别人的会话目录名）。裸形一律 403，列目录只认 `api/contents/<本会话前缀>/...`。
        raise HTTPException(status_code=403, detail="列目录请走 api/contents/<本会话目录>")
    if path.startswith("api/contents/"):
        if not _prefix_ok(prefix, path[len("api/contents/"):]):
            raise HTTPException(status_code=403, detail="越出本会话目录")
        return
    if path.startswith("api/kernels"):
        return  # kernel 归属在 _guard_kernel 里查
    if path.startswith("api/sessions"):
        return  # 列表在响应侧按前缀过滤
    if path.startswith(("tree/", "notebooks/", "edit/", "view/")):
        if not _prefix_ok(prefix, path.split("/", 1)[1]):
            raise HTTPException(status_code=403, detail="越出本会话目录")
        return
    if path.startswith("lab/tree/"):
        # JupyterLab 深链：同样锁在本会话前缀里。
        if not _prefix_ok(prefix, path[len("lab/tree/"):]):
            raise HTTPException(status_code=403, detail="越出本会话目录")
        return
    raise HTTPException(status_code=403, detail=f"开发环未开放该路径: {path.split('/')[0]}")


async def _guard_kernel(path: str, prefix: str) -> None:
    # 只对 `api/kernels/<id>/...` 这一类带 kernel id 的路径生效；
    # 对 contents/tree/static 等路径乱抓 parts[2] 会把整个开发环锁死。
    if not path.startswith("api/kernels/"):
        return
    kernel_id = path.split("/")[2]
    if not await jg.owns_kernel(kernel_id, prefix):
        raise HTTPException(status_code=403, detail="该 kernel 不属于本会话")


def _forward_headers(request: Request) -> dict[str, str]:
    hop = {"host", "connection", "cookie", "authorization", "content-length", "transfer-encoding", "keep-alive", "upgrade", "sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions"}
    headers = {k: v for k, v in request.headers.items() if k.lower() not in hop}
    token = jg.upstream_token()
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def _upstream_url(path: str, query: str) -> str:
    params = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k != "token"]
    # PUBLIC_PATH 自带前导斜杠：多一个 `/` 就会变成 `GET //api/jupyter/...`，
    # Tornado 按 base_url 匹配不上，所有转发请求都会空转成 404。
    url = f"{jg.upstream_base()}{jg.PUBLIC_PATH}/{path}"
    if params:
        url += "?" + urlencode(params)
    return url


@router.post("/api/jupyter/tickets")
async def create_ticket(
    notebook_id: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    """为笔记本的开发环发一张一次性票据（iframe 用它换 Cookie）。"""
    from app.api.notebooks import _ensure_dev_session, _load_notebook

    if not jg.configured():
        raise HTTPException(status_code=503, detail="开发环未配置：CDS_JUPYTER_PROXY_BASE_URL 为空")
    import uuid as _uuid

    try:
        notebook = await _load_notebook(db, _uuid.UUID(notebook_id), current_user)
    except ValueError:
        raise HTTPException(status_code=404, detail="Notebook not found") from None

    try:
        session, _ = await _ensure_dev_session(db, notebook, current_user)
        prefix = jg.url_prefix_for(session)
        files_dir = jg.session_files_dir(session)
        nb_name = jg.seed_notebook(files_dir, notebook.title, notebook.content)
        injected: list[str] = []
        inject_errors: list[str] = []
        inject_subset: list[str] | None = None
        inject_excluded = 0
        if notebook.contract_id:
            contract = await _load_contract(db, notebook.contract_id)
            if contract is None:
                raise HTTPException(status_code=409, detail="笔记本关联的合约已不存在")
            inj = await jg.inject_contract_data(db, files_dir, contract, notebook.data_resource_ids)
            injected, inject_errors = inj["written"], inj["errors"]
            inject_subset, inject_excluded = inj["subset"], inj["excluded"]
    except HTTPException:
        raise
    except jg.JupyterUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from None
    except (ValueError, AttributeError) as e:
        logger.warning("[jupyter] prepare failed for notebook %s: %s", notebook_id, e)
        raise HTTPException(status_code=503, detail=f"开发环准备失败：{e}") from e

    try:
        ticket = await jg.mint_ticket(str(current_user.id), current_user.role, str(session.id), f"{prefix}{nb_name}")
    except jg.JupyterUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from None

    await audit_service.log(
        db, action="notebook.jupyter_ticket", resource_type="notebook",
        user_id=current_user.id, resource_id=str(notebook.id),
        detail={"session_id": str(session.id), "sandbox_level": session.sandbox_level,
                "prefix": prefix, "injected": injected, "inject_errors": inject_errors,
                "inject_subset": inject_subset, "inject_excluded": inject_excluded},
    )
    return {
        "url": f"{jg.PUBLIC_PATH}/login?ticket={ticket['ticket']}",
        "ttl": ticket["ttl"],
        "sandbox_level": session.sandbox_level,
        "session_id": str(session.id),
        "notebook_path": f"{prefix}{nb_name}",
        "injected": injected,
        # 供数为什么没进来，开发环里必须看得见（宁缺不造）。
        "inject_errors": inject_errors,
    }


async def _load_contract(db: AsyncSession, contract_id):
    from sqlalchemy import select

    from app.models.contract import Contract

    res = await db.execute(select(Contract).where(Contract.id == contract_id))
    return res.scalar_one_or_none()


@router.get("/api/jupyter/login")
async def login(ticket: str = Query(...), db: AsyncSession = Depends(get_db)):
    payload = await jg.consume_ticket(ticket)
    if not payload:
        raise HTTPException(status_code=403, detail="开发环票据无效或已使用")
    from app.models.sandbox_session import SandboxSession
    from sqlalchemy import select

    session = (await db.execute(select(SandboxSession).where(SandboxSession.id == payload["sid"]))).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=410, detail="开发环会话已结束")
    try:
        prefix = jg.url_prefix_for(session)
    except jg.JupyterUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from None
    nb_name = str(payload.get("nb") or "").split("/")[-1]
    cookie = jg.sign_cookie(json.dumps({"u": payload["sub"], "sid": payload["sid"], "p": prefix,
                                        "exp": int(time.time()) + COOKIE_TTL_SECONDS}, separators=(",", ":")))
    # lab-only 镜像没有 classic 的 /notebooks 与 /tree handler（实测 404），
    # 深链只能用 JupyterLab 自己的 /lab/tree/<path>，中文与空格要转义。
    target = f"{jg.PUBLIC_PATH}/lab/tree/{quote(prefix + nb_name) if nb_name else quote(prefix)}"
    resp = RedirectResponse(url=target, status_code=302)
    resp.set_cookie(jg.COOKIE_NAME, cookie, max_age=COOKIE_TTL_SECONDS, httponly=True, samesite="lax", path=jg.PUBLIC_PATH)
    return resp


def _identity(request: Request) -> dict:
    ctx = jg.read_cookie(request.cookies.get(jg.COOKIE_NAME))
    if not ctx or not ctx.get("p"):
        raise HTTPException(status_code=401, detail="开发环未登录：请从任务页重新打开")
    return ctx


@router.websocket("/api/jupyter/{path:path}")
async def proxy_websocket(websocket: WebSocket, path: str):
    ctx = jg.read_cookie(websocket.cookies.get(jg.COOKIE_NAME))
    if not ctx or not ctx.get("p"):
        await websocket.close(code=4401)
        return
    prefix = ctx["p"]
    try:
        _check(path, prefix)
        await _guard_kernel(path, prefix)
    except HTTPException as e:
        await websocket.close(code=4403, reason=e.detail)
        return
    if not path.startswith("api/kernels/"):
        await websocket.close(code=4403, reason="not a kernel channel")
        return

    import asyncio

    from websockets.exceptions import WebSocketException
    from websockets.sync.client import connect as ws_connect

    token = jg.upstream_token()
    url = _upstream_url(path, str(websocket.query_params)).replace("http://", "ws://").replace("https://", "wss://")
    headers = [("Authorization", f"token {token}")] if token else []
    try:
        upstream = ws_connect(url, additional_headers=headers, open_timeout=15)
    except (WebSocketException, OSError) as e:
        logger.warning("[jupyter] upstream ws failed: %s", e)
        await websocket.close(code=1011)
        return
    await websocket.accept()

    async def pump_down():
        while True:
            msg = await asyncio.to_thread(upstream.recv)
            if isinstance(msg, (bytes, bytearray)):
                await websocket.send_bytes(msg)
            else:
                await websocket.send_text(msg)

    async def pump_up():
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await asyncio.to_thread(upstream.send, msg["bytes"])
            elif msg.get("text") is not None:
                await asyncio.to_thread(upstream.send, msg["text"])

    try:
        await asyncio.gather(pump_down(), pump_up())
    except Exception:
        pass
    finally:
        try:
            upstream.close()
        except Exception:
            pass
        await websocket.close()


@router.api_route("/api/jupyter/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def proxy(request: Request, path: str):
    if path == "tickets":
        raise HTTPException(status_code=405, detail="tickets 用 POST")
    ctx = _identity(request)
    prefix = ctx["p"]
    _check(path, prefix)
    await _guard_kernel(path, prefix)

    # CR MAJOR-4：`api/sessions/<id>` 单项 GET/DELETE 也要归属——列表侧已有前缀过滤，
    # 但拿别人的 session id 直接 GET/DELETE 会漏元数据/连带杀 kernel。先向上游取
    # 一次会话模型验前缀；不在前缀内一律 404（不承认存在）。
    if path.startswith("api/sessions/") and request.method in ("GET", "DELETE", "PATCH", "POST"):
        item_id = path.split("/")[2]
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False) as probe_client:
            probe = await probe_client.get(
                _upstream_url(f"api/sessions/{item_id}", ""),
                headers=_forward_headers(request),
            )
        if probe.status_code != 200:
            raise HTTPException(status_code=404, detail="会话不存在或不属于本任务")
        try:
            sess_model = probe.json()
        except (json.JSONDecodeError, ValueError):
            raise HTTPException(status_code=404, detail="会话不存在或不属于本任务") from None
        if not _prefix_ok(prefix, str(sess_model.get("path") or "")):
            raise HTTPException(status_code=404, detail="会话不存在或不属于本任务")

    body = None if request.method in ("GET", "HEAD", "DELETE", "OPTIONS") else await request.body()
    if path in ("api/kernels", "api/sessions") and request.method in ("POST", "PATCH"):
        # kernel/session 的 `path` 字段决了笔记本代码落盘与启动目录，必须锁在本会话前缀内。
        try:
            spec = json.loads(body or b"{}")
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="kernel/session 请求必须是 JSON") from None
        if not _prefix_ok(prefix, str(spec.get("path") or "")):
            raise HTTPException(status_code=403, detail="只能在开发任务自己的目录里开会话")

    client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0), follow_redirects=False)
    req = client.build_request(
        request.method,
        _upstream_url(path, str(request.query_params)),
        headers=_forward_headers(request),
        content=body,
    )
    try:
        resp = await client.send(req, stream=True)
    except httpx.HTTPError as e:
        await client.aclose()
        logger.warning("[jupyter] upstream request failed: %s", e)
        raise HTTPException(status_code=502, detail="开发环上游不可达（cds-jupyter 未就绪）") from e

    if path == "api/sessions" and request.method == "GET":
        payload = await resp.aread()
        await client.aclose()
        if resp.status_code != 200:
            return Response(content=payload, status_code=resp.status_code)
        try:
            items = json.loads(payload)
            items = [it for it in items if _prefix_ok(prefix, str(it.get("path") or ""))]
        except (json.JSONDecodeError, AttributeError):
            items = []
        return JSONResponse(items, status_code=200)

    if path == "api/kernels" and request.method == "GET":
        # CR MAJOR-2：列表按归属过滤（fail-closed：未登记/属别人的都不露）。
        payload = await resp.aread()
        await client.aclose()
        if resp.status_code != 200:
            return Response(content=payload, status_code=resp.status_code)
        try:
            items = json.loads(payload)
            mine = [it for it in items if await jg.owns_kernel(str(it.get("id") or ""), prefix)]
        except (json.JSONDecodeError, AttributeError, TypeError):
            mine = []
        return JSONResponse(mine, status_code=200)

    if path == "api/kernels" and request.method == "POST":
        payload = await resp.aread()
        await client.aclose()
        try:
            meta = json.loads(payload)
            if meta.get("id"):
                await jg.claim_kernel(str(meta["id"]), prefix)
        except json.JSONDecodeError:
            pass
        headers = {k: v for k, v in resp.headers.items() if k.lower() not in {"transfer-encoding", "content-length", "connection"}}
        return Response(content=payload, status_code=resp.status_code, headers=headers)

    if path.startswith("api/kernels/") and request.method == "DELETE":
        kernel_id = path.split("/")[2]
        await jg.release_kernel(kernel_id)

    headers = {k: v for k, v in resp.headers.items() if k.lower() not in {"transfer-encoding", "content-length", "connection", "content-security-policy"}}
    return StreamingResponse(_stream(resp, client), status_code=resp.status_code, headers=headers)


async def _stream(resp: httpx.Response, client: httpx.AsyncClient):
    try:
        async for chunk in resp.aiter_raw():
            yield chunk
    finally:
        await resp.aclose()
        await client.aclose()
