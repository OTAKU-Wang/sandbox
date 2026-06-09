"""Field-level ACL service — enforces column/field access control.

Applies per-field read/aggregate-only/mask rules from policy bundles
to query results before returning to the sandbox.
"""
import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class FieldRule:
    """Access rule for a single field."""
    read: bool = False
    aggregate_only: bool = False
    mask_pattern: str | None = None  # e.g., "***", "sha256", "prefix:3"


@dataclass
class FieldACLResult:
    """Result of field-level ACL application."""
    allowed_fields: list[str]
    denied_fields: list[str]
    masked_fields: dict[str, str]  # field → mask_pattern
    aggregate_only_fields: list[str]


class FieldACLService:
    """Enforces field-level access control on data returned from queries.

    Rules come from PolicyBundle.field_acl:
    {
        "field_name": {
            "read": true/false,
            "aggregate_only": true/false,
            "mask_pattern": "***" | "sha256" | "prefix:N"
        }
    }
    """

    def parse_rules(self, acl_config: dict[str, dict]) -> dict[str, FieldRule]:
        """Parse ACL config dict into FieldRule objects."""
        rules = {}
        for field_name, config in acl_config.items():
            rules[field_name] = FieldRule(
                read=config.get("read", False),
                aggregate_only=config.get("aggregate_only", False),
                mask_pattern=config.get("mask_pattern"),
            )
        return rules

    def filter_fields(
        self, requested_fields: list[str], acl_config: dict[str, dict]
    ) -> FieldACLResult:
        """Filter requested fields through ACL rules."""
        if not acl_config:
            return FieldACLResult(
                allowed_fields=requested_fields,
                denied_fields=[],
                masked_fields={},
                aggregate_only_fields=[],
            )

        rules = self.parse_rules(acl_config)
        allowed = []
        denied = []
        masked = {}
        aggregate_only = []

        for field_name in requested_fields:
            rule = rules.get(field_name)
            if rule is None:
                # No specific rule → allow
                allowed.append(field_name)
            elif rule.read:
                allowed.append(field_name)
                if rule.mask_pattern:
                    masked[field_name] = rule.mask_pattern
                if rule.aggregate_only:
                    aggregate_only.append(field_name)
            else:
                denied.append(field_name)

        return FieldACLResult(
            allowed_fields=allowed,
            denied_fields=denied,
            masked_fields=masked,
            aggregate_only_fields=aggregate_only,
        )

    def apply_masks(
        self, rows: list[dict], masked_fields: dict[str, str]
    ) -> list[dict]:
        """Apply mask patterns to data rows."""
        if not masked_fields:
            return rows

        result = []
        for row in rows:
            masked_row = dict(row)
            for field_name, pattern in masked_fields.items():
                if field_name in masked_row:
                    masked_row[field_name] = self._mask_value(
                        masked_row[field_name], pattern
                    )
            result.append(masked_row)
        return result

    def _mask_value(self, value, pattern: str):
        """Apply a mask pattern to a single value."""
        if value is None:
            return None

        str_val = str(value)

        if pattern == "***":
            return "***"
        elif pattern == "sha256":
            from app.services.crypto_service import crypto_service
            return crypto_service.sm3_hash(str_val.encode())[:16]
        elif pattern.startswith("prefix:"):
            n = int(pattern.split(":")[1])
            return str_val[:n] + "***"
        elif pattern == "null":
            return None
        else:
            return pattern

    def build_sql_projection(
        self, requested_fields: list[str], acl_config: dict[str, dict]
    ) -> tuple[list[str], list[str]]:
        """Build SQL SELECT field list respecting ACL.

        Returns: (select_fields, aggregate_only_fields)
        For aggregate_only fields, the caller must use GROUP BY / aggregation.
        """
        result = self.filter_fields(requested_fields, acl_config)
        return result.allowed_fields, result.aggregate_only_fields

    def validate_aggregate_query(
        self, fields: list[str], acl_config: dict[str, dict], has_aggregation: bool
    ) -> list[str]:
        """Validate that aggregate-only fields are used with aggregation.

        Returns list of violations (empty = valid).
        """
        result = self.filter_fields(fields, acl_config)
        if not result.aggregate_only_fields:
            return []

        if has_aggregation:
            return []

        return [
            f"Field '{f}' requires aggregation (aggregate_only)"
            for f in result.aggregate_only_fields
        ]


# Singleton
field_acl_service = FieldACLService()
