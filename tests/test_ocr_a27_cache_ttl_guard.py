"""A27: `cache_set` 的失败口径 —— 调用方错误必须抛出，Redis 故障才允许静默。

台账：`cache_set` 对非法 ttl / 不可 JSON 序列化对象抛 `TypeError`，与 `redis` 错误一起
被 `except Exception` 吐进日志且函数正常返回 ⇒ 调用方以为写成功（表现为「缓存永不
命中」型隐故障）。

HEAD 已把 `json.dumps` 移出 `except` 护栏（`tests/test_ocr_a184_a027.py` 钉住），本文件
补的是同一条目里剩下的那条腿：**非法 ttl**。此前 `setex` 因 ttl 报的 `ResponseError` /
`ValueError` 落在护栏内被吞掉，调用方拿到 `None` 以为写入成功；ttl 是调用方常量，属于
程序错误，必须与不可序列化对象同口径地快速失败（且在触达 Redis 之前判定）。
"""
import pytest

from app.core import cache


async def _recording_redis(monkeypatch) -> list[tuple[str, int, str]]:
    calls: list[tuple[str, int, str]] = []

    class _Fake:
        async def setex(self, key, ttl, value):
            calls.append((key, ttl, value))

    async def _get():
        return _Fake()

    monkeypatch.setattr(cache, "_get_redis", _get)
    return calls


@pytest.mark.parametrize("bad_ttl", [0, -1, -60])
async def test_cache_set_rejects_non_positive_ttl(monkeypatch, bad_ttl):
    calls = await _recording_redis(monkeypatch)
    with pytest.raises(ValueError, match="ttl"):
        await cache.cache_set("k", {"v": 1}, ttl_seconds=bad_ttl)
    assert calls == [], "非法 ttl 不得先写 Redis 再报错"


@pytest.mark.parametrize("bad_ttl", ["60", 30.5, None, True])
async def test_cache_set_rejects_non_integer_ttl(monkeypatch, bad_ttl):
    calls = await _recording_redis(monkeypatch)
    with pytest.raises(TypeError, match="ttl"):
        await cache.cache_set("k", {"v": 1}, ttl_seconds=bad_ttl)
    assert calls == []


async def test_cache_set_happy_path_writes_given_ttl(monkeypatch):
    """守卫不得顺手砍掉正常路径：ttl 原样传给 setex。"""
    calls = await _recording_redis(monkeypatch)
    await cache.cache_set("monitoring:stats", {"size": 3}, ttl_seconds=120)
    assert calls == [("cache:monitoring:stats", 120, '{"size": 3}')]


async def test_cache_set_still_degrades_on_redis_outage(monkeypatch):
    """反向守卫：Redis 故障仍是「静默未命中」，不得升级成 500。"""

    class _Down:
        async def setex(self, key, ttl, value):
            raise ConnectionError("redis is down")

    async def _get():
        return _Down()

    monkeypatch.setattr(cache, "_get_redis", _get)
    await cache.cache_set("k", {"v": 1}, ttl_seconds=60)


async def test_cache_clear_empty_pattern_scope_is_documented(monkeypatch):
    """`cache_clear("")` 的清空范围仍是整个 cache: 命名空间，且元字符不得逃逸出去。"""
    matches: list[str] = []

    class _Fake:
        async def scan(self, cursor, match=None, count=None):
            matches.append(str(match))
            return 0, []

        async def delete(self, *keys):
            return 0

    async def _get():
        return _Fake()

    monkeypatch.setattr(cache, "_get_redis", _get)
    assert await cache.cache_clear("orders:[a]*?") == 0
    assert matches[0] == "cache:orders:\\[a]\\*\\?*"
    await cache.cache_clear()
    assert matches[1] == "cache:*"
