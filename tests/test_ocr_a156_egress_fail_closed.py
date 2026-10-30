"""A156（high）— 网隔隔离链上的 fail-open：运行面拿到 isolate_network=False 时无人拦。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A156（"另外 sandbox_runtime.py:135
预留了"不隔离时注入 DNS 代理环境变量"的接口，但 :396 传的是 dns_proxy_port=None 且
get_dns_env_vars 无人调用，所以那条路也是空的"）

本文件钉本轨可改的那半：L0 运行面唯一的"共享网络"入口。`isolate_network=False` 在
`_build_l0_bwrap_args` 里只是省掉 `--unshare-net`，而 `network_policy` 的 netns/iptables/DNS
代理执行面（A155/A156）一条都没接进运行面 ⇒ 请求"按白名单出网"实际得到的是"全通且无人过滤"，
且审计里写着策略已建。项目铁律是宁可拒绝不可伪造，所以这条组合必须在执行前被拒。
"""
from __future__ import annotations

import shutil

import pytest

from app.services.sandbox_runtime import ProcessAdapter


def _adapter(tmp_path):
    ws = tmp_path / "ws"
    for sub in ("input", "output", "tmp"):
        (ws / sub).mkdir(parents=True)
    return ProcessAdapter(workspace_root=str(tmp_path)), ws


def test_shared_network_without_enforcement_is_refused(tmp_path):
    adapter, ws = _adapter(tmp_path)
    with pytest.raises(RuntimeError, match="egress"):
        adapter._prepare_exec_command(
            ws, "l0-a156", "print(1)", "python", None, None,
            isolate_network=False, dns_proxy_port=None,
        )


def test_dns_proxy_allowlist_claim_is_refused(tmp_path):
    adapter, ws = _adapter(tmp_path)
    with pytest.raises(RuntimeError, match="not enforced"):
        adapter._prepare_exec_command(
            ws, "l0-a156", "print(1)", "python", None, None,
            isolate_network=False, dns_proxy_port=15353,
        )


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="needs bubblewrap (environmental)")
def test_isolated_path_still_builds_unchanged(tmp_path):
    adapter, ws = _adapter(tmp_path)
    cmd, _env, _fd = adapter._prepare_exec_command(
        ws, "l0-a156", "print(1)", "python", None, None,
        isolate_network=True, dns_proxy_port=None,
    )
    assert "--unshare-net" in cmd
