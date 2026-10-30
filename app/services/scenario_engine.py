"""Scenario Template Engine — parameterized business scenario execution.

Allows data providers to define reusable scenario templates with parameterized
SQL. Buyers fill in parameters and execute in the sandbox. Supports:
- Multi-table JOIN with constraints
- Parameter validation and sanitization
- Output schema enforcement
- Security constraint checking

A144（潜伏缺陷波修复，模块仍未接线——接不接由人拍板）：
- 参数值不再被「静默剥离」改写：含 SQL 结构 token 的取值一律显式拒绝（fail-closed），
  合法值只按 SQL 字面量规则转义单引号，其余逐字保留；
- 行数封顶的 LIMIT 感知移到模板文本（作者面、词边界），用户值不再能抑制 cap；
- forbidden_operations 的渲染期可执行项真正执行（subquery_leak/network_call 拒绝），
  渲染期不可判定项（row_level_export）在结果 constraint_notes 里如实披露，
  未知约束名报错（拼错不再静默失效）；
- allowed_operations 首次被真正读取（JOIN ⇒ multi_table_join，聚合 ⇒ aggregate）；
- `FROM a, b` 逗号隐式笛卡尔拒绝；表数按 FROM 顶层源 + JOIN 计；
- JOIN 的 ON 判定改为词边界（`JOIN button b` 不再假过），接受 USING 等值连接；
- str.format 的 KeyError/IndexError/ValueError 全部收口成错误结果；
- 同 id 模板重复注册显式拒绝。
"""
import re
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# A144/D1: 参数值里一旦出现这些 SQL 结构 token，就是注入面（黑名单从「剥离」改「拒绝」后，
# 覆盖面也从 DDL/DML 扩到查询结构词）。值被拒有明确 reason，绝不静默改写。
_SQL_STRUCT_TOKENS = (
    "DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE", "TRUNCATE",
    "EXEC", "EXECUTE", "UNION", "SELECT", "WHERE", "JOIN", "INTO",
    "GRANT", "REVOKE",
)
_SQL_TOKEN_RE = re.compile(
    r"\b(" + "|".join(_SQL_STRUCT_TOKENS) + r")\b", re.IGNORECASE
)

# A144/D4: 引擎在渲染期对每类约束的能力边界——名字表不出来就是配置错误，
# 而不是「声明了但没人执行」。
_FORBIDDEN_ENFORCEABLE = ("subquery_leak", "network_call")
_FORBIDDEN_RUNTIME_ONLY = ("row_level_export",)
_ALLOWED_KNOWN = ("filter", "aggregate", "multi_table_join")

_AGGREGATE_RE = re.compile(r"\b(?:COUNT|SUM|AVG|MIN|MAX)\s*\(|\bGROUP\s+BY\b", re.IGNORECASE)
_SUBQUERY_RE = re.compile(r"\(\s*SELECT\b", re.IGNORECASE | re.DOTALL)  # A144/D8
_NETWORK_RE = re.compile(r"\b(?:pg_sleep|dblink|lo_import|lo_export|http_)\b|COPY\s+.*\bPROGRAM\b", re.IGNORECASE)
_FROM_CLAUSE_RE = re.compile(
    r"\bFROM\b(.*?)(?=\bJOIN\b|\bWHERE\b|\bGROUP\b|\bORDER\b|\bHAVING\b|\bLIMIT\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_TEMPLATE_LIMIT_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)  # A144/D3：只看模板文本


@dataclass
class ScenarioParameter:
    """A parameter in a scenario template."""
    name: str
    type: str  # "string", "number", "integer", "boolean"
    description: str
    required: bool = True
    default: Any = None
    example: Any = None
    min_value: float | None = None
    max_value: float | None = None
    allowed_values: list[str] | None = None


@dataclass
class OutputColumn:
    """A column in the output schema."""
    name: str
    type: str
    description: str


@dataclass
class ScenarioTemplate:
    """A reusable scenario template definition."""
    id: str
    name: str
    description: str
    category: str
    parameters: list[ScenarioParameter]
    sql_template: str
    output_schema: list[OutputColumn]
    max_tables: int = 4
    max_output_rows: int = 500
    allowed_operations: list[str] = field(default_factory=lambda: [
        "filter", "aggregate", "multi_table_join",
    ])
    forbidden_operations: list[str] = field(default_factory=lambda: [
        "row_level_export", "network_call", "subquery_leak",
    ])


@dataclass
class ScenarioExecution:
    """Result of executing a scenario."""
    success: bool
    sql: str | None = None
    params: dict | None = None
    error: str | None = None
    row_count: int = 0
    elapsed_ms: int = 0
    # A144/D4（additive）：渲染期无法执行/未验证的约束在此如实披露，不静默。
    constraint_notes: list[str] = field(default_factory=list)


class ScenarioEngine:
    """Manages scenario templates and executes parameterized scenarios.

    Responsibilities:
    - Register and retrieve scenario templates
    - Validate parameters against template definitions
    - Render SQL templates with sanitized parameters
    - Enforce JOIN constraints (max tables, FK-only, no cartesian product)
    - Enforce output constraints (max rows, format)
    """

    def __init__(self):
        self._templates: dict[str, ScenarioTemplate] = {}

    def register_template(self, template: ScenarioTemplate):
        """Register a scenario template. A144/D10: same-id overwrite is refused."""
        if template.id in self._templates:
            raise ValueError(
                f"Scenario template already registered: {template.id} "
                "— overwrite is refused (A144: silent replacement used to drop the "
                "previous template's constraints without a trace)"
            )
        self._templates[template.id] = template
        logger.info(f"Registered scenario template: {template.id} ({template.name})")

    def get_template(self, template_id: str) -> ScenarioTemplate | None:
        """Retrieve a scenario template by ID."""
        return self._templates.get(template_id)

    def list_templates(self, category: str | None = None) -> list[ScenarioTemplate]:
        """List all templates, optionally filtered by category."""
        templates = list(self._templates.values())
        if category:
            templates = [t for t in templates if t.category == category]
        return templates

    def validate_parameters(
        self,
        template_id: str,
        params: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        """Validate parameters against a template's definition.

        Returns: (is_valid, list of error messages)
        """
        template = self._templates.get(template_id)
        if not template:
            return False, [f"Template not found: {template_id}"]

        errors = []

        for param_def in template.parameters:
            value = params.get(param_def.name)

            # Apply default for missing values
            if value is None and param_def.default is not None:
                params[param_def.name] = param_def.default
                value = param_def.default

            # Check required
            if param_def.required and value is None:
                errors.append(f"Missing required parameter: {param_def.name}")
                continue

            if value is None:
                continue

            # Type check
            if param_def.type == "number":
                try:
                    value = float(value)
                    params[param_def.name] = value
                except (TypeError, ValueError):
                    errors.append(f"Parameter {param_def.name} must be a number")
                    continue
            elif param_def.type == "integer":
                try:
                    value = int(value)
                    params[param_def.name] = value
                except (TypeError, ValueError):
                    errors.append(f"Parameter {param_def.name} must be an integer")
                    continue
            elif param_def.type == "boolean":
                if not isinstance(value, bool):
                    errors.append(f"Parameter {param_def.name} must be a boolean")
                    continue

            # Range check
            if param_def.type in ("number", "integer"):
                if param_def.min_value is not None and value < param_def.min_value:
                    errors.append(
                        f"Parameter {param_def.name} must be >= {param_def.min_value}"
                    )
                if param_def.max_value is not None and value > param_def.max_value:
                    errors.append(
                        f"Parameter {param_def.name} must be <= {param_def.max_value}"
                    )

            # Allowed values check — A144/D11: a number coerced to float used to
            # compare "100.0" against allowed ["100"] and falsely reject it.
            if param_def.allowed_values:
                candidates = {str(value)}
                if isinstance(value, float) and value.is_integer():
                    candidates.add(str(int(value)))
                if not candidates & set(param_def.allowed_values):
                    errors.append(
                        f"Parameter {param_def.name} must be one of: {param_def.allowed_values}"
                    )

        return len(errors) == 0, errors

    def render_sql(
        self,
        template_id: str,
        params: dict[str, Any],
    ) -> ScenarioExecution:
        """Render a scenario template with validated parameters.

        Steps:
        1. Validate parameters
        2. Reject (not strip!) values carrying SQL structure tokens
        3. Render SQL template (all format failures → error result, A144/D9)
        4. Check JOIN / operation constraints
        5. Add LIMIT from the template side if missing (A144/D3)
        """
        template = self._templates.get(template_id)
        if not template:
            return ScenarioExecution(success=False, error=f"Template not found: {template_id}")

        # A144/D4: constraint names the engine cannot execute are a config error.
        unknown_forbidden = [
            op for op in template.forbidden_operations
            if op not in _FORBIDDEN_ENFORCEABLE + _FORBIDDEN_RUNTIME_ONLY
        ]
        if unknown_forbidden:
            return ScenarioExecution(
                success=False,
                error=f"Unknown forbidden_operations (typo would silently disable the "
                      f"constraint, A144): {unknown_forbidden}",
            )
        unknown_allowed = [op for op in template.allowed_operations if op not in _ALLOWED_KNOWN]
        if unknown_allowed:
            return ScenarioExecution(
                success=False,
                error=f"Unknown allowed_operations (never enforceable, A144): {unknown_allowed}",
            )

        # Validate parameters
        is_valid, errors = self.validate_parameters(template_id, params)
        if not is_valid:
            return ScenarioExecution(success=False, error=f"Parameter errors: {'; '.join(errors)}")

        # A144/D1+D2: fail-closed value screening — refuse, never rewrite.
        value_errors = [
            err for key, value in params.items()
            if (err := self._reject_unsafe_value(key, value))
        ]
        if value_errors:
            return ScenarioExecution(
                success=False, error="Parameter rejected (not stripped): " + "; ".join(value_errors)
            )

        sanitized = self._sanitize_params(params)

        # Render SQL — A144/D9: every str.format failure becomes an error result.
        try:
            sql = template.sql_template.format(**sanitized)
        except (KeyError, IndexError, ValueError) as e:
            return ScenarioExecution(
                success=False, error=f"Template rendering failed: {e}"
            )

        # Check JOIN / allowed-operation constraints
        join_ok, join_error, notes = self._check_constraints(sql, template)
        if not join_ok:
            return ScenarioExecution(success=False, error=join_error)

        # A144/D3: LIMIT ceiling is decided by the TEMPLATE text (author-controlled)
        # with word boundaries — a value that merely contains "limit" can no longer
        # suppress max_output_rows.
        if not _TEMPLATE_LIMIT_RE.search(template.sql_template):
            sql = f"{sql} LIMIT {template.max_output_rows}"

        return ScenarioExecution(
            success=True,
            sql=sql,
            params=sanitized,
            constraint_notes=notes,
        )

    @staticmethod
    def _reject_unsafe_value(key: str, value: Any) -> str | None:
        """A144/D1+D2: reject values carrying SQL structure instead of mutating them.

        旧实现先剥关键字、再剥注释、再去分号 —— 合法值被静默改写（"3; items" 变
        "3 items"），而黑名单没覆盖的 UNION/SELECT 结构照样放行。两者都不可接受：
        现在含结构 token / 注释符 / 语句终止符的取值一律带 reason 拒绝。
        """
        if not isinstance(value, str):
            return None
        hits = sorted({m.group(0).upper() for m in _SQL_TOKEN_RE.finditer(value)})
        problems = []
        if hits:
            problems.append(f"SQL tokens {hits}")
        if ";" in value:
            problems.append("statement terminator ';'")
        for marker in ("--", "/*", "*/"):
            if marker in value:
                problems.append(f"comment marker {marker!r}")
        if problems:
            return (
                f"parameter '{key}' carries SQL structure ({', '.join(problems)}); "
                "value refused unchanged (A144 fail-closed)"
            )
        return None

    def _sanitize_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Prepare parameter values for rendering.

        A144: screening happened in `_reject_unsafe_value` (reject, not strip); the
        only mutation left is SQL-literal escaping of single quotes, which preserves
        the value's content (O'Brien → O''Brien).
        """
        sanitized = {}
        for key, value in params.items():
            if isinstance(value, str):
                sanitized[key] = value.replace("'", "''")
            else:
                sanitized[key] = value
        return sanitized

    def _check_constraints(
        self,
        sql: str,
        template: ScenarioTemplate,
    ) -> tuple[bool, str, list[str]]:
        """Check JOIN / operation constraints in the rendered SQL.

        Returns (ok, error, constraint_notes) — notes disclose constraints the
        render-time engine could not execute (A144/D4, honest degradation).
        """
        notes: list[str] = []
        sql_upper = sql.upper()

        # A144/D5: allowed_operations is READ for the first time — capability
        # declarations now gate what the rendered SQL may contain.
        join_count = len(re.findall(r'\bJOIN\b', sql_upper))
        if join_count and "multi_table_join" not in template.allowed_operations:
            return False, (
                f"SQL uses JOIN but template does not allow 'multi_table_join' (A144)"
            ), notes
        if _AGGREGATE_RE.search(sql) and "aggregate" not in template.allowed_operations:
            return False, (
                f"SQL uses aggregation but template does not allow 'aggregate' (A144)"
            ), notes

        # A144/D6: comma-separated FROM sources are an implicit cartesian product
        # AND undercounted by `join_count + 1`. Count top-level FROM sources.
        from_match = _FROM_CLAUSE_RE.search(sql)
        from_sources = 1
        if from_match:
            depth = 0
            top_level_commas = 0
            for ch in from_match.group(1):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth = max(0, depth - 1)
                elif ch == "," and depth == 0:
                    top_level_commas += 1
            from_sources = 1 + top_level_commas
            if top_level_commas:
                return False, (
                    "Comma-separated FROM (implicit cartesian product) is forbidden — "
                    "use an explicit JOIN ... ON (A144)"
                ), notes
        table_count = from_sources + join_count

        if table_count > template.max_tables:
            return False, (
                f"Too many tables: {table_count} (max: {template.max_tables})"
            ), notes

        # Check for cartesian product (CROSS JOIN)
        if "CROSS JOIN" in sql_upper:
            return False, "CROSS JOIN (cartesian product) is forbidden", notes

        # A144/D7: word-boundary ON/USING detection — `JOIN button b` (no condition
        # at all) used to false-pass because the substring "on" sits inside "button".
        join_sections = re.split(r'\bJOIN\b', sql_upper, flags=re.IGNORECASE)
        for i, section in enumerate(join_sections[1:], 1):  # Skip first (before first JOIN)
            head = section.split("WHERE")[0]
            if not (re.search(r'\bON\b', head) or re.search(r'\bUSING\b', head)):
                return False, f"JOIN #{i} missing ON clause (potential cartesian product)", notes

        # A144/D4: forbidden_operations actually executes now.
        for op in template.forbidden_operations:
            if op == "subquery_leak":
                if _SUBQUERY_RE.search(sql):
                    return False, (
                        "Subquery detected — forbidden by template constraint "
                        "'subquery_leak' (A144: used to be log-only)"
                    ), notes
            elif op == "network_call":
                if _NETWORK_RE.search(sql):
                    return False, "Network calls are forbidden in scenarios"
            elif op == "row_level_export":
                notes.append(
                    "forbidden op 'row_level_export' is a runtime/export-surface concern; "
                    "NOT verifiable at render time — enforce it at the export path (A144)"
                )

        return True, "", notes


# Singleton
scenario_engine = ScenarioEngine()
