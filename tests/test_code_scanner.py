"""Tests for code scanner — AST-based static analysis."""
import pytest
from app.services.code_scanner import CodeScanner, ScanResult, SandboxMode


scanner = CodeScanner()


# ── Python scanning ────────────────────────────────────────────────

def test_clean_python():
    code = """
import numpy as np
import pandas as pd

df = pd.DataFrame({"a": [1, 2, 3]})
result = df.describe()
"""
    result = scanner.scan(code, "python", "structured_query")
    assert result.passed is True


def test_forbidden_import_os():
    code = "import os\nos.system('rm -rf /')"
    result = scanner.scan(code, "python", "structured_query")
    assert result.passed is False
    assert any(i.code == "NOT_WHITELISTED" and "os" in i.message for i in result.issues)


def test_forbidden_import_subprocess():
    code = "from subprocess import Popen"
    result = scanner.scan(code, "python", "structured_query")
    assert result.passed is False
    assert any(i.code == "NOT_WHITELISTED" for i in result.issues)


def test_forbidden_import_network():
    code = "import requests\nrequests.get('http://evil.com')"
    result = scanner.scan(code, "python", "structured_query")
    assert result.passed is False
    assert any(i.code == "NOT_WHITELISTED" and "requests" in i.message for i in result.issues)


def test_unknown_import():
    code = "import some_random_lib"
    result = scanner.scan(code, "python", "structured_query")
    assert result.passed is False
    assert any(i.code == "NOT_WHITELISTED" for i in result.issues)


def test_dangerous_builtin_exec():
    code = "exec('print(1)')"
    result = scanner.scan(code, "python", "structured_query")
    assert result.passed is False
    assert any(i.code == "DANGEROUS_BUILTIN" and "exec" in i.message for i in result.issues)


def test_dangerous_builtin_eval():
    code = "x = eval('1+1')"
    result = scanner.scan(code, "python", "structured_query")
    assert result.passed is False
    assert any(i.code == "DANGEROUS_BUILTIN" and "eval" in i.message for i in result.issues)


def test_dangerous_builtin_open():
    code = "f = open('/etc/passwd')"
    result = scanner.scan(code, "python", "structured_query")
    assert result.passed is False
    assert any(i.code == "DANGEROUS_BUILTIN" and "open" in i.message for i in result.issues)


def test_dangerous_dunder_import():
    code = "__import__('os')"
    result = scanner.scan(code, "python", "structured_query")
    assert result.passed is False
    assert any(i.code == "DANGEROUS_BUILTIN" for i in result.issues)


def test_subclass_escape():
    code = "[c.__name__ for c in object.__subclasses__()]"
    result = scanner.scan(code, "python", "structured_query")
    assert result.passed is False
    assert any(i.code == "DANGEROUS_ATTR" for i in result.issues)


def test_syntax_error():
    code = "def foo(\n    broken"
    result = scanner.scan(code, "python", "structured_query")
    assert result.passed is False
    assert any("Syntax error" in i.message for i in result.issues)


def test_allowed_standard_lib():
    code = """
import json, math, datetime, uuid, hashlib
from collections import Counter
from pathlib import Path
"""
    result = scanner.scan(code, "python", "structured_query")
    assert result.passed is True


def test_allowed_ml_libs():
    code = """
import numpy as np
import torch
from transformers import AutoModel
import sklearn
"""
    result = scanner.scan(code, "python", "structured_query")
    assert result.passed is True


def test_training_mode_valid_save_path():
    code = 'model.save_pretrained("/sandbox/output/model/")'
    result = scanner.scan(code, "python", "llm_sft")
    assert result.passed is True


def test_training_mode_invalid_save_path():
    code = 'model.save_pretrained("/tmp/evil/")'
    result = scanner.scan(code, "python", "llm_sft")
    assert result.passed is False
    assert any(i.code == "INVALID_SAVE_PATH" for i in result.issues)


def test_training_mode_save_to_checkpoint():
    code = 'model.save_pretrained("/sandbox/checkpoints/epoch_1/")'
    result = scanner.scan(code, "python", "llm_sft")
    assert result.passed is True


def test_unknown_sandbox_mode():
    code = "x = 1"
    result = scanner.scan(code, "python", "invalid_mode")
    assert result.passed is False
    assert any("Unknown sandbox mode" in i.message for i in result.issues)


def test_unsupported_language():
    code = "SELECT 1"
    result = scanner.scan(code, "rust", "structured_query")
    assert result.passed is False
    assert any("Unsupported language" in i.message for i in result.issues)


def test_multiple_issues():
    code = """
import os
import requests
exec('bad')
eval('worse')
"""
    result = scanner.scan(code, "python", "structured_query")
    assert result.passed is False
    codes = {i.code for i in result.issues}
    assert "NOT_WHITELISTED" in codes
    assert "DANGEROUS_BUILTIN" in codes


# ── SQL scanning ───────────────────────────────────────────────────

def test_sql_select_allowed():
    result = scanner.scan("SELECT * FROM users", "sql", "structured_query")
    assert result.passed is True


def test_sql_with_cte():
    result = scanner.scan("WITH cte AS (SELECT 1) SELECT * FROM cte", "sql", "structured_query")
    assert result.passed is True


def test_sql_insert_forbidden_in_query_mode():
    result = scanner.scan("INSERT INTO users VALUES (1)", "sql", "structured_query")
    assert result.passed is False
    assert any(i.code == "SQL_FORBIDDEN_STMT" for i in result.issues)


def test_sql_delete_forbidden_in_query_mode():
    result = scanner.scan("DELETE FROM users", "sql", "structured_query")
    assert result.passed is False


def test_sql_create_allowed_in_modeling():
    result = scanner.scan("CREATE TABLE tmp AS SELECT 1", "sql", "structured_modeling")
    assert result.passed is True


def test_sql_insert_allowed_in_modeling():
    result = scanner.scan("INSERT INTO tmp VALUES (1)", "sql", "structured_modeling")
    assert result.passed is True


def test_sql_into_outfile_forbidden():
    result = scanner.scan("SELECT * INTO OUTFILE '/tmp/out.csv' FROM users", "sql", "structured_query")
    assert result.passed is False
    assert any(i.code == "SQL_FILE_IO" for i in result.issues)


def test_sql_load_data_forbidden():
    result = scanner.scan("LOAD DATA INFILE '/tmp/data.csv' INTO TABLE users", "sql", "structured_query")
    assert result.passed is False


def test_sql_catalog_access_forbidden():
    result = scanner.scan("SELECT * FROM pg_tables", "sql", "structured_query")
    assert result.passed is False
    assert any(i.code == "SQL_CATALOG_ACCESS" for i in result.issues)


def test_sql_multiple_statements():
    result = scanner.scan("SELECT 1; DROP TABLE users", "sql", "structured_query")
    assert result.passed is False


def test_sql_explain_allowed():
    result = scanner.scan("EXPLAIN SELECT * FROM users", "sql", "structured_query")
    assert result.passed is True
