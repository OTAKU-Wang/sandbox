"""A50 — sandbox-db 导入路径必须在 API 边界自证受限，且不得回显平台目录。

台账：ocr-verified-findings.md A50（json_path / parquet_path 由请求体控制，服务端直接读）。
契约：docs/error-codes.md W2 {code,message,detail,request_id}；拒绝码 FORBIDDEN
（app/api/deliveries.py、app/api/open_api.py 先例），HTTP 403。
"""
import json
import uuid
from pathlib import Path

import pytest

from app.services.secure_duckdb import SecureDuckDBEngine

DB = "/api/v1/sandbox-db"
ROOT_LISTING = "/tmp/cds-uploads"


@pytest.fixture
def import_root(tmp_path):
    inside = tmp_path / "imports"
    inside.mkdir()
    return inside, tmp_path


async def _seed_table(client, headers, sid: str) -> None:
    resp = await client.post(
        f"{DB}/create-table",
        json={"session_id": sid, "table_name": "people",
              "data": [{"name": "alice"}, {"name": "bob"}]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


async def _tables(client, headers, sid: str) -> list[str]:
    resp = await client.get(f"{DB}/sessions/{sid}/tables", headers=headers)
    assert resp.status_code == 200, resp.text
    return [t["table_name"] for t in resp.json()]


@pytest.mark.asyncio
async def test_json_import_outside_root_refused_with_w2_code_and_no_path_disclosure(
    client, auth_headers, import_root, monkeypatch
):
    """A50：越界 json_path ⇒ 403 + W2 语义码，且响应不得把平台导入根清单吐给调用方。"""
    inside, outside = import_root
    monkeypatch.setenv("CDS_SANDBOX_IMPORT_ROOTS", str(inside))
    secret = outside / "app.env.json"
    secret.write_text(json.dumps({"DB_PASSWORD": "s3cr3t"}), encoding="utf-8")

    sid = f"a50-json-{uuid.uuid4().hex[:8]}"
    await _seed_table(client, auth_headers, sid)

    resp = await client.post(
        f"{DB}/import/json",
        json={"session_id": sid, "table_name": "stolen", "json_path": str(secret)},
        headers=auth_headers,
    )
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["code"] == "FORBIDDEN", body
    assert body["request_id"]
    assert ROOT_LISTING not in resp.text, "响应回显了平台导入根清单"
    assert "s3cr3t" not in resp.text
    assert await _tables(client, auth_headers, sid) == ["people"]


@pytest.mark.asyncio
async def test_api_boundary_refuses_even_when_the_engine_guard_is_neutered(
    client, auth_headers, import_root, monkeypatch
):
    """A50：本轨道的边界必须自己把关 —— 把引擎层的白名单短路掉也进不来。"""
    inside, outside = import_root
    monkeypatch.setenv("CDS_SANDBOX_IMPORT_ROOTS", str(inside))
    monkeypatch.setattr(
        SecureDuckDBEngine, "_confine_import_path",
        lambda self, raw_path, *, kind: Path(raw_path),
        raising=True,
    )
    secret = outside / "passwd.json"
    secret.write_text(json.dumps([{"line": "root:x:0:0"}]), encoding="utf-8")

    sid = f"a50-bnd-{uuid.uuid4().hex[:8]}"
    await _seed_table(client, auth_headers, sid)

    resp = await client.post(
        f"{DB}/import/json",
        json={"session_id": sid, "table_name": "stolen", "json_path": str(secret)},
        headers=auth_headers,
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "FORBIDDEN"
    assert await _tables(client, auth_headers, sid) == ["people"]


@pytest.mark.asyncio
async def test_parquet_import_outside_root_and_glob_refused(client, auth_headers, import_root, monkeypatch):
    """A50：parquet_path 同形 —— 越界与 glob 目录枚举都是 403 FORBIDDEN，无路径回显。"""
    inside, outside = import_root
    monkeypatch.setenv("CDS_SANDBOX_IMPORT_ROOTS", str(inside))
    (outside / "victim.parquet").write_bytes(b"PAR1")

    sid = f"a50-pq-{uuid.uuid4().hex[:8]}"
    await _seed_table(client, auth_headers, sid)

    for path in (str(outside / "victim.parquet"), str(inside / "*.parquet")):
        resp = await client.post(
            f"{DB}/import/parquet",
            json={"session_id": sid, "table_name": "stolen", "parquet_path": path},
            headers=auth_headers,
        )
        assert resp.status_code == 403, f"{path} -> {resp.status_code} {resp.text}"
        assert resp.json()["code"] == "FORBIDDEN", resp.text
        assert ROOT_LISTING not in resp.text, path

    assert await _tables(client, auth_headers, sid) == ["people"]


@pytest.mark.asyncio
async def test_import_inside_root_still_works(client, auth_headers, import_root, monkeypatch):
    """A50 的反面：导入根内的合法文件照常导入（不得过度收紧）。"""
    inside, _ = import_root
    monkeypatch.setenv("CDS_SANDBOX_IMPORT_ROOTS", str(inside))
    (inside / "ok.json").write_text(json.dumps([{"a": 1}, {"a": 2}]), encoding="utf-8")

    sid = f"a50-ok-{uuid.uuid4().hex[:8]}"
    await _seed_table(client, auth_headers, sid)

    resp = await client.post(
        f"{DB}/import/json",
        json={"session_id": sid, "table_name": "ok", "json_path": str(inside / "ok.json")},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["row_count"] == 2
