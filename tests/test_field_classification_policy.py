"""Field-level classification policy tests (gap A5 · T7)."""
import pytest

from app.services.output_gateway import OutputPolicy, output_gateway


def test_field_rules_from_classifications():
    from app.services.policy_compiler import policy_compiler

    rules = policy_compiler.field_rules_from_classifications(
        {"id_card": 4, "phone": 3, "name": 2, "email": 1}
    )
    assert rules["deny_out_fields"] == ["id_card"]
    assert rules["mask_fields"] == ["phone"]
    assert "name" not in rules["mask_fields"]

    empty = policy_compiler.field_rules_from_classifications(None)
    assert empty == {"mask_fields": [], "deny_out_fields": []}
    empty2 = policy_compiler.field_rules_from_classifications({"name": "2"})
    assert empty2["mask_fields"] == [] and empty2["deny_out_fields"] == []


def test_mask_rows_by_field_rules():
    from app.services.output_security import mask_rows_by_field_rules

    rows = [
        {"id_card": "110101...", "phone": "13800000000", "name": "alice"},
        {"phone": "13900000000", "name": "bob"},
    ]
    rules = {"mask_fields": ["phone"], "deny_out_fields": ["id_card"]}
    allowed, blocked = mask_rows_by_field_rules(rows, rules)
    assert len(allowed) == 1
    assert blocked == ["id_card"]
    assert allowed[0]["phone"] == "***"
    assert allowed[0]["name"] == "bob"


def test_gateway_blocks_denied_field():
    # Neutral values so the DLP inspector does not pre-empt the field rules —
    # this test exercises the field-classification enforcement mechanism.
    data = [
        {"name": "alice", "level4_field": "cell-a"},
        {"name": "bob", "level4_field": "cell-b"},
    ]
    policy = OutputPolicy(
        max_output_rows=10,
        allowed_output_formats=["csv", "json"],
        inspection_rule_set={
            "field_classifications": {"level4_field": 4, "level3_field": 3},
        },
    )
    result = output_gateway.process(data, "json", policy)
    assert result.success is False
    assert "denied fields" in (result.error or "")


def test_gateway_masks_restricted_field():
    data = [{"name": "alice", "level3_field": "cell-x"}]
    policy = OutputPolicy(
        max_output_rows=10,
        allowed_output_formats=["json"],
        inspection_rule_set={"field_classifications": {"level3_field": 3}},
    )
    result = output_gateway.process(data, "json", policy)
    assert result.success is True
    assert '"***"' in str(result.output_data)
    assert '"name": "alice"' in str(result.output_data)
