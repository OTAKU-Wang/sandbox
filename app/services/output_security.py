"""Shared output-security helpers for all sandbox egress paths."""
from __future__ import annotations

import json
from typing import Any

from app.services.output_inspection import (
    InspectionFinding,
    InspectionResult,
    OutputInspector,
    SandboxMode,
)


BLOCKING_SEVERITIES = {"critical"}


def normalize_sandbox_mode(mode: str | None) -> str:
    """Map product sandbox modes to OutputInspector scene names."""
    value = (mode or "").strip().lower()
    if value in {SandboxMode.STRUCTURED_QUERY.value, "structured_query", "query"}:
        return SandboxMode.STRUCTURED_QUERY.value
    if value in {
        SandboxMode.STRUCTURED_MODELING.value,
        "structured_modeling",
        "llm_training",
        "train",
        "training",
    }:
        return SandboxMode.STRUCTURED_MODELING.value
    if value in {SandboxMode.DEVELOP.value, "product_dev", "dev", "develop"}:
        return SandboxMode.DEVELOP.value
    if value in {SandboxMode.APPLICATION.value, "structured_app", "app", "application"}:
        return SandboxMode.APPLICATION.value
    return SandboxMode.STRUCTURED_QUERY.value


def findings_to_dicts(findings: list[InspectionFinding]) -> list[dict[str, Any]]:
    return [
        {
            "stage": finding.stage,
            "severity": finding.severity,
            "type": finding.type,
            "message": finding.message,
            "samples": finding.samples,
        }
        for finding in findings
    ]


def inspection_to_report(result: InspectionResult) -> dict[str, Any]:
    """Serialize an InspectionResult without exposing raw sensitive samples beyond DLP samples."""
    severities = [finding.severity for finding in result.findings]
    return {
        "passed": result.passed,
        "blocked": should_block(result),
        "stage_results": result.stage_results,
        "findings": findings_to_dicts(result.findings),
        "findings_count": len(result.findings),
        "severities": severities,
        "dp_applied": result.dp_applied,
        # A192e 报告腿：dp_applied 一个布尔表达不了「剩余 ε 未知」。少了这行，11 个调用点
        # 只能从 findings 文本里猜预算到底是「核实过确实不够」还是「后端读不到」，
        # 而把未知压成 dp_applied=False 就是「没有预算」的读数。语义与 dp_applied 无关：
        # 未核实仍不计账、仍 fail-closed，只是终于能被报告出来。
        "dp_budget_state": result.dp_budget_state,
        "watermark": result.watermark,
        "signature": result.signature,
        # A191/A193 续：签名的可审计性是报告的一部分。少了这三个字段，11 个调用点只看
        # 得到 signature 有没有值，分不清部署密钥与进程内临时密钥，也看不见签名失败时的
        # 降级摘要（unsigned_digest 不是签名，绝不能顶替 signature）。
        "signature_key_source": getattr(result, "signature_key_source", None),
        "signature_public_key": getattr(result, "signature_public_key", None),
        "unsigned_digest": getattr(result, "unsigned_digest", None),
        "not_evaluated": list(getattr(result, "not_evaluated", []) or []),
        "degraded": list(getattr(result, "degraded", []) or []),
    }


def should_block(result: InspectionResult) -> bool:
    """Return True when the output must not be released even in redacted form."""
    return any(finding.severity in BLOCKING_SEVERITIES for finding in result.findings)


def redact_text(text: str, inspector: OutputInspector | None = None) -> tuple[str, bool]:
    """Redact text using the same DLP patterns as OutputInspector."""
    inspector = inspector or OutputInspector()
    redacted = text
    changed = False
    for name, (pattern, _) in inspector.PATTERNS.items():
        new_value = pattern.sub(f"[REDACTED:{name}]", redacted)
        if new_value != redacted:
            changed = True
            redacted = new_value
    return redacted, changed


def redact_data(value: Any, inspector: OutputInspector | None = None) -> Any:
    """Recursively redact string leaves in JSON-like output data."""
    inspector = inspector or OutputInspector()
    if isinstance(value, str):
        return redact_text(value, inspector)[0]
    if isinstance(value, list):
        return [redact_data(item, inspector) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_data(item, inspector) for item in value)
    if isinstance(value, dict):
        return {key: redact_data(item, inspector) for key, item in value.items()}
    return value


def mask_rows_by_field_rules(rows: list[dict], field_rules: dict) -> tuple[list[dict], list[str]]:
    """Apply field-level classification rules to output rows (gap A5/T7).

    field_rules: {"mask_fields": [...], "deny_out_fields": [...]}

    - deny_out_fields present in a row → the row is withheld (blocked).
    - mask_fields → the cell value is replaced with "***".
    Returns (allowed_rows, blocked_reasons).
    """
    if not rows or not field_rules:
        return rows, []
    mask_fields = {str(f) for f in (field_rules.get("mask_fields") or [])}
    deny_out_fields = {str(f) for f in (field_rules.get("deny_out_fields") or [])}
    if not mask_fields and not deny_out_fields:
        return rows, []

    allowed: list[dict] = []
    blocked: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            allowed.append(row)
            continue
        present_denied = [f for f in deny_out_fields if f in row]
        if present_denied:
            blocked.append(",".join(present_denied))
            continue
        if mask_fields:
            row = {k: ("***" if k in mask_fields else v) for k, v in row.items()}
        allowed.append(row)
    return allowed, blocked


def inspect_text_output(
    output: str,
    *,
    user_id: str,
    session_id: str,
    sandbox_mode: str | None = None,
    dp_epsilon: float | None = None,
    output_rows: list[dict] | None = None,
    detail: dict | None = None,
    inspector: OutputInspector | None = None,
) -> InspectionResult:
    """Inspect a text payload through the shared OutputInspector."""
    inspector = inspector or OutputInspector()
    return inspector.inspect(
        output,
        user_id or "unknown-user",
        session_id or "unknown-session",
        dp_epsilon=dp_epsilon,
        sandbox_mode=normalize_sandbox_mode(sandbox_mode),
        output_rows=output_rows,
        detail=detail,
    )


def safe_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
