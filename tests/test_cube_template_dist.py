"""CB-05: cube template distribution, GC, scheduling and the W11 build op.

Fake gRPC at the proto level (CB-01 pattern: injected client, real proto
messages): distribution order (deterministic node_id order), sha mismatch →
FAILED + critical alert, presigned-URL TTL expiry → re-issue + retry, GC
Conflict → deferred (isIgnorableArtifactDeleteError mapping), and the
scheduling constraint (no READY node → 503 NO_NODE_TEMPLATE via the API).
"""
from __future__ import annotations

import uuid

import pytest

from app._stubs.cube.images.v1 import images_pb2
from app._stubs.cube.errorcode.v1 import errorcode_pb2
from app.core.config import get_settings
from app.core.database import async_session
from app.models.cube_template import (
    CubeTemplate,
    CubeTemplateNode,
    CubeTemplateNodeStatus,
    CubeTemplateStatus,
)
from app.models.sandbox_node import NodeStatus, SandboxNode
from app.services import session_operations as ops
from app.services.cube_runtime_adapter import pinned_cube_version
from app.services.cube_template_service import (
    ART_ANNOT_ID,
    ART_ANNOT_JOB_ID,
    ART_ANNOT_SHA256,
    ART_ANNOT_SIZE,
    ART_ANNOT_TOKEN,
    ART_ANNOT_URL,
    CubeTemplateService,
    cube_template_service,
)

SHA = "ab" * 32
SIZE = 4096


def _ret_to_error(ret_code) -> "object":
    """Mirror CubeImagesClient._call's ret mapping (map_ret_code) for fakes."""
    from app.core.errors import CDSError

    if ret_code == errorcode_pb2.Conflict:
        return CDSError("in use", code="SESSION_STATE_CONFLICT", http_status=409)
    if ret_code == errorcode_pb2.NotFound:
        return CDSError("not found", code="SANDBOX_NOT_FOUND", http_status=404)
    return CDSError("node error", code="INTERNAL_ERROR", http_status=500)


class FakeImages:
    """Proto-level fake of the node Images service (per node instance).

    Non-Success ret codes raise the same W2 CDSError the real
    ``CubeImagesClient._call`` would (fakes must preserve the contract).
    """

    def __init__(self, behavior: dict):
        # behavior: {"create": [...ret_codes], "list": [responses...],
        #           "destroy": [...ret_codes]} — popped in call order
        self.behavior = behavior
        self.create_calls: list[images_pb2.ImageSpec] = []
        self.destroy_calls: list[images_pb2.ImageSpec] = []

    def create_image(self, spec: images_pb2.ImageSpec):
        self.create_calls.append(spec)
        ret_code = self.behavior["create"].pop(0) if self.behavior["create"] else errorcode_pb2.Success
        resp = images_pb2.CreateImageRequestResponse(requestID="r")
        resp.ret.ret_code = ret_code
        if ret_code != errorcode_pb2.Success:
            raise _ret_to_error(ret_code)
        return resp

    def list_images(self, artifact_id: str):
        entry = self.behavior["list"].pop(0) if self.behavior["list"] else None
        if entry is None:
            return _empty_list()
        return entry(artifact_id) if callable(entry) else entry

    def destroy_image(self, spec: images_pb2.ImageSpec):
        self.destroy_calls.append(spec)
        ret_code = self.behavior["destroy"].pop(0) if self.behavior["destroy"] else errorcode_pb2.Success
        resp = images_pb2.DestroyImageResponse(requestID="r")
        resp.ret.ret_code = ret_code
        if ret_code != errorcode_pb2.Success:
            raise _ret_to_error(ret_code)
        return resp


def _empty_list() -> images_pb2.CubeListImageResponse:
    resp = images_pb2.CubeListImageResponse()
    resp.requestID = "r"
    resp.ret.ret_code = errorcode_pb2.Success
    return resp


def _list_with_image(artifact_id: str, size: int = SIZE, sha: str | None = None):
    resp = _empty_list()
    img = resp.images.add()
    img.id = artifact_id
    img.size = size
    if sha is not None:
        img.spec.annotations[ART_ANNOT_SHA256] = sha
    return resp


def _add_node(db, node_id: str, *, online: bool = True):
    db.add(SandboxNode(
        node_id=node_id, hostname=f"h-{node_id}", ip_address=f"10.0.0.{1}",
        status=NodeStatus.ONLINE.value if online else NodeStatus.OFFLINE.value,
        capabilities={
            "cubelet_endpoint": f"10.0.0.1:9443",
            "runtime_version": pinned_cube_version(),
        },
    ))


async def _make_template(db, svc, name: str = "dist-scene", status: str = CubeTemplateStatus.DISTRIBUTING.value):
    tpl = await svc.create_template(db, name=name, image_ref="reg.local/cds/scene:1")
    tpl.sha256 = SHA
    tpl.size_bytes = SIZE
    if status is not None and status != CubeTemplateStatus.BUILDING.value:
        # the state machine is a chain — go through DISTRIBUTING first
        svc.transition_template(tpl, CubeTemplateStatus.DISTRIBUTING.value)
        if status != CubeTemplateStatus.DISTRIBUTING.value:
            svc.transition_template(tpl, status)
    # Persist the final state via a fresh session + commit: the service
    # re-reads the row through its own session on every call, and the
    # sqlite StaticPool test DB is flaky about uncommitted cross-session
    # visibility. The row must be deterministic for the flow under test.
    async with async_session() as s2:
        t2 = await s2.get(CubeTemplate, tpl.id)
        t2.sha256 = tpl.sha256
        t2.size_bytes = tpl.size_bytes
        t2.status = tpl.status
        await s2.commit()
    await db.commit()
    return tpl


@pytest.fixture()
def patched_presign(monkeypatch):
    """Stub the presigner (MinIO absent in tests); returns the URL log."""
    monkeypatch.setattr(get_settings(), "CUBE_TEMPLATE_SIGNING_KEY", "test-signing-key")
    urls: list[str] = []

    def _presign(artifact_id: str, ttl: int) -> str:
        urls.append(f"https://minio.test/cube-templates/{artifact_id}.ext4?sig={len(urls)}")
        return urls[-1]

    monkeypatch.setattr("app.services.cube_template_service.presign_artifact_url", _presign)
    return urls


@pytest.mark.asyncio
async def test_distribution_order_and_success(db_session, patched_presign):
    fakes: dict[str, FakeImages] = {}
    artifact_holder: list[str] = []

    def images_factory(client):
        fake = FakeImages({
            "create": [],
            # respond per the artifact id that actually arrives (fakes are
            # built lazily during distribute, after the row exists)
            "list": [lambda aid: _list_with_image(aid, SIZE, SHA)],
            "destroy": [],
        })
        fakes[client.node_id] = fake
        return fake

    service = CubeTemplateService(
        client_factory=lambda node: type("N", (), {"node_id": node.node_id})(),
        images_factory=images_factory,
    )
    tpl = await _make_template(db_session, service, name="dist-ok")
    _add_node(db_session, "n-b")
    _add_node(db_session, "n-a")
    await db_session.commit()

    result = await service.distribute(tpl.id)
    assert result["nodes"] == {"n-a": "READY", "n-b": "READY"}
    assert result["template_status"] == CubeTemplateStatus.READY.value

    # deterministic node_id order
    assert list(result["nodes"]) == ["n-a", "n-b"]
    # annotations: the full upstream artifact set (const.go:242-247)
    for fake in fakes.values():
        spec = fake.create_calls[0]
        assert spec.image == tpl.artifact_id
        assert spec.storage_media == "ext4"
        a = spec.annotations
        assert a[ART_ANNOT_ID] == tpl.artifact_id
        assert a[ART_ANNOT_SHA256] == SHA
        assert a[ART_ANNOT_SIZE] == str(SIZE)
        assert a[ART_ANNOT_URL].startswith("https://minio.test/")
        assert a[ART_ANNOT_TOKEN].startswith("v1.")  # signed with the test key
        assert a[ART_ANNOT_JOB_ID]
    # node rows READY with the registered sha
    row_a = await db_session.get(CubeTemplateNode, (tpl.id, "n-a"))
    assert row_a.status == CubeTemplateNodeStatus.READY.value
    assert row_a.artifact_sha256 == SHA


@pytest.mark.asyncio
async def test_sha_mismatch_failed_and_critical_alert(db_session, patched_presign, monkeypatch):
    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.services.cube_template_service.fire_critical_alert",
        lambda t, m, dedup_key=None: alerts.append((t, m)),
    )
    fakes = {}

    def images_factory(client):
        fake = FakeImages({
            "create": [],
            # node echoes a WRONG sha in ListImages
            "list": [lambda aid: _list_with_image(aid, SIZE, "cd" * 32)],
            "destroy": [],
        })
        fakes[client.node_id] = fake
        return fake

    service = CubeTemplateService(
        client_factory=lambda node: type("N", (), {"node_id": node.node_id})(),
        images_factory=images_factory,
    )
    tpl = await _make_template(db_session, service, name="dist-sha")
    _add_node(db_session, "n1")
    await db_session.commit()

    result = await service.distribute(tpl.id)
    assert result["nodes"] == {"n1": "FAILED"}
    # hard fail: no retry re-issue (CreateImage called exactly once)
    assert len(fakes["n1"].create_calls) == 1
    row = await db_session.get(CubeTemplateNode, (tpl.id, "n1"))
    assert row.status == CubeTemplateNodeStatus.FAILED.value
    assert "sha256 mismatch" in row.last_error
    assert alerts and alerts[0][0] == "cube_template_dist_failed"
    assert "n1" in alerts[0][1]
    # template never READY
    await db_session.refresh(tpl)
    assert tpl.status != CubeTemplateStatus.READY.value


@pytest.mark.asyncio
async def test_presigned_ttl_expiry_reissue_and_retry(db_session, patched_presign):
    urls = patched_presign
    fakes = {}

    def images_factory(client):
        fake = FakeImages({
            "create": [],
            # first verify: image missing (node's download of the expired URL
            # failed); second verify: present.
            "list": [lambda aid: _empty_list(), lambda aid: _list_with_image(aid, SIZE, SHA)],
            "destroy": [],
        })
        fakes[client.node_id] = fake
        return fake

    service = CubeTemplateService(
        client_factory=lambda node: type("N", (), {"node_id": node.node_id})(),
        images_factory=images_factory,
    )
    tpl = await _make_template(db_session, service, name="dist-ttl")
    _add_node(db_session, "n1")
    await db_session.commit()

    result = await service.distribute(tpl.id)
    assert result["nodes"] == {"n1": "READY"}
    # retried with a RE-ISSUED url + job (TTL-expiry path); the token is a
    # signed capability — equal tokens are possible inside the same expiry
    # window, but both must be signed v1 tokens
    assert len(fakes["n1"].create_calls) == 2
    u1, u2 = (c.annotations[ART_ANNOT_URL] for c in fakes["n1"].create_calls)
    t1, t2 = (c.annotations[ART_ANNOT_TOKEN] for c in fakes["n1"].create_calls)
    j1, j2 = (c.annotations[ART_ANNOT_JOB_ID] for c in fakes["n1"].create_calls)
    assert u1 != u2 and j1 != j2
    assert t1.startswith("v1.") and t2.startswith("v1.")
    assert len(urls) == 2


@pytest.mark.asyncio
async def test_gc_conflict_defers_then_completes(db_session, patched_presign):
    fakes = {}
    behaviors = {"n-a": [errorcode_pb2.Success], "n-b": [errorcode_pb2.Conflict]}

    def images_factory(client):
        fake = FakeImages({"create": [], "list": [], "destroy": behaviors[client.node_id]})
        fakes[client.node_id] = fake
        return fake

    service = CubeTemplateService(
        client_factory=lambda node: type("N", (), {"node_id": node.node_id})(),
        images_factory=images_factory,
    )
    tpl = await _make_template(db_session, service, name="gc-scene", status=CubeTemplateStatus.READY.value)
    _add_node(db_session, "n-a")
    _add_node(db_session, "n-b")
    for node_id in ("n-a", "n-b"):
        db_session.add(CubeTemplateNode(template_id=tpl.id, node_id=node_id,
                                        status=CubeTemplateNodeStatus.READY.value))
    # COMMIT (not flush): the service opens its own session — on the
    # sqlite StaticPool test DB, uncommitted fixture rows are
    # intermittently invisible cross-session.
    await db_session.commit()
    tpl = await db_session.get(CubeTemplate, tpl.id)
    result = await service.delete_template(tpl.id)
    assert result["deleted"] is False
    assert result["gone"] == ["n-a"]
    assert [d["node_id"] for d in result["deferred"]] == ["n-b"]
    row_b = await db_session.get(CubeTemplateNode, (tpl.id, "n-b"))
    assert row_b is not None  # row stays — deferred to next GC round
    assert "in-use (Conflict)" in row_b.last_error
    # template row survives (artifact still referenced)
    assert await db_session.get(tpl.__class__, tpl.id) is not None

    # round 2 (idempotent retry): n-b now free → full delete
    fakes.clear()
    behaviors["n-b"] = [errorcode_pb2.Success]
    tpl_id = tpl.id  # capture before expire_all — expired attr access needs await
    result = await service.delete_template(tpl_id)
    assert result["deleted"] is True
    # the deletions happened in the service's session — drop the test
    # session's cached copies before re-reading
    db_session.expire_all()
    assert await db_session.get(tpl.__class__, tpl_id) is None
    assert await db_session.get(CubeTemplateNode, (tpl_id, "n-b")) is None


@pytest.mark.asyncio
async def test_no_ready_node_503_no_node_template(client, operator_headers, publish_product, db_session, monkeypatch):
    """Scheduling constraint: template with no READY node → 503 NO_NODE_TEMPLATE."""
    monkeypatch.setattr(get_settings(), "CUBE_DATAPLANE_ENABLED", True)
    tpl = await cube_template_service.create_template(db_session, name="sched-scene")
    cube_template_service.transition_template(tpl, CubeTemplateStatus.DISTRIBUTING.value)
    _add_node(db_session, "n-ready-less")  # online, pinned, but no READY row
    await db_session.commit()

    product_resp = await client.post("/api/v1/data-products", json={"name": f"t-{uuid.uuid4().hex[:6]}"}, headers=operator_headers)
    assert product_resp.status_code == 201, product_resp.text
    await publish_product(product_resp.json()["id"])

    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_resp.json()["id"], "sandbox_level": "l2c",
              "template": "sched-scene"},
        headers=operator_headers,
    )
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["code"] == "NO_NODE_TEMPLATE"
    assert body["detail"]["template_id"] == "sched-scene"
    assert body["detail"]["reason"] == "no_ready_node"


@pytest.mark.asyncio
async def test_unknown_template_name_still_400(client, operator_headers, publish_product, db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "CUBE_DATAPLANE_ENABLED", True)
    product_resp = await client.post("/api/v1/data-products", json={"name": f"t-{uuid.uuid4().hex[:6]}"}, headers=operator_headers)
    assert product_resp.status_code == 201, product_resp.text
    await publish_product(product_resp.json()["id"])
    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_resp.json()["id"],
              "sandbox_level": "l2c", "template": "ghost-template"},
        headers=operator_headers,
    )
    # unknown template is rejected before the node gate
    assert resp.status_code == 400, resp.text
    assert "Unknown session template" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_w11_async_build_returns_202_and_polls(client, operator_headers, db_session, monkeypatch):
    """W11 async model: ?async=true → 202 + operation_id; poll → succeeded."""
    # synthetic artifact path (docker conversion not run in the suite)
    monkeypatch.setattr(
        "app.services.cube_template_service.convert_rootfs",
        lambda image_ref, out_dir: {"artifact_path": "/tmp/synthetic.ext4", "sha256": SHA,
                                     "size_bytes": SIZE, "probe_config": {"type": "http"}},
    )
    monkeypatch.setattr("app.services.cube_template_service.upload_artifact",
                        lambda path: "cube-templates/synthetic.ext4")

    async def _no_snapshot(template_id):
        return {"node_id": "none", "rootfs_size_bytes": 0, "template_id": "", "snapshot_path": ""}

    monkeypatch.setattr(cube_template_service, "_snapshot_check", _no_snapshot)
    monkeypatch.setattr("app.services.cube_template_service.presign_artifact_url",
                        lambda aid, ttl: f"https://minio.test/{aid}?sig=1")
    monkeypatch.setattr(get_settings(), "CUBE_TEMPLATE_SIGNING_KEY", "k")

    fakes = {}

    def images_factory(client):
        fake = FakeImages({
            "create": [],
            # respond per the artifact id that actually arrives (the row is
            # built inside the background operation, id not known up front)
            "list": [lambda aid: _list_with_image(aid, SIZE, SHA)],
            "destroy": [],
        })
        fakes[client.node_id] = fake
        return fake

    monkeypatch.setattr(
        cube_template_service, "_images_factory",
        lambda client: images_factory(client),
    )
    monkeypatch.setattr(cube_template_service, "_client_factory",
                        lambda node: type("N", (), {"node_id": node.node_id})())
    _add_node(db_session, "n1")
    await db_session.commit()

    resp = await client.post(
        "/api/v1/cube-templates?async=true",
        json={"name": "w11-scene", "image_ref": "reg.local/cds/w11:1"},
        headers=operator_headers,
    )
    assert resp.status_code == 202, resp.text
    op_id = resp.json()["operation_id"]
    assert resp.json()["op_type"] == "template_build"

    await ops.flush_background_operations()
    poll = await client.get(f"/api/v1/cube-templates/operations/{op_id}", headers=operator_headers)
    assert poll.status_code == 200, poll.text
    payload = poll.json()
    assert payload["status"] == "succeeded", payload
    ref = payload["result_ref"]
    assert ref["sha256"] == SHA
    assert ref["nodes"] == {"n1": "READY"}

    # registry row is READY
    tpl_resp = await client.get("/api/v1/cube-templates", headers=operator_headers)
    assert tpl_resp.status_code == 200
    items = tpl_resp.json()["items"]
    assert any(t["name"] == "w11-scene" and t["status"] == CubeTemplateStatus.READY.value for t in items)
