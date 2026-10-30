"""OCR A70 — 数据资源上传先整份进内存再校大小（high）。

`app/api/data_resources.py` 的 `upload_data_resource` 是 `content = await file.read()`
一次性把整个上传体读进 API 进程内存，之后才 `if len(content) > MAX_UPLOAD_SIZE`
（500MB）。也就是说 500MB 只在入内存**之后**生效：一个未认证前的限流缺口 + 一个
500MB 体就是一次 API 进程 OOM。台账同时认定该处的文件名「路径穿越」那一半不成立，
所以本文件只钉有界读取，不去改 object_name。
"""
import pytest
from httpx import AsyncClient
from starlette.datastructures import UploadFile

from app.api import data_resources

LIMIT = 4096
CHUNK_SLACK = 64 * 1024


def _spy_reads(monkeypatch: pytest.MonkeyPatch) -> list:
    requested: list = []
    original = UploadFile.read

    async def spy(self, size: int = -1):
        requested.append(size)
        return await original(self, size)

    monkeypatch.setattr(UploadFile, "read", spy)
    return requested


async def _upload(client: AsyncClient, headers: dict, payload: bytes, filename: str):
    return await client.post(
        "/api/v1/data-resources/upload",
        data={"name": "A70 bounded read"},
        files={"file": (filename, payload, "text/csv")},
        headers=headers,
    )


async def test_oversized_upload_is_refused_without_an_unbounded_read(
    client: AsyncClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(data_resources, "MAX_UPLOAD_SIZE", LIMIT)
    requested = _spy_reads(monkeypatch)

    resp = await _upload(client, auth_headers, b"id,v\n" + b"1," + b"x" * 200_000, "big.csv")

    assert resp.status_code == 413, f"expected 413, got {resp.status_code}: {resp.text[:200]}"
    assert resp.json()["code"] == "HTTP_413", resp.json()
    assert requested, "上传体一次都没有被读取，断言失去意义"
    assert all(size > 0 for size in requested), (
        f"上传体被一次无界 read() 整份读进内存: {requested}"
    )
    assert sum(requested) <= LIMIT + CHUNK_SLACK, (
        f"读取字节数远超上限，封顶没有前置: {requested}"
    )


async def test_upload_within_the_limit_still_works(
    client: AsyncClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(data_resources, "MAX_UPLOAD_SIZE", LIMIT)
    _spy_reads(monkeypatch)

    resp = await _upload(client, auth_headers, b"id,name\n1,Alice\n2,Bob\n", "people.csv")

    assert resp.status_code == 200, resp.text[:400]
    assert resp.json()["row_count"] == 2
