"""SS-15 单元面 — 调度时点、幂等键、准入门禁、推送 SSRF/签名/退避、Notebook 后端披露。

这里只测纯函数与不依赖沙箱的逻辑；需要真实执行链的路径留给远程 e2e。
"""
import base64
import json
import uuid
from datetime import datetime, timezone

import pytest

from app.services import compute_scheduler as cs
from app.services import delivery_service as dv
from app.services import notebook_service as nb
from app.services import workflow_service as wf


class TestComputeNextRun:
    def test_manual_returns_none(self):
        assert cs.compute_next_run(auto_compute=True, frequency="manual", minute=0, hour=2, tz_name="Asia/Shanghai") is None

    def test_auto_compute_off_returns_none(self):
        assert cs.compute_next_run(auto_compute=False, frequency="hourly", minute=5, hour=2, tz_name="UTC") is None

    def test_hourly_rolls_to_next_hour(self):
        after = datetime(2026, 10, 15, 3, 40, tzinfo=timezone.utc)
        nxt = cs.compute_next_run(auto_compute=True, frequency="hourly", minute=15, hour=0, tz_name="UTC", after=after)
        assert nxt == datetime(2026, 10, 15, 4, 15, tzinfo=timezone.utc)

    def test_hourly_same_hour_future_minute_keeps_slot(self):
        after = datetime(2026, 10, 15, 3, 5, tzinfo=timezone.utc)
        nxt = cs.compute_next_run(auto_compute=True, frequency="hourly", minute=30, hour=0, tz_name="UTC", after=after)
        assert nxt == datetime(2026, 10, 15, 3, 30, tzinfo=timezone.utc)

    def test_daily_uses_local_timezone(self):
        after = datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc)
        nxt = cs.compute_next_run(auto_compute=True, frequency="daily", minute=30, hour=9, tz_name="Asia/Shanghai", after=after)
        assert nxt.astimezone(timezone.utc).hour == 1
        assert nxt.astimezone(timezone.utc).minute == 30
        assert nxt.astimezone(timezone.utc).day == 16

    def test_unknown_timezone_falls_back_to_utc(self):
        after = datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc)
        nxt = cs.compute_next_run(auto_compute=True, frequency="daily", minute=0, hour=3, tz_name="Mars/Olympus", after=after)
        assert nxt.astimezone(timezone.utc).hour == 3

    def test_idempotency_key_stable_and_utc(self):
        key = cs.idempotency_key("11111111-1111-1111-1111-111111111111", datetime(2026, 10, 15, 1, 30, tzinfo=timezone.utc))
        assert key.endswith(":20261015T013000Z")

    def test_idempotency_key_normalizes_offset(self):
        from datetime import timedelta

        tz = timezone(timedelta(hours=8))
        a = cs.idempotency_key("11111111-1111-1111-1111-111111111111", datetime(2026, 10, 15, 9, 30, tzinfo=tz))
        b = cs.idempotency_key("11111111-1111-1111-1111-111111111111", datetime(2026, 10, 15, 1, 30, tzinfo=timezone.utc))
        assert a == b


class TestWorkflowGate:
    def test_hash_entrypoint_is_deterministic(self):
        assert wf.hash_entrypoint("print(1)") == wf.hash_entrypoint("print(1)")
        assert wf.hash_entrypoint("print(1)") != wf.hash_entrypoint("print(2)")

    def test_transition_table_rejects_publish_to_retired_chain_gaps(self):
        with pytest.raises(wf.WorkloadGateError) as exc:
            wf.assert_transition("draft", "published")
        assert exc.value.code == wf.GATE_STATE_CONFLICT

    def test_transition_allows_test_passed_to_published(self):
        wf.assert_transition("test_passed", "published")

    def test_evaluate_checks_requires_every_check(self):
        assert wf.evaluate_checks([{"name": "a", "passed": True}]) is True
        assert wf.evaluate_checks([{"name": "a", "passed": True}, {"name": "b", "passed": False}]) is False
        assert wf.evaluate_checks([]) is False

    def test_entrypoint_from_notebook_skips_markdown(self):
        doc = nb.make_notebook("t")
        doc["cells"] = [
            {"cell_type": "markdown", "source": "# 说明", "metadata": {}},
            {"cell_type": "code", "source": "import os\nprint(1)", "metadata": {}, "outputs": [], "execution_count": None},
        ]
        text = wf.entrypoint_from_notebook(doc)
        assert "print(1)" in text
        assert "说明" not in text


class TestWebhookSecurity:
    def test_public_https_url_accepted(self, monkeypatch):
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "WEBHOOK_ALLOW_PRIVATE_URLS", True)
        monkeypatch.setattr(dv.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
        assert dv.validate_webhook_url("https://example.com/hook").startswith("https://example.com")

    def test_private_address_rejected_when_not_allowed(self, monkeypatch):
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "WEBHOOK_ALLOW_PRIVATE_URLS", False)
        monkeypatch.setattr(dv.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 443))])
        with pytest.raises(dv.WebhookUrlRejected):
            dv.validate_webhook_url("https://internal.example.com/hook")

    def test_loopback_metadata_ip_rejected(self, monkeypatch):
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "WEBHOOK_ALLOW_PRIVATE_URLS", False)
        monkeypatch.setattr(dv.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("169.254.169.254", 80))])
        with pytest.raises(dv.WebhookUrlRejected):
            dv.validate_webhook_url("http://169.254.169.254/latest/meta-data")

    def test_non_http_scheme_rejected(self):
        with pytest.raises(dv.WebhookUrlRejected):
            dv.validate_webhook_url("file:///etc/passwd")

    def test_plain_http_requires_private_allowance(self, monkeypatch):
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "WEBHOOK_ALLOW_PRIVATE_URLS", False)
        with pytest.raises(dv.WebhookUrlRejected):
            dv.validate_webhook_url("http://partner.example.com/hook")

    def test_signature_roundtrip_and_tamper_detection(self):
        sig = dv.sign_payload("s3cret", "1700000000", '{"a":1}')
        assert dv.verify_signature("s3cret", "1700000000", '{"a":1}', sig)
        assert not dv.verify_signature("s3cret", "1700000000", '{"a":2}', sig)
        assert not dv.verify_signature("other", "1700000000", '{"a":1}', sig)

    def test_backoff_grows_and_caps(self):
        d = [dv.backoff_delay(i).total_seconds() for i in range(1, 14)]
        assert d == sorted(d)
        assert d[-1] == dv.BACKOFF_CAP_SECONDS

    def test_slug_generation_is_unique_and_safe(self):
        taken = {"daily-report"}
        slug = dv.generate_slug("Daily Report", taken)
        assert slug == "daily-report-2"
        assert dv.generate_slug("  ", set()) == "endpoint"


class TestEndpointRateLimit:
    def test_rate_limit_blocks_after_capacity(self):
        from app.models.api_endpoint import ApiEndpoint

        dv.reset_rate_windows()
        endpoint = ApiEndpoint(name="n", slug="s", rate_limit_per_minute=2)
        dv.check_endpoint_rate_limit(endpoint, "caller-1")
        dv.check_endpoint_rate_limit(endpoint, "caller-1")
        with pytest.raises(dv.EndpointUnavailable) as exc:
            dv.check_endpoint_rate_limit(endpoint, "caller-1")
        assert exc.value.code == "RATE_LIMITED"
        dv.check_endpoint_rate_limit(endpoint, "caller-2")
        dv.reset_rate_windows()


class TestOpenApiDocument:
    def test_document_lists_endpoints_and_security(self):
        class _C:
            contract_no = "CT-1"

        class _E:
            slug = "daily-report"
            name = "Daily Report"
            status = "enabled"
            request_schema = {"type": "object", "properties": {"day": {"type": "string"}}}
            response_schema = None
            rate_limit_per_minute = 30
            quota_total = 100
            calls_used = 3

        doc = dv.build_openapi_doc(_C(), [_E()], base_url="https://cds.example.com")
        assert doc["openapi"] == "3.1.0"
        op = doc["paths"]["/open/v1/daily-report"]["post"]
        assert op["security"] == [{"CdsApiKey": []}]
        assert doc["components"]["securitySchemes"]["CdsApiKey"]["name"] == "X-CDS-API-Key"
        assert op["x-rate-limit-per-minute"] == 30


class TestNotebookBackendDisclosure:
    @pytest.mark.asyncio
    async def test_kernel_envelope_is_reported_as_cds_kernel(self):
        payload = {"ok": True, "backend": "cds_kernel", "stateful": True, "stdout": "x\n", "repr": "1", "exit_code": 0}
        envelope = base64.b64encode(json.dumps(payload).encode()).decode()

        async def executor(code: str) -> dict:
            return {"output": f"x\n{nb.KERNEL_MARKER}{envelope}\n", "exit_code": 0}

        result = await nb.run_cell(executor, "print(1)")
        assert result["backend"] == "cds_kernel"
        assert result["stateful"] is True
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_missing_kernel_degrades_to_stateless_and_says_so(self):
        async def executor(code: str) -> dict:
            return {"output": "1\n", "exit_code": 0}

        result = await nb.run_cell(executor, "print(1)")
        assert result["backend"] == "cds_stateless"
        assert result["stateful"] is False
        assert result.get("degraded_reason")


class TestCellDriverTrust:
    def test_driver_is_trusted_only_byte_exact(self):
        script = nb.build_cell_driver("print(1)", "c1")
        trusted = nb.split_trusted_driver(script)
        assert trusted is not None
        assert trusted["code"] == "print(1)"
        assert trusted["cell_id"] == "c1"
        assert nb.split_trusted_driver(script + "\nimport os\n") is None
        assert nb.split_trusted_driver("import os\nprint(1)") is None
        assert nb.split_trusted_driver(script.replace("kernel.sock", "kernel2.sock")) is None

    def test_platform_constant_probe_is_trusted_only_byte_exact(self):
        trusted = nb.split_trusted_driver(nb.JUPYTER_PROBE)
        assert trusted is not None and trusted["cell_id"] == "jupyter_probe"
        assert nb.split_trusted_driver(nb.JUPYTER_PROBE + "\nimport os\n") is None
        assert nb.split_trusted_driver(nb.JUPYTER_PROBE.replace("shutil.which", "__import__")) is None

    def test_user_code_inside_trusted_driver_is_still_scanned(self):
        from app.services.code_scanner import code_scanner

        trusted = nb.split_trusted_driver(nb.build_cell_driver("import os\nos.system('id')"))
        assert trusted is not None
        assert code_scanner.scan(trusted["code"], "python", "product_dev").passed is False


class _ContractModes:
    def __init__(self, modes):
        self.allowed_sandbox_modes = modes


class TestSandboxModeResolution:
    """合约用业务词表，会话用执行档词表 —— 两者必须在此对齐。"""

    def test_business_vocabulary_maps_to_execution_profile(self):
        assert wf.resolve_sandbox_mode(_ContractModes(["develop", "query"]), "product_dev") == "product_dev"
        assert wf.resolve_sandbox_mode(_ContractModes(["application"]), "structured_app") == "structured_app"
        assert wf.resolve_sandbox_mode(_ContractModes(["train"]), "llm_training") == "llm_training"

    def test_no_restriction_keeps_preferred(self):
        assert wf.resolve_sandbox_mode(_ContractModes(None), "product_dev") == "product_dev"
        assert wf.resolve_sandbox_mode(_ContractModes([]), "product_dev") == "product_dev"

    def test_alias_preferred_is_canonicalized(self):
        assert wf.resolve_sandbox_mode(_ContractModes(["develop"]), "develop") == "product_dev"

    def test_unpermitted_mode_fails_closed_instead_of_substituting(self):
        from app.core.errors import CDSError

        with pytest.raises(CDSError) as exc:
            wf.resolve_sandbox_mode(_ContractModes(["query"]), "product_dev")
        assert exc.value.code == "SANDBOX_MODE_NOT_PERMITTED"

    def test_unknown_preferred_is_rejected(self):
        from app.core.errors import CDSError

        with pytest.raises(CDSError) as exc:
            wf.resolve_sandbox_mode(_ContractModes(["query"]), "quantum")
        assert exc.value.code == "SANDBOX_MODE_UNSUPPORTED"

    def test_contract_schema_rejects_unknown_mode_at_create(self):
        from pydantic import ValidationError

        from app.schemas.contract import ContractCreate

        def make(modes):
            return ContractCreate(
                contract_type="product_dev",
                buyer_id=uuid.uuid4(),
                product_ids=[uuid.uuid4()],
                title="t",
                allowed_sandbox_modes=modes,
            )

        with pytest.raises(ValidationError, match="无法识别的沙箱模式"):
            make(["teleport"])
        assert make(["query", "train", "develop", "application"]).allowed_sandbox_modes == ["query", "train", "develop", "application"]


class _NoopDb:
    async def flush(self):
        return None


class _UnreachableClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *args, **kwargs):
        raise RuntimeError("")


class TestDeliveryFailureReason:
    async def test_empty_exception_message_still_records_a_reason(self, monkeypatch):
        from types import SimpleNamespace

        from app.models.webhook_delivery import DeliveryStatus, WebhookDelivery

        monkeypatch.setattr(dv.httpx, "AsyncClient", _UnreachableClient)
        delivery = WebhookDelivery(
            subscription_id=uuid.uuid4(),
            event_type="webhook.test",
            event_id=uuid.uuid4().hex,
            payload={},
            status="pending",
            attempt=0,
            max_attempts=6,
        )
        sub = SimpleNamespace(
            id=uuid.uuid4(),
            url="http://partner.example.com/hook",
            secret_enc=None,
            consecutive_failures=0,
            last_error=None,
            last_delivered_at=None,
            status="active",
        )

        out = await dv.deliver_once(_NoopDb(), delivery, sub)

        assert out.status == DeliveryStatus.FAILED.value
        assert out.last_error == "RuntimeError"
        assert sub.last_error == "RuntimeError"
