"""N5: inference service sandbox — runner, registry, invoke gating, metering.

The in-sandbox runner is executed as a real subprocess (same pattern as
test_rag_runner) against a tiny real ONNX Add model built with onnx/onnxruntime,
proving host→sandbox consistency and the fail-closed posture.
"""
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import delete, select

from app.models.contract import Contract, ContractStatus
from app.models.data_product import DataProduct
from app.models.sandbox_session import SandboxSession, SessionStatus


def _tiny_onnx_model() -> bytes:
    """A real ONNX model: y = x + [1.0, 2.0] (opset 13, float32 [N,2])."""
    import onnx
    from onnx import TensorProto, helper

    x = helper.make_tensor_value_info("x", TensorProto.FLOAT, [None, 2])
    y = helper.make_tensor_value_info("y", TensorProto.FLOAT, [None, 2])
    bias = helper.make_tensor("bias", TensorProto.FLOAT, [2], [1.0, 2.0])
    node = helper.make_node("Add", ["x", "bias"], ["y"])
    graph = helper.make_graph([node], "add_model", [x], [y], [bias])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    return model.SerializeToString()


# ── runner template ────────────────────────────────────────────────
def test_build_inference_runner_is_stable():
    from app.services.inference_service import build_inference_runner, validate_inference_runner

    mid = uuid.uuid4()
    r1 = build_inference_runner(mid)
    r2 = build_inference_runner(mid)
    assert r1 == r2
    assert "onnxruntime" in r1 and "model.onnx" in r1 and "inference_input.json" in r1
    passed, reason = validate_inference_runner(r1, mid)
    assert passed and reason == "inference_runner_verified"


def test_validate_inference_runner_rejects_tampering():
    from app.services.inference_service import build_inference_runner, validate_inference_runner

    mid = uuid.uuid4()
    tampered = build_inference_runner(mid) + '\nprint("TAMPERED_EXFIL")\n'
    passed, _ = validate_inference_runner(tampered, mid)
    assert passed is False
    # A different model_id also fails (the runner is bound to its model).
    passed2, _ = validate_inference_runner(build_inference_runner(mid), uuid.uuid4())
    assert passed2 is False


def test_runner_executes_onnx_model():
    """Real execution: tiny ONNX Add model, JSON in/out, backend=local_sandbox."""
    from app.services.inference_service import build_inference_runner

    mid = uuid.uuid4()
    runner = build_inference_runner(mid)
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "input").mkdir(parents=True)
        (ws / "input" / "model.onnx").write_bytes(_tiny_onnx_model())
        (ws / "input" / "inference_input.json").write_text(
            json.dumps({"inputs": {"x": [[1.0, 2.0], [3.0, 4.0]]}})
        )
        proc = subprocess.run(
            [sys.executable, "-c", runner], cwd=ws, capture_output=True, text=True,
            env={**os.environ},
        )
        assert proc.returncode == 0, f"runner failed: {proc.stderr}"
        result = json.loads([ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1])

    assert result["backend"] == "local_sandbox"
    assert result["inference_model"] == str(mid)
    assert np.allclose(result["outputs"]["y"], [[2.0, 4.0], [4.0, 6.0]])


# ── shared env ──────────────────────────────────────────────────────
async def _mk_model_env(db_session, make_user, purpose=None):
    """provider/buyer headers + product + active contract + buyer session."""
    provider_headers, provider = await make_user("data_provider", "prov")
    buyer_headers, buyer = await make_user("buyer", "buy")
    product = DataProduct(provider_id=uuid.UUID(provider), name="inf-prod", status="published")
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)
    contract = Contract(
        contract_no=f"C-{uuid.uuid4().hex[:8]}",
        contract_type="data_query",
        provider_id=uuid.UUID(provider),
        buyer_id=uuid.UUID(buyer),
        title="inference-test",
        product_ids=[str(product.id)],
        status=ContractStatus.ACTIVE.value,
        purpose=purpose,
    )
    db_session.add(contract)
    await db_session.flush()
    await db_session.refresh(contract)
    session = SandboxSession(
        user_id=uuid.UUID(buyer),
        data_product_id=product.id,
        sandbox_level="L3",
        status=SessionStatus.READY.value,
        container_id="bwrap-inference-test",
        contract_id=str(contract.id),
        timeout_seconds=3600,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)
    return provider_headers, buyer_headers, product, contract, session


async def _register_model(client, provider_headers, product_id, name="add-model"):
    resp = await client.post(
        "/api/v1/inference/models",
        files={"file": ("model.onnx", _tiny_onnx_model(), "application/octet-stream")},
        data={"name": name, "format": "onnx", "product_id": str(product_id)},
        headers=provider_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["model_id"]


# ── registration ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_register_valid_onnx_model(client, db_session, make_user):
    provider_headers, _, product, _, _ = await _mk_model_env(db_session, make_user)
    resp = await client.post(
        "/api/v1/inference/models",
        files={"file": ("model.onnx", _tiny_onnx_model(), "application/octet-stream")},
        data={"name": "add-model", "format": "onnx", "product_id": str(product.id)},
        headers=provider_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "registered" and body["format"] == "onnx"


@pytest.mark.asyncio
async def test_register_corrupt_onnx_rejected(client, db_session, make_user):
    provider_headers, _, product, _, _ = await _mk_model_env(db_session, make_user)
    resp = await client.post(
        "/api/v1/inference/models",
        files={"file": ("bad.onnx", b"not-a-real-onnx-model", "application/octet-stream")},
        data={"name": "bad", "format": "onnx", "product_id": str(product.id)},
        headers=provider_headers,
    )
    assert resp.status_code == 400
    assert "ONNX model validation failed" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_register_buyer_forbidden(client, db_session, make_user):
    _, buyer_headers, product, _, _ = await _mk_model_env(db_session, make_user)
    resp = await client.post(
        "/api/v1/inference/models",
        files={"file": ("m.onnx", _tiny_onnx_model(), "application/octet-stream")},
        data={"name": "m", "format": "onnx", "product_id": str(product.id)},
        headers=buyer_headers,
    )
    assert resp.status_code == 403


# ── invoke gating ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_invoke_valid_creates_task_and_usage(client, db_session, make_user):
    provider_headers, buyer_headers, product, contract, session = await _mk_model_env(db_session, make_user)
    model_id = await _register_model(client, provider_headers, product.id)

    resp = await client.post(
        f"/api/v1/inference/{model_id}/invoke",
        json={"inputs": {"x": [[5.0, 6.0]]}, "purpose": None},
        headers=buyer_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["task_id"].startswith("task-")
    assert body["session_id"] == str(session.id)

    from app.models.trained_model import InferenceUsage
    result = await db_session.execute(
        select(InferenceUsage).where(InferenceUsage.task_id == body["task_id"])
    )
    usage = result.scalar_one()
    assert usage.status == "submitted" and usage.input_tokens >= 1
    assert usage.model_id == uuid.UUID(model_id)


@pytest.mark.asyncio
async def test_invoke_without_session_rejected(client, db_session, make_user):
    provider_headers, buyer_headers, product, _, _ = await _mk_model_env(db_session, make_user)
    model_id = await _register_model(client, provider_headers, product.id)
    await db_session.execute(delete(SandboxSession))
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/inference/{model_id}/invoke",
        json={"inputs": {"x": [[1.0, 1.0]]}},
        headers=buyer_headers,
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_invoke_revoked_model_rejected(client, db_session, make_user):
    provider_headers, buyer_headers, product, _, _ = await _mk_model_env(db_session, make_user)
    model_id = await _register_model(client, provider_headers, product.id)
    rev = await client.post(f"/api/v1/inference/{model_id}/revoke", headers=provider_headers)
    assert rev.status_code == 200

    resp = await client.post(
        f"/api/v1/inference/{model_id}/invoke",
        json={"inputs": {"x": [[1.0, 1.0]]}},
        headers=buyer_headers,
    )
    assert resp.status_code == 409
    assert "revoked" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_invoke_purpose_gate(client, db_session, make_user):
    provider_headers, buyer_headers, product, _, _ = await _mk_model_env(
        db_session, make_user, purpose="statistical_analysis"
    )
    model_id = await _register_model(client, provider_headers, product.id)

    resp = await client.post(
        f"/api/v1/inference/{model_id}/invoke",
        json={"inputs": {"x": [[1.0, 1.0]]}, "purpose": "model_training"},
        headers=buyer_headers,
    )
    assert resp.status_code == 400
    assert "does not match contract purpose" in resp.json()["detail"]


# ── metering ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_metering_lists_records(client, db_session, make_user):
    provider_headers, buyer_headers, product, _, _ = await _mk_model_env(db_session, make_user)
    model_id = await _register_model(client, provider_headers, product.id)
    await client.post(
        f"/api/v1/inference/{model_id}/invoke",
        json={"inputs": {"x": [[1.0, 2.0]]}},
        headers=buyer_headers,
    )
    resp = await client.get("/api/v1/inference/metering", headers=provider_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1
    assert resp.json()["records"][0]["model_id"] == model_id


# ── model materialization ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_prepare_model_for_task_materializes_and_encrypts(db_session, make_user, tmp_path):
    """Artifact download → workspace/input + re-encryption with the session DEK."""
    from app.services.inference_service import InferenceService, prepare_model_for_task
    from app.services.kms_service import kms_service
    from Cryptodome.Cipher import AES

    _, provider = await make_user("data_provider", "prov")
    svc = InferenceService()
    model = await svc.register_model(
        db_session,
        owner_id=uuid.UUID(provider),
        name="m",
        fmt="onnx",
        artifact_bytes=_tiny_onnx_model(),
        product_id=None,
    )

    workspace = tmp_path
    (workspace / "input").mkdir(parents=True, exist_ok=True)
    key_result = kms_service.generate_data_key("inference-test-workspace")
    (workspace / ".security_config.json").write_text(
        json.dumps({"workspace_key_id": key_result["key_id"]})
    )

    result = await prepare_model_for_task(
        db_session, session=None, workspace=workspace, model_id=model.model_id,
        input_payload={"inputs": {"x": [[1.0, 2.0]]}},
    )
    assert result is not None and result["format"] == "onnx"

    model_blob = (workspace / "input" / "model.onnx").read_bytes()
    # Re-encrypted with the session DEK — decrypt and compare to the original.
    dek = key_result["key_bytes"]
    nonce, tag, ct = model_blob[:12], model_blob[12:28], model_blob[28:]
    plaintext = AES.new(dek, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ct, tag)
    assert plaintext == _tiny_onnx_model()

    input_blob = (workspace / "input" / "inference_input.json").read_bytes()
    nonce2, tag2, ct2 = input_blob[:12], input_blob[12:28], input_blob[28:]
    input_plaintext = AES.new(dek, AES.MODE_GCM, nonce=nonce2).decrypt_and_verify(ct2, tag2)
    assert json.loads(input_plaintext) == {"inputs": {"x": [[1.0, 2.0]]}}


@pytest.mark.asyncio
async def test_prepare_model_for_task_unknown_model_returns_none(db_session, make_user, tmp_path):
    from app.services.inference_service import prepare_model_for_task

    workspace = tmp_path
    (workspace / "input").mkdir(parents=True, exist_ok=True)
    result = await prepare_model_for_task(
        db_session, session=None, workspace=workspace,
        model_id=uuid.uuid4(), input_payload={},
    )
    assert result is None
