"""Test Data Generator — produces sample data for data product development.

Three modes:
1. Mock — Generated from schema definitions (random but structurally valid)
2. Real sampling — Privacy-preserving extraction from actual data resources
3. Desensitized — Real data with PII masking applied

Used during data product development to test pipelines before publishing.
"""
import io
import csv
import json
import random
import string
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Chinese PII patterns for desensitization
_PII_PATTERNS = {
    "phone": ("1[3-9]\\d{9}", lambda: f"1{random.choice('3456789')}{''.join(random.choices(string.digits, k=9))}"),
    "id_card": ("[1-9]\\d{5}(19|20)\\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\\d|3[01])\\d{3}[\\dXx]",
                lambda: f"{random.randint(10000, 99999)}{random.randint(1960, 2005)}{random.randint(1, 12):02d}{random.randint(1, 28):02d}{random.randint(100, 999)}{random.choice('0123456789X')}"),
    "email": ("[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}",
              lambda: f"{''.join(random.choices(string.ascii_lowercase, k=8))}@example.com"),
    "bank_card": ("[1-9]\\d{15,18}",
                  lambda: f"{''.join(random.choices(string.digits, k=16))}"),
    "name": ("[\\u4e00-\\u9fa5]{2,4}",
             lambda: random.choice(["张三", "李四", "王五", "赵六", "陈七", "刘八", "周九", "吴十"])),
}

# Type-aware generators for mock data
_TYPE_GENERATORS = {
    "string": lambda: random.choice(["示例文本", "测试数据", "产品A", "类别B", "样本C"]),
    "integer": lambda: random.randint(1, 10000),
    "float": lambda: round(random.uniform(0, 1000), 2),
    "boolean": lambda: random.choice([True, False]),
    "date": lambda: (datetime.now() - timedelta(days=random.randint(0, 365))).strftime("%Y-%m-%d"),
    "datetime": lambda: (datetime.now() - timedelta(days=random.randint(0, 365))).isoformat(),
    "email": lambda: f"test_{''.join(random.choices(string.ascii_lowercase, k=6))}@example.com",
    "phone": lambda: f"1{random.choice('3456789')}{''.join(random.choices(string.digits, k=9))}",
    "address": lambda: random.choice(["北京市海淀区", "上海市浦东新区", "深圳市南山区", "杭州市西湖区", "成都市高新区"]) + random.choice(["XX路", "XX街", "XX大道"]) + str(random.randint(1, 999)) + "号",
    "name": lambda: random.choice(["张三", "李四", "王五", "赵六", "陈七", "刘八", "周九", "吴十"]),
    "url": lambda: f"https://example.com/{''.join(random.choices(string.ascii_lowercase, k=8))}",
    "json": lambda: json.dumps({"key": f"value_{random.randint(1, 100)}"}),
    "unknown": lambda: f"field_{random.randint(1, 1000)}",
}


class TestDataGenerator:
    """Generates test data for data product development."""

    def generate_mock(self, schema: list[dict], row_count: int = 100) -> str:
        """Generate mock data from schema definitions.

        Args:
            schema: List of field definitions [{name, type, sensitivity, ...}]
            row_count: Number of rows to generate

        Returns:
            CSV string with generated data
        """
        if not schema:
            return ""

        output = io.StringIO()
        fieldnames = [f["name"] for f in schema]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        for _ in range(row_count):
            row = {}
            for field_def in schema:
                field_type = field_def.get("type", "string")
                generator = _TYPE_GENERATORS.get(field_type, _TYPE_GENERATORS["unknown"])
                row[field_def["name"]] = generator()
            writer.writerow(row)

        return output.getvalue()

    def sample_from_resource(self, data: bytes, format: str, sample_size: int = 100,
                              method: str = "random") -> str:
        """Sample rows from an actual data resource.

        Args:
            data: Raw data bytes (decrypted)
            format: Data format (csv, json, parquet)
            sample_size: Number of rows to sample
            method: Sampling method — "random", "systematic", "first_n"

        Returns:
            CSV string with sampled data
        """
        if format == "csv":
            return self._sample_csv(data, sample_size, method)
        elif format == "json":
            return self._sample_json(data, sample_size, method)
        else:
            logger.warning(f"Unsupported format for sampling: {format}")
            return ""

    def desensitize(self, data: bytes, format: str, schema: list[dict] | None = None) -> str:
        """Apply PII masking to real data.

        Args:
            data: Raw data bytes (decrypted)
            format: Data format
            schema: Optional schema with sensitivity annotations

        Returns:
            CSV string with PII masked
        """
        if format == "csv":
            return self._desensitize_csv(data, schema)
        elif format == "json":
            return self._desensitize_json(data, schema)
        else:
            logger.warning(f"Unsupported format for desensitization: {format}")
            return ""

    def _sample_csv(self, data: bytes, sample_size: int, method: str) -> str:
        """Sample rows from CSV data."""
        try:
            text = data.decode("utf-8", errors="ignore")
            reader = csv.DictReader(io.StringIO(text))
            rows = list(reader)

            if not rows:
                return ""

            if method == "first_n":
                sampled = rows[:sample_size]
            elif method == "systematic":
                step = max(1, len(rows) // sample_size)
                sampled = rows[::step][:sample_size]
            else:  # random
                sampled = random.sample(rows, min(sample_size, len(rows)))

            if not sampled:
                return ""

            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=list(sampled[0].keys()))
            writer.writeheader()
            writer.writerows(sampled)
            return output.getvalue()
        except Exception as e:
            logger.error(f"CSV sampling failed: {e}")
            return ""

    def _sample_json(self, data: bytes, sample_size: int, method: str) -> str:
        """Sample items from JSON data."""
        try:
            parsed = json.loads(data)
            if isinstance(parsed, list):
                items = parsed
            elif isinstance(parsed, dict) and "data" in parsed:
                items = parsed["data"]
            else:
                return ""

            if method == "first_n":
                sampled = items[:sample_size]
            elif method == "systematic":
                step = max(1, len(items) // sample_size)
                sampled = items[::step][:sample_size]
            else:
                sampled = random.sample(items, min(sample_size, len(items)))

            # Convert to CSV
            if not sampled:
                return ""
            if isinstance(sampled[0], dict):
                output = io.StringIO()
                writer = csv.DictWriter(output, fieldnames=list(sampled[0].keys()))
                writer.writeheader()
                writer.writerows(sampled)
                return output.getvalue()
            return ""
        except Exception as e:
            logger.error(f"JSON sampling failed: {e}")
            return ""

    def _desensitize_csv(self, data: bytes, schema: list[dict] | None) -> str:
        """Apply PII masking to CSV data."""
        try:
            text = data.decode("utf-8", errors="ignore")
            reader = csv.DictReader(io.StringIO(text))
            rows = list(reader)

            if not rows:
                return ""

            # Build sensitivity map from schema
            sensitive_fields = {}
            if schema:
                for field_def in schema:
                    if field_def.get("sensitivity") in ("pii", "sensitive", "high"):
                        sensitive_fields[field_def["name"]] = field_def.get("type", "string")

            output = io.StringIO()
            fieldnames = list(rows[0].keys())
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()

            for row in rows:
                masked_row = {}
                for key, value in row.items():
                    if key in sensitive_fields:
                        masked_row[key] = self._mask_value(value, sensitive_fields[key])
                    else:
                        masked_row[key] = value
                writer.writerow(masked_row)

            return output.getvalue()
        except Exception as e:
            logger.error(f"CSV desensitization failed: {e}")
            return ""

    def _desensitize_json(self, data: bytes, schema: list[dict] | None) -> str:
        """Apply PII masking to JSON data."""
        try:
            parsed = json.loads(data)
            items = parsed if isinstance(parsed, list) else parsed.get("data", [])

            sensitive_fields = {}
            if schema:
                for field_def in schema:
                    if field_def.get("sensitivity") in ("pii", "sensitive", "high"):
                        sensitive_fields[field_def["name"]] = field_def.get("type", "string")

            masked_items = []
            for item in items:
                if isinstance(item, dict):
                    masked = {}
                    for key, value in item.items():
                        if key in sensitive_fields:
                            masked[key] = self._mask_value(str(value), sensitive_fields[key])
                        else:
                            masked[key] = value
                    masked_items.append(masked)
                else:
                    masked_items.append(item)

            # Convert to CSV
            if not masked_items:
                return ""
            if isinstance(masked_items[0], dict):
                output = io.StringIO()
                writer = csv.DictWriter(output, fieldnames=list(masked_items[0].keys()))
                writer.writeheader()
                writer.writerows(masked_items)
                return output.getvalue()
            return ""
        except Exception as e:
            logger.error(f"JSON desensitization failed: {e}")
            return ""

    def _mask_value(self, value: str, field_type: str) -> str:
        """Mask a single value based on its type."""
        if not value:
            return value

        # Hash-based masking for consistent results
        hash_val = hashlib.md5(value.encode()).hexdigest()[:8]

        if field_type == "phone":
            # Keep first 3 and last 4 digits
            if len(value) >= 11:
                return value[:3] + "****" + value[-4:]
            return f"1**{'* * ' * 3}{hash_val[:4]}"
        elif field_type == "id_card":
            # Keep first 6 and last 4 digits
            if len(value) >= 18:
                return value[:6] + "********" + value[-4:]
            return f"**{hash_val}**"
        elif field_type == "email":
            parts = value.split("@")
            if len(parts) == 2:
                name = parts[0]
                masked_name = name[0] + "***" + (name[-1] if len(name) > 1 else "")
                return f"{masked_name}@{parts[1]}"
            return f"***@example.com"
        elif field_type == "name":
            if len(value) >= 2:
                return value[0] + "*" * (len(value) - 1)
            return "***"
        elif field_type == "address":
            # Keep city, mask detailed address
            if len(value) >= 4:
                return value[:4] + "****"
            return "****"
        elif field_type == "bank_card":
            if len(value) >= 8:
                return value[:4] + " **** **** " + value[-4:]
            return f"****{hash_val[:4]}****"
        else:
            # Generic masking: replace with hash
            return f"MASKED_{hash_val}"


# Singleton
test_data_generator = TestDataGenerator()
