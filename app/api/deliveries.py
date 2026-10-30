import io
import json
import secrets
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audit import AUDIT_FAILURE_MARKERS, audit_outcome
from app.core.database import get_db
from app.core.deps import get_business_user
from app.core.errors import CDSError
from app.core.metrics import record_output_inspection
from app.models.api_endpoint import ApiEndpoint, ApiEndpointStatus
from app.models.audit_log import AuditLog
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
from app.utils.office_watermark import (
    WATERMARK_METHOD_DOCX,
    WATERMARK_METHOD_PREVIEW_ONLY,
    WATERMARK_METHOD_TEXT_STEGO,
    WATERMARK_METHOD_XLSX_HEADER,
    embed_docx_background_watermark,
    embed_xlsx_header_watermark,
)
from app.utils.watermark import embed_text_watermark, generate_watermark

router = APIRouter()

MAX_BUNDLE_BYTES = 512 * 1024 * 1024
TEXT_WATERMARK_SUFFIXES = (".txt", ".md", ".json", ".jsonl")
# 2026-10-30 拍板：Office 文件走「背景水印」。docx 有真背景水印能力（OOXML 页眉 VML
# WordArt）；xlsx 没有——诚实降级为页眉文本 + docProps 自定义属性（header_text）；
# csv/tsv 这类纯文本数据表没有"背景"概念，文件字节一个不动，水印只活在平台在线
# 预览层（preview_only）。清单与交付中心列表按这三档如实标注，绝不合并口径。
# .jsonl 保留更强的零宽内嵌水印（存量能力，只强不弱），不降级为 preview_only。
DOCX_WATERMARK_SUFFIXES = (".docx",)
XLSX_WATERMARK_SUFFIXES = (".xlsx",)
PREVIEW_WATERMARK_SUFFIXES = (".csv", ".tsv")
WATERMARK_LEGEND = {
    WATERMARK_METHOD_TEXT_STEGO: "不可见零宽字符水印（文本类，文件字节已改写）",
    WATERMARK_METHOD_DOCX: "OOXML 页眉背景水印（VML WordArt，斜向半透明，文件字节已改写）",
    WATERMARK_METHOD_XLSX_HEADER: "降级：打印页眉文本 + docProps 自定义属性（OOXML 表格无背景水印能力，非真背景水印）",
    WATERMARK_METHOD_PREVIEW_ONLY: "文件本体未修改；水印仅由平台在线预览层叠加（当前用户+时间+包号）",
}
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


def _ensure_session_contract(session: SandboxSession, contract: Contract) -> None:
    """D-e2e-15: 会话必须属于签好约的那份合约。

    `sandbox_sessions.contract_id` 是 String(255)（数字合约 ID），而 `Contract.id` 是
    `UUID(as_uuid=True)`：直接 `!=` 会拿 str 跟 UUID 比，恒为 True，于是**任何**会话
    都会被误判 `CONTRACT_MISMATCH`，数据集打包永远 403。两侧统一按 str 比。
    """
    if session.contract_id and str(session.contract_id) != str(contract.id):
        raise CDSError(
            "会话不属于该合约",
            code="CONTRACT_MISMATCH",
            http_status=403,
            detail={"session_id": str(session.id), "contract_id": str(contract.id)},
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
        # SS-18: 采集按 HARVEST_DIRS 优先级逐文件标源目录（/sandbox/output 为产物口径,
        # /scratch 为兼容期）; 老版 listing 无 entries 时回退到 files。
        items = listing.get("entries") or [{"name": n, "dir": ""} for n in listing.get("files") or []]
        entries = []
        for item in items:
            name = item["name"]
            payload = await _l2c_file_op(session, "download", name, where=item.get("dir") or "")
            entries.append({"name": name, "data": payload["data"], "encrypted": False})
        return entries

    workspace = _session_workspace_or_400(session)
    dek = await _session_dek(session)
    entries = []
    # 递归采集 files/（排除 input/ tmp/）：开发环产物写在 files/output/ 下，
    # 旧 list_files() 只看顶层，那部分会被当成“没有产出”。SS-19 D-e2e-11。
    for meta in sf.list_output_files(workspace):
        name = meta["name"]
        data, _read_meta = sf.read_output_file(workspace, name, dek=dek)
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
    if not text.strip():
        # 审查器对空文本判 passed=False（empty_output），但 findings 里只有 medium，
        # 沿用旧分支会被记成 passed_with_findings 并进包。空文件单独命名，不冒充已审。
        return {"data": data, "status": "empty", "findings": findings, "blocked": blocked}
    if findings and redacted is not None:
        return {"data": redacted.encode("utf-8"), "status": "redacted", "findings": findings, "blocked": blocked}
    status = "passed" if not findings else "passed_with_findings"
    return {"data": data, "status": status, "findings": findings, "blocked": blocked}


DATA_TABLE_SUFFIXES = (".csv", ".tsv", ".jsonl")


def _data_row_count(name: str, data: bytes) -> int | None:
    """数据表文件的行数：.csv/.tsv 去掉表头，.jsonl 逐行；非数据表或解不出文本返回 None（未知，不按 0 交付）。"""
    lowered = name.lower()
    if not lowered.endswith(DATA_TABLE_SUFFIXES):
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = [line for line in text.splitlines() if line.strip()]
    if lowered.endswith(".jsonl"):
        return len(lines)
    return max(len(lines) - 1, 0)


def _t5_review_status(manifest_files: list[dict[str, Any]]) -> str:
    """聚合口径：命中/脱敏 → passed_with_findings；有未送审二进制或空文件 → partially_reviewed。"""
    statuses = [str(item.get("output_review")) for item in manifest_files]
    if any(status in ("redacted", "passed_with_findings") for status in statuses):
        return "passed_with_findings"
    if any(status in ("skipped_binary", "empty") for status in statuses):
        return "partially_reviewed"
    return "passed"


def _watermark_status(requested: bool, manifest_files: list[dict[str, Any]]) -> str:
    """包级水印口径（向后兼容扩展，2026-10-30 背景水印拍板）。

    - ``applied`` / ``partially_applied`` / ``not_applied`` / ``not_requested``：与
      SS-18 原语义逐字一致——``applied`` 仍要求包内**全部**文件的字节都被写入过水印
      （零宽文本、docx 背景 WordArt、xlsx 页眉文本都算写进了文件字节）。
    - ``preview_only``（新增）：申请了水印，但包里没有任何文件的字节被改写，且全部
      文件都是标了 ``watermark_method=preview_only`` 的纯文本数据表（csv/tsv）——
      水印只存在于平台在线预览层。既不能谎称 ``applied``，也不该按"该嵌没嵌"记
      ``not_applied``。
    - 兼容：历史清单字典没有 ``watermark_method`` 键时只按 ``watermark_applied`` 聚合，
      与 SS-18 落库口径逐位一致（tests/test_ss18_bundle_columns.py 钉住）。
    """
    if not requested:
        return "not_requested"
    applied = sum(1 for item in manifest_files if item.get("watermark_applied"))
    if not applied:
        preview_only = sum(1 for item in manifest_files if item.get("watermark_method") == WATERMARK_METHOD_PREVIEW_ONLY)
        if preview_only and preview_only == len(manifest_files):
            return WATERMARK_METHOD_PREVIEW_ONLY
        return "not_applied"
    return "applied" if applied == len(manifest_files) else "partially_applied"

@router.post("/dataset/bundle", status_code=201)
async def create_dataset_bundle(
    body: DatasetBundleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    contract = await _load_contract(db, body.contract_id, current_user)
    _require_method(contract, DeliveryMethod.DATASET)
    session = await _load_session(db, body.session_id, current_user)
    _ensure_session_contract(session, contract)

    files = await _collect_session_files(db, session)
    if not files:
        raise CDSError("会话没有可交付的输出文件", code="NO_OUTPUT_FILES", http_status=409, detail={"session_id": str(session.id)})

    from app.services.storage_service import storage_service

    bundle_id = uuid.uuid4()
    watermark = generate_watermark(str(current_user.id), str(session.id))
    generated_at = datetime.now(timezone.utc).isoformat()
    # 可见水印文本（docx 背景 WordArt / xlsx 页眉 / 前端预览层共用同一口径）：
    # 成果包编号 + 时间戳 + 主体（打包人用户 ID）。
    wm_visible = f"{bundle_id} | {generated_at} | user:{current_user.id}"
    manifest_files: list[dict[str, Any]] = []
    buffer = io.BytesIO()
    total = 0
    total_findings = 0
    skipped_binary = 0
    total_rows = 0
    files_with_rows = 0
    empty_files = 0
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
            data_rows = _data_row_count(entry["name"], data)
            if data_rows is not None:
                total_rows += data_rows
                files_with_rows += 1
            if review["status"] not in ("skipped_binary", "empty"):
                record_output_inspection("redacted" if review["status"] == "redacted" else "passed")
            total += len(data)
            total_findings += review["findings"]
            if review["status"] == "skipped_binary":
                skipped_binary += 1
            elif review["status"] == "empty":
                empty_files += 1
            if total > MAX_BUNDLE_BYTES:
                raise CDSError("交付包超过 512MB 上限，请分批交付或走数据产品通道", code="BUNDLE_TOO_LARGE", http_status=413)
            name = entry["name"]
            applied = False
            method: str | None = None
            reason = "数据文件不注入不可见水印，避免破坏下游解析"
            if body.watermark:
                lowered = name.lower()
                if lowered.endswith(TEXT_WATERMARK_SUFFIXES):
                    try:
                        data = embed_text_watermark(data.decode("utf-8"), watermark).encode("utf-8")
                        applied = True
                        method = WATERMARK_METHOD_TEXT_STEGO
                        reason = "已注入按买方+会话生成的不可见文本水印"
                    except Exception as exc:
                        reason = f"水印注入失败，按原文交付: {exc}"
                elif lowered.endswith(DOCX_WATERMARK_SUFFIXES):
                    try:
                        data = embed_docx_background_watermark(data, wm_visible)
                        applied = True
                        method = WATERMARK_METHOD_DOCX
                        reason = "已注入 OOXML 背景水印：页眉 VML WordArt（斜向 -45°、半透明、页面居中、文字下层），文件字节已改写"
                    except Exception as exc:
                        reason = f"docx 背景水印注入失败（不是合法 OOXML 容器等），按原文交付: {exc}"
                elif lowered.endswith(XLSX_WATERMARK_SUFFIXES):
                    try:
                        data = embed_xlsx_header_watermark(data, wm_visible)
                        applied = True
                        method = WATERMARK_METHOD_XLSX_HEADER
                        reason = "诚实降级：OOXML 表格没有背景水印概念——已写打印页眉文本（oddHeader 居中段）+ docProps 自定义属性，非真背景水印"
                    except Exception as exc:
                        reason = f"xlsx 页眉水印注入失败，按原文交付: {exc}"
                elif lowered.endswith(PREVIEW_WATERMARK_SUFFIXES):
                    method = WATERMARK_METHOD_PREVIEW_ONLY
                    reason = "纯文本数据表：文件字节未动（改一个字节都可能破坏下游解析）；水印由平台在线预览层叠加（当前用户+时间+包号），下载件不含水印"
            zf.writestr(f"files/{name}", data)
            manifest_files.append({"name": name, "size_bytes": len(data), "data_rows": data_rows, "watermark_applied": applied, "watermark_method": method, "watermark_note": reason, "output_review": review["status"], "output_findings": review["findings"]})
        manifest = {
            "bundle_id": str(bundle_id),
            "contract_id": str(contract.id),
            "contract_no": contract.contract_no,
            "session_id": str(session.id),
            "generated_at": generated_at,
            "generated_by": str(current_user.id),
            "file_count": len(manifest_files),
            "files": manifest_files,
            "watermark_scope": "office-embedded+text-stego+preview-layer",
            "watermark_text": wm_visible,
            "watermark_legend": WATERMARK_LEGEND,
            "output_review_scope": "utf-8 文本文件已过 T5 出口审查；二进制文件未送审（status=skipped_binary）",
            "output_findings_total": total_findings,
            "output_review_skipped_binary": skipped_binary,
            "output_review_empty": empty_files,
        }
        zf.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    t5_review_status = _t5_review_status(manifest_files)
    watermark_status = _watermark_status(body.watermark, manifest_files)
    row_count = total_rows if files_with_rows else None

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
        t5_review_status=t5_review_status,
        watermark_status=watermark_status,
        row_count=row_count,
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
        "watermark_scope": "office-embedded+text-stego+preview-layer",
        "watermark_legend": WATERMARK_LEGEND,
        "output_review": {"findings_total": total_findings, "skipped_binary": skipped_binary, "empty": empty_files, "scope": "utf-8 text files"},
        "t5_review_status": t5_review_status,
        "watermark_status": watermark_status,
        "row_count": row_count,
    }


@router.get("/dataset/bundles", status_code=200)
async def list_dataset_bundles(
    contract_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    """成果包历史（SS-17）：分页返回调用方可见的数据集成果包。

    归属口径与下载端点**同源**：`/dataset/{bundle_id}/download` 经 `_load_contract`
    → `_is_party`（买方 / 提供方，或 admin / platform_admin）判定；这里把同一条规则
    写成 SQL 谓词，既免掉逐行查合约的 N+1，也不会出现"列表里看得见、下载 403"
    （或反之）的口径漂移。合约行缺失的孤儿包因 JOIN 自然不落进列表——与下载端点
    的 404/403 保持同一口径。

    只下发交付包表已落库的字段：T5 审查状态、内容水印状态与数据表行数是打包当时落在行上的
    结论（SS-18 §8 P0-②）；这两列之前的包为 NULL，按「未记录」下发，不从包体反推。
    水印状态自 2026-10-30 起多一个 ``preview_only`` 取值（纯文本数据表包：文件字节未动，
    水印只在平台在线预览层）——历史取值语义不变，前端读不认识的取值原样展示。
    """
    query = (
        select(DeliveryBundle, Contract.contract_no)
        .join(Contract, Contract.id == DeliveryBundle.contract_id)
    )
    if getattr(current_user, "role", None) not in ("admin", "platform_admin"):
        query = query.where(
            or_(Contract.buyer_id == current_user.id, Contract.provider_id == current_user.id)
        )
    if contract_id is not None:
        query = query.where(DeliveryBundle.contract_id == contract_id)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    res = await db.execute(
        query.order_by(DeliveryBundle.created_at.desc(), DeliveryBundle.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [
            {
                "bundle_id": str(bundle.id),
                "contract_id": str(bundle.contract_id),
                "contract_no": contract_no,
                "session_id": str(bundle.session_id) if bundle.session_id else None,
                # 对象名就是交付包的文件名口径（delivery/<bundle_id>.zip）：首页与结果集列表
                # 需要「叫什么名字」，之前只下发 size/file_count，前端只能自己编占位名。
                "file_name": bundle.object_name,
                "file_count": bundle.file_count,
                "size_bytes": bundle.size_bytes,
                "row_count": bundle.row_count,
                "t5_review_status": bundle.t5_review_status,
                "watermark_status": bundle.watermark_status,
                "created_at": bundle.created_at,
                "download_url": f"/api/v1/deliveries/dataset/{bundle.id}/download",
            }
            for bundle, contract_no in res.all()
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
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


PREVIEW_SCAN_CHARS = 200_000
TABULAR_SUFFIXES = (".csv", ".tsv")


def _first_tabular_member(zf: zipfile.ZipFile) -> str | None:
    names = [n for n in zf.namelist() if n.startswith("files/") and not n.endswith("/")]
    for name in names:
        if name.lower().endswith(TABULAR_SUFFIXES):
            return name
    return None


def _excerpt_csv(text: str, rows: int) -> tuple[str, int]:
    lines = text.split("\n")
    total = len([ln for ln in lines if ln.strip() != ""])
    if not lines:
        return "", 0
    return "\n".join(lines[: rows + 1]), max(0, total - 1)


@router.get("/dataset/{bundle_id}/preview")
async def preview_dataset_bundle(
    bundle_id: uuid.UUID,
    rows: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    """预览交付包内首个表格类成果的前 N 行（不下载整包）。

    预览是一条新的数据出域口子，因此比下载更保守：先走与下载完全相同的归属
    与 T5 审查门禁，再对即将返回的片段**重新跑一遍出口审查**，`should_block` 为真
    就直接 403 不返回任何内容字节。包内无表格文件时如实说“只能整包下载”，
    不猜、不用其它文件凑一个表。
    """
    from app.services.output_inspection import output_inspector
    from app.services.output_security import should_block
    from app.services.storage_service import storage_service

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

    if not _bundle_output_reviewed(payload):
        await audit_service.log_standalone(
            action="delivery.dataset.review_required", resource_type="delivery_bundle",
            user_id=current_user.id, resource_id=str(bundle_id), detail={"purpose": "preview"},
        )
        raise CDSError(
            "该交付包生成于出口审查加固之前，未经 T5 审查不予出域，请重新生成交付包",
            code="OUTPUT_REVIEW_REQUIRED",
            http_status=409,
            detail={"bundle_id": str(bundle_id)},
        )

    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        manifest = json.loads(zf.read("MANIFEST.json").decode("utf-8"))
        member = _first_tabular_member(zf)
        members = [n for n in zf.namelist() if n.startswith("files/") and not n.endswith("/")]
        text = zf.read(member).decode("utf-8", errors="replace") if member else ""

    file_review = next(
        (f for f in (manifest.get("files") or []) if f.get("name") == (member or "").removeprefix("files/")),
        None,
    )

    if member is None:
        await audit_service.log(
            db, action="delivery.dataset.preview", resource_type="delivery_bundle",
            user_id=current_user.id, resource_id=str(bundle_id),
            detail={"available": False, "reason": "no_tabular_member", "members": len(members)},
        )
        return {
            "bundle_id": str(bundle_id),
            "available": False,
            "reason": "交付包内没有表格类文件（csv/tsv），只能整包下载后查看",
            "member": None,
            "members": [m.removeprefix("files/") for m in members],
            "output_review_scope": manifest.get("output_review_scope"),
        }

    scan_text = text[:PREVIEW_SCAN_CHARS]
    inspect_result = output_inspector.inspect(
        scan_text,
        user_id=str(current_user.id),
        session_id=str(owner.session_id or bundle_id),
        sandbox_mode="structured_query",
    )
    findings = len(inspect_result.findings or [])
    if should_block(inspect_result):
        await audit_service.log_standalone(
            action="delivery.dataset.preview_blocked", resource_type="delivery_bundle",
            user_id=current_user.id, resource_id=str(bundle_id),
            detail={"member": member, "findings": findings},
        )
        raise CDSError(
            "预览内容命中出口审查规则，已拦截，不返回任何数据字节",
            code="OUTPUT_BLOCKED",
            http_status=403,
            detail={"bundle_id": str(bundle_id), "member": member.removeprefix("files/"), "findings": findings},
        )

    excerpt, total_rows = _excerpt_csv(text, rows)
    await audit_service.log(
        db, action="delivery.dataset.preview", resource_type="delivery_bundle",
        user_id=current_user.id, resource_id=str(bundle_id),
        detail={"available": True, "member": member, "rows": min(rows, total_rows), "findings": findings},
    )
    return {
        "bundle_id": str(bundle_id),
        "available": True,
        "member": member.removeprefix("files/"),
        "data": excerpt,
        "rows_returned": min(rows, total_rows),
        "row_count_total": total_rows,
        "truncated": total_rows > rows or len(text) > PREVIEW_SCAN_CHARS,
        "output_review": {
            "bundle_scope": manifest.get("output_review_scope"),
            "file_status": (file_review or {}).get("output_review"),
            "file_findings": (file_review or {}).get("output_findings"),
            "preview_findings": findings,
            "preview_blocked": False,
        },
    }


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


def _slug_conflict(slug: str) -> CDSError:
    return CDSError("该 slug 已被占用", code="ENDPOINT_SLUG_TAKEN", http_status=409, detail={"slug": slug})


_SLUG_SUFFIX_ATTEMPTS = 20


async def _slug_in_use(db: AsyncSession, slug: str) -> bool:
    """A11：占用探测只读这一行（走 uq_api_endpoints_slug 的等值索引），不再把整表物化成 ORM 对象集。"""
    res = await db.execute(select(ApiEndpoint.id).where(ApiEndpoint.slug == slug).limit(1))
    return res.first() is not None


async def _reserve_slug(db: AsyncSession, name: str, slug: str | None) -> str:
    """定下可用 slug：显式入参撞名直接 409；未给则按 generate_slug 的后缀规则逐个试探。"""
    requested = slug.strip().lower() if slug else ""
    if requested:
        if await _slug_in_use(db, requested):
            raise _slug_conflict(requested)
        return requested

    taken: set[str] = set()
    for _ in range(_SLUG_SUFFIX_ATTEMPTS):
        candidate = dv.generate_slug(name, taken)
        if not await _slug_in_use(db, candidate):
            return candidate
        taken.add(candidate)
    raise CDSError(
        "自动生成的 slug 连续冲突，请显式指定 slug",
        code="ENDPOINT_SLUG_TAKEN",
        http_status=409,
        detail={"prefix": dv.generate_slug(name, set()), "attempts": _SLUG_SUFFIX_ATTEMPTS},
    )


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

    slug = await _reserve_slug(db, body.name, body.slug)

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
    try:
        # 上面的探测与这条 INSERT 之间存在同瞬并发窗口，最终裁判是唯一约束；
        # 用保存点只吞掉这次 INSERT（不动请求事务，鉴权用户与后续审计继续可用）。
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        raise _slug_conflict(slug) from exc
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

OPENAPI_CALL_ACTIONS = ("openapi.call", "openapi.call_failed")
OPENAPI_CALL_BY_STATUS = {"succeeded": ("openapi.call",), "failed": ("openapi.call_failed",)}

# ---------------------------------------------------------------------------
# 拍板③（2026-10-30）：成功率 = 近 7 天窗口内「成功 / 全部调用（分母含被拒调用）」。
#
# 词表全部从 app/ 真实字面量派生，禁止在这里发明动作词或错误码：
#   · 成功/失败的唯一判据 = ``app/api/audit.py::audit_outcome``（失败标记集
#     ``AUDIT_FAILURE_MARKERS = failed/denied/rejected/blocked``）。审计里没有
#     「结果」列，结果只由动作名派生，这条规则由 tests/test_audit_query_filters.py 机检，
#     本端点不另立口径（与 app/api/flow_runtime.py::_audit_window_counts 同一做法）。
#   · 「被拒」= 失败里**没进沙箱执行**的那一类：动作名命中拒绝标记（denied/rejected/blocked，
#     当前 openapi 侧还没有这类动作名，留作前向兼容），或失败行的 ``detail.code`` 是
#     ``OPENAPI_REFUSAL_CODES`` 里的准入拒绝码。执行后才失败的
#     （WORKLOAD_FAILED / OUTPUT_BLOCKED）算 failed，不算被拒。
#   ``tests/test_openapi_call_stats.py`` 机检：拒绝码必须出现在
#   ``delivery_service.execute_endpoint`` 里创建沙箱会话**之前**，执行失败码必须出现在
#   之后 —— 这样「被拒/失败」的分界不是一句注释，而是被源码顺序钉住的事实。
# ---------------------------------------------------------------------------

OPENAPI_CALLS_WINDOW_DAYS = 7
# 拒绝类标记：失败标记集里去掉「执行失败」那一个，剩下的三个都是「没跑起来就被挡下」。
OPENAPI_REJECTION_MARKERS: tuple[str, ...] = tuple(
    marker for marker in AUDIT_FAILURE_MARKERS if marker != "failed"
)
# 准入拒绝码（app/services/delivery_service.py::execute_endpoint 在 create_sandbox_session 之前 raise）
OPENAPI_REFUSAL_CODES: tuple[str, ...] = (
    "ENDPOINT_DISABLED",
    "RATE_LIMITED",
    "QUOTA_EXCEEDED",
    "WORKLOAD_UNAVAILABLE",
    "CALLER_UNKNOWN",
)
# 执行后失败码（同一函数里，会话已经建起来之后才可能抛）
OPENAPI_EXECUTION_FAILURE_CODES: tuple[str, ...] = ("WORKLOAD_FAILED", "OUTPUT_BLOCKED")


def _call_bucket(action: str | None, code: str | None) -> str:
    """一条调用日志归到 succeeded / rejected / failed 三桶之一（三者互斥、并集为全部）。"""
    if audit_outcome(action) == "success":
        return "succeeded"
    lowered = (action or "").lower()
    if any(marker in lowered for marker in OPENAPI_REJECTION_MARKERS):
        return "rejected"
    if (code or "") in OPENAPI_REFUSAL_CODES:
        return "rejected"
    return "failed"


async def _load_endpoint(db: AsyncSession, endpoint_id: uuid.UUID, user: User) -> ApiEndpoint:
    res = await db.execute(select(ApiEndpoint).where(ApiEndpoint.id == endpoint_id))
    endpoint = res.scalar_one_or_none()
    if endpoint is None:
        raise HTTPException(status_code=404, detail="endpoint not found")
    await _load_contract(db, endpoint.contract_id, user)
    return endpoint


def _call_dict(entry: AuditLog, endpoint: ApiEndpoint) -> dict[str, Any]:
    """调用明细一行 = 一条审计日志：失败行没有输出行数/耗时，保持 None，不按 0 交付。

    ``outcome`` 用与 ``stats`` 同一个判据（``_call_bucket``）：明细里的「被拒」和
    KPI 里的被拒数是同一批行，不是两处各数一遍。
    """
    detail = entry.detail or {}
    failed = entry.action == "openapi.call_failed"
    code = detail.get("code") if failed else None
    return {
        "id": str(entry.id),
        "endpoint_id": str(endpoint.id),
        "slug": detail.get("slug") or endpoint.slug,
        "status": "failed" if failed else "succeeded",
        "outcome": _call_bucket(entry.action, code),
        "code": code,
        "message": detail.get("message") if failed else None,
        "output_rows": detail.get("output_rows"),
        "duration_ms": detail.get("duration_ms"),
        "request_sha256": detail.get("request_sha256"),
        "caller_id": str(entry.user_id) if entry.user_id else None,
        "client_ip": entry.ip_address,
        "created_at": entry.created_at,
    }


CALL_WINDOWS: dict[str, int | None] = {
    "all": None,
    "7d": OPENAPI_CALLS_WINDOW_DAYS,
    "30d": 30,
}


async def _call_stats(
    db: AsyncSession,
    *,
    scoped: list,
    window: str,
    since: datetime | None,
    now: datetime,
) -> dict[str, Any]:
    """同一窗口条件下**全部调用动作**的分桶统计（成功 / 失败 / 被拒 + 成功率）。

    - 不受明细的 ``status`` 筛选影响：成功率说的是「这个端点的调用有多成功」，
      不该因为用户在明细里勾了「只看失败」就变成 0%。口径写在 ``status_scope`` 里。
    - 分母 = succeeded + failed + rejected（拍板③：含被拒调用）。一桶都不排除。
    - ``total == 0`` 时 ``success_rate`` 必须是 ``null``，不是 0 也不是 100：
      「没记录」和「全失败/全成功」是三种不同的事实（前端渲染「未记录」）。
    - 分组在 SQL 里做（一个聚合查询），判定在 Python 里做（复用 audit_outcome +
      本模块词表），与 GET /api/v1/audit/records?outcome=failure 逐行一致。
    """
    code_expr = AuditLog.detail["code"].as_string()
    rows = (
        await db.execute(
            select(AuditLog.action, code_expr, func.count())
            .where(*scoped, AuditLog.action.in_(OPENAPI_CALL_ACTIONS))
            .group_by(AuditLog.action, code_expr)
        )
    ).all()
    buckets = {"succeeded": 0, "failed": 0, "rejected": 0}
    for action, code, count in rows:
        buckets[_call_bucket(action, code)] += int(count or 0)
    total = sum(buckets.values())
    succeeded = buckets["succeeded"]
    return {
        "window": window,
        "window_days": CALL_WINDOWS[window],
        "since": since.isoformat() if since else None,
        "until": now.isoformat(),
        "total": total,
        "succeeded": succeeded,
        "failed": buckets["failed"],
        "rejected": buckets["rejected"],
        "success_rate": round(succeeded * 100.0 / total, 1) if total else None,
        "data_available": total > 0,
        "reason": None if total else "no_calls_in_window",
        # 口径披露（前端照抄展示，不再各自解释）
        "denominator": "succeeded + failed + rejected（含被拒调用，一桶都不排除）",
        "rate_caliber": (
            f"成功率 = 成功 / 全部调用；窗口 = {'近 ' + str(CALL_WINDOWS[window]) + ' 天' if CALL_WINDOWS[window] else '全部时间'}"
            "；无调用记录时成功率为 null（不是 0，也不是 100）"
        ),
        "status_scope": "统计不受明细 status 筛选影响，始终覆盖窗口内全部调用",
        "source": "audit_logs（带 Merkle 锚定的唯一调用账本）",
        "vocabulary": {
            "actions": list(OPENAPI_CALL_ACTIONS),
            "failure_markers": list(AUDIT_FAILURE_MARKERS),
            "rejection_markers": list(OPENAPI_REJECTION_MARKERS),
            "refusal_codes": list(OPENAPI_REFUSAL_CODES),
            "execution_failure_codes": list(OPENAPI_EXECUTION_FAILURE_CODES),
        },
        "note": (
            "在鉴权依赖层就被拒的请求（无权限 / 无效凭据）不产生调用日志行，"
            "这类缺口照实说「不补也不猜」，不把分母做小来抬高成功率"
        ),
    }


@router.get("/openapi/endpoints/{endpoint_id}/calls")
async def list_openapi_endpoint_calls(
    endpoint_id: uuid.UUID,
    status_filter: str | None = Query(None, alias="status", pattern="^(succeeded|failed)$"),
    window: str = Query("all", pattern="^(all|7d|30d)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    """端点调用明细 + **同一次查询算出的**调用统计（SS-18 §8 P0-③ / 2026-10-30 拍板③）。

    数据面每次调用都写在带 Merkle 锚定的审计日志上（openapi.call / openapi.call_failed，
    resource_type=api_endpoint），这里按 (action, resource_type, resource_id) 精确读那份日志，
    不再另建一张无人锚定的影子调用表。归属口径与端点增删改同源（_load_contract → _is_party）。

    在依赖层就被拒掉的请求（无权限 / 无效凭据）本来没有调用日志行，这里不补也不猜。

    ``stats`` 与 ``items`` 在同一个响应里、同一份窗口条件上算出来：ss-19 §10.2 记的
    「KPI 说 100% 而明细里有失败」正是 KPI 与明细分两次查、各算各的口径出来的。现在
    监控页的 KPI 只能读 ``stats``，明细只能读 ``items``，两者共享 ``window``。
    统计的窗口口径默认近 7 天（``window=7d``）；``window=all`` 保持 SS-18 原全量明细行为，
    统计随之变成全量并在 ``window`` 字段里照实写 "all"，不假装还是 7 天。
    """
    endpoint = await _load_endpoint(db, endpoint_id, current_user)
    now = datetime.now(timezone.utc)
    since = None if CALL_WINDOWS[window] is None else now - timedelta(days=CALL_WINDOWS[window])
    scoped = [
        AuditLog.resource_type == "api_endpoint",
        AuditLog.resource_id == str(endpoint.id),
    ]
    if since is not None:
        scoped.append(AuditLog.created_at >= since)
    wanted = OPENAPI_CALL_BY_STATUS[status_filter] if status_filter else OPENAPI_CALL_ACTIONS
    stmt = select(AuditLog).where(*scoped, AuditLog.action.in_(wanted))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    res = await db.execute(
        stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return {
        "items": [_call_dict(entry, endpoint) for entry in res.scalars().all()],
        "total": total,
        "page": page,
        "page_size": page_size,
        "window": window,
        "window_days": CALL_WINDOWS[window],
        "since": since.isoformat() if since else None,
        "until": now.isoformat(),
        "stats": await _call_stats(db, scoped=scoped, window=window, since=since, now=now),
    }
