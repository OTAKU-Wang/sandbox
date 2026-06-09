"""RLS Strategy Engine — Row-Level Security policy evaluation.

Provides three policy types for fine-grained data access control:
- Region policy: Filter by geographic region/tenant
- Time policy: Filter by time window (data retention, embargo periods)
- Sensitivity policy: Filter by security level clearance

Policies are evaluated and converted to SQL WHERE clauses for injection
into database queries.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class PolicyType(str, Enum):
    REGION = "region"
    TIME = "time"
    SENSITIVITY = "sensitivity"


class SensitivityLevel(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    SECRET = "secret"

    @classmethod
    def clearance_rank(cls, level: str) -> int:
        """Return numeric rank for comparison (higher = more sensitive)."""
        ranks = {
            cls.PUBLIC.value: 0,
            cls.INTERNAL.value: 1,
            cls.CONFIDENTIAL.value: 2,
            cls.SECRET.value: 3,
        }
        return ranks.get(level, -1)


@dataclass
class RLSPolicy:
    """A single Row-Level Security policy."""
    policy_id: str
    name: str
    policy_type: PolicyType
    enabled: bool = True
    priority: int = 0  # Higher = evaluated first
    conditions: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def to_sql_condition(self, context: dict[str, Any]) -> str | None:
        """Generate SQL WHERE clause from policy + evaluation context.

        Returns None if policy doesn't apply to the current context.
        """
        if not self.enabled:
            return None

        if self.policy_type == PolicyType.REGION:
            return self._region_condition(context)
        elif self.policy_type == PolicyType.TIME:
            return self._time_condition(context)
        elif self.policy_type == PolicyType.SENSITIVITY:
            return self._sensitivity_condition(context)
        return None

    def _region_condition(self, context: dict[str, Any]) -> str | None:
        """Generate region-based WHERE clause.

        Conditions:
        - allowed_regions: list of allowed region codes
        - region_column: column name containing the region data
        """
        allowed = self.conditions.get("allowed_regions", [])
        region_column = self.conditions.get("region_column", "region")
        user_region = context.get("user_region")

        if not allowed or not user_region:
            return None

        if user_region not in allowed:
            # Deny access — return impossible condition
            return "FALSE"

        return f"{region_column} = '{self._escape(user_region)}'"

    def _time_condition(self, context: dict[str, Any]) -> str | None:
        """Generate time-based WHERE clause.

        Conditions:
        - time_column: column name for timestamp filtering
        - min_age_hours: minimum data age (no access to data newer than this)
        - max_age_hours: maximum data age (no access to data older than this)
        - embargo_hours: block access to data within this many hours of creation
        """
        time_column = self.conditions.get("time_column", "created_at")
        min_age_hours = self.conditions.get("min_age_hours")
        max_age_hours = self.conditions.get("max_age_hours")
        embargo_hours = self.conditions.get("embargo_hours")

        now = datetime.now(timezone.utc)
        conditions = []

        if embargo_hours is not None:
            cutoff = now - timedelta(hours=embargo_hours)
            conditions.append(f"{time_column} <= '{cutoff.isoformat()}'")

        if min_age_hours is not None:
            cutoff = now - timedelta(hours=min_age_hours)
            conditions.append(f"{time_column} <= '{cutoff.isoformat()}'")

        if max_age_hours is not None:
            cutoff = now - timedelta(hours=max_age_hours)
            conditions.append(f"{time_column} >= '{cutoff.isoformat()}'")

        if not conditions:
            return None

        return " AND ".join(conditions)

    def _sensitivity_condition(self, context: dict[str, Any]) -> str | None:
        """Generate sensitivity-level WHERE clause.

        Conditions:
        - sensitivity_column: column name for sensitivity level
        - max_allowed_level: maximum sensitivity level the user can access
        """
        sensitivity_column = self.conditions.get("sensitivity_column", "security_level")
        max_level = self.conditions.get("max_allowed_level", "public")
        user_clearance = context.get("user_clearance", "public")

        max_rank = SensitivityLevel.clearance_rank(max_level)
        user_rank = SensitivityLevel.clearance_rank(user_clearance)

        if user_rank >= max_rank:
            # User has sufficient clearance — no filtering needed
            return None

        # Filter to only levels the user can access
        allowed_levels = [
            level.value
            for level in SensitivityLevel
            if SensitivityLevel.clearance_rank(level.value) <= user_rank
        ]
        in_clause = ", ".join(f"'{l}'" for l in allowed_levels)
        return f"{sensitivity_column} IN ({in_clause})"

    @staticmethod
    def _escape(value: str) -> str:
        """Basic SQL string escaping."""
        return value.replace("'", "''")


@dataclass
class RLSContext:
    """Evaluation context for RLS policies."""
    user_id: str | None = None
    user_region: str | None = None
    user_clearance: str | None = None
    user_roles: list[str] = field(default_factory=list)
    request_time: datetime | None = None
    session_id: str | None = None


class RLSEngine:
    """Row-Level Security policy engine.

    Manages policies, evaluates them against request context, and generates
    SQL WHERE clauses for injection into database queries.
    """

    def __init__(self):
        self._policies: dict[str, RLSPolicy] = {}

    def add_policy(self, policy: RLSPolicy) -> None:
        """Register a new RLS policy."""
        self._policies[policy.policy_id] = policy
        logger.info(f"Registered RLS policy: {policy.policy_id} ({policy.policy_type.value})")

    def remove_policy(self, policy_id: str) -> bool:
        """Remove an RLS policy."""
        if policy_id in self._policies:
            del self._policies[policy_id]
            return True
        return False

    def get_policy(self, policy_id: str) -> RLSPolicy | None:
        """Get a policy by ID."""
        return self._policies.get(policy_id)

    def list_policies(self, policy_type: PolicyType | None = None) -> list[RLSPolicy]:
        """List all policies, optionally filtered by type."""
        policies = list(self._policies.values())
        if policy_type:
            policies = [p for p in policies if p.policy_type == policy_type]
        return sorted(policies, key=lambda p: p.priority, reverse=True)

    def evaluate_sql(
        self,
        context: RLSContext,
        table_name: str | None = None,
        policy_ids: list[str] | None = None,
    ) -> str | None:
        """Evaluate all applicable policies and return combined SQL WHERE clause.

        Args:
            context: Request context with user info
            table_name: Optional table name for table-specific policies
            policy_ids: Optional list of specific policy IDs to evaluate

        Returns:
            SQL WHERE clause string, or None if no filtering needed.
            Multiple policies are AND-combined.
        """
        eval_context = {
            "user_id": context.user_id,
            "user_region": context.user_region,
            "user_clearance": context.user_clearance,
            "user_roles": context.user_roles,
            "request_time": context.request_time,
        }

        # Select policies to evaluate
        policies = list(self._policies.values())
        if policy_ids:
            policies = [p for p in policies if p.policy_id in policy_ids]

        # Filter to enabled, sorted by priority
        policies = [p for p in policies if p.enabled]
        policies.sort(key=lambda p: p.priority, reverse=True)

        conditions = []
        for policy in policies:
            condition = policy.to_sql_condition(eval_context)
            if condition:
                if condition == "FALSE":
                    return "FALSE"  # Deny all — short circuit
                conditions.append(f"({condition})")

        if not conditions:
            return None

        return " AND ".join(conditions)

    def inject_rls_where(self, sql: str, rls_where: str) -> str:
        """Inject RLS WHERE clause into a SQL query.

        Handles:
        - SELECT with existing WHERE: append AND
        - SELECT without WHERE: add WHERE
        - Subqueries: inject into outermost SELECT
        """
        if not rls_where:
            return sql

        # Normalize whitespace
        sql = sql.strip()

        # Find the main FROM clause to inject after it
        # Simple approach: find WHERE or add one
        sql_upper = sql.upper()

        if " WHERE " in sql_upper:
            # Find the last WHERE (outermost query)
            where_idx = sql_upper.rfind(" WHERE ")
            insert_pos = where_idx + len(" WHERE ")
            return (
                sql[:insert_pos]
                + f"({rls_where}) AND "
                + sql[insert_pos:]
            )
        elif " GROUP BY " in sql_upper:
            idx = sql_upper.find(" GROUP BY ")
            return (
                sql[:idx]
                + f" WHERE ({rls_where}) "
                + sql[idx:]
            )
        elif " ORDER BY " in sql_upper:
            idx = sql_upper.find(" ORDER BY ")
            return (
                sql[:idx]
                + f" WHERE ({rls_where}) "
                + sql[idx:]
            )
        elif " LIMIT " in sql_upper:
            idx = sql_upper.find(" LIMIT ")
            return (
                sql[:idx]
                + f" WHERE ({rls_where}) "
                + sql[idx:]
            )
        else:
            # Append WHERE at end
            return f"{sql} WHERE ({rls_where})"

    def validate_policy(self, policy: RLSPolicy) -> list[str]:
        """Validate a policy configuration. Returns list of errors."""
        errors = []

        if not policy.policy_id:
            errors.append("policy_id is required")
        if not policy.name:
            errors.append("name is required")

        if policy.policy_type == PolicyType.REGION:
            if "allowed_regions" not in policy.conditions:
                errors.append("Region policy requires 'allowed_regions' condition")
        elif policy.policy_type == PolicyType.TIME:
            if not any(
                k in policy.conditions
                for k in ("min_age_hours", "max_age_hours", "embargo_hours")
            ):
                errors.append("Time policy requires at least one of: min_age_hours, max_age_hours, embargo_hours")
        elif policy.policy_type == PolicyType.SENSITIVITY:
            if "max_allowed_level" not in policy.conditions:
                errors.append("Sensitivity policy requires 'max_allowed_level' condition")
            else:
                max_level = policy.conditions["max_allowed_level"]
                if max_level not in SensitivityLevel.__members__.values():
                    errors.append(f"Invalid sensitivity level: {max_level}")

        return errors

    # ── Row-level evaluation API (simplified) ──────────────────

    def evaluate(
        self,
        policy: dict[str, Any],
        row: dict[str, Any],
        user_region: str | None = None,
        user_clearance: int | None = None,
    ) -> bool:
        """Evaluate a single policy against a row (row-level access control).

        Args:
            policy: Policy dict with 'type' and type-specific fields
            row: Data row to evaluate
            user_region: User's region for region policies
            user_clearance: User's clearance level for sensitivity policies

        Returns:
            True if access allowed, False if denied.
        """
        policy_type = policy.get("type", "")

        if policy_type == "region":
            allowed_regions = policy.get("allowed_regions", [])
            if user_region is None:
                return False
            row_region = row.get("region", "")
            return user_region in allowed_regions and row_region in allowed_regions

        elif policy_type == "sensitivity":
            max_level = policy.get("max_level", 0)
            row_sensitivity = row.get("sensitivity", 0)
            if user_clearance is None:
                return False
            return user_clearance >= row_sensitivity and user_clearance >= max_level

        elif policy_type == "time":
            return self.evaluate_time_policy(policy)

        return True

    def evaluate_time_policy(
        self,
        policy: dict[str, Any],
        current_hour: int | None = None,
    ) -> bool:
        """Evaluate a time-based policy.

        Args:
            policy: Policy dict with 'start_hour' and 'end_hour'
            current_hour: Current hour (0-23). If None, uses actual current hour.

        Returns:
            True if within allowed time window, False otherwise.
        """
        if current_hour is None:
            from datetime import datetime
            current_hour = datetime.now().hour

        start = policy.get("start_hour", 0)
        end = policy.get("end_hour", 24)

        # Inclusive start, exclusive end
        return start <= current_hour < end

    def evaluate_composite(
        self,
        policies: list[dict[str, Any]],
        row: dict[str, Any],
        user_region: str | None = None,
        user_clearance: int | None = None,
    ) -> bool:
        """Evaluate multiple policies against a row (AND logic).

        Returns True only if ALL policies allow access.
        """
        for policy in policies:
            if not self.evaluate(policy, row, user_region, user_clearance):
                return False
        return True

    def inject_filter(
        self,
        sql: str,
        policy: dict[str, Any],
        user_region: str | None = None,
        user_clearance: int | None = None,
    ) -> str:
        """Inject a WHERE clause into SQL based on policy.

        Returns rewritten SQL with appropriate WHERE clause.
        """
        policy_type = policy.get("type", "")

        if policy_type == "region":
            column = policy.get("column", "region")
            if user_region:
                condition = f"{column} = '{self._escape_sql(user_region)}'"
                return self._inject_where(sql, condition)

        elif policy_type == "sensitivity":
            column = policy.get("column", "sensitivity_level")
            if user_clearance is not None:
                condition = f"{column} <= {user_clearance}"
                return self._inject_where(sql, condition)

        return sql

    @staticmethod
    def _inject_where(sql: str, condition: str) -> str:
        """Inject a WHERE condition into a SQL query."""
        sql = sql.strip()
        sql_upper = sql.upper()

        if " WHERE " in sql_upper:
            idx = sql_upper.find(" WHERE ")
            insert_pos = idx + len(" WHERE ")
            return sql[:insert_pos] + f"{condition} AND " + sql[insert_pos:]
        elif " GROUP BY " in sql_upper:
            idx = sql_upper.find(" GROUP BY ")
            return sql[:idx] + f" WHERE {condition} " + sql[idx:]
        elif " ORDER BY " in sql_upper:
            idx = sql_upper.find(" ORDER BY ")
            return sql[:idx] + f" WHERE {condition} " + sql[idx:]
        elif " LIMIT " in sql_upper:
            idx = sql_upper.find(" LIMIT ")
            return sql[:idx] + f" WHERE {condition} " + sql[idx:]
        else:
            return f"{sql} WHERE {condition}"

    @staticmethod
    def _escape_sql(value: str) -> str:
        """Basic SQL string escaping."""
        return value.replace("'", "''")


# Singleton
rls_engine = RLSEngine()
