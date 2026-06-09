"""Encryption helpers for persisted sandbox task code."""
from __future__ import annotations

import base64

_PREFIX = "kms:v1:"


def encrypt_task_code(code: str | None, *, scope: str) -> str | None:
    """Encrypt task source code before DB persistence."""
    if code is None:
        return None
    from app.services.kms_service import kms_service

    key = kms_service.generate_data_key(f"task-code-{scope}")
    ciphertext = kms_service.encrypt_with_key(key["key_id"], code.encode("utf-8"))
    encoded = base64.b64encode(ciphertext).decode("ascii")
    return f"{_PREFIX}{key['key_id']}:{encoded}"


def decrypt_task_code(stored_code: str | None) -> str:
    """Decrypt persisted task code, accepting legacy plaintext values."""
    if not stored_code:
        return ""
    if not stored_code.startswith(_PREFIX):
        return stored_code

    from app.services.kms_service import kms_service

    body = stored_code[len(_PREFIX):]
    key_id, encoded = body.split(":", 1)
    ciphertext = base64.b64decode(encoded.encode("ascii"))
    plaintext = kms_service.decrypt_with_key(key_id, ciphertext)
    return plaintext.decode("utf-8")
