"""W19: egress audit JSONL encryption at rest (SM4-GCM)."""
import json
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.services import egress_audit as ea


@pytest.fixture(autouse=True)
def _crypto_settings(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "EGRESS_AUDIT_ENABLED", True)
    monkeypatch.setattr(settings, "EGRESS_AUDIT_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(settings, "AUDIT_ENCRYPTION_KEY", "test-master-secret-w19")
    monkeypatch.setattr(settings, "EGRESS_AUDIT_LOG_PATH", str(tmp_path / "enc" / "access.jsonl"))
    ea._queue = None
    ea._writer_task = None
    ea._dropped_count = 0
    yield settings


async def _flush():
    await ea.shutdown_egress_audit()
    ea._queue = None
    ea._writer_task = None


@pytest.mark.asyncio
async def test_encrypted_at_rest_and_transparent_read(_crypto_settings):
    ea.log_egress_event(
        session_id="sess-enc", event_type="access", host="secret-host.example",
        path="/v1/x?token=TOPSECRET", verdict="allow",
    )
    await _flush()

    raw = Path(_crypto_settings.EGRESS_AUDIT_LOG_PATH).read_text().strip()
    assert raw and not raw.startswith("{")  # not plaintext JSON
    assert "secret-host.example" not in raw
    assert "TOPSECRET" not in raw

    records = ea.read_egress_records("sess-enc")
    assert len(records) == 1
    assert records[0]["host"] == "secret-host.example"
    assert records[0]["path"] == "/v1/x?token=***"


@pytest.mark.asyncio
async def test_wrong_key_skips_records_without_crash(_crypto_settings, monkeypatch):
    ea.log_egress_event(session_id="s1", event_type="access", verdict="allow")
    await _flush()

    def _bad_cipher():
        from app.utils.crypto import SM4Cipher

        return SM4Cipher(key=b"\x01" * 32)

    monkeypatch.setattr(ea, "_audit_cipher", _bad_cipher)
    assert ea.read_egress_records("s1") == []


@pytest.mark.asyncio
async def test_plaintext_fallback_when_encryption_disabled(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "EGRESS_AUDIT_ENABLED", True)
    monkeypatch.setattr(settings, "EGRESS_AUDIT_ENCRYPTION_ENABLED", False)
    monkeypatch.setattr(settings, "EGRESS_AUDIT_LOG_PATH", str(tmp_path / "plain" / "access.jsonl"))
    ea._queue = None
    ea._writer_task = None

    ea.log_egress_event(session_id="s2", event_type="access", verdict="allow", host="plain.example")
    await _flush()

    raw = Path(settings.EGRESS_AUDIT_LOG_PATH).read_text().strip()
    assert json.loads(raw)["host"] == "plain.example"
    assert ea.read_egress_records("s2")[0]["host"] == "plain.example"


def test_line_roundtrip_and_tamper_detection():
    from app.utils.crypto import SM4Cipher

    cipher = SM4Cipher(key=b"k" * 32)
    blob = ea.encrypt_line('{"a": 1}', cipher)
    assert ea.decrypt_line(blob, cipher) == '{"a": 1}'

    tampered = bytearray(bytearray.fromhex(blob.encode().hex()))
    raw = bytearray(__import__("base64").b64decode(blob))
    raw[-1] ^= 0xFF
    with pytest.raises(ValueError):
        ea.decrypt_line(__import__("base64").b64encode(bytes(raw)).decode(), cipher)
