"""A10 service-layer second gate: `register_model` must reject non-executable formats itself.

backend-24a closed the API boundary (`app/api/inference.py` returns 400 for
pickle/safetensors), but that is only the FIRST gate: any caller that invokes
`InferenceService.register_model` directly (future training-artifact wiring,
batch jobs, internal tooling) bypasses the API and still gets a `registered`
row with a permanently resident pickle/safetensors artifact that the fixed
`ort.InferenceSession` runner can never load.

The gate's single source of truth is the module-level `_EXECUTABLE_FORMATS`;
this test derives the rejected set from `MODEL_FORMATS - _EXECUTABLE_FORMATS`
(computed, not hardcoded) so the service layer can never pass by hardcoding a
second, drifting list.
"""
import uuid

import pytest

from app.services.inference_service import (
    MODEL_FORMATS,
    _EXECUTABLE_FORMATS,
    InferenceService,
)

_NON_EXECUTABLE = sorted(MODEL_FORMATS - _EXECUTABLE_FORMATS)


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


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", _NON_EXECUTABLE)
async def test_service_register_rejects_non_executable_formats(db_session, make_user, fmt):
    """Direct service call (API gate bypassed) must fail closed, nothing persisted."""
    _, provider = await make_user("data_provider", f"a10svc_{fmt}")
    svc = InferenceService()
    with pytest.raises(ValueError, match="not executable"):
        await svc.register_model(
            db_session,
            owner_id=uuid.UUID(provider),
            name=f"a10svc-{fmt}",
            fmt=fmt,
            artifact_bytes=b"\x80\x04\x95 pickle-looking artifact",
            product_id=None,
        )
    listed = await svc.list_models(db_session)
    assert [m for m in listed if m.format == fmt] == [], (
        f"format={fmt} reached the registry via the service layer — the API gate "
        f"is the only gate, the second gate is missing"
    )


@pytest.mark.asyncio
async def test_service_register_off_whitelist_format_message_unchanged(db_session, make_user):
    """Off-whitelist strings keep failing the MODEL_FORMATS check (not reworded)."""
    _, provider = await make_user("data_provider", "a10svc_off")
    svc = InferenceService()
    with pytest.raises(ValueError, match="Unsupported model format"):
        await svc.register_model(
            db_session, owner_id=uuid.UUID(provider), name="x", fmt="garbage",
            artifact_bytes=b"x", product_id=None,
        )


@pytest.mark.asyncio
async def test_service_register_executable_format_still_registers(db_session, make_user):
    """Over-tightening guard: every _EXECUTABLE_FORMATS member registers directly."""
    pytest.importorskip("onnx")
    _, provider = await make_user("data_provider", "a10svc_onnx")
    svc = InferenceService()
    model = await svc.register_model(
        db_session, owner_id=uuid.UUID(provider), name="a10svc-onnx", fmt="onnx",
        artifact_bytes=_tiny_onnx_model(), product_id=None,
    )
    assert model.status == "registered"
