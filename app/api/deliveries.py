import io
import json
import secrets
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_business_user
from app.core.errors import CDSError
from app.core.metrics import record_output_inspection
from app.models.api_endpoint import ApiEndpoint, ApiEndpointStatus
from app.models.contract import Contract, DeliveryMethod
from app.models.delivery_bundle import DeliveryBundle
from app.models.sandbox_session import SandboxSession
from app.models.user import User
from app.models.webhook import WebhookEvent, WebhookStatus, WebhookSubscription
from app.models.webhook_delivery import DeliveryStatus, WebhookDelivery
from app.models.workload import Workload
from app.schemas.flow import (
    DatasetBundleRequest,
    OpenApiEndpointCreate,
    OpenApiEndpointUpdate,
    WebhookCreate,
    WebhookUpdate,
)
from app.services import delivery_service as dv
from app.services import workflow_service as wf
from app.services.audit_service import audit_service
from app.utils.watermark import embed_text_watermark, generate_watermark

router = APIRouter()

MAX_BUNDLE_BYTES = 512 * 1024 * 1024
TEXT_WATERMARK_SUFFIXES = (".txt", ".md", ".json", ".jsonl")
VALID_EVENTS = {e.value for e in WebhookEvent}


def _is_party(contract: Contract, user: User) -> bool:
    return contract.buyer_id == user.id or contract.provider_id == user.id or getattr(user, "role", None) in ("admin", "platform_admin")


async def _load_contract(db: AsyncSession, contract_id: uuid.UUID, user: User) -> Contract:
    res = await db.execute(select(Contract).where(Contract.id == contract_id))
    contract = res.scalar_one_or_none()
    if contract is None:
        raise HTTPException(status_code=404, detail="contract not found")
    if not _is_party(contract, user):
        raise HTTPException(status_code=403, detail="not a party of this contract")
    return contract


def _require_method(contract: Contract, method: DeliveryMethod) -> None:
    methods = contract.delivery_methods or []
    if method.value not in methods:
        raise CDSError(
            f"合约未选择 {method.value} 交付方式，请先在合约的交付配置中开启",
            code="DELIVERY_METHOD_NOT_ALLOWED",
            http_status=403,
            detail={"contract_id": str(contract.id), "configured": methods, "required": method.value},
        )


async def _load_session(db: AsyncSession, session_id: uuid.UUID, user: User) -> SandboxSession:
    res = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
    session = res.scalar_one_or_none()
    if session is None:
        raise CDSError("会话不存在", code="SESSION_NOT_FOUND", http_status=404, detail={"session_id": str(session_id)})
    if session.user_id != user.id and getattr(user, "role", None) not in ("admin", "platform_admin"):
        raise CDSError("该会话不属于当前用户", code="FORBIDDEN", http_status=403, detail={"session_id": str(session_id)})
    return session


async def _collect_session_files(db: AsyncSession, session: SandboxSession) -> list[dict[str, Any]]:
    from app.api.sandbox_sessions import _l2c_file_op, _session_dek, _session_workspace_or_400
    from app.services import session_files as sf

    if session.sandbox_level == "l2c":
        listing = await _l2c_file_op(session, "list")
        entries = []
        for name in listing.get("files") or []:
            payload = await _l2c_file_op(session, "download", name)
            entries.append({"name": name, "data": payload["data"], "encrypted": False})
        return entries

    workspace = _session_workspace_or_400(session)
    dek = await _session_dek(session)
    entries = []
    for meta in sf.list_files(workspace):
        name = meta["name"]
        data, _read_meta = sf.read_file(workspace, name, dek=dek)
        entries.append({"name": name, "data": data, "size_bytes": meta.get("size"), "encrypted": bool(meta.get("encrypted"))})
    return entries


def _review_output_bytes(data: bytes, user_id: str, session: SandboxSession) -> dict[str, Any]:
    from app.services.output_inspection import output_inspector
    from app.services.output_security import should_block

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"data": data, "status": "skipped_binary", "findings": 0, "blocked": False}

    result = output_inspector.inspect(
        text,
        user_id=user_id,
        session_id=str(session.id),
        sandbox_mode=getattr(session, "sandbox_mode", None) or "structured_query",
    )
    findings = len(result.findings or [])
    blocked = should_block(result)
    redacted = getattr(result, "redacted_output", None)
    if findings and redacted is not None:
        return {"data": redacted.encode("utf-8"), "status": "redacted", "findings": findings, "blocked": blocked}
    status = "passed" if not findings else "passed_with_findings"
    return {"data": data, "status": status, "findings": findings, "blocked": blocked}


@router.post("/dataset/bundle", status_code=201)
async def create_dataset_bundle(
    body: DatasetBundleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    contract = await _load_contract(db, body.contract_id, current_user)
    _require_method(contract, DeliveryMethod.DATASET)
    session = await _load_session(db, body.session_id, current_user)
    if session.contract_id and session.contract_id != contract.id:
        raise CDSError("会话不属于该合约", code="CONTRACT_MISMATCH", http_status=403, detail={"session_id": str(session.id), "contract_id": str(contract.id)})

    files = await _collect_session_files(db, session)
    if not files:
        raise CDSError("会话没有可交付的输出文件", code="NO_OUTPUT_FILES", http_status=409, detail={"session_id": str(session.id)})

    from app.services.storage_service import storage_service

    bundle_id = uuid.uuid4()
    watermark = generate_watermark(str(current_user.id), str(session.id))
    manifest_files: list[dict[str, Any]] = []
    buffer = io.BytesIO()
    total = 0
    total_findings = 0
    skipped_binary = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in files:
            # T5 出口审查：单文件下载已按 SS-14 RT-07 过审，打包通道必须同契约，
            # 否则“单下拦截、打包带走”就是围堆。拦截时包体尚未上传，不存在部分交付。
            review = _review_output_bytes(entry["data"], str(current_user.id), session)
            if review["blocked"]:
                record_output_inspection("blocked")
                await audit_service.log_standalone(
                    action="delivery.dataset.blocked", resource_type="contract",
                    user_id=current_user.id, resource_id=str(contract.id),
                    detail={"session_id": str(session.id), "file": entry["name"], "findings": review["findings"]},
                )
                raise CDSError(
                    "交付文件被出口审查拦截，被拦数据不会交付",
                    code="OUTPUT_BLOCKED",
                    http_status=502,
                    detail={"session_id": str(session.id), "file": entry["name"], "findings": review["findings"]},
                )
            data = review["data"]
            if review["status"] != "skipped_binary":
                record_output_inspection("redacted" if review["status"] == "redacted" else "passed")
            total += len(data)
            total_findings += review["findings"]
            if review["status"] == "skipped_binary":
                skipped_binary += 1
            if total > MAX_BUNDLE_BYTES:
                raise CDSError("交付包超过 512MB 上限，请分批交付或走数据产品通道", code="BUNDLE_TOO_LARGE", http_status=413)
            name = entry["name"]
            applied = False
            reason = "数据文件不注入不可见水印，避免破坏下游解析"
            if body.watermark and name.lower().endswith(TEXT_WATERMARK_SUFFIXES):
                try:
                    data = embed_text_watermark(data.decode("utf-8"), watermark).encode("utf-8")
                    applied = True
                    reason = "已注入按买方+会话生成的不可见文本水印"
                except Exception as exc:
                    reason = f"水印注入失败，按原文交付: {exc}"
            zf.writestr(f"files/{name}", data)
            manifest_files.append({"name": name, "size_bytes": len(data), "watermark_applied": applied, "watermark_note": reason, "output_review": review["status"], "output_findings": review["findings"]})
        manifest = {
            "bundle_id": str(bundle_id),
            "contract_id": str(contract.id),
            "contract_no": contract.contract_no,
            "session_id": str(session.id),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generated_by": str(current_user.id),
            "file_count": len(manifest_files),
            "files": manifest_files,
            "watermark_scope": "text-files-only",
            "output_review_scope": "utf-8 文本文件已过 T5 出口审查；二进制文件未送审（status=skipped_binary）",
            "output_findings_total": total_findings,
            "output_review_skipped_binary": skipped_binary,
        }
        zf.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    payload = buffer.getvalue()
    object_name = f"delivery/{bundle_id}.zip"
    storage_service.upload(payload, object_name, content_type="application/zip")
    # 归属记录与包体同事务落库：没有这条记录，下载端点无法判定调用方是否为合约当事方。
    db.add(DeliveryBundle(
        id=bundle_id,
        contract_id=contract.id,
        session_id=session.id,
        created_by=current_user.id,
        object_name=object_name,
        size_bytes=len(payload),
        file_count=len(manifest_files),
    ))
    await audit_service.log(
        db, action="delivery.dataset.bundled", resource_type="contract",
        user_id=current_user.id, resource_id=str(contract.id),
        detail={"bundle_id": str(bundle_id), "session_id": str(session.id), "file_count": len(manifest_files), "size_bytes": len(payload), "object": object_name, "output_findings": total_findings, "output_review_skipped_binary": skipped_binary},
    )
    return {
        "bundle_id": str(bundle_id),
        "files": manifest_files,
        "size_bytes": len(payload),
        "download_url": f"/api/v1/deliveries/dataset/{bundle_id}/download",
        "watermark_applied": any(f["watermark_applied"] for f in manifest_files),
        "watermark_scope": "text-files-only",
        "output_review": {"findings_total": total_findings, "skipped_binary": skipped_binary, "scope": "utf-8 text files"},
    }


def _bundle_output_reviewed(payload: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            manifest = json.loads(zf.read("MANIFEST.json").decode("utf-8"))
    except Exception:
        return False
    return isinstance(manifest, dict) and "output_review_scope" in manifest


@router.get("/dataset/{bundle_id}/download")
async def download_dataset_bundle(
    bundle_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    from app.services.storage_service import storage_service

    # fail-closed：包字节按 bundle_id 存在对象存储里，光靠 UUID 不可猜不算授权。
    # 无归属记录（0021 之前的历史包）一律 404，有记录则必须是该合约的当事方。
    own_res = await db.execute(select(DeliveryBundle).where(DeliveryBundle.id == bundle_id))
    owner = own_res.scalar_one_or_none()
    if owner is None:
        raise HTTPException(status_code=404, detail="交付包不存在或无归属记录，请重新生成交付包")
    await _load_contract(db, owner.contract_id, current_user)

    object_name = owner.object_name or f"delivery/{bundle_id}.zip"
    try:
        payload = storage_service.download(object_name)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"交付包不存在或已过期: {exc}") from exc

    # 与本端点对 0021 之前历史包同样 fail-closed：加固前生成的包字节未过 T5，
    # 不在下载路径重实现第二遍审查，而是要求重新生成（一个 POST 的成本）。
    if not _bundle_output_reviewed(payload):
        await audit_service.log_standalone(
            action="delivery.dataset.review_required", resource_type="delivery_bundle",
            user_id=current_user.id, resource_id=str(bundle_id), detail={"size_bytes": len(payload)},
        )
        raise CDSError(
            "该交付包生成于出口审查加固之前，未经 T5 审查不予出域，请重新生成交付包",
            code="OUTPUT_REVIEW_REQUIRED",
            http_status=409,
            detail={"bundle_id": str(bundle_id)},
        )
    await audit_service.log(
        db, action="delivery.dataset.downloaded", resource_type="delivery_bundle",
        user_id=current_user.id, resource_id=str(bundle_id), detail={"size_bytes": len(payload)},
    )
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="cds-delivery-{bundle_id}.zip"'},
    )


def _sub_dict(sub: WebhookSubscription, *, secret: str | None = None) -> dict[str, Any]:
    data = {
        "id": str(sub.id),
        "name": sub.name,
        "url": sub.url,
        "events": sub.events or [],
        "contract_id": str(sub.contract_id) if sub.contract_id else None,
        "status": sub.status,
        "consecutive_failures": sub.consecutive_failures,
        "last_error": sub.last_error,
        "last_delivered_at": sub.last_delivered_at,
        "created_at": sub.created_at,
    }
    if secret is not None:
        data["secret"] = secret
    return data


async def _load_subscription(db: AsyncSession, subscription_id: uuid.UUID, user: User) -> WebhookSubscription:
    res = await db.execute(select(WebhookSubscription).where(WebhookSubscription.id == subscription_id))
    sub = res.scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="webhook subscription not found")
    if sub.user_id != user.id and getattr(user, "role", None) not in ("admin", "platform_admin"):
        raise HTTPException(status_code=403, detail="not your webhook subscription")
    return sub


@router.post("/webhooks", status_code=201)
async def create_webhook(
    body: WebhookCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    unknown = [e for e in body.events if e not in VALID_EVENTS]
    if unknown:
        raise CDSError("不支持的推送事件类型", code="WEBHOOK_EVENT_UNSUPPORTED", http_status=400, detail={"unknown": unknown, "supported": sorted(VALID_EVENTS)})
    try:
        url = dv.validate_webhook_url(body.url)
    except dv.WebhookUrlRejected as exc:
        raise CDSError(exc.reason, code="WEBHOOK_URL_REJECTED", http_status=400, detail={"url": body.url}) from exc
    if body.contract_id is not None:
        await _load_contract(db, body.contract_id, current_user)

    from app.services.task_code_security import encrypt_task_code

    secret = secrets.token_urlsafe(32)
    sub_id = uuid.uuid4()
    sub = WebhookSubscription(
        id=sub_id,
        user_id=current_user.id,
        contract_id=body.contract_id,
        name=body.name,
        url=url,
        events=body.events,
        secret_enc=encrypt_task_code(secret, scope=f"webhook-{sub_id}"),
        status=WebhookStatus.ACTIVE.value,
    )
    db.add(sub)
    await db.flush()
    await audit_service.log(
        db, action="webhook.subscription_created", resource_type="webhook_subscription",
        user_id=current_user.id, resource_id=str(sub.id), detail={"url": url, "events": body.events},
    )
    return _sub_dict(sub, secret=secret)


@router.get("/webhooks")
async def list_webhooks(
    contract_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    stmt = select(WebhookSubscription).where(WebhookSubscription.user_id == current_user.id)
    if contract_id is not None:
        stmt = stmt.where(WebhookSubscription.contract_id == contract_id)
    res = await db.execute(stmt.order_by(WebhookSubscription.created_at.desc()))
    items = [_sub_dict(sub) for sub in res.scalars().all()]
    return {"items": items, "total": len(items), "page": 1, "page_size": len(items) or 1}


@router.patch("/webhooks/{subscription_id}")
async def update_webhook(
    subscription_id: uuid.UUID,
    body: WebhookUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    sub = await _load_subscription(db, subscription_id, current_user)
    if body.url is not None:
        try:
            sub.url = dv.validate_webhook_url(body.url)
        except dv.WebhookUrlRejected as exc:
            raise CDSError(exc.reason, code="WEBHOOK_URL_REJECTED", http_status=400, detail={"url": body.url}) from exc
    if body.name is not None:
        sub.name = body.name
    if body.events is not None:
        unknown = [e for e in body.events if e not in VALID_EVENTS]
        if unknown:
            raise CDSError("不支持的推送事件类型", code="WEBHOOK_EVENT_UNSUPPORTED", http_status=400, detail={"unknown": unknown})
        sub.events = body.events
    if body.status is not None:
        sub.status = body.status
        if body.status == WebhookStatus.ACTIVE.value:
            sub.consecutive_failures = 0
            sub.last_error = None
    await db.flush()
    await audit_service.log(db, action="webhook.subscription_updated", resource_type="webhook_subscription", user_id=current_user.id, resource_id=str(sub.id), detail=body.model_dump(exclude_none=True))
    return _sub_dict(sub)


@router.delete("/webhooks/{subscription_id}", status_code=204)
async def delete_webhook(subscription_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_business_user)):
    sub = await _load_subscription(db, subscription_id, current_user)
    await db.execute(update(WebhookDelivery).where(WebhookDelivery.subscription_id == subscription_id).values(subscription_id=None))
    await db.delete(sub)
    await audit_service.log(db, action="webhook.subscription_deleted", resource_type="webhook_subscription", user_id=current_user.id, resource_id=str(subscription_id), detail={"url": sub.url})
    return Response(status_code=204)


@router.post("/webhooks/{subscription_id}/rotate-secret")
async def rotate_webhook_secret(subscription_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_business_user)):
    from app.services.task_code_security import encrypt_task_code

    sub = await _load_subscription(db, subscription_id, current_user)
    secret = secrets.token_urlsafe(32)
    sub.secret_enc = encrypt_task_code(secret, scope=f"webhook-{sub.id}")
    await audit_service.log(db, action="webhook.secret_rotated", resource_type="webhook_subscription", user_id=current_user.id, resource_id=str(sub.id), detail={})
    return {"secret": secret}


@router.get("/webhooks/{subscription_id}/deliveries")
async def list_webhook_deliveries(
    subscription_id: uuid.UUID,
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    await _load_subscription(db, subscription_id, current_user)
    stmt = select(WebhookDelivery).where(WebhookDelivery.subscription_id == subscription_id)
    if status_filter:
        stmt = stmt.where(WebhookDelivery.status == status_filter)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    res = await db.execute(stmt.order_by(WebhookDelivery.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    items = [
        {
            "id": str(d.id),
            "event_id": d.event_id,
            "event_type": d.event_type,
            "status": d.status,
            "attempt": d.attempt,
            "max_attempts": d.max_attempts,
            "next_retry_at": d.next_retry_at,
            "response_status": d.response_status,
            "response_snippet": (d.response_snippet or "")[:500] or None,
            "last_error": d.last_error,
            "duration_ms": d.duration_ms,
            "created_at": d.created_at,
            "delivered_at": d.delivered_at,
        }
        for d in res.scalars().all()
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def _delivery_dict(delivery: WebhookDelivery) -> dict[str, Any]:
    return {
        "id": str(delivery.id),
        "event_id": delivery.event_id,
        "event_type": delivery.event_type,
        "status": delivery.status,
        "attempt": delivery.attempt,
        "max_attempts": delivery.max_attempts,
        "next_retry_at": delivery.next_retry_at,
        "response_status": delivery.response_status,
        "response_snippet": (delivery.response_snippet or "")[:500] or None,
        "last_error": delivery.last_error,
        "duration_ms": delivery.duration_ms,
        "created_at": delivery.created_at,
        "delivered_at": delivery.delivered_at,
    }


@router.post("/webhooks/{subscription_id}/deliveries/{delivery_id}/redeliver")
async def redeliver_webhook(
    subscription_id: uuid.UUID,
    delivery_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    sub = await _load_subscription(db, subscription_id, current_user)
    res = await db.execute(select(WebhookDelivery).where(WebhookDelivery.id == delivery_id, WebhookDelivery.subscription_id == subscription_id))
    delivery = res.scalar_one_or_none()
    if delivery is None:
        raise HTTPException(status_code=404, detail="delivery not found")
    delivery.status = DeliveryStatus.PENDING.value
    delivery.attempt = 0
    delivery.max_attempts = max(delivery.max_attempts, 3)
    delivery.next_retry_at = datetime.now(timezone.utc)
    sub.consecutive_failures = 0
    await dv.deliver_once(db, delivery, sub)
    await audit_service.log(db, action="webhook.redelivered", resource_type="webhook_subscription", user_id=current_user.id, resource_id=str(sub.id), detail={"delivery_id": str(delivery.id), "status": delivery.status})
    return _delivery_dict(delivery)


@router.post("/webhooks/{subscription_id}/test")
async def test_webhook(subscription_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_business_user)):
    sub = await _load_subscription(db, subscription_id, current_user)
    try:
        dv.validate_webhook_url(sub.url)
    except dv.WebhookUrlRejected as exc:
        raise CDSError(exc.reason, code="WEBHOOK_URL_REJECTED", http_status=400, detail={"url": sub.url}) from exc
    delivery = WebhookDelivery(
        subscription_id=sub.id,
        event_type="webhook.test",
        event_id=uuid.uuid4().hex,
        payload={"message": "CDS 推送连通性测试", "subscription_id": str(sub.id), "created_by": str(current_user.id)},
        status=DeliveryStatus.PENDING.value,
        next_retry_at=datetime.now(timezone.utc),
    )
    db.add(delivery)
    await db.flush()
    await dv.deliver_once(db, delivery, sub)
    await audit_service.log(db, action="webhook.tested", resource_type="webhook_subscription", user_id=current_user.id, resource_id=str(sub.id), detail={"status": delivery.status, "response_status": delivery.response_status})
    return {
        "delivered": delivery.status == DeliveryStatus.DELIVERED.value,
        "response_status": delivery.response_status,
        "detail": delivery.last_error or (delivery.response_snippet or "")[:300] or ("已接收 2xx 响应" if delivery.status == DeliveryStatus.DELIVERED.value else "未送达：接收端未返回响应"),
        "delivery": _delivery_dict(delivery),
    }


@router.get("/openapi/contract/{contract_id}")
async def contract_openapi_document(
    contract_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    contract = await _load_contract(db, contract_id, current_user)
    res = await db.execute(
        select(ApiEndpoint).where(ApiEndpoint.contract_id == contract_id).order_by(ApiEndpoint.created_at.asc())
    )
    endpoints = list(res.scalars().all())
    base = str(request.base_url).rstrip("/")
    return dv.build_openapi_doc(contract, endpoints, base_url=base)


def _endpoint_dict(endpoint: ApiEndpoint) -> dict[str, Any]:
    return {
        "id": str(endpoint.id),
        "contract_id": str(endpoint.contract_id),
        "workload_id": str(endpoint.workload_id) if endpoint.workload_id else None,
        "name": endpoint.name,
        "slug": endpoint.slug,
        "method": endpoint.method,
        "status": endpoint.status,
        "path": f"/open/v1/{endpoint.slug}",
        "request_schema": endpoint.request_schema,
        "response_schema": endpoint.response_schema,
        "timeout_seconds": endpoint.timeout_seconds,
        "rate_limit_per_minute": endpoint.rate_limit_per_minute,
        "quota_total": endpoint.quota_total,
        "calls_used": endpoint.calls_used,
        "last_called_at": endpoint.last_called_at,
        "published_at": endpoint.published_at,
        "created_at": endpoint.created_at,
    }


@router.post("/openapi/endpoints", status_code=201)
async def create_openapi_endpoint(
    body: OpenApiEndpointCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    contract = await _load_contract(db, body.contract_id, current_user)
    _require_method(contract, DeliveryMethod.OPEN_API)

    workload = None
    if body.workload_id is not None:
        wres = await db.execute(select(Workload).where(Workload.id == body.workload_id))
        workload = wres.scalar_one_or_none()
        if workload is None:
            raise HTTPException(status_code=404, detail="workload not found")
        if workload.contract_id != contract.id:
            raise CDSError("工作负载不属于该合约", code="CONTRACT_MISMATCH", http_status=403)
    else:
        workload = await wf.get_production_head(db, contract.id)
    if workload is None:
        raise CDSError(
            "该合约没有已发布的生产工作负载：请先在测试沙箱验证并发布后再创建 API 端点",
            code="WORKLOAD_NOT_PUBLISHED",
            http_status=412,
        )

    taken = {e.slug for e in (await db.execute(select(ApiEndpoint))).scalars().all()}
    slug = body.slug.strip().lower() if body.slug else dv.generate_slug(body.name, taken)
    if slug in taken:
        raise CDSError("该 slug 已被占用", code="ENDPOINT_SLUG_TAKEN", http_status=409, detail={"slug": slug})

    endpoint = ApiEndpoint(
        owner_id=current_user.id,
        contract_id=contract.id,
        workload_id=workload.id,
        name=body.name,
        slug=slug,
        request_schema=body.request_schema,
        rate_limit_per_minute=body.rate_limit_per_minute,
        quota_total=body.quota_total,
        status=ApiEndpointStatus.ENABLED.value,
        published_at=datetime.now(timezone.utc),
    )
    db.add(endpoint)
    await db.flush()
    await audit_service.log(
        db, action="openapi.endpoint_created", resource_type="contract",
        user_id=current_user.id, resource_id=str(contract.id),
        detail={"endpoint_id": str(endpoint.id), "slug": slug, "workload_id": str(workload.id), "workload_hash": workload.entrypoint_hash},
    )
    return _endpoint_dict(endpoint)


@router.get("/openapi/endpoints")
async def list_openapi_endpoints(
    contract_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    stmt = select(ApiEndpoint)
    if getattr(current_user, "role", None) not in ("admin", "platform_admin"):
        stmt = stmt.where(ApiEndpoint.owner_id == current_user.id)
    if contract_id is not None:
        await _load_contract(db, contract_id, current_user)
        stmt = stmt.where(ApiEndpoint.contract_id == contract_id)
    res = await db.execute(stmt.order_by(ApiEndpoint.created_at.desc()))
    items = [_endpoint_dict(e) for e in res.scalars().all()]
    return {"items": items, "total": len(items), "page": 1, "page_size": len(items) or 1}


@router.patch("/openapi/endpoints/{endpoint_id}")
async def update_openapi_endpoint(
    endpoint_id: uuid.UUID,
    body: OpenApiEndpointUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    res = await db.execute(select(ApiEndpoint).where(ApiEndpoint.id == endpoint_id))
    endpoint = res.scalar_one_or_none()
    if endpoint is None:
        raise HTTPException(status_code=404, detail="endpoint not found")
    await _load_contract(db, endpoint.contract_id, current_user)
    if body.status is not None:
        endpoint.status = body.status
    if body.rate_limit_per_minute is not None:
        endpoint.rate_limit_per_minute = body.rate_limit_per_minute
    if body.quota_total is not None:
        endpoint.quota_total = body.quota_total
    await db.flush()
    await audit_service.log(db, action="openapi.endpoint_updated", resource_type="contract", user_id=current_user.id, resource_id=str(endpoint.contract_id), detail={"endpoint_id": str(endpoint.id), **body.model_dump(exclude_none=True)})
    return _endpoint_dict(endpoint)


@router.delete("/openapi/endpoints/{endpoint_id}", status_code=204)
async def delete_openapi_endpoint(endpoint_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_business_user)):
    res = await db.execute(select(ApiEndpoint).where(ApiEndpoint.id == endpoint_id))
    endpoint = res.scalar_one_or_none()
    if endpoint is None:
        raise HTTPException(status_code=404, detail="endpoint not found")
    await _load_contract(db, endpoint.contract_id, current_user)
    await db.delete(endpoint)
    await audit_service.log(db, action="openapi.endpoint_deleted", resource_type="contract", user_id=current_user.id, resource_id=str(endpoint.contract_id), detail={"endpoint_id": str(endpoint.id), "slug": endpoint.slug})
    return Response(status_code=204)
