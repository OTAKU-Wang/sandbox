"""SS-15 SDK: flow-orchestration surface (notebook → workload → delivery).

httpx.MockTransport only — no server. Pins the wire contract (method, path, query and
body shape) that the deployed backend exposes for the three-stage business flow.
"""
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from cds_sdk import CDSApiError, CDSClient  # noqa: E402

BASE = "http://test"
TOKEN = "test-token"

FLOW_METHODS = [
    "list_notebooks", "create_notebook", "get_notebook", "update_notebook",
    "archive_notebook", "start_notebook_dev_session", "execute_notebook_cell",
    "get_notebook_jupyter", "run_notebook",
    "list_workloads", "create_workload", "get_workload", "test_workload",
    "list_workload_test_runs", "get_workload_test_run", "publish_workload",
    "rollback_workload", "retire_workload",
    "create_dataset_bundle", "download_dataset_bundle", "get_contract_openapi",
    "create_openapi_endpoint", "list_openapi_endpoints", "update_openapi_endpoint",
    "delete_openapi_endpoint", "invoke_open_endpoint",
    "create_webhook", "list_webhooks", "update_webhook", "delete_webhook",
    "list_webhook_deliveries", "redeliver_webhook_delivery", "rotate_webhook_secret",
    "test_webhook",
    "trigger_compute_run", "list_compute_runs", "get_compute_run", "cancel_compute_run",
    "get_compute_schedule", "update_contract_flow_config",
]


def _client(handler):
    return CDSClient(BASE, token=TOKEN, transport=httpx.MockTransport(handler))


def test_sdk_covers_every_flow_endpoint():
    assert len(FLOW_METHODS) == 40
    missing = [name for name in FLOW_METHODS if not callable(getattr(CDSClient, name, None))]
    assert missing == []


def test_notebook_paths_and_bodies():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, str(request.url.query), request.content))
        return httpx.Response(200, json={"id": "nb-1", "version": 7})

    with _client(handler) as c:
        c.list_notebooks(contract_id="c-1", page=2, page_size=5)
        c.create_notebook("分析笔记", "/work/a.ipynb", contract_id="c-1")
        c.get_notebook("nb-1")
        c.update_notebook("nb-1", content="{}")
        c.archive_notebook("nb-1")
        c.start_notebook_dev_session("nb-1")
        c.execute_notebook_cell("nb-1", "print(1)", cell_index=0)
        c.get_notebook_jupyter("nb-1")
        c.run_notebook("nb-1")

    assert [(m, p) for m, p, _, _ in seen] == [
        ("GET", "/api/v1/notebooks"),
        ("POST", "/api/v1/notebooks"),
        ("GET", "/api/v1/notebooks/nb-1"),
        ("PUT", "/api/v1/notebooks/nb-1"),
        ("DELETE", "/api/v1/notebooks/nb-1"),
        ("POST", "/api/v1/notebooks/nb-1/dev-session"),
        ("POST", "/api/v1/notebooks/nb-1/cells/execute"),
        ("GET", "/api/v1/notebooks/nb-1/jupyter"),
        ("POST", "/api/v1/notebooks/nb-1/run"),
    ]
    assert "contract_id=c-1" in seen[0][2] and "page=2" in seen[0][2]
    created = json.loads(seen[1][3])
    assert created["title"] == "分析笔记" and created["language"] == "python"
    assert "contract_id" in created and "session_id" not in created
    put_body = json.loads(seen[3][3])
    assert put_body == {"content": "{}"}
    assert json.loads(seen[6][3]) == {"code": "print(1)", "persist": False, "cell_index": 0}


def test_unset_optionals_are_never_sent():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content) if request.content else {})
        return httpx.Response(200, json={})

    with _client(handler) as c:
        c.update_webhook("sub-1", status="paused")
        c.update_openapi_endpoint("ep-1", quota_total=100)
        c.update_contract_flow_config("c-1", auto_compute=False, compute_frequency="hourly")

    assert bodies[0] == {"status": "paused"}
    assert bodies[1] == {"quota_total": 100}
    assert bodies[2] == {"auto_compute": False, "compute_frequency": "hourly"}


def test_workload_gate_calls_use_subresource_paths():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"id": "wl-1", "status": "test_passed"})

    with _client(handler) as c:
        c.create_workload("夜间计算", notebook_id="nb-1", contract_id="c-1")
        c.test_workload("wl-1")
        c.list_workload_test_runs("wl-1")
        c.get_workload_test_run("wl-1", "run-9")
        c.publish_workload("wl-1")
        c.rollback_workload("wl-1")
        c.retire_workload("wl-1")

    assert seen == [
        ("POST", "/api/v1/workloads"),
        ("POST", "/api/v1/workloads/wl-1/test"),
        ("GET", "/api/v1/workloads/wl-1/test-runs"),
        ("GET", "/api/v1/workloads/wl-1/test-runs/run-9"),
        ("POST", "/api/v1/workloads/wl-1/publish"),
        ("POST", "/api/v1/workloads/wl-1/rollback"),
        ("POST", "/api/v1/workloads/wl-1/retire"),
    ]


def test_publish_gate_error_is_typed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            412,
            json={"code": "WORKLOAD_NOT_PUBLISHED", "message": "测试未通过", "detail": {}, "request_id": "r-1"},
        )

    with _client(handler) as c:
        with pytest.raises(CDSApiError) as exc:
            c.publish_workload("wl-1")
    assert exc.value.status_code == 412
    assert exc.value.code == "WORKLOAD_NOT_PUBLISHED"
    assert exc.value.request_id == "r-1"


def test_dataset_bundle_download_returns_bytes_and_raises_on_foreign_bundle():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/ok/download"):
            return httpx.Response(200, content=b"PK\x03\x04zip", headers={"content-type": "application/zip"})
        return httpx.Response(404, json={"code": "HTTP_404", "message": "交付包不存在或无归属记录", "detail": None, "request_id": "r-2"})

    with _client(handler) as c:
        assert c.download_dataset_bundle("ok") == b"PK\x03\x04zip"
        with pytest.raises(CDSApiError) as exc:
            c.download_dataset_bundle("foreign")
    assert exc.value.status_code == 404
    assert exc.value.message == "交付包不存在或无归属记录"


def test_webhook_and_open_channel_shapes():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, str(request.url.query), request.content))
        return httpx.Response(200, json={"ok": True})

    with _client(handler) as c:
        c.create_webhook("结果推送", "http://sink/hook", ["workload.published"])
        c.list_webhooks(contract_id="c-1")
        c.list_webhook_deliveries("sub-1", status="failed")
        c.redeliver_webhook_delivery("sub-1", "d-1")
        c.rotate_webhook_secret("sub-1")
        c.test_webhook("sub-1")
        c.create_openapi_endpoint("c-1", "推理入口", workload_id="wl-1", rate_limit_per_minute=30)
        c.list_openapi_endpoints(contract_id="c-1")
        c.delete_openapi_endpoint("ep-1")
        c.invoke_open_endpoint("nightly-slug", {"input": {"a": 1}})

    assert seen[0][1] == "/api/v1/deliveries/webhooks"
    assert json.loads(seen[0][3]) == {
        "name": "结果推送",
        "url": "http://sink/hook",
        "events": ["workload.published"],
    }
    assert "contract_id=c-1" in seen[1][2]
    assert "status=failed" in seen[2][2]
    assert seen[3][1] == "/api/v1/deliveries/webhooks/sub-1/deliveries/d-1/redeliver"
    assert seen[4][1] == "/api/v1/deliveries/webhooks/sub-1/rotate-secret"
    assert seen[5][1] == "/api/v1/deliveries/webhooks/sub-1/test"
    ep_body = json.loads(seen[6][3])
    assert ep_body == {"contract_id": "c-1", "name": "推理入口", "workload_id": "wl-1",
                       "rate_limit_per_minute": 30}
    assert (seen[9][0], seen[9][1]) == ("POST", "/open/v1/nightly-slug")
    assert json.loads(seen[9][3]) == {"input": {"a": 1}}


def test_compute_and_flow_config_paths():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, str(request.url.query), request.content))
        return httpx.Response(200, json={"items": []})

    with _client(handler) as c:
        c.trigger_compute_run("c-1")
        c.list_compute_runs(status="succeeded")
        c.get_compute_run("run-1")
        c.cancel_compute_run("run-1")
        c.get_compute_schedule(contract_id="c-1")
        c.update_contract_flow_config(
            "c-1",
            delivery_methods=["dataset", "result_push"],
            auto_compute=True,
            compute_frequency="daily",
            compute_hour=2,
            compute_minute=30,
            compute_tz="Asia/Shanghai",
            sandbox_resource_spec={"vcpu": 2, "memory_mb": 2048},
        )

    assert json.loads(seen[0][3]) == {"contract_id": "c-1"}
    assert "status=succeeded" in seen[1][2]
    assert seen[2][1] == "/api/v1/compute/runs/run-1"
    assert seen[3][1] == "/api/v1/compute/runs/run-1/cancel"
    assert "contract_id=c-1" in seen[4][2]
    assert seen[5][1] == "/api/v1/contracts/c-1/flow-config"
    cfg = json.loads(seen[5][3])
    assert cfg["delivery_methods"] == ["dataset", "result_push"]
    assert cfg["compute_hour"] == 2 and cfg["compute_minute"] == 30
    assert "compute_weekday" not in cfg


def test_204_endpoints_return_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    with _client(handler) as c:
        assert c.archive_notebook("nb-1") is None
        assert c.delete_webhook("sub-1") is None
        assert c.delete_openapi_endpoint("ep-1") is None
