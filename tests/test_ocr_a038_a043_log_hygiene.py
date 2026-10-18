"""A38 + A43: 日志面卫生（本机 3.12 门口径）。

A38 — `Redis connected: {REDIS_URL}` 把带密码的连接串直写 INFO 日志（生产
     CDS_LOG_JSON=true 时直接进采集链路）。
A43 — LOG_JSON=true 时 stdlib 日志被剥成裸消息：无时间戳/级别/logger 名/
     request_id，而且根本不是 JSON；root.handlers 被整体替换；structlog 那套
     是死代码（本机门里 structlog 甚至没装）。
"""
import io
import json
import logging
import re

import pytest

import app.core.logging as log_module
from app.core.config import get_settings

# 经模块属性取用：缺失的机制在红灯里表现为「该属性不存在」，而不是整文件收集失败
setup_logging = lambda json_mode: log_module.setup_logging(json_mode)
request_id_var = log_module.request_id_var

SECRET = "Sup3rS3cretP@ss"
REDIS_URL = f"redis://:{SECRET}@redis.prod.internal:6379/0"


# --------------------------------------------------------------------------- #
# A38: Redis 连接日志不得带凭据
# --------------------------------------------------------------------------- #
class _FakeRedis:
    def __init__(self, ping_error: Exception | None = None):
        self._ping_error = ping_error
        self.closed = False

    async def ping(self):
        if self._ping_error is not None:
            raise self._ping_error
        return True

    async def aclose(self):
        self.closed = True


class _FakeAioredis:
    def __init__(self, client: _FakeRedis):
        self.client = client
        self.urls: list[str] = []

    def from_url(self, url, **kwargs):
        self.urls.append(url)
        return self.client


@pytest.fixture
def redis_module(monkeypatch):
    import app.core.redis as redis_module

    monkeypatch.setattr(redis_module, "_redis_client", None)
    monkeypatch.setattr(redis_module, "_last_connect_failure", 0.0)
    monkeypatch.setattr(get_settings(), "REDIS_URL", REDIS_URL)
    return redis_module


def _records(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


@pytest.mark.asyncio
async def test_successful_connect_logs_no_password(redis_module, caplog):
    fake = _FakeAioredis(_FakeRedis())
    monkey = pytest.MonkeyPatch()
    monkey.setattr(redis_module, "aioredis", fake)
    try:
        with caplog.at_level(logging.INFO, logger="app.core.redis"):
            await redis_module.get_redis()
    finally:
        monkey.undo()

    msgs = _records(caplog)
    assert msgs, "expected a connect log line"
    assert all(SECRET not in m for m in msgs), msgs
    # 但主机/端口/db 必须仍然可读——排障不能靠猜
    assert any("redis.prod.internal:6379" in m for m in msgs), msgs


@pytest.mark.asyncio
async def test_failed_connect_logs_no_password(redis_module, caplog):
    # redis 异常文本本身就可能回显整条 URL（含密码）
    err = ConnectionError(f"Error 111 connecting to {REDIS_URL}: refused")
    fake = _FakeAioredis(_FakeRedis(ping_error=err))
    monkey = pytest.MonkeyPatch()
    monkey.setattr(redis_module, "aioredis", fake)
    try:
        with caplog.at_level(logging.INFO, logger="app.core.redis"):
            with pytest.raises(ConnectionError):
                await redis_module.get_redis()
    finally:
        monkey.undo()

    msgs = _records(caplog)
    assert any("Redis connection failed" in m for m in msgs), msgs
    assert all(SECRET not in m for m in msgs), msgs
    assert all("redis.prod.internal" in m or "connection" in m.lower() for m in msgs)
    assert fake.client.closed is True


def test_redact_helper_scrubs_credentials(redis_module):
    redact = redis_module.redact_url
    assert SECRET not in redact(REDIS_URL)
    assert "redis.prod.internal:6379" in redact(REDIS_URL)
    # 无凭据的 URL 原样可读
    assert redact("redis://cache:6379/1") == "redis://cache:6379/1"
    # 任意文本里内嵌的带凭据 URL / 查询串口令都要被抹掉
    assert SECRET not in redact(f"boom while dialing {REDIS_URL}")
    assert "hunter2" not in redact("rediss://host:6379/0?password=hunter2")


# --------------------------------------------------------------------------- #
# A43: LOG_JSON=true 必须是真 JSON，且带上下文、不丢既有 handler
# --------------------------------------------------------------------------- #
@pytest.fixture
def root_logging():
    """快照并复原 root logger（setup_logging 是进程级副作用）。"""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_formatters = {id(h): h.formatter for h in root.handlers}
    saved_level = root.level
    saved_filters = list(root.filters)
    token = request_id_var.set("-")
    yield root
    request_id_var.reset(token)
    for handler in list(root.handlers):
        if handler not in saved_handlers:
            root.removeHandler(handler)
            handler.close()
    root.handlers[:] = saved_handlers
    for handler in saved_handlers:
        handler.setFormatter(saved_formatters[id(handler)])
    root.setLevel(saved_level)
    root.filters[:] = saved_filters


def _emit_and_parse(handler_buf: io.StringIO, logger_name: str, message: str) -> dict:
    setup_logging(True)
    logging.getLogger(logger_name).warning(message)
    line = handler_buf.getvalue().strip().splitlines()[-1]
    return json.loads(line)  # A43 的核心断言：这一行必须真的是 JSON


def test_json_mode_emits_structured_json_for_stdlib_loggers(root_logging):
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    root_logging.addHandler(handler)
    request_id_var.set("req-abcdef123456")

    doc = _emit_and_parse(buf, "app.services.demo", "quota exhausted for %s" % "tenant-7")

    assert doc["level"] == "WARNING"
    assert doc["logger"] == "app.services.demo"
    assert doc["message"] == "quota exhausted for tenant-7"
    assert doc["request_id"] == "req-abcdef123456"
    # ISO8601 UTC 时间戳（可被采集端解析）
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", doc["timestamp"]), doc


def test_json_mode_keeps_traceback(root_logging):
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    root_logging.addHandler(handler)

    setup_logging(True)
    try:
        1 / 0
    except ZeroDivisionError:
        logging.getLogger("app.boom").exception("divide failed")

    doc = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert doc["message"] == "divide failed"
    assert "ZeroDivisionError" in doc["exception"]


def test_json_mode_does_not_replace_existing_handlers(root_logging):
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    sentinel = logging.Formatter("%(levelname)s|legacy")
    handler.setFormatter(sentinel)
    root_logging.addHandler(handler)
    before = list(root_logging.handlers)

    setup_logging(True)
    setup_logging(True)  # 幂等：重复调用不得叠加 handler

    assert handler in root_logging.handlers
    assert list(root_logging.handlers) == before, "handler 集必须稳定"
    logging.getLogger("app.legacy").warning("still here")
    line = buf.getvalue().strip().splitlines()[-1]
    assert json.loads(line)["message"] == "still here"


def test_json_level_policy_never_overrides_explicit_config(root_logging):
    # 未配置（NOTSET → 有效级别 WARNING）时要把 INFO 放出来…
    root_logging.setLevel(logging.NOTSET)
    setup_logging(True)
    assert root_logging.level == logging.INFO

    # …但已有的显式配置（无论更松还是更严）一律尊重，不强制覆盖
    root_logging.setLevel(logging.DEBUG)
    setup_logging(True)
    assert root_logging.level == logging.DEBUG

    root_logging.setLevel(logging.ERROR)
    setup_logging(True)
    assert root_logging.level == logging.ERROR


def test_text_mode_leaves_logging_untouched(root_logging):
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    plain = logging.Formatter("%(levelname)s:%(name)s:%(message)s")
    handler.setFormatter(plain)
    root_logging.addHandler(handler)
    root_logging.setLevel(logging.INFO)

    setup_logging(False)

    assert handler.formatter is plain
    logging.getLogger("app.text").info("plain text line")
    assert buf.getvalue().strip() == "INFO:app.text:plain text line"


def test_request_id_filter_is_installed_first(root_logging):
    root_logging.filters[:] = []
    setup_logging(True)
    assert any(isinstance(f, log_module.RequestIdFilter) for f in root_logging.filters)


# --------------------------------------------------------------------------- #
# A43 后半段：request_id 上下文必须真的有人写
# （RequestIdFilter 挂在 root 上，stdlib 子 logger 的记录根本不经过它）
# --------------------------------------------------------------------------- #
async def _scope_call(middleware, scope):
    seen: dict = {}

    async def inner(receive, send):
        seen["request_id"] = request_id_var.get()

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        return None

    await middleware(scope, receive, send)
    return seen


@pytest.mark.asyncio
async def test_context_middleware_binds_request_id_from_state():
    mw = log_module.RequestIDContextMiddleware(lambda: None)
    scope = {"type": "http", "path": "/api/v1/x", "headers": [], "state": {"request_id": "rid-1"}}
    seen = await _scope_call(mw, scope)
    assert seen["request_id"] == "rid-1"
    # 请求结束必须复位，不能泄漏到别的任务
    assert request_id_var.get() == "-"


@pytest.mark.asyncio
async def test_context_middleware_uses_incoming_header_before_state():
    mw = log_module.RequestIDContextMiddleware(lambda: None)
    scope = {
        "type": "http",
        "path": "/x",
        "headers": [(b"x-request-id", b"rid-from-client")],
        "state": {},
    }
    seen = await _scope_call(mw, scope)
    assert seen["request_id"] == "rid-from-client"


@pytest.mark.asyncio
async def test_context_middleware_is_mounted_inner_of_request_id():
    from app.core.telemetry import RequestIDMiddleware
    from app.main import app

    chain = [m.cls for m in app.user_middleware]
    assert log_module.RequestIDContextMiddleware in chain, chain
    # Starlette: 后加入者在外层。request_id 上下文必须在 RequestIDMiddleware
    # 之内（读它写进 scope["state"] 的 id），又在业务路由之外。
    assert chain.index(log_module.RequestIDContextMiddleware) < chain.index(RequestIDMiddleware), chain


@pytest.mark.asyncio
async def test_request_id_reaches_logs_through_the_app(client, root_logging, monkeypatch):
    """/health 处理函数内部打的日志必须带上本次请求的 request_id。"""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    root_logging.addHandler(handler)
    setup_logging(True)

    import app.api.health as health_api

    real = health_api.health_capability_section

    def _logging_capability_section():
        logging.getLogger("app.api.health").warning("inside the request")
        return real()

    monkeypatch.setattr(health_api, "health_capability_section", _logging_capability_section)

    resp = await client.get("/health", headers={"X-Request-ID": "rid-e2e-000111"})
    assert resp.status_code == 200
    # /health 响应字段不许缺失（前端按字段渲染）
    body = resp.json()
    assert body["status"] == "ok" and body["service"] == "cds" and body["capability"]

    doc = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert doc["request_id"] == "rid-e2e-000111", doc
    assert doc["logger"] == "app.api.health"
