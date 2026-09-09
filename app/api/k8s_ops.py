"""N8: K8s runtime operations API (read-only, ADMIN/OPERATOR).

Operator-facing views over the K8s sandbox runtime (N7): deployments (sandbox
pods), network policies, shared-volume PVCs and pod/session logs. Every
endpoint is honest about cluster availability — without a reachable cluster
the payloads degrade to DB-backed state with ``cluster_available: false``
(never a fabricated live list).
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.network_policy import NetworkPolicy
from app.models.sandbox_session import SandboxSession
from app.models.shared_volume import SharedVolume, SharedVolumeAttachment
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)

router = APIRouter()


def _cluster_view() -> tuple[object | None, str]:
    """Return (k8s_sandbox_adapter|None, namespace). Adapter is None when the
    python client or kubectl is unavailable — callers degrade honestly."""
    try:
        from app.services.k8s_sandbox import get_k8s_adapter
        from app.core.config import get_settings
        adapter = get_k8s_adapter()
        namespace = getattr(adapter, "namespace", None) or get_settings().SANDBOX_K8S_NAMESPACE
        return adapter, namespace
    except Exception as e:
        logger.warning("[ops] k8s adapter unavailable: %s", e)
        return None, "cds-sandbox"


@router.get("/deployments")
async def list_deployments(
    user_id: str | None = Query(None, max_length=64),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """List K8s sandbox pods (deployments) with live phase/readiness.

    Reads from the live cluster when the python client is available; returns
    an empty list with ``cluster_available: false`` otherwise.
    """
    adapter, namespace = _cluster_view()
    if adapter is None or getattr(adapter, "_k8s", None) is None:
        return {"cluster_available": False, "namespace": namespace, "items": [], "count": 0}
    try:
        pods = adapter.list_sandboxes(user_id=user_id)
        items = []
        for pod in pods:
            status = adapter.get_status(pod["pod_name"])
            items.append({
                "pod_name": pod["pod_name"],
                "phase": status.get("phase", pod.get("phase", "Unknown")),
                "cds_status": status.get("cds_status", "unknown"),
                "ready": bool(status.get("ready", False)),
                "pod_ip": status.get("pod_ip", ""),
                "user_id": pod.get("user_id", ""),
                "session_id": pod.get("session_id", ""),
                "reason": status.get("reason", ""),
            })
        return {"cluster_available": True, "namespace": namespace, "items": items, "count": len(items)}
    except Exception as e:
        logger.warning("[ops] deployments list failed: %s", e)
        return {"cluster_available": False, "namespace": namespace, "items": [], "count": 0, "error": str(e)}


@router.get("/network-policies")
async def list_ops_network_policies(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """List session network policies (ops view across all sessions).

    DB-backed (authoritative policy intent) with a live-cluster check for the
    actual applied K8s NetworkPolicy state.
    """
    total = (await db.execute(select(func.count()).select_from(NetworkPolicy))).scalar() or 0
    rows = (await db.execute(
        select(NetworkPolicy).order_by(NetworkPolicy.id).offset(skip).limit(limit)
    )).scalars().all()

    adapter, _namespace = _cluster_view()
    live_names = set()
    cluster_available = adapter is not None and getattr(adapter, "_k8s", None) is not None
    if cluster_available:
        try:
            live = adapter._k8s.list_pods()
            live_names = {pod.metadata.name for pod in live}
        except Exception as e:
            logger.warning("[ops] network-policy live check failed: %s", e)
            cluster_available = False

    items = []
    for p in rows:
        pod_name = p.session_id[:16] if p.session_id else ""
        live_pod = bool(live_names) and (f"sandbox-{pod_name}" in live_names or pod_name in live_names)
        items.append({
            "session_id": p.session_id,
            "mode": p.mode,
            "allowed_ips": p.allowed_ips or [],
            "allowed_domains": p.allowed_domains or [],
            "allowed_ports": p.allowed_ports or [],
            "dns_proxy_enabled": bool(p.dns_proxy_enabled),
            "live_pod": live_pod,
        })
    return {
        "cluster_available": cluster_available,
        "items": items,
        "total": total,
        "page": skip // limit + 1,
        "page_size": limit,
    }


@router.get("/pvcs")
async def list_ops_pvcs(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """List shared volumes with their K8s PVC claim state.

    DB-backed volume/attachment records (authoritative) plus best-effort live
    PVC status from the cluster when the python client is available.
    """
    rows = (await db.execute(
        select(SharedVolume).order_by(SharedVolume.created_at.desc())
    )).scalars().all()
    attach_rows = (await db.execute(
        select(SharedVolumeAttachment, SharedVolume)
        .join(SharedVolume, SharedVolume.id == SharedVolumeAttachment.volume_id)
    )).all()
    attachments_by_volume: dict[str, list[dict]] = {}
    for att, _vol in attach_rows:
        key = str(att.volume_id)
        attachments_by_volume.setdefault(key, []).append({
            "session_id": str(att.session_id),
            "read_only": bool(att.read_only),
        })

    adapter, _namespace = _cluster_view()
    cluster_available = adapter is not None and getattr(adapter, "_k8s", None) is not None
    live_claims: dict[str, dict] = {}
    if cluster_available:
        try:
            for pvc in adapter._k8s.list_pvcs():
                live_claims[pvc.metadata.name] = {
                    "phase": getattr(pvc.status, "phase", "Unknown"),
                    "capacity": (pvc.status.capacity or {}).get("storage"),
                    "access_modes": getattr(pvc.spec, "access_modes", None) or [],
                    "storage_class": getattr(getattr(pvc.spec, "storage_class_name", None), "value", None)
                    if hasattr(getattr(pvc.spec, "storage_class_name", None), "value") else getattr(pvc.spec, "storage_class_name", None),
                }
        except Exception as e:
            logger.warning("[ops] pvc live check failed: %s", e)
            cluster_available = False

    items = []
    for v in rows:
        claim_name = f"cds-shared-{v.id}"
        live = live_claims.get(claim_name)
        items.append({
            "volume_id": str(v.id),
            "name": v.name,
            "owner_id": str(v.owner_id),
            "size_limit_mb": v.size_limit_mb,
            "read_only": bool(v.read_only),
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "claim_name": claim_name,
            "attachments": attachments_by_volume.get(str(v.id), []),
            "pvc_status": live.get("phase", "unavailable") if live else ("Bound" if cluster_available else "unavailable"),
            "pvc_capacity": live.get("capacity") if live else None,
            "pvc_access_modes": live.get("access_modes", []) if live else [],
            "pvc_storage_class": live.get("storage_class") if live else None,
        })
    return {"cluster_available": cluster_available, "items": items, "count": len(items)}


@router.get("/logs/{session_id}")
async def get_ops_logs(
    session_id: uuid.UUID,
    tail: int = Query(200, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Tail logs for a sandbox session.

    K8s sessions with a live cluster return the pod stdout/stderr (source
    ``k8s``); otherwise the DB audit trail for the session is returned
    (source ``audit``). Honest about which source produced the logs.
    """
    session = (await db.execute(
        select(SandboxSession).where(SandboxSession.id == session_id)
    )).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    adapter, _namespace = _cluster_view()
    if (
        getattr(session, "sandbox_level", None) == "k8s"
        and adapter is not None
        and getattr(adapter, "_k8s", None) is not None
        and getattr(session, "container_id", None)
    ):
        pod_name = f"sandbox-{str(session.container_id).replace('k8s-', '')[:16]}"
        try:
            raw = adapter._k8s.pod_logs(pod_name)
            lines = [ln for ln in raw.splitlines() if ln.strip()][-tail:]
            return {
                "session_id": str(session_id),
                "source": "k8s",
                "pod_name": pod_name,
                "cluster_available": True,
                "logs": [{"line": ln, "stream": "stdout"} for ln in lines],
                "count": len(lines),
            }
        except Exception as e:
            logger.warning("[ops] pod logs failed for %s: %s", pod_name, e)
            return {
                "session_id": str(session_id),
                "source": "k8s",
                "pod_name": pod_name,
                "cluster_available": True,
                "logs": [],
                "count": 0,
                "error": str(e),
            }

    # Audit-trail fallback (works without a cluster).
    from app.models.audit_log import AuditLog
    rows = (await db.execute(
        select(AuditLog).where(AuditLog.session_id == session_id)
        .order_by(AuditLog.created_at.desc()).limit(tail)
    )).scalars().all()
    return {
        "session_id": str(session_id),
        "source": "audit",
        "pod_name": None,
        "cluster_available": False,
        "logs": [
            {
                "line": f"[{row.action}] {(row.detail or '')[:200]}",
                "stream": "audit",
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
        "count": len(rows),
    }
