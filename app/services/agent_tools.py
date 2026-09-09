"""N10: sandbox-internal agent tool registry (platform-fixed tools only).

Tools are platform-registered implementations — a user may select tools for an
agent run but never supply tool code (that would widen the code_scanner import
whitelist). Network-capable tools carry ``network_targets`` and are gated at
runner build time by the session's network policy allowlist.
"""
import ipaddress
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentTool:
    id: str
    description: str
    params_schema: dict
    network_targets: list[str] = field(default_factory=list)
    budget_cost: int = 1
    roles: tuple[str, ...] = ()

    def validate_args(self, args: dict | None) -> str | None:
        """Lightweight JSON-Schema-ish check. Returns an error string or None."""
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return f"{self.id}: args must be an object"
        for required in self.params_schema.get("required", []):
            if required not in args:
                return f"{self.id}: missing required parameter {required!r}"
        properties = self.params_schema.get("properties", {})
        for key, value in args.items():
            if key not in properties:
                return f"{self.id}: unknown parameter {key!r}"
            expected = properties[key].get("type")
            if expected == "string" and not isinstance(value, str):
                return f"{self.id}: parameter {key!r} must be a string"
            if expected == "integer" and not isinstance(value, int):
                return f"{self.id}: parameter {key!r} must be an integer"
            if expected == "number" and not isinstance(value, (int, float)):
                return f"{self.id}: parameter {key!r} must be a number"
            if expected == "boolean" and not isinstance(value, bool):
                return f"{self.id}: parameter {key!r} must be a boolean"
            if expected == "array" and not isinstance(value, list):
                return f"{self.id}: parameter {key!r} must be an array"
        return None


def _host_allowed(host: str, allowed_domains: list[str]) -> bool:
    host = host.rstrip(".").lower()
    for pattern in allowed_domains:
        pattern = pattern.strip().lower()
        if not pattern:
            continue
        if pattern.startswith("*."):
            suffix = pattern[1:]  # ".example.com"
            if host.endswith(suffix) and host != suffix[1:]:
                return True
        elif host == pattern:
            return True
    return False


def _ip_allowed(host: str, allowed_ips: list[str]) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    for cidr in allowed_ips:
        try:
            if ip in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def validate_network_target(url: str, allowed_domains: list[str], allowed_ips: list[str]) -> bool:
    """Return True when the URL host is allowed by the session network policy.

    Domain allowlist supports exact + ``*.domain`` wildcard (mirrors the k8s
    network policy matcher); IP allowlist supports CIDR ranges. The k8s
    NetworkPolicy deny_all remains the second gate at the network layer.
    """
    if not url:
        return False
    from urllib.parse import urlparse

    try:
        host = urlparse(url).hostname
    except ValueError:
        return False
    if not host:
        return False
    return _host_allowed(host, allowed_domains) or _ip_allowed(host, allowed_ips)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        self._tools[tool.id] = tool

    def get(self, tool_id: str) -> AgentTool | None:
        return self._tools.get(tool_id)

    def list(self) -> list[AgentTool]:
        return sorted(self._tools.values(), key=lambda t: t.id)

    def validate_run(self, tool_ids: list[str]) -> str | None:
        """Validate a tool selection for an agent run. Returns error or None."""
        if not tool_ids:
            return "at least one tool is required"
        seen: set[str] = set()
        for tool_id in tool_ids:
            if tool_id in seen:
                return f"duplicate tool {tool_id!r}"
            seen.add(tool_id)
            if self.get(tool_id) is None:
                return f"unknown tool {tool_id!r}"
        return None


def _schema(*, required: list[str], properties: dict) -> dict:
    return {"type": "object", "required": required, "properties": properties}


BUILTIN_TOOLS = {
    "compute": AgentTool(
        id="compute",
        description="在沙箱内用 pandas/numpy 对给定数据进行聚合/统计计算（不离开数据域）",
        params_schema=_schema(
            required=["expression"],
            properties={
                "expression": {"type": "string", "description": "对 data 求值的 python 表达式，引用 data"},
                "data": {"description": "输入数据（list/numbers/rows）"},
            },
        ),
        budget_cost=1,
        roles=("admin", "operator", "data_provider", "buyer"),
    ),
    "rag_retrieve": AgentTool(
        id="rag_retrieve",
        description="在会话语料内检索与 query 相关的片段（RAG，域内检索）",
        params_schema=_schema(
            required=["query"],
            properties={
                "query": {"type": "string", "description": "检索问句"},
                "top_k": {"type": "integer", "description": "返回条数（默认 3）"},
            },
        ),
        budget_cost=1,
        roles=("admin", "operator", "data_provider", "buyer"),
    ),
    "inference": AgentTool(
        id="inference",
        description="对已注册的模型做推理（N5 runner 契约：inputs 为命名张量字典，模型物化到沙箱）",
        params_schema=_schema(
            required=["inputs"],
            properties={
                "inputs": {"description": "命名张量字典 {input_name: 数组}"},
                "input_text": {"type": "string", "description": "可选说明文本（不参与推理输入）"},
            },
        ),
        budget_cost=1,
        roles=("admin", "operator", "data_provider"),
    ),
    "db_query": AgentTool(
        id="db_query",
        description="对沙箱内数据库执行只读 SELECT（SQL 白名单，非 DML）",
        params_schema=_schema(
            required=["query"],
            properties={
                "query": {"type": "string", "description": "只读 SELECT 语句"},
            },
        ),
        budget_cost=1,
        roles=("admin", "operator", "data_provider", "buyer"),
    ),
    "http": AgentTool(
        id="http",
        description="拉取 allowlist 域名/IP 下的外部数据（URL 必须命中会话网络策略白名单）",
        params_schema=_schema(
            required=["url"],
            properties={
                "url": {"type": "string", "description": "http(s) URL，host 必须在会话 allowlist 内"},
                "method": {"type": "string", "description": "GET/POST（默认 GET）"},
                "headers": {"description": "附加请求头"},
                "timeout": {"type": "integer", "description": "超时秒数（默认 10）"},
            },
        ),
        network_targets=[],
        budget_cost=1,
        roles=("admin", "operator", "data_provider"),
    ),
    "llm_reason": AgentTool(
        id="llm_reason",
        description="推理步：挂 N6 generative ONNX 钩子（供方模型）或确定性规划器退化的总结",
        params_schema=_schema(
            required=["context"],
            properties={
                "context": {"type": "string", "description": "推理上下文（工具结果等）"},
            },
        ),
        budget_cost=2,
        roles=("admin", "operator", "data_provider"),
    ),
}

tool_registry = ToolRegistry()
for _tool in BUILTIN_TOOLS.values():
    tool_registry.register(_tool)
