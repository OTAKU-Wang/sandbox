"""A10 — the model-format whitelist admitted formats the runner can never run.

`POST /api/v1/inference/models` whitelisted `onnx|pickle|safetensors`, while
the service's promised fail-closed gate `_EXECUTABLE_FORMATS = {"onnx"}` was
defined and never referenced anywhere. Registering a pickle artifact returned
201 + `registered`, left the artifact in the registry, metered its invokes —
and every invoke died inside the sandbox because the runner is a fixed
`ort.InferenceSession` on `model.onnx`. Accepting pickle at an upload
endpoint is also a latent deserialisation-RCE surface.

Invariants pinned here:
* `format=pickle` / `format=safetensors` are refused at registration (400)
  and never enter the registry;
* an off-whitelist string keeps failing wire validation (422);
* a valid ONNX artifact still registers (over-tightening guard).
"""
import pytest

from app.services.inference_service import MODEL_FORMATS, _EXECUTABLE_FORMATS


def _tiny_onnx_model() -> bytes:
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


async def _register(client, headers, fmt: str, artifact: bytes):
    return await client.post(
        "/api/v1/inference/models",
        files={"file": (f"model.{fmt}", artifact, "application/octet-stream")},
        data={"name": f"a10-{fmt}", "format": fmt},
        headers=headers,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["pickle", "safetensors"])
async def test_non_executable_formats_refused_at_registration(
    client, db_session, make_user, fmt
):
    prov, _ = await make_user("data_provider", "a10_prov")
    resp = await _register(client, prov, fmt, b"\x80\x04\x95 garbage artifact")
    assert resp.status_code == 400, (
        f"format={fmt} was not refused at registration — the fail-closed "
        f"gate must reject formats the v1 runner cannot execute: "
        f"got {resp.status_code} {resp.json() if resp.content else ''}"
    )
    listed = await client.get("/api/v1/inference/models", headers=prov)
    assert listed.status_code == 200
    assert [m for m in listed.json() if m["format"] == fmt] == []


@pytest.mark.asyncio
async def test_off_whitelist_format_still_fails_wire_validation(client, db_session, make_user):
    prov, _ = await make_user("data_provider", "a10_prov2")
    resp = await _register(client, prov, "garbage", b"x")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_valid_onnx_still_registers(client, db_session, make_user):
    pytest.importorskip("onnx")
    prov, _ = await make_user("data_provider", "a10_prov3")
    resp = await _register(client, prov, "onnx", _tiny_onnx_model())
    assert resp.status_code == 201, resp.text
    assert resp.json()["format"] == "onnx"


def test_executable_gate_is_a_strict_subset_of_the_wire_whitelist():
    assert _EXECUTABLE_FORMATS == {"onnx"}
    assert _EXECUTABLE_FORMATS < MODEL_FORMATS
