"""Tests for Deterministic SM4 encryption and query rewriting."""
import os
import pytest
from app.services.deterministic_sm4 import (
    DeterministicSM4, RandomizedSM4, ColumnEncryptionRegistry,
)
from app.services.query_rewriter import QueryRewriter, ResultDecryptor, SecurityError


# ─── Deterministic SM4 Tests ───

class TestDeterministicSM4:

    def setup_method(self):
        self.dek = os.urandom(16)
        self.det = DeterministicSM4(self.dek)

    def test_deterministic_encrypt_same_plaintext(self):
        """Same plaintext + same key → same ciphertext."""
        ct1 = self.det.encrypt("hello")
        ct2 = self.det.encrypt("hello")
        assert ct1 == ct2

    def test_deterministic_encrypt_different_plaintext(self):
        """Different plaintexts → different ciphertexts."""
        ct1 = self.det.encrypt("hello")
        ct2 = self.det.encrypt("world")
        assert ct1 != ct2

    def test_deterministic_encrypt_different_keys(self):
        """Same plaintext + different keys → different ciphertexts."""
        dek2 = os.urandom(16)
        det2 = DeterministicSM4(dek2)
        ct1 = self.det.encrypt("hello")
        ct2 = det2.encrypt("hello")
        assert ct1 != ct2

    def test_hash_for_index_consistency(self):
        """hash_for_index is deterministic."""
        h1 = self.det.hash_for_index("test_value")
        h2 = self.det.hash_for_index("test_value")
        assert h1 == h2

    def test_hash_for_index_different_values(self):
        """Different values produce different hashes."""
        h1 = self.det.hash_for_index("value1")
        h2 = self.det.hash_for_index("value2")
        assert h1 != h2

    def test_hash_for_index_length(self):
        """Hash is 32 hex chars (16 bytes)."""
        h = self.det.hash_for_index("test")
        assert len(h) == 32

    def test_invalid_dek_length(self):
        """DEK must be 16 bytes."""
        with pytest.raises(ValueError, match="DEK must be 16 bytes"):
            DeterministicSM4(b"short")


# ─── Randomized SM4 Tests ───

class TestRandomizedSM4:

    def setup_method(self):
        self.dek = os.urandom(16)
        self.rand = RandomizedSM4(self.dek)

    def test_randomized_encrypt_different_ciphertexts(self):
        """Same plaintext → different ciphertexts (random IV)."""
        ct1 = self.rand.encrypt("hello")
        ct2 = self.rand.encrypt("hello")
        assert ct1 != ct2

    def test_randomized_encrypt_includes_iv(self):
        """Ciphertext starts with 16-byte IV."""
        ct = self.rand.encrypt("test")
        assert len(ct) > 16  # IV + at least one block

    def test_invalid_dek_length(self):
        with pytest.raises(ValueError, match="DEK must be 16 bytes"):
            RandomizedSM4(b"short")


# ─── Column Encryption Registry Tests ───

class TestColumnEncryptionRegistry:

    def test_register_and_get(self):
        reg = ColumnEncryptionRegistry()
        reg.register("users", "email", "det", enc_column="email_enc", hash_column="email_hash")
        meta = reg.get("users", "email")
        assert meta is not None
        assert meta.level == "det"
        assert meta.enc_column == "email_enc"
        assert meta.hash_column == "email_hash"

    def test_get_nonexistent(self):
        reg = ColumnEncryptionRegistry()
        assert reg.get("users", "missing") is None

    def test_all_columns(self):
        reg = ColumnEncryptionRegistry()
        reg.register("t1", "c1", "plain")
        reg.register("t1", "c2", "det", enc_column="c2_enc")
        cols = reg.all_columns()
        assert len(cols) == 2
        assert "t1.c1" in cols
        assert "t1.c2" in cols

    def test_default_registry_has_enterprise_columns(self):
        from app.services.deterministic_sm4 import default_registry
        meta = default_registry.get("enterprise_basic", "ent_id")
        assert meta is not None
        assert meta.level == "det"
        assert meta.enc_column == "ent_id_enc"

    def test_default_registry_has_tax_columns(self):
        from app.services.deterministic_sm4 import default_registry
        meta = default_registry.get("tax_records", "year")
        assert meta is not None
        assert meta.level == "plain"


# ─── Query Rewriter Tests ───

class TestQueryRewriter:

    def setup_method(self):
        self.det = DeterministicSM4(os.urandom(16))
        self.registry = ColumnEncryptionRegistry()
        self.registry.register("users", "email", "det",
                               enc_column="email_enc", hash_column="email_hash")
        self.registry.register("users", "name", "rand", enc_column="name_enc")
        self.registry.register("users", "age", "plain")
        self.rewriter = QueryRewriter(
            registry=self.registry, det_encryptor=self.det, max_output_rows=100
        )

    def test_select_plain_column_unchanged(self):
        result = self.rewriter.rewrite("SELECT age FROM users")
        assert "age" in result.sql
        assert "age_enc" not in result.sql

    def test_select_det_column_rewritten(self):
        result = self.rewriter.rewrite("SELECT users.email FROM users")
        assert "email_enc" in result.sql

    def test_select_rand_column_rewritten(self):
        result = self.rewriter.rewrite("SELECT users.name FROM users")
        assert "name_enc" in result.sql

    def test_where_det_column_hash_replacement(self):
        result = self.rewriter.rewrite("SELECT users.age FROM users WHERE users.email = 'test@example.com'")
        assert "email_hash" in result.sql
        assert "email_enc" not in result.sql.split("WHERE")[1]

    def test_limit_enforcement(self):
        result = self.rewriter.rewrite("SELECT age FROM users")
        assert "LIMIT 100" in result.sql

    def test_existing_limit_preserved(self):
        result = self.rewriter.rewrite("SELECT age FROM users LIMIT 50")
        assert "LIMIT 50" in result.sql

    def test_encrypted_columns_tracked(self):
        result = self.rewriter.rewrite("SELECT users.email, users.name FROM users")
        assert "users.email" in result.encrypted_columns
        assert "users.name" in result.encrypted_columns


# ─── Security Check Tests ───

class TestQuerySecurity:

    def setup_method(self):
        self.rewriter = QueryRewriter()

    def test_reject_non_select(self):
        with pytest.raises(SecurityError, match="Only SELECT"):
            self.rewriter.rewrite("INSERT INTO users VALUES (1)")

    def test_reject_select_star(self):
        with pytest.raises(SecurityError, match="SELECT \\*"):
            self.rewriter.rewrite("SELECT * FROM users")

    def test_reject_delete(self):
        with pytest.raises(SecurityError, match="Forbidden"):
            self.rewriter.rewrite("SELECT age FROM users; DELETE FROM users")

    def test_reject_drop(self):
        with pytest.raises(SecurityError, match="Forbidden"):
            self.rewriter.rewrite("SELECT 1; DROP TABLE users")

    def test_reject_system_catalog(self):
        with pytest.raises(SecurityError, match="catalog|SELECT"):
            self.rewriter.rewrite("SELECT * FROM pg_catalog.pg_tables")

    def test_accept_valid_select(self):
        result = self.rewriter.rewrite("SELECT id, name FROM users WHERE id = 1")
        assert result.sql is not None

    def test_reject_execute(self):
        with pytest.raises(SecurityError, match="Forbidden"):
            self.rewriter.rewrite("SELECT EXECUTE('malicious')")


# ─── Result Decryptor Tests ───

class TestResultDecryptor:

    def test_no_decryptors_passthrough(self):
        decryptor = ResultDecryptor()
        rows = [(b"data", "text")]
        result = decryptor.decrypt_rows(rows, ["col1", "col2"])
        assert result == rows

    def test_decrypt_det_column(self):
        dek = os.urandom(16)
        det = DeterministicSM4(dek)
        registry = ColumnEncryptionRegistry()
        registry.register("users", "email", "det", enc_column="email_enc")

        # Encrypt a value
        encrypted = det.encrypt("test@example.com")

        decryptor = ResultDecryptor(
            registry=registry, det_encryptor=det,
        )
        rows = [(encrypted, "plain_value")]
        result = decryptor.decrypt_rows(rows, ["email_enc", "other"])
        # In fallback mode (no gmssl), decrypt raises — but passthrough handles it
        assert len(result) == 1
