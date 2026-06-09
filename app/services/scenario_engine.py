"""Scenario Template Engine — parameterized business scenario execution.

Allows data providers to define reusable scenario templates with parameterized
SQL. Buyers fill in parameters and execute in the sandbox. Supports:
- Multi-table JOIN with constraints
- Parameter validation and sanitization
- Output schema enforcement
- Security constraint checking
"""
import re
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


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
        """Register a scenario template."""
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

            # Allowed values check
            if param_def.allowed_values and str(value) not in param_def.allowed_values:
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
        2. Sanitize parameter values (prevent SQL injection)
        3. Render SQL template
        4. Check JOIN constraints
        5. Add LIMIT if missing
        """
        template = self._templates.get(template_id)
        if not template:
            return ScenarioExecution(success=False, error=f"Template not found: {template_id}")

        # Validate parameters
        is_valid, errors = self.validate_parameters(template_id, params)
        if not is_valid:
            return ScenarioExecution(success=False, error=f"Parameter errors: {'; '.join(errors)}")

        # Sanitize parameters
        sanitized = self._sanitize_params(params)

        # Render SQL
        try:
            sql = template.sql_template.format(**sanitized)
        except KeyError as e:
            return ScenarioExecution(
                success=False, error=f"Missing template variable: {e}"
            )

        # Check JOIN constraints
        join_ok, join_error = self._check_join_constraints(sql, template)
        if not join_ok:
            return ScenarioExecution(success=False, error=join_error)

        # Ensure LIMIT
        if "LIMIT" not in sql.upper():
            sql = f"{sql} LIMIT {template.max_output_rows}"

        return ScenarioExecution(
            success=True,
            sql=sql,
            params=sanitized,
        )

    def _sanitize_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Sanitize parameter values to prevent SQL injection."""
        sanitized = {}
        for key, value in params.items():
            if isinstance(value, str):
                # Remove dangerous SQL patterns
                cleaned = value
                # Remove SQL keywords that could be injection attempts
                cleaned = re.sub(r'(?:DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE)\b',
                                 '', cleaned, flags=re.IGNORECASE)
                # Remove SQL comment markers
                cleaned = cleaned.replace('--', '').replace('/*', '').replace('*/', '')
                # Remove semicolons (statement terminators)
                cleaned = cleaned.replace(';', '')
                # Escape single quotes
                cleaned = cleaned.replace("'", "''")
                sanitized[key] = cleaned
            else:
                sanitized[key] = value
        return sanitized

    def _check_join_constraints(
        self,
        sql: str,
        template: ScenarioTemplate,
    ) -> tuple[bool, str]:
        """Check JOIN constraints in the rendered SQL."""
        sql_upper = sql.upper()

        # Count JOINs
        join_count = len(re.findall(r'\bJOIN\b', sql_upper))
        # Estimate table count = FROM + JOIN + 1
        table_count = join_count + 1

        if table_count > template.max_tables:
            return False, (
                f"Too many tables: {table_count} (max: {template.max_tables})"
            )

        # Check for cartesian product (CROSS JOIN or missing ON)
        if "CROSS JOIN" in sql_upper:
            return False, "CROSS JOIN (cartesian product) is forbidden"

        # Check that each JOIN has an ON clause
        join_sections = re.split(r'\bJOIN\b', sql_upper, flags=re.IGNORECASE)
        for i, section in enumerate(join_sections[1:], 1):  # Skip first (before first JOIN)
            if "ON" not in section.split("WHERE")[0]:
                return False, f"JOIN #{i} missing ON clause (potential cartesian product)"

        # Check for forbidden operations
        for op in template.forbidden_operations:
            if op == "subquery_leak":
                # Check for correlated subqueries that could leak data
                if re.search(r'WHERE\s+.*?\(SELECT\b', sql_upper):
                    logger.warning("Subquery detected in WHERE — potential data leak")
            elif op == "network_call":
                if re.search(r'\b(pg_sleep|dblink|http)', sql_upper, re.IGNORECASE):
                    return False, "Network calls are forbidden in scenarios"

        return True, ""


# Singleton
scenario_engine = ScenarioEngine()
