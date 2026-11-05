"""D6 (2026-11-05): clickhouse_driver 运行时导入但 requirements 从未声明 —— api 镜像内
`from clickhouse_driver import Client` 直接 ModuleNotFoundError，沙箱审计事件整段丢弃
（现场: [sandbox-audit] ClickHouse unavailable: No module named 'clickhouse_driver'）。

与 cryptography 同族教训：运行时导入面必须与 requirements 声明面对齐，这里给
clickhouse 面钉一道机器闸，防止再次「代码里 import、镜像里没有」。
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_RUNTIME_IMPORTERS = (
    "app/services/gateway_service.py",
    "app/services/sandbox_audit.py",
    "app/services/audit_service.py",
    "app/api/sandbox_sessions.py",
)


def _runtime_import_sites() -> list[str]:
    sites = []
    for rel in _RUNTIME_IMPORTERS:
        src = (REPO / rel).read_text(encoding="utf-8")
        if re.search(r"from clickhouse_driver import|import clickhouse_driver", src):
            sites.append(rel)
    return sites


def test_clickhouse_driver_is_declared_in_requirements():
    req = (REPO / "requirements.txt").read_text(encoding="utf-8")
    declared = [
        line.strip()
        for line in req.splitlines()
        if line.strip() and not line.strip().startswith("#")
        and re.match(r"^\s*clickhouse[-_]driver\b", line)
    ]
    assert declared, (
        "clickhouse_driver 被这些运行时路径导入却没有在 requirements.txt 声明: "
        f"{_runtime_import_sites()}"
    )


def test_runtime_import_surface_has_no_untracked_clickhouse_importers():
    """导入面漂移预警：新增 clickhouse_driver 导入文件必须进 _RUNTIME_IMPORTERS。"""
    found = set()
    for py in (REPO / "app").rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if re.search(r"from clickhouse_driver import|import clickhouse_driver", text):
            found.add(str(py.relative_to(REPO)))
    assert found <= set(_RUNTIME_IMPORTERS), (
        f"clickhouse_driver 导入面扩大到 {sorted(found - set(_RUNTIME_IMPORTERS))}，"
        "请把它们加入本测试清单并确认 requirements 声明仍然覆盖"
    )
