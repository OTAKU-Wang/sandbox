"""N10 T1: agent tool registry + network target validation."""
import pytest

from app.services.agent_tools import (
    AgentTool,
    BUILTIN_TOOLS,
    ToolRegistry,
    tool_registry,
    validate_network_target,
)


def test_registry_has_all_builtin_tools():
    ids = {t.id for t in tool_registry.list()}
    assert ids == {"compute", "rag_retrieve", "inference", "db_query", "http", "llm_reason"}


def test_validate_run_rejects_unknown_and_empty():
    assert tool_registry.validate_run([]) is not None
    assert tool_registry.validate_run(["compute", "nope"]) is not None
    assert tool_registry.validate_run(["compute", "compute"]) is not None
    assert tool_registry.validate_run(["compute", "rag_retrieve"]) is None


def test_tool_validate_args():
    compute = BUILTIN_TOOLS["compute"]
    assert compute.validate_args({}) is not None  # expression required
    assert compute.validate_args({"expression": "data.mean()"}) is None
    assert compute.validate_args({"expression": 42}) is not None  # not a string
    assert compute.validate_args({"expression": "x", "bogus": 1}) is not None


def test_validate_network_target_domain_and_wildcard():
    assert validate_network_target("https://api.example.com/x", ["api.example.com"], []) is True
    assert validate_network_target("https://a.b.example.com/x", ["*.example.com"], []) is True
    assert validate_network_target("https://evil.example.org/x", ["*.example.com"], []) is False
    assert validate_network_target("https://example.com/x", ["*.example.com"], []) is False  # bare apex not matched by *.


def test_validate_network_target_ip_cidr():
    assert validate_network_target("http://10.20.30.40/x", [], ["10.20.0.0/16"]) is True
    assert validate_network_target("http://10.99.0.1/x", [], ["10.20.0.0/16"]) is False
    assert validate_network_target("http://not-a-host/x", [], ["10.20.0.0/16"]) is False


def test_validate_network_target_empty_denies():
    assert validate_network_target("https://anything.example.com/x", [], []) is False


def test_custom_registry_register_and_get():
    reg = ToolRegistry()
    tool = AgentTool(id="t1", description="x", params_schema={"type": "object", "required": [], "properties": {}})
    reg.register(tool)
    assert reg.get("t1") is tool
    assert reg.get("missing") is None
