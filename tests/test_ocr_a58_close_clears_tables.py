"""A58 — `SecureDuckDBEngine.close()` must release table metadata and table DEKs.

`close()` used to close the DuckDB connection (and do the tmpfs encrypt-and-wipe
dance) but left `self._tables` / `self._table_deks` / `self._mask_rules` attached to
the engine object.  In `mode="memory"` (the mode the session engines actually use)
that means plaintext table metadata and the per-table DEKs stay alive for as long as
any other reference to the engine keeps it reachable — which is exactly the retention
window A48's fire-and-forget policy-attach closure used to create.
"""

from __future__ import annotations

import pytest

from app.services.secure_duckdb import MaskRule, SecureDuckDBEngine

pytest.importorskip("duckdb")


ROWS = [{"name": "alice", "ssn": "111-22-3333"}, {"name": "bob", "ssn": "444-55-6666"}]


def _build_engine(session_id: str = "a58-close") -> SecureDuckDBEngine:
    engine = SecureDuckDBEngine(session_id, mode="memory")
    engine.register_table("plain_tbl", ROWS)
    engine.register_table_encrypted(
        "enc_tbl", ROWS, encrypted_columns={"ssn": "sm4-gcm"}
    )
    engine.set_mask_rules("plain_tbl", [MaskRule(field_name="name", mask_type="REDACT")])
    engine.create_masked_view("plain_tbl")
    return engine


def test_close_clears_table_registry_and_deks():
    """A58: after close(), no table metadata and no table DEK survive on the engine."""
    engine = _build_engine()

    assert {t.table_name for t in engine.list_tables()} == {"plain_tbl", "enc_tbl"}
    dek_before = engine._table_deks["enc_tbl"][0]
    assert isinstance(dek_before, bytes) and len(dek_before) == 16
    assert engine._mask_rules

    engine.close()

    assert engine._tables == {}
    assert engine.list_tables() == []
    assert engine.get_table_info("plain_tbl") is None
    assert getattr(engine, "_table_deks", {}) == {}
    assert engine._mask_rules == {}


def test_close_is_idempotent_and_still_usable_afterwards():
    """A58: the explicit wipe must not make a second close() raise."""
    engine = _build_engine("a58-close-twice")
    engine.close()
    engine.close()
    assert engine.list_tables() == []


def test_close_still_wipes_tmpfs_plaintext(tmp_path):
    """Regression guard: the A58 wipe must not remove the existing tmpfs hygiene."""
    from app.services.secure_duckdb import _encrypt_db_file  # noqa: F401  (import path check)

    db_path = tmp_path / "sandbox.db"
    engine = SecureDuckDBEngine("a58-tmpfs", mode="tmpfs", db_path=str(db_path),
                                dek=b"0123456789abcdef")
    engine.register_table("t", ROWS)
    engine.close()

    assert db_path.with_suffix(".db.enc").exists()
    assert engine._tables == {}
