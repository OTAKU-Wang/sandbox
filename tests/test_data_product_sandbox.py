"""Tests for data product development sandbox service."""
import tempfile
from pathlib import Path

import pytest

from app.services.data_product_sandbox import (
    DataProductDevSandbox,
    DevSandboxConfig,
    DevMode,
    StructuredDevTools,
    UnstructuredDevTools,
    SemiStructuredDevTools,
)
from app.models.sandbox_session import SessionStatus


@pytest.fixture
def sandbox():
    return DataProductDevSandbox()


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


# ── DevSandboxConfig ────────────────────────────────────────────────────────

def test_config_defaults():
    config = DevSandboxConfig(mode=DevMode.STRUCTURED)
    assert config.sandbox_level == "L3"
    assert config.max_duration_seconds == 7200
    assert config.max_input_files == 100


# ── DataProductDevSandbox session lifecycle ──────────────────────────────────

def test_create_session_structured(sandbox):
    config = DevSandboxConfig(mode=DevMode.STRUCTURED)
    session = sandbox.create_session(config)
    assert session.session_id is not None
    assert session.config.mode == DevMode.STRUCTURED
    assert session.status in (SessionStatus.RUNNING.value, SessionStatus.FAILED.value)


def test_create_session_unstructured(sandbox):
    config = DevSandboxConfig(mode=DevMode.UNSTRUCTURED)
    session = sandbox.create_session(config)
    assert session.config.mode == DevMode.UNSTRUCTURED


def test_create_session_semi_structured(sandbox):
    config = DevSandboxConfig(mode=DevMode.SEMI_STRUCTURED)
    session = sandbox.create_session(config)
    assert session.config.mode == DevMode.SEMI_STRUCTURED


@pytest.mark.asyncio
async def test_get_session(sandbox):
    config = DevSandboxConfig(mode=DevMode.STRUCTURED)
    session = sandbox.create_session(config)
    retrieved = await sandbox.get_session(session.session_id)
    assert retrieved is not None
    assert retrieved.session_id == session.session_id


@pytest.mark.asyncio
async def test_get_nonexistent_session(sandbox):
    assert await sandbox.get_session("nonexistent") is None


@pytest.mark.asyncio
async def test_list_sessions(sandbox):
    config = DevSandboxConfig(mode=DevMode.STRUCTURED)
    sandbox.create_session(config)
    sandbox.create_session(config)
    sessions = await sandbox.list_sessions()
    assert len(sessions) >= 2


@pytest.mark.asyncio
async def test_terminate_session(sandbox):
    config = DevSandboxConfig(mode=DevMode.STRUCTURED)
    session = sandbox.create_session(config)
    result = await sandbox.terminate_session(session.session_id)
    assert result is True
    updated = await sandbox.get_session(session.session_id)
    assert updated.status == SessionStatus.TERMINATED.value


@pytest.mark.asyncio
async def test_terminate_nonexistent(sandbox):
    assert await sandbox.terminate_session("nonexistent") is False


# ── Upload data ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_structured_csv(sandbox, tmp_dir):
    import csv
    csv_path = Path(tmp_dir) / "test.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "value"])
        writer.writerow(["a", 1])
        writer.writerow(["b", 2])

    config = DevSandboxConfig(mode=DevMode.STRUCTURED)
    session = sandbox.create_session(config)
    result = await sandbox.upload_data(session.session_id, str(csv_path))
    assert result.success is True
    assert result.records_processed == 2


@pytest.mark.asyncio
async def test_upload_unstructured_image(sandbox, tmp_dir):
    img_path = Path(tmp_dir) / "test.png"
    import struct, zlib
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)
    raw = zlib.compress(b'\x00\x00\x00\x00')

    def chunk(ctype, data):
        c = ctype + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)

    img_path.write_bytes(sig + chunk(b'IHDR', ihdr) + chunk(b'IDAT', raw) + chunk(b'IEND', b''))

    config = DevSandboxConfig(mode=DevMode.UNSTRUCTURED)
    session = sandbox.create_session(config)
    result = await sandbox.upload_data(session.session_id, str(img_path))
    assert result.success is True


@pytest.mark.asyncio
async def test_upload_semi_structured_json(sandbox, tmp_dir):
    import json
    json_path = Path(tmp_dir) / "test.json"
    json_path.write_text(json.dumps([{"a": 1}, {"a": 2}]))

    config = DevSandboxConfig(mode=DevMode.SEMI_STRUCTURED)
    session = sandbox.create_session(config)
    result = await sandbox.upload_data(session.session_id, str(json_path))
    assert result.success is True
    assert result.records_processed == 2


@pytest.mark.asyncio
async def test_upload_nonexistent_file(sandbox):
    config = DevSandboxConfig(mode=DevMode.STRUCTURED)
    session = sandbox.create_session(config)
    result = await sandbox.upload_data(session.session_id, "/nonexistent/file.csv")
    assert result.success is False


@pytest.mark.asyncio
async def test_upload_to_nonexistent_session(sandbox, tmp_dir):
    csv_path = Path(tmp_dir) / "test.csv"
    csv_path.write_text("a,b\n1,2\n")
    result = await sandbox.upload_data("nonexistent", str(csv_path))
    assert result.success is False


# ── Execute code ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_in_session(sandbox):
    config = DevSandboxConfig(mode=DevMode.STRUCTURED)
    session = sandbox.create_session(config)
    result = await sandbox.execute_code(session.session_id, "print('hello')")
    assert "output" in result


@pytest.mark.asyncio
async def test_execute_in_nonexistent_session(sandbox):
    result = await sandbox.execute_code("nonexistent", "print('hello')")
    assert result["exit_code"] == -1


# ── Dev Tools ────────────────────────────────────────────────────────────────

def test_sql_template():
    template = StructuredDevTools.generate_sql_template("users", [
        {"name": "id", "type": "INT"},
        {"name": "name", "type": "TEXT"},
    ])
    assert "users" in template
    assert "id" in template
    assert "name" in template


def test_pandas_template():
    template = StructuredDevTools.generate_pandas_template("/data/test.csv")
    assert "pandas" in template
    assert "/data/test.csv" in template


def test_image_batch_template():
    template = UnstructuredDevTools.generate_image_batch_template("/in", "/out")
    assert "/in" in template
    assert "/out" in template


def test_json_etl_template():
    template = SemiStructuredDevTools.generate_json_etl_template("/in.json", "/out.json")
    assert "/in.json" in template
    assert "/out.json" in template


def test_csv_transform_template():
    template = SemiStructuredDevTools.generate_csv_transform_template("/in.csv", "/out.csv")
    assert "/in.csv" in template
    assert "/out.csv" in template
