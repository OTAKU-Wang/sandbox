"""A98 — 「白名单即闸门」的自述不成立：白名单自带文件/外部数据源能力必须被披露。

条目原文（docs/ocr-review-2026-10-18/ocr-verified-findings.md ### A98）指出
ALLOWED_IMPORTS 含 io/pathlib/pyarrow/duckdb/sqlalchemy/PIL/cv2 ⇒ 不调 `open()`
也能读写文件与访问外部数据源，而文件头写着 "the whitelist IS the gate"。
这不是沙箱逃逸（真边界是 bwrap/Cube 的挂载集 + exec 通道注入 + 只回收 HARVEST_DIRS），
所以修法不是把这些库判死（不得强判），而是：
  1) 白名单继续放行（passed 不变），但把「该库自带的能力」作为 WARNING 落进
     扫描结果（task_pipeline / task_worker 会把全部 issues 写进审计）；
  2) 扫描时以 WARNING 日志披露同一事实（sandbox_sessions / connectors 只把
     FATAL 透给调用方，否则这条披露就没有任何出口）。

因此本文件断言的是机器可读的 issue code 与日志记录，不是文案。
"""
import logging

import pytest

from app.services.code_scanner import (
    ALLOWED_IMPORTS,
    ScanSeverity,
    code_scanner,
)

MODE = "structured_query"


def _codes(result):
    return {i.code for i in result.issues}


def _find(result, code):
    return [i for i in result.issues if i.code == code]


def test_capability_carrying_modules_are_actually_whitelisted():
    """前提：这些库确实在白名单里（条目成立的事实基础）。"""
    assert {"pathlib", "pyarrow", "duckdb", "sqlalchemy", "PIL", "cv2"} <= ALLOWED_IMPORTS


def test_pathlib_file_io_is_disclosed_not_silently_allowed():
    """`from pathlib import Path` + read_text 不调 open()，但能力等价于文件读写。"""
    code = (
        "from pathlib import Path\n"
        "p = Path('/sandbox/data/x.csv')\n"
        "text = p.read_text()\n"
    )
    result = code_scanner.scan(code, "python", MODE)
    assert result.passed is True, "披露不等于拒绝：白名单仍然放行"
    hits = _find(result, "FILE_IO_CAPABILITY")
    assert hits, f"pathlib 的文件读写能力未被披露，issues={result.issues}"
    assert hits[0].severity is ScanSeverity.WARNING
    assert "pathlib" in hits[0].message


def test_external_data_source_import_is_disclosed():
    """duckdb / sqlalchemy 在白名单里 = 可从沙箱内连外部数据源。"""
    code = (
        "import duckdb\n"
        "import sqlalchemy\n"
        "con = duckdb.connect('/tmp/scratch.db')\n"
        "engine = sqlalchemy.create_engine('postgresql://u:p@evil.internal/db')\n"
    )
    result = code_scanner.scan(code, "python", MODE)
    assert result.passed is True
    hits = _find(result, "EXTERNAL_DATA_SOURCE")
    disclosed = " ".join(h.message for h in hits)
    assert "duckdb" in disclosed, f"duckdb 外部数据源能力未披露，issues={result.issues}"
    assert "sqlalchemy" in disclosed, f"sqlalchemy 外部数据源能力未披露，issues={result.issues}"
    assert all(h.severity is ScanSeverity.WARNING for h in hits)


def test_capability_is_disclosed_in_logs_for_callers_that_only_surface_fatal():
    """sandbox_sessions/connectors 只把 FATAL 返给调用方 ⇒ 日志是 WARNING 的唯一出口。"""
    code = "import pyarrow.parquet as pq\npq.write_table(t, '/sandbox/output/x.parquet')\n"
    with pytest.MonkeyPatch.context() as mp:
        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Capture()
        logger = logging.getLogger("app.services.code_scanner")
        logger.addHandler(handler)
        try:
            result = code_scanner.scan(code, "python", MODE)
        finally:
            logger.removeHandler(handler)
        mp.undo()

    assert result.passed is True
    assert _find(result, "FILE_IO_CAPABILITY"), result.issues
    disclosed = [r for r in records if r.levelno >= logging.WARNING]
    assert any(
        "FILE_IO_CAPABILITY" in r.getMessage() for r in disclosed
    ), f"扫描器没有把白名单能力披露到日志：{[r.getMessage() for r in disclosed]}"


def test_benign_data_science_code_gets_no_capability_noise():
    """不得强判：普通 numpy/pandas/io(BytesIO) 代码不能被扣上能力告警。"""
    code = (
        "import io\n"
        "import numpy as np\n"
        "import pandas as pd\n"
        "buf = io.BytesIO()\n"
        "df = pd.DataFrame({'a': np.arange(3)})\n"
        "result = df.describe()\n"
    )
    result = code_scanner.scan(code, "python", MODE)
    assert result.passed is True
    assert result.issues == [], f"普通代码被误报能力告警：{result.issues}"


def test_capability_warning_never_flips_the_verdict_and_fatal_still_wins():
    """WARNING 不得变成隐性 fail-open，也不得把 FATAL 挤掉。"""
    ok = code_scanner.scan("import cv2\ncv2.imread('/x.png')\n", "python", MODE)
    assert ok.passed is True and _codes(ok) == {"FILE_IO_CAPABILITY"}

    bad = code_scanner.scan("import cv2\nimport os\nos.system('id')\n", "python", MODE)
    assert bad.passed is False
    assert "NOT_WHITELISTED" in _codes(bad)
