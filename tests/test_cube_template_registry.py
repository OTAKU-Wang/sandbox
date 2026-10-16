"""CB-05: cube template registry — state machine, resolution, sha gate.

Template lifecycle (BUILDING → DISTRIBUTING → READY) and per-node
distribution state machine (PENDING → DISTRIBUTING → READY / FAILED,
FAILED → retry re-distribution), plus the rule that a node row only becomes
READY when the ListImages verification (size + sha256) passes, and the
``template`` field resolution chain (id/name/JSON-template declaration).
"""
from __future__ import annotations

import uuid

import pytest

from app._stubs.cube.images.v1 import images_pb2
from app._stubs.cube.errorcode.v1 import errorcode_pb2
from app.models.cube_template import (
    CubeTemplate,
    CubeTemplateNode,
    CubeTemplateNodeStatus,
    CubeTemplateStatus,
)
from app.services.cube_template_service import (
    ART_ANNOT_SHA256,
    DEFAULT_PROBE_CONFIG,
    CubeTemplateService,
    TemplateError,
    cube_template_service,
)

SVC = CubeTemplateService()


@pytest.mark.asyncio
async def test_create_template_registry_row(db_session):
    tpl = await SVC.create_template(
        db_session, name="scene-a", version="1.0.0",
        image_ref="reg.local/cds/scene-a:1", created_by="u-1",
    )
    assert tpl.status == CubeTemplateStatus.BUILDING.value
    assert tpl.artifact_id.startswith("rfs-")
    assert tpl.probe_config == DEFAULT_PROBE_CONFIG  # C4: HTTP 2xx = READY
    assert tpl.created_by == "u-1"
    assert tpl.sha256 is None and tpl.size_bytes is None


@pytest.mark.asyncio
async def test_create_template_duplicate_name_version_rejected(db_session):
    await SVC.create_template(db_session, name="scene-a", version="1.0.0")
    with pytest.raises(TemplateError):
        await SVC.create_template(db_session, name="scene-a", version="1.0.0")
    # same name, different version is fine
    await SVC.create_template(db_session, name="scene-a", version="2.0.0")


@pytest.mark.asyncio
async def test_template_state_machine_transitions(db_session):
    tpl = await SVC.create_template(db_session, name="sm-tpl")

    # allowed chain: BUILDING → DISTRIBUTING → READY
    SVC.transition_template(tpl, CubeTemplateStatus.DISTRIBUTING.value)
    SVC.transition_template(tpl, CubeTemplateStatus.READY.value)

    tpl2 = await SVC.create_template(db_session, name="sm-tpl2", version="2.0.0")
    # BUILDING → READY is not allowed (must pass through DISTRIBUTING)
    with pytest.raises(TemplateError):
        SVC.transition_template(tpl2, CubeTemplateStatus.READY.value)
    # DISTRIBUTING is reachable from BUILDING
    SVC.transition_template(tpl2, CubeTemplateStatus.DISTRIBUTING.value)
    # READY is terminal
    SVC.transition_template(tpl2, CubeTemplateStatus.READY.value)
    with pytest.raises(TemplateError):
        SVC.transition_template(tpl2, CubeTemplateStatus.DISTRIBUTING.value)
    with pytest.raises(TemplateError):
        SVC.transition_template(tpl2, CubeTemplateStatus.BUILDING.value)


@pytest.mark.asyncio
async def test_node_state_machine_including_failed_retry(db_session):
    tpl = await SVC.create_template(db_session, name="sm-node")
    row = CubeTemplateNode(template_id=tpl.id, node_id="n1")
    db_session.add(row)
    await db_session.commit()

    assert row.status == CubeTemplateNodeStatus.PENDING.value
    # PENDING → DISTRIBUTING → READY
    SVC.transition_node(row, CubeTemplateNodeStatus.DISTRIBUTING.value)
    SVC.transition_node(row, CubeTemplateNodeStatus.READY.value)
    # READY is terminal
    with pytest.raises(TemplateError):
        SVC.transition_node(row, CubeTemplateNodeStatus.FAILED.value)
    with pytest.raises(TemplateError):
        SVC.transition_node(row, CubeTemplateNodeStatus.DISTRIBUTING.value)

    row2 = CubeTemplateNode(template_id=tpl.id, node_id="n2")
    db_session.add(row2)
    await db_session.commit()
    # PENDING → FAILED
    SVC.transition_node(row2, CubeTemplateNodeStatus.FAILED.value)
    # FAILED → READY is NOT allowed (must retry via DISTRIBUTING)
    with pytest.raises(TemplateError):
        SVC.transition_node(row2, CubeTemplateNodeStatus.READY.value)
    # FAILED → DISTRIBUTING (retry re-distribution) is allowed
    SVC.transition_node(row2, CubeTemplateNodeStatus.DISTRIBUTING.value)
    # ...and the retried node can then go READY
    SVC.transition_node(row2, CubeTemplateNodeStatus.READY.value)


def _list_resp(artifact_id: str, size: int, sha: str | None = None) -> images_pb2.CubeListImageResponse:
    resp = images_pb2.CubeListImageResponse()
    resp.requestID = "req-1"
    resp.ret.ret_code = errorcode_pb2.Success
    img = resp.images.add()
    img.id = artifact_id
    img.size = size
    if sha is not None:
        img.spec.annotations[ART_ANNOT_SHA256] = sha
    return resp


class _FakeImages:
    def __init__(self, response: images_pb2.CubeListImageResponse):
        self._response = response

    def create_image(self, spec):
        return images_pb2.CreateImageRequestResponse(requestID="r")

    def list_images(self, artifact_id: str):
        return self._response

    def destroy_image(self, spec):
        return images_pb2.DestroyImageResponse(requestID="r")


@pytest.mark.asyncio
async def test_verify_distribution_ready_requires_sha_match(db_session):
    sha = "ab" * 32
    # all good → verified
    ok, err = await CubeTemplateService._verify_distribution(
        _FakeImages(_list_resp("rfs-1", 4096, sha)), "rfs-1", sha, 4096)
    assert ok and err == ""
    # sha mismatch → not verified (hard fail, never READY)
    bad_sha = "cd" * 32
    ok, err = await CubeTemplateService._verify_distribution(
        _FakeImages(_list_resp("rfs-1", 4096, bad_sha)), "rfs-1", sha, 4096)
    assert not ok and "sha256 mismatch" in err
    # size mismatch → not verified
    ok, err = await CubeTemplateService._verify_distribution(
        _FakeImages(_list_resp("rfs-1", 8192, sha)), "rfs-1", sha, 4096)
    assert not ok and "size mismatch" in err
    # image absent (e.g. presigned URL expired) → not verified (retry path)
    ok, err = await CubeTemplateService._verify_distribution(
        _FakeImages(images_pb2.CubeListImageResponse(requestID="r")), "rfs-1", sha, 4096)
    assert not ok


@pytest.mark.asyncio
async def test_resolve_cube_template_chain(db_session, monkeypatch):
    tpl = await SVC.create_template(db_session, name="scene-x")
    # by name
    assert (await SVC.get_by_ref(db_session, "scene-x")).id == tpl.id
    # by UUID id
    assert (await SVC.get_by_ref(db_session, str(tpl.id))).id == tpl.id
    # unknown
    assert await SVC.get_by_ref(db_session, "nope") is None
    assert await SVC.get_by_ref(db_session, "not-a-uuid") is None

    # Round 40 JSON session template declaring cube_template_id
    from app.services import session_templates as st

    monkeypatch.setitem(
        st.BUILTIN_TEMPLATES, "json-scene",
        {"description": "d", "files": {}, "env": {}, "cube_template_id": "scene-x"},
    )
    resolved = await SVC.resolve_cube_template(db_session, "json-scene")
    assert resolved is not None and resolved.id == tpl.id
    # JSON template without a cube reference resolves to None (no constraint)
    monkeypatch.setitem(
        st.BUILTIN_TEMPLATES, "json-bare",
        {"description": "d", "files": {}, "env": {}, "cube_template_id": None},
    )
    assert await SVC.resolve_cube_template(db_session, "json-bare") is None


@pytest.mark.asyncio
async def test_ready_node_ids(db_session):
    tpl = await SVC.create_template(db_session, name="loc")
    db_session.add(CubeTemplateNode(template_id=tpl.id, node_id="n1",
                                    status=CubeTemplateNodeStatus.READY.value))
    db_session.add(CubeTemplateNode(template_id=tpl.id, node_id="n2",
                                    status=CubeTemplateNodeStatus.FAILED.value))
    await db_session.commit()
    assert await SVC.ready_node_ids(db_session, tpl.id) == {"n1"}
