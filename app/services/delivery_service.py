"""成果交付服务 — 数据集打包 / OpenAPI 端点 / 结果推送（SS-15）。

推送签名使用 HMAC-SHA256 而非 SM3：接收方是外部系统，标准 HMAC 才能用各语言现成库
一行验证；平台内部审计与 Merkle 仍走 SM3，两套并存且各自如实命名，不互相冒充。
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.api_endpoint import ApiEndpoint, ApiEndpointStatus
from app.models.webhook import WebhookStatus, WebhookSubscription
from app.models.webhook_delivery import DeliveryStatus, WebhookDelivery

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-CDS-Signature-Sha256"
TIMESTAMP_HEADER = "X-CDS-Timestamp"
EVENT_ID_HEADER = "X-CDS-Event-Id"
DELIVERY_TIMEOUT_SECONDS = 10.0
MAX_CONSECUTIVE_FAILURES = 10
BACKOFF_BASE_SECONDS = 30
BACKOFF_CAP_SECONDS = 3600


class WebhookUrlRejected(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class EndpointUnavailable(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def sign_payload(secret: str, timestamp: str, body: str) -> str:
    return hmac.new(secret.encode("utf-8"), f"{timestamp}.{body}".encode("utf-8"), hashlib.sha256).hexdigest()


def verify_signature(secret: str, timestamp: str, body: str, signature: str) -> bool:
    return hmac.compare_digest(sign_payload(secret, timestamp, body), signature or "")


def backoff_delay(attempt: int) -> timedelta:
    seconds = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** max(0, attempt - 1)))
    return timedelta(seconds=seconds)


def validate_webhook_url(url: str, *, allow_private: bool | None = None) -> str:
    parsed = urlparse(url or "")
    settings = get_settings()
    allow = settings.WEBHOOK_ALLOW_PRIVATE_URLS if allow_private is None else allow_private
    if parsed.scheme not in ("https", "http"):
        raise WebhookUrlRejected("只支持 http/https 回调地址")
    if parsed.scheme == "http" and not allow:
        raise WebhookUrlRejected("生产环境回调地址必须使用 https")
    host = parsed.hostname
    if not host:
        raise WebhookUrlRejected("回调地址缺少主机名")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise WebhookUrlRejected(f"回调地址无法解析: {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not allow and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise WebhookUrlRejected("回调地址解析到内网/保留地址，已被 SSRF 防护拒绝")
    return parsed.geturl()


async def dispatch_event(
    db: AsyncSession,
    event_type: str,
    payload: dict[str, Any],
    *,
    contract_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> int:
    stmt = select(WebhookSubscription).where(
        WebhookSubscription.status == WebhookStatus.ACTIVE.value,
        WebhookSubscription.events.is_not(None),
    )
    if user_id is not None:
        stmt = stmt.where(WebhookSubscription.user_id == user_id)
    res = await db.execute(stmt)
    created = 0
    for sub in res.scalars().all():
        events = sub.events or []
        if event_type not in events:
            continue
        if sub.contract_id and contract_id and sub.contract_id != contract_id:
            continue
        db.add(WebhookDelivery(subscription_id=sub.id, event_type=event_type, event_id=uuid.uuid4().hex, payload=payload, status=DeliveryStatus.PENDING.value, next_retry_at=datetime.now(timezone.utc)))
        created += 1
    await db.flush()
    return created


async def deliver_once(db: AsyncSession, delivery: WebhookDelivery, subscription) -> WebhookDelivery:
    from app.services.task_code_security import decrypt_task_code

    secret = decrypt_task_code(subscription.secret_enc) if subscription.secret_enc else ""
    body = json.dumps({"event_id": delivery.event_id, "event_type": delivery.event_type, "data": delivery.payload}, ensure_ascii=False, default=str)
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    headers = {"Content-Type": "application/json; charset=utf-8", EVENT_ID_HEADER: delivery.event_id, TIMESTAMP_HEADER: timestamp}
    if secret:
        headers[SIGNATURE_HEADER] = sign_payload(secret, timestamp, body)

    delivery.status = DeliveryStatus.DELIVERING.value
    delivery.attempt = (delivery.attempt or 0) + 1
    started = datetime.now(timezone.utc)
    await db.flush()

    try:
        async with httpx.AsyncClient(timeout=DELIVERY_TIMEOUT_SECONDS, follow_redirects=False) as client:
            resp = await client.post(subscription.url, content=body.encode("utf-8"), headers=headers)
        delivery.response_status = resp.status_code
        delivery.response_snippet = (resp.text or "")[:2000]
        delivery.signature = headers.get(SIGNATURE_HEADER)
        delivery.duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        if 200 <= resp.status_code < 300:
            delivery.status = DeliveryStatus.DELIVERED.value
            delivery.delivered_at = datetime.now(timezone.utc)
            delivery.next_retry_at = None
            delivery.last_error = None
            subscription.consecutive_failures = 0
            subscription.last_delivered_at = delivery.delivered_at
        else:
            delivery.status = DeliveryStatus.FAILED.value if delivery.attempt < delivery.max_attempts else DeliveryStatus.DEAD.value
            delivery.last_error = f"HTTP {resp.status_code}"
            delivery.next_retry_at = datetime.now(timezone.utc) + backoff_delay(delivery.attempt) if delivery.status == DeliveryStatus.FAILED.value else None
            subscription.consecutive_failures = (subscription.consecutive_failures or 0) + 1
            subscription.last_error = delivery.last_error[:500]
    except Exception as exc:
        delivery.status = DeliveryStatus.FAILED.value if delivery.attempt < delivery.max_attempts else DeliveryStatus.DEAD.value
        delivery.last_error = (str(exc) or exc.__class__.__name__)[:500]
        delivery.response_status = None
        delivery.duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        delivery.next_retry_at = datetime.now(timezone.utc) + backoff_delay(delivery.attempt) if delivery.status == DeliveryStatus.FAILED.value else None
        subscription.consecutive_failures = (subscription.consecutive_failures or 0) + 1
        subscription.last_error = delivery.last_error[:500]
        logger.warning("webhook delivery failed sub=%s attempt=%s: %s", subscription.id, delivery.attempt, exc or exc.__class__.__name__)

    if (subscription.consecutive_failures or 0) >= MAX_CONSECUTIVE_FAILURES and subscription.status == WebhookStatus.ACTIVE.value:
        subscription.status = WebhookStatus.PAUSED.value
        subscription.last_error = f"连续失败 {subscription.consecutive_failures} 次，订阅已自动暂停"
    await db.flush()
    return delivery


async def dispatch_pending(db: AsyncSession, *, limit: int = 20, now: datetime | None = None) -> dict[str, int]:
    moment = now or datetime.now(timezone.utc)
    res = await db.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.status.in_([DeliveryStatus.PENDING.value, DeliveryStatus.FAILED.value]))
        .where((WebhookDelivery.next_retry_at.is_(None)) | (WebhookDelivery.next_retry_at <= moment))
        .order_by(WebhookDelivery.created_at.asc())
        .limit(limit)
    )
    due = list(res.scalars().all())
    counters = {"attempted": 0, "delivered": 0, "failed": 0}
    for delivery in due:
        sub_res = await db.execute(select(WebhookSubscription).where(WebhookSubscription.id == delivery.subscription_id))
        subscription = sub_res.scalar_one_or_none()
        if subscription is None:
            delivery.status = DeliveryStatus.DEAD.value
            delivery.last_error = "订阅已删除"
            continue
        if subscription.status != WebhookStatus.ACTIVE.value:
            continue
        counters["attempted"] += 1
        await deliver_once(db, delivery, subscription)
        counters["delivered" if delivery.status == DeliveryStatus.DELIVERED.value else "failed"] += 1
    await db.flush()
    return counters


async def webhook_dispatcher_loop(interval_seconds: int | None = None) -> None:
    import asyncio

    from app.core.database import async_session

    settings = get_settings()
    interval = interval_seconds or settings.WEBHOOK_DISPATCH_INTERVAL_SECONDS
    while True:
        try:
            async with async_session() as db:
                counters = await dispatch_pending(db)
                await db.commit()
                if counters["attempted"]:
                    logger.info("webhook dispatcher: %s", counters)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("webhook dispatcher tick failed: %s", exc)
        await asyncio.sleep(interval)


def build_openapi_doc(contract, endpoints: list[ApiEndpoint], *, base_url: str) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for ep in endpoints:
        request_schema = ep.request_schema or {"type": "object", "properties": {}, "additionalProperties": True}
        response_schema = ep.response_schema or {
            "type": "object",
            "properties": {
                "result": {"type": "string", "description": "工作负载输出"},
                "output_rows": {"type": "integer"},
                "contract_id": {"type": "string"},
            },
        }
        paths[f"/open/v1/{ep.slug}"] = {
            "post": {
                "operationId": ep.slug.replace("-", "_"),
                "summary": ep.name,
                "description": f"合约 {getattr(contract, 'contract_no', '')} 已发布工作负载的调用端点（状态 {ep.status}）。",
                "security": [{"CdsApiKey": []}],
                "requestBody": {"required": True, "content": {"application/json": {"schema": request_schema}}},
                "responses": {
                    "200": {"description": "调用成功", "content": {"application/json": {"schema": response_schema}}},
                    "401": {"description": "API Key 缺失或无效"},
                    "403": {"description": "API Key 未获得该合约授权"},
                    "429": {"description": "超过限流或配额"},
                },
                "x-rate-limit-per-minute": ep.rate_limit_per_minute,
                "x-quota-total": ep.quota_total,
                "x-calls-used": ep.calls_used,
            }
        }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": f"CDS 数据交付 API — 合约 {getattr(contract, 'contract_no', '')}",
            "version": "1.0.0",
            "description": "由已通过测试沙箱验证并发布的工作负载自动生成；未发布的工作负载不会出现在此文档中。",
        },
        "servers": [{"url": base_url}],
        "paths": paths,
        "components": {"securitySchemes": {"CdsApiKey": {"type": "apiKey", "in": "header", "name": "X-CDS-API-Key"}}},
    }


def generate_slug(name: str, taken: set[str]) -> str:
    import re

    base = re.sub(r"[^a-z0-9-]+", "-", (name or "").lower()).strip("-")[:40] or "endpoint"
    slug = base
    counter = 2
    while slug in taken:
        slug = f"{base}-{counter}"
        counter += 1
    return slug


_rate_windows: dict[str, list[float]] = {}


def check_endpoint_rate_limit(endpoint: ApiEndpoint, caller_id: str) -> None:
    key = f"{endpoint.id}:{caller_id}"
    now = time.monotonic()
    window = [t for t in _rate_windows.get(key, []) if now - t < 60.0]
    if len(window) >= max(1, endpoint.rate_limit_per_minute):
        raise EndpointUnavailable("RATE_LIMITED", f"超过每分钟 {endpoint.rate_limit_per_minute} 次限制")
    window.append(now)
    _rate_windows[key] = window


def reset_rate_windows() -> None:
    _rate_windows.clear()


async def execute_endpoint(db: AsyncSession, endpoint: ApiEndpoint, body: dict[str, Any], *, caller_id: uuid.UUID, workload) -> dict[str, Any]:
    from app.api.sandbox_sessions import create_sandbox_session, execute_in_sandbox
    from app.schemas.sandbox_session import SandboxExecuteRequest, SandboxSessionCreate
    from app.models.contract import Contract, WorkloadEnv
    from app.models.sandbox_session import SandboxSession
    from app.services.session_lifecycle import terminate_session
    from app.services.session_state_machine import session_state_machine
    from app.services.workflow_service import resolve_sandbox_mode
    from app.utils.crypto import sm3_hash

    if endpoint.status != ApiEndpointStatus.ENABLED.value:
        raise EndpointUnavailable("ENDPOINT_DISABLED", "端点已停用")
    if endpoint.quota_total is not None and endpoint.calls_used >= endpoint.quota_total:
        raise EndpointUnavailable("QUOTA_EXCEEDED", "调用配额已用尽")
    check_endpoint_rate_limit(endpoint, str(caller_id))
    if workload is None or not (workload.entrypoint or "").strip():
        raise EndpointUnavailable("WORKLOAD_UNAVAILABLE", "端点关联的工作负载不可用")

    product_ids = workload.data_product_ids or []
    if not product_ids:
        raise EndpointUnavailable("WORKLOAD_UNAVAILABLE", "工作负载未绑定数据产品")

    from app.models.user import User

    user_res = await db.execute(select(User).where(User.id == caller_id))
    actor = user_res.scalar_one_or_none()
    if actor is None:
        raise EndpointUnavailable("CALLER_UNKNOWN", "调用方用户不存在")

    payload_hash = sm3_hash(json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
    spec = workload.resource_spec or {}
    code = (
        "import json, hashlib\n"
        f"REQUEST = json.loads({json.dumps(json.dumps(body, ensure_ascii=False, default=str))})\n"
        "CDS_REQUEST_SHA256 = hashlib.sha256(json.dumps(REQUEST, sort_keys=True).encode()).hexdigest()[:16]\n"
        "print('CDS_REQUEST_SHA256=' + CDS_REQUEST_SHA256)\n"
        "# --- published workload entrypoint ---\n"
        + (workload.entrypoint or "")
    )
    session_id = None
    started = datetime.now(timezone.utc)
    contract = await db.get(Contract, endpoint.contract_id) if endpoint.contract_id else None
    try:
        created = await create_sandbox_session(
            body=SandboxSessionCreate(
                data_product_id=uuid.UUID(str(product_ids[0])),
                sandbox_level="l2c",
                sandbox_mode=resolve_sandbox_mode(contract, "structured_query"),
                contract_id=str(endpoint.contract_id),
                timeout_seconds=int(spec.get("timeout_seconds") or endpoint.timeout_seconds),
                resource_limits=spec or None,
            ),
            db=db,
            current_user=actor,
        )
        session_id = created.id
        sess_res = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
        sess = sess_res.scalar_one_or_none()
        if sess is not None:
            sess.env = WorkloadEnv.PRODUCTION.value
            sess.workload_id = workload.id
        exec_res = await execute_in_sandbox(
            session_id=session_id,
            body=SandboxExecuteRequest(code=code, language=workload.language or "python"),
            db=db,
            current_user=actor,
        )
    finally:
        if session_id is not None:
            try:
                sess_res = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
                sess = sess_res.scalar_one_or_none()
                if sess is not None and session_state_machine.is_active(sess.status):
                    await terminate_session(sess, db, reason="SS-15 OpenAPI 调用结束")
            except Exception as exc:
                logger.warning("endpoint call session cleanup failed: %s", exc)

    exit_code = int(exec_res.get("exit_code", -1) if isinstance(exec_res, dict) else -1)
    output = str((exec_res or {}).get("output") or "")
    if exit_code != 0:
        raise EndpointUnavailable("WORKLOAD_FAILED", f"工作负载执行失败（退出码 {exit_code}）")
    if (exec_res or {}).get("output_blocked"):
        raise EndpointUnavailable("OUTPUT_BLOCKED", str((exec_res or {}).get("blocked_reason") or "输出被策略拦截"))

    endpoint.calls_used = (endpoint.calls_used or 0) + 1
    endpoint.last_called_at = datetime.now(timezone.utc)
    await db.flush()
    return {
        "result": output,
        "output_rows": int((exec_res or {}).get("output_rows") or 0),
        "contract_id": str(endpoint.contract_id),
        "endpoint": endpoint.slug,
        "workload_hash": workload.entrypoint_hash,
        "request_sha256": payload_hash[:16],
        "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
    }
