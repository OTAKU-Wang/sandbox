"""OCR A163（SS-15 准入门禁）— `run_test` 必须先查 env/status 再动手。

一次 `POST /workloads/{id}/test` 不得把已发布的生产 head 打回 `testing`，也不得把它与
已通过测试运行（`test_run_id`）的绑定解掉——后续 `publish` / `get_production_head` /
依赖该引用的合约校验都读这两个字段；已 `retired` 的工件同样不能被拉回发布链。

只替掉 `create_sandbox_session` / `execute_in_sandbox` 两个入口（与
tests/test_ss15_flow_unit.py 的 D-e2e-45 手法同构），不触真实执行链。
"""
import uuid

import pytest
from sqlalchemy import func, select

from app.models.user import User
from app.models.workload import Workload, WorkloadStatus
from app.models.workload_test_run import WorkloadTestRun
from app.models.contract import WorkloadEnv
from app.services import workflow_service as wf


async def _mk_user(db, prefix: str = "a163") -> User:
    from app.services.auth_service import hash_password

    username = f"{prefix}_{uuid.uuid4().hex[:8]}"
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("testpass123"),
        role="data_provider",
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def _mk_workload(db, user, *, env: str, status: str, bind_passed_run: bool = True) -> Workload:
    """造一个工件；bind_passed_run=True 时同时补一条**已通过**的测试运行并绑定 test_run_id。"""
    entrypoint = "print('hello')"
    workload = Workload(
        name=f"a163-{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        contract_id=None,
        entrypoint=entrypoint,
        language="python",
        entrypoint_hash=wf.hash_entrypoint(entrypoint),
        data_product_ids=[str(uuid.uuid4())],
        resource_spec={},
        env=env,
        status=status,
        is_latest=True,
    )
    db.add(workload)
    await db.flush()
    if bind_passed_run:
        run = WorkloadTestRun(
            workload_id=workload.id,
            triggered_by=user.id,
            status="passed",
            passed=True,
            checks=[{"name": "entrypoint_executed", "passed": True}],
            report={"entrypoint_hash": workload.entrypoint_hash},
        )
        db.add(run)
        await db.flush()
        workload.test_run_id = run.id
        await db.flush()
    return workload


async def _run_count(db, workload_id) -> int:
    res = await db.execute(select(func.count()).select_from(WorkloadTestRun).where(WorkloadTestRun.workload_id == workload_id))
    return int(res.scalar_one())


def _install_fake_sandbox(monkeypatch, captured: dict) -> None:
    """替掉测试沙箱的两个入口：只记录调用，不建真会话、不跑真代码。"""
    from types import SimpleNamespace

    import app.api.sandbox_sessions as ss

    async def fake_create(*, body, db, current_user):
        captured["level"] = body.sandbox_level
        captured["create_calls"] = captured.get("create_calls", 0) + 1
        return SimpleNamespace(id=uuid.uuid4(), sandbox_level=body.sandbox_level)

    async def fake_exec(**_kw):
        captured["exec_calls"] = captured.get("exec_calls", 0) + 1
        return {"exit_code": 0, "output": "ok", "stderr": "", "output_blocked": False}

    monkeypatch.setattr(ss, "create_sandbox_session", fake_create)
    monkeypatch.setattr(ss, "execute_in_sandbox", fake_exec)


class TestRunTestAdmissionGate:
    async def test_published_production_head_is_not_demoted(self, db_session):
        """生产 head：调一次 test 既不能改 status，也不能解绑 test_run_id（A163 主症状）。"""
        user = await _mk_user(db_session)
        workload = await _mk_workload(
            db_session, user, env=WorkloadEnv.PRODUCTION.value, status=WorkloadStatus.PUBLISHED.value
        )
        bound_run = workload.test_run_id
        runs_before = await _run_count(db_session, workload.id)

        with pytest.raises(wf.WorkloadGateError) as exc:
            await wf.run_test(db_session, workload, user)

        assert exc.value.code == wf.GATE_NOT_TEST_ENV
        assert exc.value.http_status == 412
        assert workload.status == WorkloadStatus.PUBLISHED.value
        assert workload.test_run_id == bound_run
        assert await _run_count(db_session, workload.id) == runs_before

    async def test_production_draft_is_rejected_too(self, db_session):
        """env 才是判据主体：生产态 draft 也不允许在测试沙箱入口上跑。"""
        user = await _mk_user(db_session)
        workload = await _mk_workload(
            db_session, user, env=WorkloadEnv.PRODUCTION.value, status=WorkloadStatus.DRAFT.value, bind_passed_run=False
        )

        with pytest.raises(wf.WorkloadGateError) as exc:
            await wf.run_test(db_session, workload, user)

        assert exc.value.code == wf.GATE_NOT_TEST_ENV
        assert workload.status == WorkloadStatus.DRAFT.value

    async def test_retired_workload_cannot_be_pulled_back(self, db_session):
        """RETIRED 在 ALLOWED_TRANSITIONS 里是终态：测试入口也不能把它拉回发布链。"""
        user = await _mk_user(db_session)
        workload = await _mk_workload(
            db_session, user, env=WorkloadEnv.TEST.value, status=WorkloadStatus.RETIRED.value
        )
        bound_run = workload.test_run_id

        with pytest.raises(wf.WorkloadGateError) as exc:
            await wf.run_test(db_session, workload, user)

        assert exc.value.code == wf.GATE_STATE_CONFLICT
        assert exc.value.http_status == 412
        assert workload.status == WorkloadStatus.RETIRED.value
        assert workload.test_run_id == bound_run

    async def test_inflight_testing_is_not_double_run(self, db_session):
        """已在跑的工件不允许再叠一次测试运行（状态机 TESTING→TESTING 不存在）。"""
        user = await _mk_user(db_session)
        workload = await _mk_workload(
            db_session, user, env=WorkloadEnv.TEST.value, status=WorkloadStatus.TESTING.value
        )

        with pytest.raises(wf.WorkloadGateError) as exc:
            await wf.run_test(db_session, workload, user)

        assert exc.value.code == wf.GATE_STATE_CONFLICT
        assert exc.value.detail.get("from") == WorkloadStatus.TESTING.value
        assert exc.value.detail.get("to") == WorkloadStatus.TESTING.value

    async def test_gate_error_is_a_w2_error_so_the_api_returns_412(self, db_session):
        """`POST /workloads/{id}/test` 端点没有 gate 异常的 try/except：门禁异常必须本身就是
        W2 CDSError，否则会退化成 500（app/api/workloads.py 不在本轨可改文件内）。"""
        user = await _mk_user(db_session)
        workload = await _mk_workload(
            db_session, user, env=WorkloadEnv.PRODUCTION.value, status=WorkloadStatus.PUBLISHED.value
        )

        with pytest.raises(wf.WorkloadGateError) as exc:
            await wf.run_test(db_session, workload, user)

        from app.core.errors import CDSError

        assert isinstance(exc.value, CDSError)
        assert exc.value.http_status == 412

    @pytest.mark.parametrize(
        "status",
        [WorkloadStatus.DRAFT.value, WorkloadStatus.TEST_FAILED.value, WorkloadStatus.TEST_PASSED.value],
    )
    async def test_legitimate_test_env_still_runs(self, db_session, monkeypatch, status):
        """反向守卫（不许过度收紧）：测试态的 draft/test_failed/test_passed 仍必须能跑测试。"""
        captured: dict = {}
        _install_fake_sandbox(monkeypatch, captured)
        user = await _mk_user(db_session)
        workload = await _mk_workload(db_session, user, env=WorkloadEnv.TEST.value, status=status, bind_passed_run=False)

        run = await wf.run_test(db_session, workload, user)

        assert captured.get("create_calls") == 1
        assert run.passed is True
        assert run.status == "passed"
        assert workload.status == WorkloadStatus.TEST_PASSED.value
        assert workload.test_run_id == run.id
