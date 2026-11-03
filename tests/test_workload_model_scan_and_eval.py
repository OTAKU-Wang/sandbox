"""Task 9（2026-11-03 realignment 计划）：模型工件安全扫描 + 多版本评估指标对比.

契约按代码实测派生（非计划草案照抄）：
- 指标的家是 ``WorkloadTestRun.report``（JSON），本接口约定其 ``metrics`` 子对象
  为 ``{name: number}``；无 metrics 的历史 run 以「不参与对比」如实呈现。
- 版本 lineage 走 ``Workload.parent_id``/``version``（SS-15 既有语义），
  ``GET /api/v1/workloads/metrics-compare?workload_id=`` 接受任意版本 id，
  回溯到根后返回整条 lineage 的每版本最新一次测试 run 的指标。
- ``scan_model_artifact_bytes`` 返回 ``{passed, violations, format, ...}``；
  判定 fail-closed：pickle 内任何非白名单 GLOBAL 都是违规（RCE gadget），
  无法识别的容器格式直接拒绝——宁可误拒，不可误放。
"""
import base64
import uuid

import pytest

from app.models.workload import Workload, WorkloadStatus
from app.models.workload_test_run import WorkloadTestRun
from app.services.code_scanner import scan_model_artifact_bytes


def test_scan_model_artifact_blocks_pickle_exec():
    malicious_pickle = b"cos\nsystem\n(S'id'\ntR."
    result = scan_model_artifact_bytes(malicious_pickle, filename="model.pkl")
    assert result["passed"] is False
    assert any("os.system" in r or "pickle" in r.lower() for r in result["violations"])


def test_scan_model_artifact_allows_clean_safetensors_header():
    clean_bytes = b'{"__metadata__": {"format": "pt"}, "weight": {"dtype": "F32"}}'
    result = scan_model_artifact_bytes(clean_bytes, filename="model.safetensors")
    assert result["passed"] is True
    assert result["violations"] == []


def test_scan_model_artifact_rejects_unrecognized_binary():
    # fail-closed：非 zip/pickle/safetensors 可解析形态一律拒绝
    result = scan_model_artifact_bytes(b"\x00\x01\x02garbage\xff", filename="model.bin")
    assert result["passed"] is False
    assert result["violations"]


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class TestWorkloadArtifactScanApi:
    async def _mk_workload(self, db, user_id, name="scan-wl"):
        wl = Workload(
            name=name,
            user_id=uuid.UUID(user_id),
            entrypoint="print('x')",
            language="python",
            env="test",
            status=WorkloadStatus.DRAFT.value,
        )
        db.add(wl)
        await db.flush()
        await db.refresh(wl)
        return wl

    async def test_artifact_scan_endpoint_verdict_and_audit(self, client, db_session, make_user):
        headers, user_id = await make_user(role="data_provider", prefix="scan_dp")
        wl = await self._mk_workload(db_session, user_id)

        resp = await client.post(
            f"/api/v1/workloads/{wl.id}/artifact-scan",
            json={"filename": "model.pkl", "content_b64": _b64(b"cos\nsystem\n(S'id'\ntR.")},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["passed"] is False
        assert body["violations"]

        # 审计必须真的落库（audit 行与请求同会话可见）
        from sqlalchemy import select

        from app.models.audit_log import AuditLog

        res = await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "workload.artifact_scan",
                AuditLog.resource_id == str(wl.id),
            )
        )
        assert res.scalars().first() is not None

    async def test_artifact_scan_foreign_workload_403(self, client, db_session, make_user):
        headers_a, uid_a = await make_user(role="data_provider", prefix="scan_a")
        headers_b, _ = await make_user(role="data_provider", prefix="scan_b")
        wl = await self._mk_workload(db_session, uid_a)
        resp = await client.post(
            f"/api/v1/workloads/{wl.id}/artifact-scan",
            json={"filename": "m.safetensors", "content_b64": _b64(b"{}")},
            headers=headers_b,
        )
        assert resp.status_code == 403


class TestMetricsCompareApi:
    async def _mk_lineage(self, db, user_id):
        v1 = Workload(
            name="lineage-wl", user_id=uuid.UUID(user_id), entrypoint="print(1)",
            language="python", env="test", status=WorkloadStatus.PUBLISHED.value,
            version=1, is_latest=False,
        )
        db.add(v1)
        await db.flush()
        v2 = Workload(
            name="lineage-wl", user_id=uuid.UUID(user_id), entrypoint="print(2)",
            language="python", env="test", status=WorkloadStatus.PUBLISHED.value,
            version=2, parent_id=v1.id, is_latest=True,
        )
        db.add(v2)
        await db.flush()
        for wl, metrics, passed in ((v1, {"auc": 0.71, "f1": 0.60}, True), (v2, {"auc": 0.83, "f1": 0.74}, True)):
            db.add(
                WorkloadTestRun(
                    workload_id=wl.id, status="succeeded", passed=passed,
                    report={"metrics": dict(metrics)},
                )
            )
        await db.flush()
        return v1, v2

    async def test_metrics_compare_returns_lineage_and_diff(self, client, db_session, make_user):
        headers, user_id = await make_user(role="data_provider", prefix="cmp_dp")
        v1, v2 = await self._mk_lineage(db_session, user_id)

        resp = await client.get(
            f"/api/v1/workloads/metrics-compare?workload_id={v2.id}", headers=headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["lineage_root"] == str(v1.id)
        versions = [it["version"] for it in body["items"]]
        assert versions == [1, 2]
        assert body["common_metrics"] == ["auc", "f1"]
        by_ver = {it["version"]: it for it in body["items"]}
        assert by_ver[1]["metrics"]["auc"] == 0.71
        assert by_ver[2]["metrics"]["auc"] == 0.83
        # 相邻版本 delta 显式给出，前端不再自己做减法
        assert by_ver[2]["delta_vs_previous"]["auc"] == pytest.approx(0.12)

    async def test_metrics_compare_foreign_lineage_403(self, client, db_session, make_user):
        headers_owner, uid_owner = await make_user(role="data_provider", prefix="cmp_own")
        headers_out, _ = await make_user(role="data_provider", prefix="cmp_out")
        _, v2 = await self._mk_lineage(db_session, uid_owner)
        resp = await client.get(
            f"/api/v1/workloads/metrics-compare?workload_id={v2.id}", headers=headers_out
        )
        assert resp.status_code == 403
