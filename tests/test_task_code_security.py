"""Tests for encrypted sandbox task code persistence."""

import pytest

from app.services.task_code_security import decrypt_task_code, encrypt_task_code


def test_task_code_encrypts_and_decrypts():
    code = "print('secret analysis')"
    stored = encrypt_task_code(code, scope="unit-test")

    assert stored is not None
    assert stored.startswith("kms:v1:")
    assert code not in stored
    assert decrypt_task_code(stored) == code


def test_legacy_plaintext_task_code_still_supported():
    assert decrypt_task_code("print(1)") == "print(1)"


def test_tampered_task_code_fails_closed():
    stored = encrypt_task_code("print(1)", scope="unit-test")
    tampered = stored[:-2] + "AA"

    with pytest.raises(Exception):
        decrypt_task_code(tampered)
