"""Cube template pipeline (SS-10 CB-05): image → artifact → node distribution.

Build (spec §4 CB-05 step 2, 红线 2): the rootfs conversion tool runs on an
INDEPENDENT build machine — the CDS build host, never a compute node. The
self-built fallback script ``scripts/cube-rootfs-convert.sh`` (docker export →
mkfs.ext4 → populate → e2fsck → artifact + sha256 + size) replaces the upstream
TC build chain, which is a Go service coupled to CubeTemplateCenter (M1 spike
verdict: not reusable as a script; see repo report / spec §8 execution record).

The artifact (ext4 rootfs) lands in CDS MinIO via the existing
``storage_service`` client. Distribution (step 3) issues a MinIO presigned
URL (TTL) per node and drives the node-side ``Images`` gRPC service
(``CreateImage`` with the ``cube.master.rootfs.artifact.*`` annotations —
upstream contract ``Cubelet/pkg/constants/const.go:242-247``; the node
downloads from the URL, sends ``X-Cube-Artifact-Token``, and self-verifies
sha256 — ``Cubelet/internal/cube/server/images/ext4image/utils.go``).
``ListImages`` then verifies (size + echoed sha annotation); sha mismatch →
node row FAILED + critical alert (不降级).

Node consumption of the presigned URL is pass-through by design: upstream
``rewriteDownloadHost`` leaves S3/MinIO presigned URLs (``X-Amz-Signature``)
untouched (regression test ``TestRewriteDownloadHostLeavesS3PresignedURLUntouched``).

GC (step 5): template delete → per-node ``DestroyImage``; Conflict (artifact
in use) → the node row stays, deferred to the next GC round. This mirrors the
upstream ``isIgnorableArtifactDeleteError`` handling: Conflict on artifact
delete is a *protection* signal (some sandbox still references the artifact),
not an error — the delete is idempotent and retried by the next round.

Scheduling (step 4): ``ready_node_ids`` feeds the CB-01 selector's
template-locality filter (``cube_runtime_adapter.apply_template_locality``);
empty READY set → 503 ``NO_NODE_TEMPLATE`` (W2, registered in
``docs/error-codes.md``).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import subprocess
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app._stubs.cube.cubebox.v1 import cubebox_pb2
from app._stubs.cube.images.v1 import images_pb2, images_pb2_grpc
from app.core.config import get_settings
from app.core.errors import CDSError
from app.models.cube_template import (
    CubeTemplate,
    CubeTemplateNode,
    CubeTemplateNodeStatus,
    CubeTemplateStatus,
)
from app.services.cube_node_client import _map_grpc_status, map_ret_code

import grpc

logger = logging.getLogger(__name__)

# ── Upstream contract constants ──────────────────────────────────────────────
# Artifact download annotations (upstream Cubelet/pkg/constants/const.go:242-247;
# contract-watch path scripts/cube-contract-paths.txt tracks const.go).
ART_ANNOT_ID = "cube.master.rootfs.artifact.id"
ART_ANNOT_JOB_ID = "cube.master.rootfs.artifact.job_id"
ART_ANNOT_URL = "cube.master.rootfs.artifact.url"
ART_ANNOT_TOKEN = "cube.master.rootfs.artifact.token"
ART_ANNOT_SHA256 = "cube.master.rootfs.artifact.sha256"
ART_ANNOT_SIZE = "cube.master.rootfs.artifact.size_bytes"
# Node-side AppSnapshot primitive (upstream const.go:193-194).
APPSNAP_CREATE_ANNOT = "cube.master.appsnapshot.create"
APPSNAP_TEMPLATE_ID_ANNOT = "cube.master.appsnapshot.template.id"

_MINIO_OBJECT_PREFIX = "cube-templates"

# C4 readiness semantics: HTTP probe 2xx = READY (probe config travels with
# the registry row, spec CB-05 step 2).
DEFAULT_PROBE_CONFIG = {
    "type": "http",
    "port": 80,
    "path": "/",
    "expected_status": "2xx",
}

# ── State machines ───────────────────────────────────────────────────────────
_TEMPLATE_TRANSITIONS: dict[str, set[str]] = {
    CubeTemplateStatus.BUILDING.value: {CubeTemplateStatus.DISTRIBUTING.value},
    CubeTemplateStatus.DISTRIBUTING.value: {
        CubeTemplateStatus.DISTRIBUTING.value,
        CubeTemplateStatus.READY.value,
    },
    CubeTemplateStatus.READY.value: set(),
}
_NODE_TRANSITIONS: dict[str, set[str]] = {
    CubeTemplateNodeStatus.PENDING.value: {
        CubeTemplateNodeStatus.DISTRIBUTING.value,
        CubeTemplateNodeStatus.FAILED.value,
    },
    CubeTemplateNodeStatus.DISTRIBUTING.value: {
        CubeTemplateNodeStatus.READY.value,
        CubeTemplateNodeStatus.FAILED.value,
    },
    # FAILED → retry re-distribution (new presigned URL + token).
    CubeTemplateNodeStatus.FAILED.value: {CubeTemplateNodeStatus.DISTRIBUTING.value},
    CubeTemplateNodeStatus.READY.value: set(),
}


class TemplateError(CDSError):
    """Template-pipeline domain error (W2-contract CDSError subclass)."""


def _check_transition(kind: str, src: str, dst: str) -> None:
    table = _TEMPLATE_TRANSITIONS if kind == "template" else _NODE_TRANSITIONS
    allowed = table.get(src, set())
    if dst not in allowed:
        raise TemplateError(
            f"invalid {kind} transition {src} -> {dst}",
            code="VALIDATION_ERROR",
            http_status=400,
            detail={"from": src, "to": dst},
        )


# ── Artifact capability token (CDS_CUBE_TEMPLATE_SIGNING_KEY) ───────────────
def _b64url(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def issue_artifact_token(artifact_id: str, sha256: str, ttl_seconds: int) -> tuple[str, int]:
    """Sign an artifact download token (HMAC-SHA256 over id|sha|expires).

    The node sends it as ``X-Cube-Artifact-Token``; CDS re-verifies it on the
    retry path. Without a configured signing key (dev/test only) the token is
    marked ``unsigned`` — disclosed via warning, never a silent fake in prod
    (``validate_security_config`` RAISEs when the key is absent in production).
    """
    expires = int(time.time()) + max(1, ttl_seconds)
    payload = f"{artifact_id}|{sha256}|{expires}"
    key = get_settings().CUBE_TEMPLATE_SIGNING_KEY
    if not key:
        logger.warning(
            "[CubeTemplate] CDS_CUBE_TEMPLATE_SIGNING_KEY not set — issuing "
            "UNSIGNED artifact token (dev/test only; production is gated)"
        )
        return f"unsigned.{_b64url(payload)}", expires
    sig = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"v1.{_b64url(payload)}.{sig}", expires


def verify_artifact_token(artifact_id: str, sha256: str, token: str) -> bool:
    key = get_settings().CUBE_TEMPLATE_SIGNING_KEY
    parts = (token or "").split(".")
    if len(parts) != 3 or parts[0] != "v1":
        return False
    if not key:
        return False
    try:
        payload = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)).decode()
    except Exception:  # noqa: BLE001 — malformed token
        return False
    pid, psha, pexp = payload.split("|", 2)
    if pid != artifact_id or psha != sha256:
        return False
    if int(pexp) < time.time():
        return False  # TTL expired → re-issue
    want = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, parts[2])


# ── Rootfs conversion (build machine; 红线 2 — no compute node involved) ────
def convert_rootfs(image_ref: str, out_dir: str, script_path: str | None = None) -> dict:
    """Run the fallback build tool: image → ext4 artifact + sha256 + size.

    Emits one JSON object on stdout:
    ``{"artifact_path", "sha256", "size_bytes", "probe_config"}``.
    Raises TemplateError on any failure (docker absent, mkfs/e2fsck failure) —
    no silent fallback, no simulated artifact.
    """
    script = script_path or str(
        Path(__file__).resolve().parent.parent.parent / "scripts" / "cube-rootfs-convert.sh"
    )
    if not os.path.exists(script):
        raise TemplateError(
            f"rootfs conversion script not found: {script}",
            code="RUNTIME_UNAVAILABLE",
            http_status=500,
            detail={"reason": "conversion_script_missing"},
        )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["bash", script, image_ref, str(out)],
            capture_output=True,
            text=True,
            timeout=3600,
        )
    except FileNotFoundError as exc:
        raise TemplateError(
            f"rootfs conversion unavailable (bash/docker missing): {exc}",
            code="RUNTIME_UNAVAILABLE",
            http_status=500,
            detail={"reason": "build_tool_unavailable"},
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise TemplateError(
            "rootfs conversion timed out (3600s)",
            code="REQUEST_TIMEOUT",
            http_status=408,
            detail={"reason": "conversion_timeout"},
        ) from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise TemplateError(
            f"rootfs conversion failed (exit {proc.returncode}): {tail}",
            code="RUNTIME_UNAVAILABLE",
            http_status=500,
            detail={"reason": "conversion_failed"},
        )
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise TemplateError(
            f"rootfs conversion produced no JSON result: {proc.stdout[-300:]}",
            code="INTERNAL_ERROR",
            http_status=500,
            detail={"reason": "conversion_bad_output"},
        ) from exc
    sha = str(result.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise TemplateError(
            "rootfs conversion result has no valid sha256",
            code="INTERNAL_ERROR",
            http_status=500,
            detail={"reason": "conversion_bad_sha"},
        )
    return {
        "artifact_path": result["artifact_path"],
        "sha256": sha,
        "size_bytes": int(result["size_bytes"]),
        "probe_config": result.get("probe_config") or dict(DEFAULT_PROBE_CONFIG),
    }


# ── Images gRPC client (same per-node channel as the CubeboxMgr client) ─────
class CubeImagesClient:
    """Typed wrappers for the node-side ``Images`` service (CreateImage /
    ListImages / DestroyImage) over an existing ``CubeNodeClient`` channel."""

    def __init__(self, node_client: Any, stub: Any = None):
        # ``stub``: injectable ImagesStub (tests use proto-level fakes; the
        # default builds the real stub over the node client's channel).
        self._stub = stub if stub is not None else images_pb2_grpc.ImagesStub(node_client._channel)
        self._deadline_ms = getattr(node_client, "_deadline_ms", 5000)

    def _call(self, rpc: str, method, request) -> Any:
        request.requestID = str(uuid.uuid4())
        try:
            resp = method(request, timeout=self._deadline_ms / 1000.0)
        except Exception as exc:  # noqa: BLE001 — grpc.RpcError + friends
            if isinstance(exc, grpc.RpcError):
                status, code, retry_after = _map_grpc_status(exc.code())
                raise CDSError(
                    f"cubelet {rpc} RPC failed: {exc.details()}",
                    code=code,
                    http_status=status,
                    retry_after=retry_after,
                    detail={"rpc": rpc, "grpc_status": str(exc.code())},
                ) from exc
            raise CDSError(
                f"cubelet {rpc} RPC failed: {exc}",
                code="INTERNAL_ERROR",
                http_status=500,
                detail={"rpc": rpc},
            ) from exc
        err = map_ret_code(getattr(resp, "ret", None))
        if err is not None:
            raise err
        return resp

    @staticmethod
    def _spec(artifact_id: str, annotations: dict[str, str] | None = None) -> images_pb2.ImageSpec:
        spec = images_pb2.ImageSpec(image=artifact_id, storage_media="ext4")
        for key, value in (annotations or {}).items():
            spec.annotations[key] = value
        return spec

    def create_image(self, spec: images_pb2.ImageSpec) -> images_pb2.CreateImageRequestResponse:
        return self._call("CreateImage", self._stub.CreateImage, images_pb2.CreateImageRequest(spec=spec))

    def list_images(self, artifact_id: str) -> images_pb2.CubeListImageResponse:
        req = images_pb2.CubeListImageRequest()
        req.filter.image.image = artifact_id
        return self._call("ListImages", self._stub.ListImages, req)

    def destroy_image(self, spec: images_pb2.ImageSpec) -> images_pb2.DestroyImageResponse:
        return self._call("DestroyImage", self._stub.DestroyImage, images_pb2.DestroyImageRequest(spec=spec))


def fire_critical_alert(alert_type: str, message: str, dedup_key: str | None = None) -> None:
    """Durable critical AlertRecord (same path as CB-01's destroy-conflict alert).

    ``dedup_key`` is stable per failure identity (AlertRecord.dedup_key is
    NOT NULL + unique): a repeat of the same failure within the dedup window
    hits the existing row instead of raising."""
    logger.error("[CubeTemplate][CRITICAL] %s: %s", alert_type, message)

    async def _persist() -> None:
        from sqlalchemy.exc import IntegrityError

        from app.core.database import async_session
        from app.models.alert import AlertRecord

        async with async_session() as db:
            db.add(
                AlertRecord(
                    dedup_key=(dedup_key or f"{alert_type}:{uuid.uuid4().hex[:16]}")[:128],
                    alert_type=alert_type,
                    severity="critical",
                    message=message[:2000],
                    metadata={"domain": "cube_template"},
                )
            )
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()  # duplicate within the dedup window — already recorded

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_persist())
    except RuntimeError:
        try:
            asyncio.run(_persist())
        except Exception as exc:  # noqa: BLE001 — alert persist must not raise
            logger.error("[CubeTemplate] failed to persist critical alert: %s", exc)


def minio_object_name(artifact_id: str) -> str:
    return f"{_MINIO_OBJECT_PREFIX}/{artifact_id}.ext4"


def _minio_client_or_none():
    from app.services.storage_service import storage_service

    return storage_service._get_minio_client()


def presign_artifact_url(artifact_id: str, ttl_seconds: int) -> str | None:
    """MinIO presigned GET URL (SigV4; the node passes it through unchanged).

    Returns None when MinIO is unreachable (local-fallback dev mode) — callers
    must surface that honestly (the node could not download the artifact).
    """
    settings = get_settings()
    client = _minio_client_or_none()
    if client is None:
        return None
    return client.presigned_get_object(
        settings.MINIO_BUCKET,
        minio_object_name(artifact_id),
        expires=timedelta(seconds=ttl_seconds),
    )


def upload_artifact(artifact_path: str, artifact_id: str) -> str:
    """Upload the built artifact to CDS MinIO (plaintext: it is a scenario
    image, and the node must download the raw ext4 — envelope encryption is
    not viable for the distribution path).

    The object key is ``minio_object_name(artifact_id)`` — the very key
    ``presign_artifact_url`` signs (A91). Nodes pull the presigned URL, so a
    key derived from the local file name (the conversion script always emits
    ``artifact.ext4``) would hand every node a NoSuchKey 404 and make every
    template overwrite one shared object.
    """
    settings = get_settings()
    client = _minio_client_or_none()
    object_name = minio_object_name(artifact_id)
    if client is not None:
        client.fput_object(settings.MINIO_BUCKET, object_name, artifact_path)
        return object_name
    # Local-fallback mode (no MinIO): keep the file, disclose the path.
    logger.warning(
        "[CubeTemplate] MinIO unavailable — artifact kept at local path %s "
        "(dev/test only; nodes cannot download it)",
        artifact_path,
    )
    return artifact_path


# ── Service ──────────────────────────────────────────────────────────────────
NodeClientFactory = Callable[[Any], Any]


class CubeTemplateService:
    """Template registry + build + distribution + GC (spec CB-05 steps 1-6)."""

    def __init__(
        self,
        client_factory: NodeClientFactory | None = None,
        images_factory: Callable[[Any], Any] | None = None,
    ):
        # node (SandboxNode-like) → CubeNodeClient (CB-01 pool by default).
        self._client_factory = client_factory
        # node client → Images-gRPC client (default: CubeImagesClient over the
        # same channel; tests inject proto-level fakes).
        self._images_factory = images_factory or (lambda client: CubeImagesClient(client))

    # ── resolution helpers (scheduling + JSON template mapping) ─────────────
    async def get_by_ref(self, db: AsyncSession, ref: str) -> CubeTemplate | None:
        """Resolve a cube template by UUID id or by name."""
        ref = (ref or "").strip()
        if not ref:
            return None
        try:
            return await db.get(CubeTemplate, uuid.UUID(ref))
        except ValueError:
            pass
        result = await db.execute(select(CubeTemplate).where(CubeTemplate.name == ref))
        return result.scalar_one_or_none()

    async def resolve_cube_template(self, db: AsyncSession, ref: str) -> CubeTemplate | None:
        """`template` field resolution (spec CB-05 step 6).

        Order: (1) direct cube template by id/name; (2) Round 40 JSON session
        template that declares ``cube_template_id`` → resolve that.
        """
        tpl = await self.get_by_ref(db, ref)
        if tpl is not None:
            return tpl
        from app.services import session_templates as st

        json_tpl = st.get_template(ref)
        declared = (json_tpl or {}).get("cube_template_id")
        if declared:
            return await self.get_by_ref(db, str(declared))
        return None

    async def ready_node_ids(self, db: AsyncSession, template_id: uuid.UUID) -> set[str]:
        result = await db.execute(
            select(CubeTemplateNode.node_id).where(
                CubeTemplateNode.template_id == template_id,
                CubeTemplateNode.status == CubeTemplateNodeStatus.READY.value,
            )
        )
        return {row[0] for row in result.all()}

    # ── registry (step 1) ───────────────────────────────────────────────────
    async def create_template(
        self,
        db: AsyncSession,
        *,
        name: str,
        version: str = "1.0.0",
        image_ref: str | None = None,
        created_by: str | None = None,
        probe_config: dict | None = None,
    ) -> CubeTemplate:
        existing = await db.execute(
            select(CubeTemplate).where(CubeTemplate.name == name, CubeTemplate.version == version)
        )
        if existing.scalar_one_or_none() is not None:
            raise TemplateError(
                f"cube template {name!r} v{version} already exists",
                code="VALIDATION_ERROR",
                http_status=400,
                detail={"name": name, "version": version},
            )
        tpl = CubeTemplate(
            name=name,
            version=version,
            artifact_id=f"rfs-{uuid.uuid4().hex[:12]}",
            image_ref=image_ref,
            status=CubeTemplateStatus.BUILDING.value,
            probe_config=probe_config or dict(DEFAULT_PROBE_CONFIG),
            created_by=created_by,
        )
        db.add(tpl)
        await db.flush()
        return tpl

    @staticmethod
    def transition_template(tpl: CubeTemplate, status: str) -> None:
        _check_transition("template", tpl.status, status)
        tpl.status = status

    @staticmethod
    def transition_node(row: CubeTemplateNode, status: str) -> None:
        _check_transition("node", row.status, status)
        row.status = status

    # ── build (step 2, W11 async operation model) ───────────────────────────
    @staticmethod
    def op_session_id(template_id: uuid.UUID) -> uuid.UUID:
        # W11 SessionOperation rows are keyed by session_id; template
        # operations use a stable derived id (no session exists for builds).
        return uuid.uuid5(uuid.NAMESPACE_URL, f"cds-cube-template/{template_id}")

    async def build_and_distribute(self, template_id: uuid.UUID) -> dict:
        """Full build pipeline: convert → MinIO → AppSnapshot → distribute.

        Runs on the CDS build host (独立构建机, 红线 2); compute nodes are
        only touched for the AppSnapshot compatibility check + distribution.
        Returns the result_ref payload for the W11 operation row.
        """
        settings = get_settings()
        from app.core.database import async_session

        async with async_session() as db:
            tpl = await db.get(CubeTemplate, template_id)
            if tpl is None:
                raise TemplateError("template vanished during build", code="SESSION_NOT_FOUND", http_status=404)
            work_dir = f"/tmp/cds-cube-template-build/{tpl.artifact_id}"
            # (a) rootfs conversion on the build machine (M1 fallback path;
            # TC chain verdict: Go service, not reusable as a script).
            conv = await asyncio.to_thread(convert_rootfs, tpl.image_ref or "", work_dir)
            # (b) upload artifact to CDS MinIO → register sha256/size.
            object_name = await asyncio.to_thread(upload_artifact, conv["artifact_path"], tpl.artifact_id)
            tpl.sha256 = conv["sha256"]
            tpl.size_bytes = conv["size_bytes"]
            if isinstance(tpl.probe_config, dict) and not tpl.probe_config:
                tpl.probe_config = dict(DEFAULT_PROBE_CONFIG)
            self.transition_template(tpl, CubeTemplateStatus.DISTRIBUTING.value)
            await db.commit()

        await self._snapshot_check(template_id)

        # (c) distribution fan-out (step 3).
        await self.distribute(template_id)
        result_ref = await self._template_result(template_id)
        await self._finalize_template_status(template_id)
        return result_ref

    async def _template_result(self, template_id: uuid.UUID) -> dict:
        from app.core.database import async_session

        async with async_session() as db:
            tpl = await db.get(CubeTemplate, template_id)
            rows = list(
                (await db.execute(
                    select(CubeTemplateNode).where(CubeTemplateNode.template_id == template_id)
                )).scalars().all()
            )
            return {
                "template_id": str(template_id),
                "artifact_id": tpl.artifact_id,
                "sha256": tpl.sha256,
                "size_bytes": tpl.size_bytes,
                "nodes": {r.node_id: r.status for r in rows},
            }

    async def _finalize_template_status(self, template_id: uuid.UUID) -> None:
        """READY ⇔ ≥1 node READY and none PENDING/DISTRIBUTING (FAILED stays
        retryable via the re-distribute path)."""
        from app.core.database import async_session

        async with async_session() as db:
            tpl = await db.get(CubeTemplate, template_id)
            rows = list(
                (await db.execute(
                    select(CubeTemplateNode).where(CubeTemplateNode.template_id == template_id)
                )).scalars().all()
            )
            statuses = [r.status for r in rows]
            if (
                rows
                and CubeTemplateNodeStatus.READY.value in statuses
                and not any(
                    s in (
                        CubeTemplateNodeStatus.PENDING.value,
                        CubeTemplateNodeStatus.DISTRIBUTING.value,
                    )
                    for s in statuses
                )
                and tpl.status == CubeTemplateStatus.DISTRIBUTING.value
            ):
                tpl.status = CubeTemplateStatus.READY.value
                await db.commit()

    async def _snapshot_check(self, template_id: uuid.UUID) -> dict:
        """Stage (b): AppSnapshot on one online cube node (node primitive).

        With the default xfs backend the snapshot bytes stay node-local (the
        canonical distributable artifact is the CDS MinIO object from stage
        a); ``backend=s3`` (CB-07) pushes directly to CDS MinIO. The RPC is
        the node-side compatibility check + the template.id binding.
        """
        from app.core.database import async_session
        from app.models.sandbox_node import NodeStatus, SandboxNode
        from app.services.cube_node_client import node_cubelet_endpoint

        async with async_session() as db:
            result = await db.execute(
                select(SandboxNode)
                .where(
                    SandboxNode.status == NodeStatus.ONLINE.value,
                    SandboxNode.scheduling_disabled == False,  # noqa: E712
                )
                .order_by(SandboxNode.node_id)
            )
            nodes = [n for n in result.scalars().all() if node_cubelet_endpoint(n)]
        if not nodes:
            raise TemplateError(
                "no online cube node for the AppSnapshot compatibility check",
                code="NO_NODE_TEMPLATE",
                http_status=503,
                detail={"reason": "no_online_node"},
            )
        node = nodes[0]
        client = self._client_factory(node) if self._client_factory else _default_node_client(node)
        async with async_session() as db:
            tpl = await db.get(CubeTemplate, template_id)
            req = _build_app_snapshot_request(tpl.artifact_id)
        resp = await asyncio.to_thread(client.app_snapshot, req)
        result = {
            "node_id": node.node_id,
            "rootfs_size_bytes": int(getattr(resp, "rootfs_size_bytes", 0) or 0),
            "template_id": getattr(resp, "templateID", "") or "",
            "snapshot_path": getattr(resp, "snapshotPath", "") or "",
        }
        logger.info("[CubeTemplate] AppSnapshot ok on %s: %s", node.node_id, result)
        return result

    # ── distribution (step 3) ───────────────────────────────────────────────
    async def _target_nodes(self) -> list[Any]:
        """All online, schedulable cube nodes — deterministic node_id order."""
        from app.core.database import async_session
        from app.models.sandbox_node import NodeStatus, SandboxNode
        from app.services.cube_node_client import node_cubelet_endpoint

        async with async_session() as db:
            result = await db.execute(
                select(SandboxNode)
                .where(
                    SandboxNode.status == NodeStatus.ONLINE.value,
                    SandboxNode.scheduling_disabled == False,  # noqa: E712
                )
                .order_by(SandboxNode.node_id)
            )
            rows = list(result.scalars().all())
            return [n for n in rows if node_cubelet_endpoint(n)]

    async def distribute(self, template_id: uuid.UUID, node_ids: list[str] | None = None) -> dict:
        """Per-node CreateImage (artifact annotations) + ListImages sha verify.

        Order is deterministic (node_id ascending). sha mismatch → node row
        FAILED + critical alert (不降级); missing image after the retry
        budget → FAILED (covers presigned-URL TTL expiry: each attempt
        re-issues a fresh URL + token).
        """
        settings = get_settings()
        from app.core.database import async_session

        async with async_session() as db:
            tpl = await db.get(CubeTemplate, template_id)
            if tpl is None:
                raise TemplateError("template not found", code="SESSION_NOT_FOUND", http_status=404)
            if tpl.sha256 is None or tpl.size_bytes is None:
                raise TemplateError(
                    "template artifact not registered (sha256/size missing) — build first",
                    code="VALIDATION_ERROR",
                    http_status=400,
                    detail={"template_id": str(template_id)},
                )
            if tpl.status == CubeTemplateStatus.BUILDING.value:
                self.transition_template(tpl, CubeTemplateStatus.DISTRIBUTING.value)
            elif tpl.status != CubeTemplateStatus.DISTRIBUTING.value:
                raise TemplateError(
                    f"template in state {tpl.status} cannot be distributed",
                    code="VALIDATION_ERROR",
                    http_status=400,
                    detail={"status": tpl.status},
                )
            await db.commit()

        nodes = await self._target_nodes()
        if node_ids is not None:
            wanted = set(node_ids)
            nodes = [n for n in nodes if n.node_id in wanted]
        outcomes: dict[str, str] = {}
        for node in nodes:
            outcomes[node.node_id] = await self._distribute_one(tpl, node)

        await self._finalize_template_status(template_id)
        async with async_session() as db:
            tpl = await db.get(CubeTemplate, template_id)
            return {"nodes": outcomes, "template_status": tpl.status}

    async def _distribute_one(self, tpl: CubeTemplate, node: Any) -> str:
        from app.core.database import async_session

        settings = get_settings()
        retries = max(1, int(settings.CUBE_TEMPLATE_DIST_RETRY))
        async with async_session() as db:
            row = await db.get(CubeTemplateNode, (tpl.id, node.node_id))
            if row is None:
                row = CubeTemplateNode(
                    template_id=tpl.id, node_id=node.node_id,
                    status=CubeTemplateNodeStatus.PENDING.value,
                )
                db.add(row)
            if row.status == CubeTemplateNodeStatus.READY.value:
                return "READY"
            self.transition_node(row, CubeTemplateNodeStatus.DISTRIBUTING.value)
            await db.commit()
            tpl_id, artifact_id, sha, size = tpl.id, tpl.artifact_id, tpl.sha256, int(tpl.size_bytes)

        client = self._client_factory(node) if self._client_factory else _default_node_client(node)
        images = self._images_factory(client)
        last_error = ""
        for attempt in range(1, retries + 1):
            ttl = int(settings.CUBE_TEMPLATE_URL_TTL_SECONDS)
            url = await asyncio.to_thread(presign_artifact_url, artifact_id, ttl)
            if url is None:
                last_error = "presigned URL unavailable (MinIO unreachable)"
                continue  # re-issued on the next attempt (TTL-expiry retry path)
            token, _expires = issue_artifact_token(artifact_id, sha, ttl)
            job_id = str(uuid.uuid4())
            annotations = {
                ART_ANNOT_ID: artifact_id,
                ART_ANNOT_URL: url,
                ART_ANNOT_TOKEN: token,
                ART_ANNOT_SHA256: sha,
                ART_ANNOT_SIZE: str(size),
                ART_ANNOT_JOB_ID: job_id,
            }
            spec = CubeImagesClient._spec(artifact_id, annotations)
            try:
                await asyncio.to_thread(images.create_image, spec)
            except CDSError as exc:
                last_error = f"CreateImage failed: {exc.message}"
                continue
            verified, last_error = await self._verify_distribution(images, artifact_id, sha, size)
            if verified:
                async with async_session() as db:
                    row = await db.get(CubeTemplateNode, (tpl_id, node.node_id))
                    self.transition_node(row, CubeTemplateNodeStatus.READY.value)
                    row.artifact_sha256 = sha
                    row.last_error = None
                    await db.commit()
                return "READY"
            if "sha256 mismatch" in last_error:
                break  # hard integrity failure — retrying cannot fix it
        # exhausted retries → FAILED + critical alert (never silent, 不降级)
        async with async_session() as db:
            row = await db.get(CubeTemplateNode, (tpl_id, node.node_id))
            self.transition_node(row, CubeTemplateNodeStatus.FAILED.value)
            row.last_error = last_error[:2000]
            await db.commit()
        fire_critical_alert(
            "cube_template_dist_failed",
            f"template {tpl.artifact_id} distribution to node {node.node_id} failed: {last_error}",
            dedup_key=f"cube_template_dist_failed:{artifact_id}:{node.node_id}",
        )
        return "FAILED"

    @staticmethod
    async def _verify_distribution(
        images: CubeImagesClient, artifact_id: str, sha: str, size: int
    ) -> tuple[bool, str]:
        """ListImages verification: image present + size match + (when the
        node echoes the annotation) sha256 match. Missing → (False, …) which
        the caller treats as the TTL-expiry/retry path; sha mismatch is a
        hard fail."""
        resp = await asyncio.to_thread(images.list_images, artifact_id)
        img = next(
            (i for i in resp.images if i.id == artifact_id or i.spec.image == artifact_id),
            None,
        )
        if img is None:
            return False, f"image {artifact_id} not present after CreateImage (URL TTL expired?)"
        echoed_sha = img.spec.annotations.get(ART_ANNOT_SHA256)
        if echoed_sha and not hmac.compare_digest(echoed_sha.lower(), sha.lower()):
            return False, f"sha256 mismatch: node reports {echoed_sha}"
        if img.size and int(img.size) != int(size):
            return False, f"size mismatch: node reports {img.size}, registered {size}"
        return True, ""

    # ── GC / delete (step 5) ────────────────────────────────────────────────
    async def delete_template(self, template_id: uuid.UUID) -> dict:
        """GC round: per-node DestroyImage; Conflict (in-use) → row stays.

        Upstream mapping: ``isIgnorableArtifactDeleteError`` treats
        ``Ret.ret_code = Conflict`` on artifact delete as ignorable — the
        artifact is still referenced by a live sandbox, so the delete is a
        protection signal, not an error. CDS mapping (same semantics):
        Conflict → keep the node row (defer to the next GC round, i.e. the
        next DELETE call); ContainerNotFound → artifact already gone → drop
        the row. The operation is idempotent: re-calling DELETE retries only
        the deferred rows.
        """
        from app.core.database import async_session

        async with async_session() as db:
            tpl = await db.get(CubeTemplate, template_id)
            if tpl is None:
                raise TemplateError("template not found", code="SESSION_NOT_FOUND", http_status=404)
            rows = list(
                (await db.execute(
                    select(CubeTemplateNode).where(CubeTemplateNode.template_id == tpl.id)
                )).scalars().all()
            )
            pending_ids = [r.node_id for r in sorted(rows, key=lambda r: r.node_id)]
            artifact_id = tpl.artifact_id
        if not pending_ids:
            async with async_session() as db:
                tpl = await db.get(CubeTemplate, template_id)
                await db.delete(tpl)
                await db.commit()
            return {"deleted": True, "deferred": []}

        nodes = {n.node_id: n for n in await self._target_nodes()}
        deferred: list[dict] = []
        gone: list[str] = []
        failed: list[dict] = []
        for node_id in pending_ids:
            node = nodes.get(node_id)
            if node is None:
                # Node no longer managed by CDS — the row cannot be
                # reconciled; keep it (defer) rather than leak the artifact.
                deferred.append({"node_id": node_id, "reason": "node not managed"})
                continue
            client = self._client_factory(node) if self._client_factory else _default_node_client(node)
            images = self._images_factory(client)
            spec = CubeImagesClient._spec(artifact_id)
            try:
                await asyncio.to_thread(images.destroy_image, spec)
                gone.append(node_id)
            except CDSError as exc:
                if exc.code == "SESSION_STATE_CONFLICT":
                    # isIgnorableArtifactDeleteError: in-use protection → defer.
                    deferred.append({"node_id": node_id, "reason": "in-use (Conflict)"})
                elif exc.code == "SANDBOX_NOT_FOUND":
                    # artifact already absent on the node → treat as gone.
                    gone.append(node_id)
                else:
                    failed.append({"node_id": node_id, "reason": exc.message})
        async with async_session() as db:
            tpl = await db.get(CubeTemplate, template_id)
            for node_id in gone:
                row = await db.get(CubeTemplateNode, (tpl.id, node_id))
                if row is not None:
                    await db.delete(row)
            for item in deferred + failed:
                row = await db.get(CubeTemplateNode, (tpl.id, item["node_id"]))
                if row is not None:
                    row.last_error = f"deferred: {item['reason']}"
            remaining = (await db.execute(
                select(CubeTemplateNode).where(CubeTemplateNode.template_id == tpl.id)
            )).scalars().all()
            if not remaining:
                await db.delete(tpl)
            await db.commit()
        deleted = not remaining
        if deferred and not deleted:
            logger.warning(
                "[CubeTemplate] GC deferred for %s (in-use protection, next round): %s",
                artifact_id, [d["node_id"] for d in deferred],
            )
        return {"deleted": deleted, "gone": gone, "deferred": deferred, "failed": failed}


def _build_app_snapshot_request(artifact_id: str) -> "cubebox_pb2.AppSnapshotRequest":
    """AppSnapshot create_request: the scenario sandbox run + the required
    ``cube.master.appsnapshot.create="true"`` / ``template.id`` annotations."""
    settings = get_settings()
    create = cubebox_pb2.RunCubeSandboxRequest()
    create.requestID = str(uuid.uuid4())
    container = create.containers.add()
    container.name = "cds-template-snapshot"
    container.image.image = settings.CUBE_SANDBOX_IMAGE
    container.image.storage_media = "ext4"
    env = container.envs.add()
    env.key = "CDS_TEMPLATE_SNAPSHOT"
    env.value = "true"
    create.annotations[APPSNAP_CREATE_ANNOT] = "true"
    create.annotations[APPSNAP_TEMPLATE_ID_ANNOT] = artifact_id
    req = cubebox_pb2.AppSnapshotRequest()
    req.create_request.CopyFrom(create)
    req.snapshot_dir = ""
    req.backend = ""  # xfs (default); s3 = CB-07 (direct CDS-MinIO push)
    return req


def _default_node_client(node: Any):
    from app.services.cube_node_client import get_cube_node_client

    return get_cube_node_client(node)


cube_template_service = CubeTemplateService()
