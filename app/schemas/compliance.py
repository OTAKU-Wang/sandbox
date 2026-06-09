"""Compliance report schemas — TC609-6-2025-01 aligned."""
from datetime import datetime
from pydantic import BaseModel, Field


class ComplianceReportRequest(BaseModel):
    start_date: datetime
    end_date: datetime
    report_type: str = Field(default="tc609", pattern="^(tc609|security|full)$")
    include_sections: list[str] | None = None  # None = all sections


class AuditSummary(BaseModel):
    total_events: int
    events_by_type: dict[str, int]
    events_by_severity: dict[str, int]
    security_incidents: int
    policy_denials: int


class AccessControlReport(BaseModel):
    total_auth_events: int
    failed_auth_attempts: int
    role_distribution: dict[str, int]
    privileged_operations: int


class DataProtectionReport(BaseModel):
    encryption_events: int
    pii_redaction_events: int
    dp_budget_charges: int
    output_blocks: int


class ComplianceReportResponse(BaseModel):
    report_id: str
    report_type: str
    generated_at: datetime
    period_start: datetime
    period_end: datetime
    space_id: str
    audit_summary: AuditSummary
    access_control: AccessControlReport
    data_protection: DataProtectionReport
    compliance_status: dict[str, str]  # check_name -> pass/fail/warn
    recommendations: list[str]
