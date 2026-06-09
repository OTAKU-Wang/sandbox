"""Output Gateway — unified output processing with contract policy enforcement.

Chains: inspection → format conversion → row limiting → delivery.
Enforces contract limits (max_output_rows, allowed_output_formats).
"""
import csv
import io
import json
import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class OutputFormat(str, Enum):
    CSV = "csv"
    JSON = "json"
    PARQUET = "parquet"


VALID_FORMATS = {f.value for f in OutputFormat}


@dataclass
class OutputPolicy:
    """Contract-derived output constraints."""
    max_output_rows: int = 10000
    allowed_output_formats: list[str] = field(default_factory=lambda: ["csv", "json"])
    dp_epsilon_budget: float | None = None
    inspection_rule_set: dict | None = None


@dataclass
class GatewayResult:
    """Result of output gateway processing."""
    success: bool
    output_data: str | bytes | None = None
    output_format: str = "csv"
    row_count: int = 0
    truncated: bool = False
    findings: list[dict] = field(default_factory=list)
    security_report: dict = field(default_factory=dict)
    watermark: str | None = None
    signature: str | None = None
    error: str | None = None


class OutputGateway:
    """Processes sandbox output through policy enforcement pipeline."""

    def process(
        self,
        data: list[dict],
        output_format: str,
        policy: OutputPolicy,
        user_id: str = "",
        session_id: str = "",
    ) -> GatewayResult:
        """Process output data through the gateway.

        Steps:
        1. Validate output format against policy
        2. Enforce row limit
        3. Convert to requested format
        4. Return processed output
        """
        # Step 1: Validate format
        if output_format not in VALID_FORMATS:
            return GatewayResult(
                success=False,
                error=f"Invalid output format: {output_format}. Valid: {VALID_FORMATS}",
            )
        if output_format not in policy.allowed_output_formats:
            return GatewayResult(
                success=False,
                error=f"Format '{output_format}' not allowed by contract. Allowed: {policy.allowed_output_formats}",
            )

        # Step 2: Enforce row limit
        truncated = False
        if len(data) > policy.max_output_rows:
            data = data[:policy.max_output_rows]
            truncated = True

        # Step 3: Inspect serialized output before release. Watermark/signature
        # metadata is returned alongside the payload so structured formats remain
        # parseable.
        rules = policy.inspection_rule_set or {}
        inspection_text = json.dumps(data, ensure_ascii=False, default=str)
        try:
            from app.services.output_security import (
                inspect_text_output,
                inspection_to_report,
                redact_data,
                should_block,
            )

            inspector_rows = data if rules.get("k_anonymity") or rules.get("enforce_k_anonymity") else None
            inspection = inspect_text_output(
                inspection_text,
                user_id=user_id,
                session_id=session_id,
                sandbox_mode=rules.get("sandbox_mode"),
                dp_epsilon=policy.dp_epsilon_budget,
                output_rows=inspector_rows,
                detail={"source_rows": rules.get("source_rows")} if rules.get("source_rows") else None,
            )
            security_report = inspection_to_report(inspection)
            findings = security_report["findings"]
            if should_block(inspection):
                return GatewayResult(
                    success=False,
                    output_format=output_format,
                    row_count=len(data),
                    truncated=truncated,
                    findings=findings,
                    security_report=security_report,
                    watermark=inspection.watermark,
                    signature=inspection.signature,
                    error="Output inspection blocked release",
                )
            if findings:
                data = redact_data(data)
        except Exception as e:
            logger.warning("Output inspection failed closed: %s", e)
            return GatewayResult(success=False, error=f"Output inspection failed: {e}")

        # Step 4: Convert format
        try:
            if output_format == OutputFormat.CSV.value:
                output = self._to_csv(data)
            elif output_format == OutputFormat.JSON.value:
                output = json.dumps(data, ensure_ascii=False, default=str)
            elif output_format == OutputFormat.PARQUET.value:
                output = self._to_parquet(data)
            else:
                return GatewayResult(success=False, error=f"Unsupported format: {output_format}")
        except Exception as e:
            return GatewayResult(success=False, error=f"Format conversion failed: {e}")

        return GatewayResult(
            success=True,
            output_data=output,
            output_format=output_format,
            row_count=len(data),
            truncated=truncated,
            findings=findings,
            security_report=security_report,
            watermark=inspection.watermark,
            signature=inspection.signature,
        )

    def _to_csv(self, data: list[dict]) -> str:
        """Convert list of dicts to CSV string."""
        if not data:
            return ""
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
        return output.getvalue()

    def _to_parquet(self, data: list[dict]) -> bytes:
        """Convert list of dicts to Parquet bytes.

        Uses pyarrow if available, falls back to JSON with parquet marker.
        """
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq

            if not data:
                return b""

            # Convert to columnar format
            columns = {key: [row.get(key) for row in data] for key in data[0].keys()}
            table = pa.table(columns)
            buf = io.BytesIO()
            pq.write_table(table, buf)
            return buf.getvalue()
        except ImportError:
            # Fallback: JSON with parquet marker
            logger.warning("pyarrow not installed, falling back to JSON for Parquet output")
            return json.dumps(data, ensure_ascii=False, default=str).encode()


# Singleton
output_gateway = OutputGateway()
