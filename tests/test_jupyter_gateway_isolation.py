"""SS-19 开发环反代的隔离面：前缀强制、kernel 归属、票据一次性、Cookie 不可伪造。

不依赖真实 Jupyter：这些是代理自己的判定逻辑，能在 CI 里确定性失败。
"""
from __future__ import annotations

import json
import time

import pytest

from app.api import jupyter_proxy as px
from app.services import jupyter_gateway as jg

PREFIX = "u-1/s-1/files/"


def _allowed(path: str) -> bool:
    try:
        px._check(path, PREFIX)
        return True
    except Exception:
        return False


class TestPrefixEnforcement:
    def test_own_tree_and_contents_allowed(self):
        assert _allowed("tree/u-1/s-1/files")
        assert _allowed("api/contents/u-1/s-1/files/output/result.csv")
        assert _allowed("notebooks/u-1/s-1/files/task.ipynb")

    def test_other_session_and_root_listing_denied(self):
        assert not _allowed("api/contents/u-1/s-2/files/x.csv")
        assert not _allowed("api/contents")
        assert not _allowed("api/contents/")
        assert not _allowed("tree/u-2")
        assert not _allowed("api/contents/u-1/s-1/files/../s-2/secret.csv")

    def test_plain_path_traversal_outside_prefix_denied(self):
        # 目录名里带 .. 必须挡在代理这一层，不能指望 Jupyter 自己消化。
        assert not _allowed("api/contents/u-1/s-1/files/../../data-load/x.csv")

    def test_bare_contents_listing_denied(self):
        # 实测：这个镜像吐掉 `?path=`，裸形总返回根目录（会漏别人的会话目录名），必须拦。
        assert not _allowed("api/contents")
        assert _allowed("api/contents/u-1/s-1/files")

    def test_lab_deep_link_follows_prefix(self):
        assert _allowed("lab/tree/u-1/s-1/files/task.ipynb")
        assert not _allowed("lab/tree/u-2/x.ipynb")

    def test_shared_service_control_apis_denied(self):
        assert not _allowed("api/terminals")
        assert not _allowed("api/shutdown")
        assert not _allowed("api/metrics")

    def test_static_and_kernel_specs_allowed(self):
        assert _allowed("static/style/style.min.css")
        assert _allowed("api/specs/kernels/python3")
        assert _allowed("lab")


class TestKernelOwnership:
    """CR MAJOR-6：归属真值在 Redis（跨重启/多副本一致），进程内只剩降级层。"""

    @pytest.fixture(autouse=True)
    def _fake_redis(self, monkeypatch):
        import fakeredis.aioredis as fake_aioredis

        import app.core.redis as core_redis

        fake = fake_aioredis.FakeRedis(decode_responses=True)
        monkeypatch.setattr(core_redis, "_redis_client", fake)
        return fake

    async def test_foreign_kernel_denied_own_kernel_allowed(self):
        await jg.release_kernel("k-mine")
        with pytest.raises(Exception):
            await px._guard_kernel("api/kernels/k-other/channels", PREFIX)
        await jg.claim_kernel("k-mine", PREFIX)
        assert await jg.owns_kernel("k-mine", PREFIX)
        await px._guard_kernel("api/kernels/k-mine/interrupt", PREFIX)
        # 另一个会话拿同一个前缀也拿不到别人的 kernel（归属按 kernel id 记账）。
        assert not await jg.owns_kernel("k-mine", "other/prefix/")

    async def test_ownership_survives_process_local_reset(self, _fake_redis):
        # 重启语义：进程内 dict 清空后，Redis 里的归属仍然是权威——旧版会全量 403。
        await jg.claim_kernel("k-restart", PREFIX)
        jg._kernel_owner_local.clear()
        assert await jg.owns_kernel("k-restart", PREFIX)

    async def test_unknown_kernel_fail_closed(self):
        assert not await jg.owns_kernel("k-never-claimed", PREFIX)

    async def test_kernel_listing_not_blocked(self):
        await px._guard_kernel("api/kernels", PREFIX)

    async def test_non_kernel_paths_are_not_kernel_guarded(self):
        # parts[2] 不是 kernel id 的路径必须原样经过去（否则 contents/tree/静态全 403）。
        await px._guard_kernel("api/contents/" + PREFIX + "output/r.csv", PREFIX)
        await px._guard_kernel("tree/" + PREFIX, PREFIX)
        await px._guard_kernel("static/style/style.min.css", PREFIX)
        await px._guard_kernel("api/sessions", PREFIX)


class TestCookie:
    def test_signed_cookie_roundtrip(self):
        raw = jg.sign_cookie(json.dumps({"u": "u-1", "p": PREFIX, "exp": int(time.time()) + 60}))
        ctx = jg.read_cookie(raw)
        assert ctx and ctx["p"] == PREFIX

    def test_tampered_prefix_rejected(self):
        raw = jg.sign_cookie(json.dumps({"u": "u-1", "p": PREFIX, "exp": int(time.time()) + 60}))
        digest = raw.rsplit(".", 1)[1]
        forged = jg.sign_cookie(json.dumps({"u": "u-1", "p": "", "exp": int(time.time()) + 60}))
        assert digest != forged.rsplit(".", 1)[1]
        assert jg.read_cookie(raw.rsplit(".", 1)[0][:-1] + "." + digest) is None

    def test_expired_cookie_rejected(self):
        raw = jg.sign_cookie(json.dumps({"u": "u-1", "p": PREFIX, "exp": int(time.time()) - 1}))
        assert jg.read_cookie(raw) is None

    def test_garbage_cookie_rejected(self):
        assert jg.read_cookie(None) is None
        assert jg.read_cookie("nonsense") is None
        assert jg.read_cookie("{}.deadbeef") is None


class TestUnavailablePaths:
    def test_unconfigured_gateway_says_so(self, monkeypatch):
        monkeypatch.setattr(jg, "get_settings", lambda: type("S", (), {"JUPYTER_PROXY_BASE_URL": None})())
        assert jg.configured() is False
        with pytest.raises(jg.JupyterUnavailable):
            jg.upstream_base()

    def test_unsupported_level_says_so(self):
        with pytest.raises(jg.JupyterUnavailable):
            jg.layout("l2c")

    def test_supported_levels_match_compose_layout(self):
        # 手镜像快照：与 jupyter_gateway.layout 及 compose 卷挂载逐字对应。
        # 诚实口径（CR MEDIUM-3）：改 compose 并**不会**自动在这里变红——本测试不读
        # compose 文件；同步点靠 jupyter_gateway.layout() 作为唯一取值口 + 改动时人工
        # 同步这一处（改 gateway 常量会红这里，改 compose 不会）。
        assert jg.layout("L0") == ("/tmp/cds-sandbox-l0", "l0")
        assert jg.layout("L3") == ("/tmp/cds-sandbox", "")
        assert jg.JUPYTER_ROOT_DIR == "/tmp/cds-sandbox"

    async def test_ticket_refused_without_redis(self, monkeypatch):
        # 没有 Redis 就没法保证一次性：宁可不发票据（与 ws-ticket 同口径）。
        import app.core.redis as redis_mod

        async def boom():
            raise RuntimeError("redis down")

        monkeypatch.setattr(redis_mod, "get_redis", boom)
        with pytest.raises(jg.JupyterUnavailable):
            await jg.mint_ticket("u-1", "buyer", "s-1", "task.ipynb")


class TestContractDataInjection:
    """供数注入必须能说清“为什么 input/ 是空的”（SS-19 e2e：原件丢在容器可写层，库里仍 ready）。"""

    @staticmethod
    def _db(script):
        class _Res:
            def __init__(self, v):
                self._v = v

            def scalar_one_or_none(self):
                return self._v

        class _DB:
            def __init__(self):
                self._it = iter(script)

            async def execute(self, _stmt):
                return _Res(next(self._it))

        return _DB()

    async def test_missing_object_reports_reason_instead_of_quiet_empty(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from app.models.data_resource import ResourceStatus
        from app.services.storage_service import storage_service

        rid = "7ab7c79d-a3ff-4bac-bfe3-d6f4882b5f92"
        product = SimpleNamespace(id=rid, name="客单价产品", resource_id=rid)
        resource = SimpleNamespace(
            id=rid, name="订单流水", status=ResourceStatus.READY.value,
            storage_path=f"/tmp/cds-storage/data-resources/{rid}/orders.csv", format="csv", file_size_bytes=532,
        )

        def boom(_path):
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr(storage_service, "download_decrypted", boom)
        out = await jg.inject_contract_data(
            self._db([product, resource]), tmp_path, SimpleNamespace(product_ids=[rid]),
        )
        assert out["written"] == []
        assert len(out["errors"]) == 1
        assert "订单流水" in out["errors"][0] and "FileNotFoundError" in out["errors"][0]
        assert (tmp_path / "input").is_dir()

    async def test_not_ready_resource_says_so(self, tmp_path):
        from types import SimpleNamespace

        rid = "11111111-1111-1111-1111-111111111111"
        product = SimpleNamespace(id=rid, name="P", resource_id=rid)
        resource = SimpleNamespace(id=rid, name="R", status="pending_review", storage_path=None, format="csv", file_size_bytes=0)
        out = await jg.inject_contract_data(self._db([product, resource]), tmp_path, SimpleNamespace(product_ids=[rid]))
        assert out["written"] == [] and out["errors"] and "pending_review" in out["errors"][0]

    async def test_happy_path_writes_file_and_reports_no_errors(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from app.models.data_resource import ResourceStatus
        from app.services.storage_service import storage_service

        rid = "22222222-2222-2222-2222-222222222222"
        product = SimpleNamespace(id=rid, name="P", resource_id=rid)
        resource = SimpleNamespace(id=rid, name="R", status=ResourceStatus.READY.value, storage_path="x", format="csv", file_size_bytes=3)
        monkeypatch.setattr(storage_service, "download_decrypted", lambda _p: b"a,b\n")
        out = await jg.inject_contract_data(self._db([product, resource]), tmp_path, SimpleNamespace(product_ids=[rid]))
        assert out == {"written": [f"{rid}.csv"], "errors": [], "subset": None, "excluded": 0}
        assert (tmp_path / "input" / f"{rid}.csv").read_bytes() == b"a,b\n"


class TestUrlPrefix:
    """SS-19 e2e：前缀必须落在产物目录 `files/`（本文件顶部的 PREFIX 一直是这个约定）。

    指到工作区根时，笔记本里相对路径 `input/` 指向工作区那个永远为空的 input/，
    kernel 的 cwd 也比笔记本高一层（实测 `INPUTS []`），而面板已写“已注入 1 个文件”。
    """

    def test_prefix_is_the_artifacts_dir_not_the_workspace(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from app.services import sandbox_runtime as sr

        ws = tmp_path / "u-1" / "s-1"
        (ws / "files").mkdir(parents=True)
        monkeypatch.setattr(jg, "layout", lambda _lvl: (str(tmp_path), ""))
        monkeypatch.setattr(sr.sandbox_runtime, "get_workspace", lambda _cid, _lvl: str(ws))
        session = SimpleNamespace(container_id="l3-x", sandbox_level="L3")
        assert jg.url_prefix_for(session) == "u-1/s-1/files/"

    def test_prefix_with_url_root_keeps_files_suffix(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from app.services import sandbox_runtime as sr

        ws = tmp_path / "u-2" / "s-2"
        (ws / "files").mkdir(parents=True)
        monkeypatch.setattr(jg, "layout", lambda _lvl: (str(tmp_path), "l0"))
        monkeypatch.setattr(sr.sandbox_runtime, "get_workspace", lambda _cid, _lvl: str(ws))
        session = SimpleNamespace(container_id="l0-x", sandbox_level="L0")
        assert jg.url_prefix_for(session) == "l0/u-2/s-2/files/"


class TestNotebookSeed:
    def test_seed_writes_parseable_ipynb_and_is_idempotent(self, tmp_path):
        name = jg.seed_notebook(tmp_path, "客单价分析 3f2a", None)
        first = (tmp_path / name).read_text(encoding="utf-8")
        doc = json.loads(first)
        assert doc["nbformat"] == 4 and doc["cells"]
        assert name.endswith(".ipynb")
        assert jg.seed_notebook(tmp_path, "客单价分析 3f2a", {"cells": [{"cell_type": "markdown"}]}) == name
        # 幂等：已存在的笔记本不能被后到的 content 覆盖掉用户正在写的东西。
        assert (tmp_path / name).read_text(encoding="utf-8") == first
