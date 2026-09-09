"""N10 T4: pipeline integration — AGENT runner code-scanning + metering."""
import pytest

from app.services.agent_service import build_agent_runner


def _runner_for(tools, prompt="统计销售额"):
    return build_agent_runner(prompt, tool_ids=tools, step_budget=8)


@pytest.mark.asyncio
async def test_agent_code_scanning_accepts_generated_runner():
    from app.services.task_pipeline import PipelineTask, task_pipeline
    from app.services.task_state_machine import TaskStatus

    handler = task_pipeline._handlers[TaskStatus.CODE_SCANNING]
    runner = _runner_for(["compute", "llm_reason"])
    task = PipelineTask(
        task_id="agent-scan-ok",
        session_id="sess-agent",
        task_type="agent",
        payload={"code": runner, "language": "python", "sandbox_mode": "structured_query",
                 "agent": True, "agent_tool_ids": ["compute", "llm_reason"]},
    )
    status, data = await handler(task)
    assert status == TaskStatus.CODE_SCANNING
    assert data["scan"] == "agent_runner_verified"


@pytest.mark.asyncio
async def test_agent_code_scanning_rejects_tampered_runner():
    from app.services.task_pipeline import PipelineTask, task_pipeline
    from app.services.task_state_machine import TaskStatus

    handler = task_pipeline._handlers[TaskStatus.CODE_SCANNING]
    runner = _runner_for(["compute"])
    tampered = runner.replace("import sys\n", "import sys\nimport socket\n", 1)
    task = PipelineTask(
        task_id="agent-scan-bad",
        session_id="sess-agent",
        task_type="agent",
        payload={"code": tampered, "language": "python", "sandbox_mode": "structured_query",
                 "agent": True, "agent_tool_ids": ["compute"]},
    )
    status, data = await handler(task)
    assert status == TaskStatus.REJECTED
    assert data.get("agent_runner") is True


@pytest.mark.asyncio
async def test_agent_code_scanning_rejects_forged_tool():
    from app.services.task_pipeline import PipelineTask, task_pipeline
    from app.services.task_state_machine import TaskStatus

    handler = task_pipeline._handlers[TaskStatus.CODE_SCANNING]
    runner = _runner_for(["compute"])
    forged = runner.replace("_AGENT_TOOLS = ['compute']", "_AGENT_TOOLS = ['compute', 'not_a_tool']")
    task = PipelineTask(
        task_id="agent-scan-forge",
        session_id="sess-agent",
        task_type="agent",
        payload={"code": forged, "language": "python", "sandbox_mode": "structured_query",
                 "agent": True, "agent_tool_ids": ["compute", "not_a_tool"]},
    )
    status, data = await handler(task)
    assert status == TaskStatus.REJECTED


@pytest.mark.asyncio
async def test_agent_task_type_registered():
    from app.models.sandbox_task import TaskType
    assert TaskType.AGENT.value == "agent"
