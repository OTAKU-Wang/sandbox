"""N6: RAG phase-2 — generative answer mode + retrieval metering.

The generative runner is executed as a real subprocess (same pattern as
test_rag_runner) against a tiny real ONNX bundle (Gemm over the tf-encoded
context → argmax over a 3-token vocab), proving the onnxruntime loader path,
the honest 400 gate when no model is provisioned, and the fail-closed posture.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

import numpy as np
import pytest

DOCS = [
    {"doc_id": "d1", "text": "三室两厅的户型适合一家四口居住，南北通透采光好。"},
    {"doc_id": "d2", "text": "小户型一居室适合单身青年，交通便利，总价低。"},
    {"doc_id": "d3", "text": "别墅拥有独立花园和车位，适合大家庭与宠物。"},
]
QUERY = "适合一家四口的房子"


def _tiny_generative_bundle(seed: int = 7) -> bytes:
    """A real ONNX bundle: Gemm(context[1,512], W, B) -> answer_logits[1,3]."""
    import onnx
    from onnx import TensorProto, helper

    rng = np.random.default_rng(seed)
    w = rng.standard_normal((512, 3)).astype(np.float32)
    b = np.zeros(3, dtype=np.float32)

    context = helper.make_tensor_value_info("context", TensorProto.FLOAT, [1, 512])
    logits = helper.make_tensor_value_info("answer_logits", TensorProto.FLOAT, [1, 3])
    w_init = helper.make_tensor("W", TensorProto.FLOAT, [512, 3], w.flatten())
    b_init = helper.make_tensor("B", TensorProto.FLOAT, [3], b.flatten())
    node = helper.make_node("Gemm", ["context", "W", "B"], ["answer_logits"])
    graph = helper.make_graph([node], "gen_model", [context], [logits], [w_init, b_init])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.checker.check_model(model)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("model.onnx", model.SerializeToString())
        zf.writestr("vocab.txt", "答案甲\n答案乙\n答案丙\n")
    return buf.getvalue()


def _run_runner(runner, workspace):
    proc = subprocess.run(
        [sys.executable, "-c", runner], cwd=workspace, capture_output=True, text=True,
        env={**os.environ},
    )
    return proc


def _parse(proc):
    assert proc.returncode == 0, f"runner failed: {proc.stderr}"
    return json.loads([ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1])


def test_generative_runner_executes_bundle():
    from app.services.rag_service import build_corpus, build_rag_runner

    runner = build_rag_runner(QUERY, top_k=2, answer_mode="generative", generative_model_id="gen-1")
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "input").mkdir(parents=True)
        index, _ = build_corpus(DOCS, chunk_size=64, overlap=8)
        (ws / "input" / "rag_index.json").write_bytes(index.to_json())
        (ws / "input" / "generative_model.bundle").write_bytes(_tiny_generative_bundle())
        result = _parse(_run_runner(runner, ws))

    meta = result["rag_meta"]
    assert meta["answer_mode"] == "generative"
    assert meta["generative_model"] == "gen-1"
    assert meta["watermark"] is True
    assert meta["embedding_engine"] == "tf"
    # The answer is a vocab token + the deterministic watermark tag.
    assert "答案" in result["answer"]
    assert "[CDS-WM:" in result["answer"]
    assert result["answer"].endswith("]")


def test_generative_runner_fails_closed_without_bundle():
    from app.services.rag_service import build_corpus, build_rag_runner

    runner = build_rag_runner(QUERY, top_k=2, answer_mode="generative", generative_model_id="gen-1")
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "input").mkdir(parents=True)
        index, _ = build_corpus(DOCS, chunk_size=64, overlap=8)
        (ws / "input" / "rag_index.json").write_bytes(index.to_json())
        proc = _run_runner(runner, ws)  # no generative_model.bundle
    assert proc.returncode != 0
    assert "generative_model" in proc.stderr or "No such file" in proc.stderr


def test_validate_rag_runner_covers_generative_params():
    from app.services.rag_service import build_rag_runner, validate_rag_runner

    runner = build_rag_runner(QUERY, answer_mode="generative", generative_model_id="gen-9")
    ok, reason, query = validate_rag_runner(runner)
    assert ok is True and query == QUERY
    # Tampering with a generative runner is still rejected.
    tampered = runner.replace('import sys\n', 'import sys\nimport socket\n', 1)
    ok2, _reason2, _ = validate_rag_runner(tampered)
    assert ok2 is False


def test_extract_rag_metrics_parses_output():
    from app.services.rag_service import _extract_rag_metrics

    out = json.dumps({"answer": "x", "rag_meta": {
        "embedding_engine": "tf", "answer_mode": "generative", "generative_model": "gen-1",
        "retrieved_chunks": 2, "top_k": 2, "watermark": True,
    }}, ensure_ascii=False)
    metrics = _extract_rag_metrics(out)
    assert metrics["retrieved_chunks"] == 2
    assert metrics["answer_mode"] == "generative"
    assert metrics["generative_model"] == "gen-1"
    assert metrics["embedding_engine"] == "tf"
    assert metrics["watermark"] is True
    assert _extract_rag_metrics("not json") == {}


@pytest.mark.asyncio
async def test_api_generative_requires_model(client, db_session, make_user):
    """answer_mode=generative without a registered model_id -> 400 (honest)."""
    from app.models.contract import Contract, ContractStatus
    from app.models.data_product import DataProduct
    from app.models.sandbox_session import SandboxSession, SessionStatus

    headers, user_id = await make_user("data_provider", "prov")
    buyer_headers, buyer = await make_user("buyer", "buy")
    product = DataProduct(provider_id=uuid.UUID(user_id), name="rag-prod", status="published")
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)
    contract = Contract(
        contract_no=f"C-{uuid.uuid4().hex[:8]}", contract_type="data_query",
        provider_id=uuid.UUID(user_id), buyer_id=uuid.UUID(buyer), title="rag",
        product_ids=[str(product.id)], status=ContractStatus.ACTIVE.value,
    )
    db_session.add(contract)
    await db_session.flush()
    await db_session.refresh(contract)
    session = SandboxSession(
        user_id=uuid.UUID(buyer), data_product_id=product.id, sandbox_level="L3",
        status=SessionStatus.READY.value, container_id="bwrap-rag-gen",
        contract_id=str(contract.id), timeout_seconds=3600,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)

    resp = await client.post(
        "/api/v1/sandbox-tasks",
        params={
            "session_id": str(session.id),
            "task_type": "rag_query",
            "rag_query": QUERY,
            "answer_mode": "generative",
        },
        headers=buyer_headers,
    )
    assert resp.status_code == 400
    assert "generative_model_id" in resp.json()["detail"]

    # Extractive mode (no model needed) still creates.
    resp2 = await client.post(
        "/api/v1/sandbox-tasks",
        params={
            "session_id": str(session.id),
            "task_type": "rag_query",
            "rag_query": QUERY,
            "answer_mode": "extractive_retrieval",
        },
        headers=buyer_headers,
    )
    assert resp2.status_code == 201, resp2.text
