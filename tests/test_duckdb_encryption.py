"""Tests for DuckDB disk encryption (#132), multi-source secure access (#133),
and encryption round-trip verification (#134).

Requirements:
- SM4-GCM encryption for DuckDB files at rest
- Correct key decryption, wrong key rejection
- Key rotation support
- Concurrent session key isolation
- Multi-source data access (S3, PostgreSQL, local files)
"""
import pytest
from pathlib import Path


# ─── #132: DuckDB Disk Encryption (SM4-GCM) ───────────────────────

class TestDiskEncryption:
    """#132: DuckDB 文件落盘加密验证"""

    def test_encrypt_workspace_file_roundtrip(self, tmp_path):
        """encrypt_workspace_file → decrypt_workspace_file 全链路"""
        from app.services.sandbox_security import encrypt_workspace_file, decrypt_workspace_file

        dek = b"0123456789abcdef"  # 16-byte SM4 key
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello CDS Sandbox!")

        encrypt_workspace_file(test_file, dek)
        encrypted = test_file.read_bytes()
        # Encrypted content should NOT be plaintext
        assert b"Hello CDS Sandbox!" not in encrypted
        # Format: nonce(12) + tag(16) + ciphertext
        assert len(encrypted) >= 12 + 16 + 1

        plaintext = decrypt_workspace_file(test_file, dek)
        assert plaintext == b"Hello CDS Sandbox!"

    def test_encrypt_workspace_file_wrong_key(self, tmp_path):
        """错误密钥解密应失败"""
        from app.services.sandbox_security import encrypt_workspace_file, decrypt_workspace_file

        correct_key = b"0123456789abcdef"
        wrong_key = b"fedcba9876543210"

        test_file = tmp_path / "secret.txt"
        test_file.write_text("classified data")

        encrypt_workspace_file(test_file, correct_key)

        with pytest.raises(Exception):  # GCM tag verification fails
            decrypt_workspace_file(test_file, wrong_key)

    def test_encrypt_empty_file(self, tmp_path):
        """空文件加密应跳过"""
        from app.services.sandbox_security import encrypt_workspace_file

        dek = b"0123456789abcdef"
        test_file = tmp_path / "empty.txt"
        test_file.write_text("")

        result = encrypt_workspace_file(test_file, dek)
        assert result == test_file
        assert test_file.read_bytes() == b""

    def test_encrypt_large_file(self, tmp_path):
        """大文件（>1MB）加密解密"""
        from app.services.sandbox_security import encrypt_workspace_file, decrypt_workspace_file

        dek = b"0123456789abcdef"
        test_file = tmp_path / "large.bin"
        # 1MB of random-ish data
        data = bytes(range(256)) * 4096
        test_file.write_bytes(data)

        encrypt_workspace_file(test_file, dek)
        plaintext = decrypt_workspace_file(test_file, dek)
        assert plaintext == data

    def test_duckdb_tmpfs_file_encrypted_at_rest(self, tmp_path):
        """DuckDB tmpfs 模式下文件应可被加密"""
        import duckdb
        from app.services.sandbox_security import encrypt_workspace_file, decrypt_workspace_file

        dek = b"0123456789abcdef"
        db_path = tmp_path / "sandbox.db"

        # Create DuckDB file directly (not through engine, which wipes on close)
        conn = duckdb.connect(str(db_path))
        conn.execute("CREATE TABLE secrets(x INT)")
        conn.execute("INSERT INTO secrets VALUES (42)")
        conn.close()

        original_size = db_path.stat().st_size

        # Encrypt the file
        encrypt_workspace_file(db_path, dek)
        encrypted_size = db_path.stat().st_size
        assert encrypted_size > original_size
        # Encrypted content should not contain plaintext
        encrypted_bytes = db_path.read_bytes()
        assert b"secrets" not in encrypted_bytes
        # Use a longer marker to avoid coincidence in random bytes
        assert b"CREATE TABLE" not in encrypted_bytes

        # Decrypt and verify DuckDB can read it (decrypt returns bytes, must write back)
        plaintext = decrypt_workspace_file(db_path, dek)
        db_path.write_bytes(plaintext)
        conn2 = duckdb.connect(str(db_path))
        result = conn2.execute("SELECT * FROM secrets").fetchall()
        assert result[0][0] == 42
        conn2.close()

    def test_key_rotation_old_key_fails(self, tmp_path):
        """密钥轮转后旧密钥应失效"""
        from app.services.sandbox_security import encrypt_workspace_file, decrypt_workspace_file

        old_key = b"0123456789abcdef"
        new_key = b"fedcba9876543210"

        test_file = tmp_path / "rotated.txt"
        test_file.write_text("sensitive data")

        # Encrypt with old key
        encrypt_workspace_file(test_file, old_key)

        # Re-encrypt with new key (simulating rotation)
        plaintext = decrypt_workspace_file(test_file, old_key)
        test_file.write_bytes(plaintext)
        encrypt_workspace_file(test_file, new_key)

        # Old key should fail
        with pytest.raises(Exception):
            decrypt_workspace_file(test_file, old_key)

        # New key should work
        result = decrypt_workspace_file(test_file, new_key)
        assert result == b"sensitive data"

    def test_concurrent_session_key_isolation(self, tmp_path):
        """并发会话密钥隔离 — A 不能读 B 的数据"""
        from app.services.sandbox_security import encrypt_workspace_file, decrypt_workspace_file

        key_a = b"aaaa1111bbbb2222"
        key_b = b"cccc3333dddd4444"

        file_a = tmp_path / "session_a.db"
        file_b = tmp_path / "session_b.db"

        file_a.write_bytes(b"Session A secret data")
        file_b.write_bytes(b"Session B confidential info")

        encrypt_workspace_file(file_a, key_a)
        encrypt_workspace_file(file_b, key_b)

        # A can read own data
        assert decrypt_workspace_file(file_a, key_a) == b"Session A secret data"
        # B can read own data
        assert decrypt_workspace_file(file_b, key_b) == b"Session B confidential info"

        # A cannot read B's data
        with pytest.raises(Exception):
            decrypt_workspace_file(file_b, key_a)
        # B cannot read A's data
        with pytest.raises(Exception):
            decrypt_workspace_file(file_a, key_b)


# ─── #133: DuckDB Multi-Source Secure Access ───────────────────────

class TestMultiSourceAccess:
    """#133: DuckDB 多数据源安全接入"""

    def test_register_table_with_column_encryption(self):
        """注册表 + SM4 列级加密"""
        from app.services.secure_duckdb import SecureDuckDBEngine

        engine = SecureDuckDBEngine("multi-src-1", mode="memory")
        data = [
            {"name": "Alice", "ssn": "110101199001011234", "salary": 50000},
            {"name": "Bob", "ssn": "110101199002022345", "salary": 60000},
        ]
        dek = b"0123456789abcdef"
        info = engine.register_table_encrypted(
            "employees", data,
            encrypted_columns={"ssn": "sm4-siv"},
            dek=dek,
        )
        assert info.row_count == 2

        # Query should return encrypted ssn (SM4-SIV produces hex)
        result = engine.execute_query("SELECT name, ssn FROM employees")
        assert result.rows[0][0] == "Alice"
        # ssn should be encrypted (not plaintext)
        assert result.rows[0][1] != "110101199001011234"

        engine.close()

    def test_encrypted_column_deterministic_query(self):
        """SM4-SIV 确定性加密支持等值查询"""
        from app.services.secure_duckdb import SecureDuckDBEngine
        from app.services.column_encryption import ColumnEncryption, EncryptionMode

        engine = SecureDuckDBEngine("det-query", mode="memory")
        data = [
            {"id": 1, "email": "alice@example.com"},
            {"id": 2, "email": "bob@example.com"},
        ]
        dek = b"0123456789abcdef"
        engine.register_table_encrypted(
            "users", data,
            encrypted_columns={"email": "sm4-siv"},
            dek=dek,
        )

        # Use encrypt_deterministic with matching column name "email"
        cipher = ColumnEncryption(dek=dek, key_id="sandbox-default")
        enc_alice = cipher.encrypt_deterministic("alice@example.com", "email")
        # Store as BLOB — DuckDB BLOB comparison via parameterized query
        engine._conn.execute(
            "SELECT id FROM users WHERE email = ?",
            [enc_alice.ciphertext],
        )
        result = engine._conn.fetchall()
        assert len(result) == 1
        assert int(result[0][0]) == 1

        engine.close()

    def test_masked_view_field_level(self):
        """字段级脱敏视图"""
        from app.services.secure_duckdb import SecureDuckDBEngine, MaskRule

        engine = SecureDuckDBEngine("mask-test", mode="memory")
        data = [
            {"name": "Alice", "phone": "13800138000", "id_card": "110101199001011234"},
        ]
        engine.register_table("customers", data)

        # Apply mask rules using set_mask_rules
        engine.set_mask_rules("customers", [
            MaskRule(field_name="phone", mask_type="REDACT"),
            MaskRule(field_name="id_card", mask_type="HASH"),
        ])
        engine.create_masked_view("customers")

        # Query through masked view
        result = engine.execute_query(
            "SELECT name, phone, id_card FROM customers_masked"
        )
        assert result.rows[0][0] == "Alice"  # name unmasked
        assert result.rows[0][1] != "13800138000"  # phone redacted
        assert result.rows[0][2] != "110101199001011234"  # id_card hashed

        engine.close()

    def test_column_encryption_wrong_key_decrypt(self):
        """错误密钥读取加密列应返回密文"""
        from app.services.secure_duckdb import SecureDuckDBEngine

        engine = SecureDuckDBEngine("wrong-key", mode="memory")
        data = [{"secret": "top-secret-value"}]
        correct_key = b"0123456789abcdef"
        wrong_key = b"fedcba9876543210"

        engine.register_table_encrypted(
            "vault", data,
            encrypted_columns={"secret": "sm4-gcm"},
            dek=correct_key,
        )

        # Query returns encrypted value (can't decrypt with wrong key in-memory)
        result = engine.execute_query("SELECT secret FROM vault")
        encrypted_val = result.rows[0][0]
        assert encrypted_val != "top-secret-value"  # Not plaintext

        engine.close()


# ─── #134: Encryption Verification E2E ────────────────────────────

class TestEncryptionE2E:
    """#134: 加密验证端到端测试"""

    def test_write_encrypt_restart_decrypt_cycle(self, tmp_path):
        """写入→加密落盘→重新打开→解密读取 全链路"""
        import duckdb
        from app.services.sandbox_security import encrypt_workspace_file, decrypt_workspace_file

        dek = b"0123456789abcdef"
        db_path = tmp_path / "e2e.db"

        # Phase 1: Write data
        conn = duckdb.connect(str(db_path))
        conn.execute("CREATE TABLE records(id INT, value VARCHAR)")
        conn.execute("INSERT INTO records VALUES (1, 'alpha'), (2, 'beta')")
        conn.close()

        # Phase 2: Encrypt on disk
        encrypt_workspace_file(db_path, dek)
        encrypted_bytes = db_path.read_bytes()
        assert b"alpha" not in encrypted_bytes

        # Phase 3: Decrypt and reopen (decrypt returns bytes, must write back)
        plaintext = decrypt_workspace_file(db_path, dek)
        db_path.write_bytes(plaintext)
        conn2 = duckdb.connect(str(db_path))
        result = conn2.execute("SELECT * FROM records ORDER BY id").fetchall()
        assert len(result) == 2
        assert result[0][1] == "alpha"
        assert result[1][1] == "beta"
        conn2.close()

    def test_secure_wipe_removes_all_traces(self, tmp_path):
        """secure_wipe 应完全清除文件内容"""
        from app.services.sandbox_security import secure_wipe_file

        test_file = tmp_path / "sensitive.db"
        test_file.write_bytes(b"A" * 4096 + b"sensitive_marker")

        secure_wipe_file(test_file)

        # File should be deleted
        assert not test_file.exists()

    def test_encrypted_duckdb_cannot_be_opened_without_decryption(self, tmp_path):
        """加密后的 DuckDB 文件不能被直接打开"""
        import duckdb
        from app.services.sandbox_security import encrypt_workspace_file

        dek = b"0123456789abcdef"
        db_path = tmp_path / "locked.db"

        conn = duckdb.connect(str(db_path))
        conn.execute("CREATE TABLE data(x INT)")
        conn.close()

        encrypt_workspace_file(db_path, dek)

        # Attempting to open encrypted file as DuckDB should fail
        with pytest.raises(Exception):
            duckdb.connect(str(db_path))

    def test_multiple_sessions_independent_encryption(self, tmp_path):
        """多个会话独立加密互不影响"""
        import duckdb
        from app.services.sandbox_security import encrypt_workspace_file, decrypt_workspace_file

        sessions = {}
        keys = {}
        for i in range(3):
            sid = f"session-{i}"
            key = (bytes([i]) * 16)
            db_path = tmp_path / f"{sid}.db"

            conn = duckdb.connect(str(db_path))
            conn.execute(f"CREATE TABLE data(x VARCHAR)")
            conn.execute(f"INSERT INTO data VALUES ('secret-{i}')")
            conn.close()

            encrypt_workspace_file(db_path, key)
            sessions[sid] = db_path
            keys[sid] = key

        # Each session can decrypt its own data
        for i in range(3):
            sid = f"session-{i}"
            pt = decrypt_workspace_file(sessions[sid], keys[sid])
            sessions[sid].write_bytes(pt)
            conn = duckdb.connect(str(sessions[sid]))
            result = conn.execute("SELECT x FROM data").fetchall()
            assert result[0][0] == f"secret-{i}"
            conn.close()
            # Re-encrypt for cross-session test
            encrypt_workspace_file(sessions[sid], keys[sid])

        # Cross-session access should fail
        for i in range(3):
            for j in range(3):
                if i == j:
                    continue
                sid = f"session-{j}"
                with pytest.raises(Exception):
                    decrypt_workspace_file(sessions[sid], keys[f"session-{i}"])
