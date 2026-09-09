"""N10 T2/T3: agent runner generation (step loop + budgets + trace) and
byte-exact validation. Runners execute as real subprocesses in a temp workspace.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from app.services.agent_service import (
    build_agent_runner,
    extract_agent_metrics,
    validate_agent_runner,
)


def _run(runner, workspace):
    return subprocess.run(
        [sys.executable, "-c", runner], cwd=workspace, capture_output=True, text=True,
        env={**os.environ},
    )


def _parse_traces(proc):
    traces = []
    result = None
    for line in proc.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("type") == "agent_trace":
            traces.append(obj)
        elif obj.get("type") == "agent_result":
            result = obj
    return traces, result


def test_runner_multistep_compute_and_reason():
    runner = build_agent_runner("统计销售额", tool_ids=["compute", "llm_reason"], step_budget=10)
    with tempfile.TemporaryDirectory() as td:
        proc = _run(runner, Path(td))
    assert proc.returncode == 0, proc.stderr
    traces, result = _parse_traces(proc)
    assert [t["tool"] for t in traces] == ["compute", "llm_reason"]
    assert all(t["status"] == "ok" for t in traces)
    assert result["steps_used"] == 2
    assert result["tools_used"] == ["compute", "llm_reason"]
    assert "确定性规划器" in result["final"]


def test_runner_step_budget_hard_limit():
    runner = build_agent_runner("只算一步", tool_ids=["compute"], step_budget=1, token_budget=100000)
    with tempfile.TemporaryDirectory() as td:
        proc = _run(runner, Path(td))
    assert proc.returncode == 0
    traces, result = _parse_traces(proc)
    assert len(traces) == 1
    assert result["steps_used"] == 1


def test_runner_token_budget_fails_closed():
    runner = build_agent_runner("超预算", tool_ids=["compute"], step_budget=5, token_budget=1)
    with tempfile.TemporaryDirectory() as td:
        proc = _run(runner, Path(td))
    assert proc.returncode == 1  # fail-closed
    assert "token budget exceeded" in proc.stdout
    lines = [json.loads(l) for l in proc.stdout.strip().splitlines() if l.strip().startswith("{")]
    assert any(o.get("type") == "agent_error" for o in lines)


def test_runner_zero_step_budget_rejected_at_build():
    with pytest.raises(ValueError, match="step_budget"):
        build_agent_runner("无预算", tool_ids=["compute"], step_budget=0, token_budget=100)


def test_runner_rag_retrieve_uses_shipped_index():
    from app.services.rag_service import build_corpus

    docs = [
        {"doc_id": "d1", "text": "三室两厅的户型适合一家四口居住，南北通透采光好。"},
        {"doc_id": "d2", "text": "小户型一居室适合单身青年，交通便利，总价低。"},
    ]
    index, _ = build_corpus(docs, chunk_size=64, overlap=8)
    runner = build_agent_runner("适合一家四口的房子", tool_ids=["rag_retrieve", "llm_reason"], step_budget=6)
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "input").mkdir(parents=True)
        (ws / "input" / "rag_index.json").write_bytes(index.to_json())
        proc = _run(runner, ws)
    assert proc.returncode == 0, proc.stderr
    traces, result = _parse_traces(proc)
    assert [t["tool"] for t in traces] == ["rag_retrieve", "llm_reason"]
    assert traces[0]["status"] == "ok"
    assert result["steps_used"] == 2


def test_runner_http_allowlist_gates_planner():
    # A wildcard-only allowlist gives the planner no concrete target -> no http
    # step is scheduled; the run terminates honestly without any fetch.
    runner = build_agent_runner("拉数据", tool_ids=["http", "llm_reason"], step_budget=6,
                                http_allow={"domains": ["*.example.com"], "ips": []})
    with tempfile.TemporaryDirectory() as td:
        proc = _run(runner, Path(td))
    assert proc.returncode == 0
    traces, result = _parse_traces(proc)
    assert [t["tool"] for t in traces] == ["llm_reason"]
    assert "http" not in result["tools_used"]


def test_extract_agent_metrics():
    out = "\n".join([
        json.dumps({"type": "agent_trace", "step": 1, "tool": "compute", "args_hash": "ab12", "status": "ok", "tokens": 30, "ms": 4}, ensure_ascii=False),
        json.dumps({"type": "agent_result", "steps_used": 1, "tokens_used": 30, "tools_used": ["compute"], "final": "x"}, ensure_ascii=False),
    ])
    m = extract_agent_metrics(out)
    assert m["steps_used"] == 1
    assert m["tokens_used"] == 30
    assert m["tools_used"] == ["compute"]
    assert len(m["traces"]) == 1
    assert extract_agent_metrics("not json")["steps_used"] == 0


# ── T3: validation ────────────────────────────────────────────────

def test_validate_agent_runner_accepts_generated():
    runner = build_agent_runner("统计", tool_ids=["compute", "llm_reason"], step_budget=8)
    ok, reason = validate_agent_runner(runner, ["compute", "llm_reason"])
    assert ok is True, reason


def test_validate_agent_runner_rejects_tamper():
    runner = build_agent_runner("统计", tool_ids=["compute"])
    tampered = runner.replace("import sys\n", "import sys\nimport socket\n", 1)
    ok, reason = validate_agent_runner(tampered, ["compute"])
    assert ok is False
    assert "template" in reason or "differs" in reason


def test_validate_agent_runner_rejects_tool_mismatch():
    runner = build_agent_runner("统计", tool_ids=["compute", "llm_reason"])
    ok, _reason = validate_agent_runner(runner, ["compute"])  # task said fewer tools
    assert ok is False


def test_validate_agent_runner_rejects_prompt_injection():
    bad = build_agent_runner("q", tool_ids=["compute"]).replace(
        "_AGENT_PROMPT = 'q'", "_AGENT_PROMPT = __import__('os').system('x')"
    )
    ok, reason = validate_agent_runner(bad, ["compute"])
    assert ok is False
    assert "literal" in reason
