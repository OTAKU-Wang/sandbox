"""W2 error contract tests — unified {code, message, detail, request_id} body,
Retry-After for transient errors, legacy ``detail`` compatibility, and SDK
parsing of the W2 shape.
"""
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import CDSError, OperationLocked, SessionTerminated
from app.core.error_handlers import register_exception_handlers

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from cds_sdk import CDSApiError  # noqa: E402


class _Credentials(BaseModel):
    """校验错误路径用的最小体模型。"""

    username: str
    password: str


def _app_with_routes() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("exploded")

    @app.get("/locked")
    async def locked():
        raise OperationLocked("rollback in progress")

    @app.get("/gone")
    async def gone():
        raise SessionTerminated("session terminated")

    @app.get("/legacy-http")
    async def legacy_http():
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="nope")

    @app.get("/custom-code")
    async def custom_code():
        raise CDSError("denied", code="DENIED", http_status=403)

    @app.post("/needs-json")
    async def needs_json(payload: _Credentials):
        return {"ok": True, "who": payload.username}

    return app


@pytest.fixture
def fresh_client():
    with TestClient(_app_with_routes(), raise_server_exceptions=False) as c:
        yield c

def test_unhandled_exception_returns_internal_error_contract(fresh_client):
    resp = fresh_client.get("/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "INTERNAL_ERROR"
    assert body["message"] == "Internal server error"
    assert re_fullmatch16(body["request_id"])
    assert resp.headers.get("X-Request-ID") == body["request_id"]


def test_operation_locked_has_retry_after(fresh_client):
    resp = fresh_client.get("/locked")
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After") == "2"
    assert resp.json()["code"] == "OPERATION_LOCKED"


def test_session_terminated_is_410(fresh_client):
    resp = fresh_client.get("/gone")
    assert resp.status_code == 410
    assert resp.json()["code"] == "SESSION_TERMINATED"


def test_starlette_http_exception_keeps_detail(fresh_client):
    resp = fresh_client.get("/legacy-http")
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"] == "nope"  # legacy field preserved
    assert body["code"] == "HTTP_404"
    assert body["request_id"]


def test_custom_code_and_status(fresh_client):
    resp = fresh_client.get("/custom-code")
    assert resp.status_code == 403
    assert resp.json()["code"] == "DENIED"


def test_validation_error_with_bytes_input_is_422_not_500(fresh_client):
    """回归：pydantic v2 把无法解析的请求体原文（bytes）放进 ``errors()["input"]``。

    旧净化器只处理 ``ctx``，于是 ``JSONResponse`` 渲染时抛
    ``TypeError: Object of type bytes is not JSON serializable``——客户端只是
    写错 Content-Type，却拿到 500 而不是 422（dev 道实测于
    ``POST /api/v1/auth/login``，服务端 traceback 里两行都有）。
    """
    resp = fresh_client.post(
        "/needs-json",
        content=b"username=u&password=p",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert isinstance(body["detail"], list) and body["detail"]
    for err in body["detail"]:
        assert not isinstance(err.get("input"), (bytes, bytearray))
    assert re_fullmatch16(body["request_id"])


def test_validation_error_with_missing_field_stays_422(fresh_client):
    """JSON 但缺字段：detail 仍是 FastAPI 422 形状，input 原样保留。"""
    resp = fresh_client.post("/needs-json", json={"username": "u"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["detail"][0]["type"] == "missing"
    assert body["detail"][0]["input"] == {"username": "u"}


def re_fullmatch16(value: str) -> bool:
    import re

    return bool(re.fullmatch(r"[0-9a-f]{16}", value or ""))


# ─── SDK parsing of the W2 shape ──────────────────────────────────


def test_sdk_parses_w2_error_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"code": "OPERATION_LOCKED", "message": "rollback in progress",
                  "detail": {"op": "rollback"}, "request_id": "abcd1234efgh5678"},
            headers={"Retry-After": "5"},
        )

    from cds_sdk import CDSClient

    with CDSClient("http://test", token="t", transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(CDSApiError) as exc:
            c.get_session("s-1")
    assert exc.value.code == "OPERATION_LOCKED"
    assert exc.value.retry_after == 5
    assert exc.value.request_id == "abcd1234efgh5678"
    assert "rollback" in exc.value.message


def test_sdk_legacy_shape_falls_back_to_http_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Sandbox session not found"})

    from cds_sdk import CDSClient

    with CDSClient("http://test", token="t", transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(CDSApiError) as exc:
            c.get_session("s-1")
    assert exc.value.code == "HTTP_404"
    assert exc.value.detail == "Sandbox session not found"


def test_sdk_is_backward_compatible_cdserror():
    from cds_sdk import CDSError

    err = CDSApiError(500, "boom", "/x")
    assert isinstance(err, CDSError)
