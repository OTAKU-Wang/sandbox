"""A286 — Redis `allkeys-lru` evicts session/rate-limit keys, and the OOM that
`noeviction` replaces it with must not be swallowed as "Redis unavailable".

Two halves (docs/ocr-review-2026-10-18/ocr-verified-findings.md ### A286):

1. CONFIG — this stack puts sessions, rate-limit counters and login short-state in
   Redis, and six deploy copies set ``--maxmemory-policy allkeys-lru``: once maxmemory
   is reached, Redis evicts exactly those keys as if they were a throwaway cache
   ("random logouts / rate limit silently opens"). Fix: ``noeviction``, in ALL SIX
   copies at once (this repo keeps seven deploy config copies).

2. CODE — with noeviction a full maxmemory no longer evicts, it makes the counter EVAL
   fail with ``OOM command not allowed when used memory > 'maxmemory'.``. That lands in
   the same ``except Exception`` in app/core/security.py as a dead connection, so the
   symptom would only have changed from "counters evicted" to "silently degraded to a
   per-replica window". The two states are different incidents with different operator
   actions, so they are classified and disclosed separately (honest degradation — still
   fail-open, never a bare 500, never silent).

No real Redis runs here and none is claimed: the two degraded states are driven with
fakeredis plus the exception class the driver itself raises — redis-py maps the ``OOM``
error prefix to ``redis.exceptions.OutOfMemoryError``
(.venv/.../redis/_parsers/base.py: ``"OOM": OutOfMemoryError``). The config half reads
the files on disk (yaml.safe_load for the declarative copies, ``bash -n`` for the two
scripted docker-run copies).
"""
import json
import re
import subprocess
from pathlib import Path

import fakeredis.aioredis
import pytest
import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import OutOfMemoryError as RedisOutOfMemoryError

REPO = Path(__file__).resolve().parents[1]

# The real Redis message for a refused write under `maxmemory-policy noeviction`.
OOM_MESSAGE = "OOM command not allowed when used memory > 'maxmemory'."

# The six copies A286 counted. All of them belong to one deploy surface: the root
# compose pair, the middleware compose, the two scripted docker-run deployers and the
# k3s manifest. Any one of them left on allkeys-lru re-opens the defect.
DEPLOY_SITES = [
    "docker-compose.yml",
    "docker-compose.prod.yml",
    "docker-compose.middleware.yml",
    "deploy/local/local_deploy.sh",
    "deploy/e2e-remote/remote_deploy.sh",
    "k8s/redis.yaml",
]


def _compose_redis_tokens(rel: str) -> list[str]:
    """Redis container command tokens of a compose file (string or list form)."""
    doc = yaml.safe_load((REPO / rel).read_text(encoding="utf-8"))
    cmd = doc["services"]["redis"]["command"]
    return cmd.split() if isinstance(cmd, str) else [str(tok) for tok in cmd]


def _k8s_redis_tokens(rel: str = "k8s/redis.yaml") -> list[str]:
    """Redis container command tokens of the k3s manifest (command is a token list)."""
    for doc in yaml.safe_load_all((REPO / rel).read_text(encoding="utf-8")):
        if doc and doc.get("kind") == "Deployment":
            return [str(tok) for tok in doc["spec"]["template"]["spec"]["containers"][0]["command"]]
    raise AssertionError(f"{rel}: no Deployment found")


_SH_RUN_HEAD = re.compile(r"^\s*run\s+\S+\s+redis:7-alpine")


def _script_redis_block(rel: str) -> str:
    """The `run <name> redis:7-alpine …` continuation chain of a scripted deployer."""
    lines = (REPO / rel).read_text(encoding="utf-8").splitlines()
    for idx, line in enumerate(lines):
        if _SH_RUN_HEAD.match(line):
            block = [line]
            while block[-1].rstrip().endswith("\\"):
                nxt = lines[idx + len(block)]
                block.append(nxt)
            return "\n".join(block)
    raise AssertionError(f"{rel}: no `run <name> redis:7-alpine` block found")


def _tokens_for(rel: str) -> list[str]:
    if rel.endswith(".yml"):
        return _compose_redis_tokens(rel)
    if rel.endswith(".yaml"):
        return _k8s_redis_tokens(rel)
    return _script_redis_block(rel).replace("\\\n", " ").split()


def _policy_of(rel: str) -> str:
    toks = _tokens_for(rel)
    assert "--maxmemory-policy" in toks, f"{rel}: redis command sets no --maxmemory-policy"
    return toks[toks.index("--maxmemory-policy") + 1]


@pytest.mark.parametrize("rel", DEPLOY_SITES)
def test_deploy_site_uses_noeviction(rel: str):
    """A286: sessions/rate-limit counters must never be eviction candidates."""
    assert _policy_of(rel) == "noeviction", (
        f"{rel}: --maxmemory-policy is {_policy_of(rel)!r}, expected 'noeviction' "
        "(allkeys-lru lets Redis evict session and rate-limit keys when maxmemory is hit)"
    )


@pytest.mark.parametrize("rel", DEPLOY_SITES)
def test_deploy_site_keeps_a_memory_cap(rel: str):
    """noeviction only makes sense with a cap that still exists: the cap is what turns
    an unbounded Redis into a *disclosed* write refusal instead of an OOM-killed
    container. Dropping the cap here would move the failure somewhere else."""
    toks = _tokens_for(rel)
    assert "--maxmemory" in toks, f"{rel}: redis command lost its --maxmemory cap"
    cap = toks[toks.index("--maxmemory") + 1]
    assert re.fullmatch(r"\d+(mb|MB|gb|GB)", cap), f"{rel}: unparsable --maxmemory {cap!r}"


def test_no_allkeys_lru_survives_in_any_deploy_copy():
    """The seven-copy drift class: one straggler re-opens the defect for that path.

    Scanned over the parsed command tokens, not the raw file text — the comments that
    record *why* the policy changed name the retired policy on purpose, and prose is not
    configuration.
    """
    stragglers = [rel for rel in DEPLOY_SITES if "allkeys-lru" in _tokens_for(rel)]
    assert stragglers == [], f"allkeys-lru still configured in: {stragglers}"


@pytest.mark.parametrize("rel", ["deploy/local/local_deploy.sh", "deploy/e2e-remote/remote_deploy.sh"])
def test_scripted_deployer_still_parses_and_keeps_run_order(rel: str):
    """Both scripted deployers mirror the root compose 1:1 with `docker run`: every
    docker flag must precede the image name and the container command must sit after a
    literal `--`; a comment line inside the backslash-continuation chain silently breaks
    the chain (it happened in this repo)."""
    path = REPO / rel
    checked = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert checked.returncode == 0, f"{rel}: bash -n → {checked.stderr.strip()}"

    block = _script_redis_block(rel)
    assert "# " not in block, f"{rel}: comment line inside the redis run continuation chain:\n{block}"
    assert " -- " in block, f"{rel}: redis container command is not after a literal `--`\n{block}"
    head, _, container_cmd = block.replace("\\\n", " ").partition(" -- ")
    assert "redis:7-alpine" in head
    docker_flags = re.findall(r"(?:^|\s)(-p|-e|-v|--name|--network|--cap-add)(?:\s|=)", " " + container_cmd + " ")
    assert not docker_flags, f"{rel}: docker flag {docker_flags} placed after `--` → docker would read it as the command"
    cmd_tokens = container_cmd.split()
    assert cmd_tokens[0] == "redis-server", f"{rel}: container command does not start with redis-server"
    assert cmd_tokens[cmd_tokens.index("--maxmemory-policy") + 1] == "noeviction"


class _RaisingRedis(fakeredis.aioredis.FakeRedis):
    """fakeredis instance whose counter EVAL fails the way a real Redis refuses it.

    Everything else stays the real fakeredis implementation, so the fallback path is
    exercised against a live client object rather than a bare stub.
    """

    def __init__(self, exc: Exception):
        super().__init__(decode_responses=True)
        self._exc = exc
        self.eval_calls = 0

    async def eval(self, script, numkeys, *keys_and_args):
        self.eval_calls += 1
        raise self._exc


def _mkapp():
    test_app = FastAPI()

    @test_app.get("/ping")
    async def ping():
        return {"ok": True}

    from app.core.security import RateLimitMiddleware

    test_app.add_middleware(RateLimitMiddleware)
    return test_app


def _client(app, ip: str = "10.0.0.1") -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 51234)), base_url="http://test")


def _install(monkeypatch, fake) -> None:
    import app.core.redis as redis_module

    monkeypatch.setattr(redis_module, "_redis_client", fake)


@pytest.mark.asyncio
async def test_healthy_redis_records_no_degradation(monkeypatch):
    """Positive control: the degradation state must not be sticky or always-on,
    otherwise the OOM disclosure proves nothing."""
    _install(monkeypatch, fakeredis.aioredis.FakeRedis(decode_responses=True))
    from app.core.security import redis_degradation_state

    async with _client(_mkapp()) as ac:
        for _ in range(3):
            assert (await ac.get("/ping")).status_code == 200
    assert redis_degradation_state() is None


@pytest.mark.asyncio
async def test_oom_is_classified_as_memory_and_disclosed_not_swallowed(monkeypatch, caplog):
    """A full maxmemory under noeviction is NOT an outage: it must say so, name
    maxmemory, and be distinguishable from a dead connection."""
    _install(monkeypatch, _RaisingRedis(RedisOutOfMemoryError(OOM_MESSAGE)))
    from app.core.security import redis_degradation_state

    with caplog.at_level("WARNING"):
        async with _client(_mkapp()) as ac:
            assert (await ac.get("/ping")).status_code == 200  # still fail-open, not a 500

    msgs = [r.message for r in caplog.records if "RATE-LIMIT" in r.message]
    assert msgs, "Redis refusing writes produced no warning at all (silent degradation)"
    assert any("maxmemory" in m.lower() for m in msgs), f"OOM disclosure does not name maxmemory: {msgs}"
    assert not any("unavailable" in m.lower() for m in msgs), (
        f"OOM was reported as an outage — the two states are indistinguishable again: {msgs}"
    )
    state = redis_degradation_state()
    assert state is not None and state["reason"] == "memory", f"degradation state = {state!r}"
    assert "oom" in state["error"].lower()


@pytest.mark.asyncio
async def test_connection_failure_is_still_classified_as_connection(monkeypatch, caplog):
    """The pre-existing outage path keeps its own wording (a sibling test pins it);
    A286 only owes it a distinct class + credential-free disclosure."""
    secret = "sup3rs3cret-redis-password"
    _install(monkeypatch, _RaisingRedis(
        RedisConnectionError(f"Error 111 connecting to redis://:{secret}@redis:6379/0."),
    ))
    from app.core.security import redis_degradation_state

    with caplog.at_level("WARNING"):
        async with _client(_mkapp()) as ac:
            assert (await ac.get("/ping")).status_code == 200

    msgs = [r.message for r in caplog.records if "RATE-LIMIT" in r.message]
    assert msgs, "Redis outage produced no warning (A286 must not regress W3 disclosure)"
    assert any(("unavailable" in m.lower() or "outage" in m.lower()) for m in msgs), msgs
    assert not any("maxmemory" in m.lower() for m in msgs), msgs
    state = redis_degradation_state()
    assert state is not None and state["reason"] == "connection", f"degradation state = {state!r}"
    joined = " ".join(msgs) + json.dumps(state, default=str)
    assert secret not in joined, f"REDIS_URL credentials leaked into the disclosure: {joined[:200]}"


@pytest.mark.asyncio
async def test_the_two_states_produce_different_disclosures(monkeypatch, caplog):
    """One shared log line for two incidents is the defect A286 inherits: capture both
    and assert they are not the same message."""
    seen = {}
    for name, exc in (("memory", RedisOutOfMemoryError(OOM_MESSAGE)),
                      ("connection", RedisConnectionError("Connection refused"))):
        _install(monkeypatch, _RaisingRedis(exc))
        caplog.clear()
        with caplog.at_level("WARNING"):
            async with _client(_mkapp()) as ac:
                await ac.get("/ping")
        seen[name] = [r.message for r in caplog.records if "RATE-LIMIT" in r.message]
        assert seen[name], f"{name}: no disclosure emitted"
    assert seen["memory"] != seen["connection"], f"both states logged identically: {seen}"


@pytest.mark.asyncio
async def test_oom_degradation_still_limits_per_replica(monkeypatch):
    """Fail-open must stay *degraded*, not *disabled*: with Redis refusing writes the
    in-process window still returns 429 at the ceiling, and no request turns into a 500."""
    from app.core.config import get_settings

    fake = _RaisingRedis(RedisOutOfMemoryError(OOM_MESSAGE))
    _install(monkeypatch, fake)
    limit = get_settings().RATE_LIMIT_PER_MINUTE_IP

    async with _client(_mkapp(), ip="10.7.7.7") as ac:
        codes = set()
        last = await ac.get("/ping")
        codes.add(last.status_code)
        for _ in range(limit):
            last = await ac.get("/ping")
            codes.add(last.status_code)
    assert codes == {200, 429}, f"unexpected status set under OOM: {codes}"
    assert last.status_code == 429
    assert last.json()["code"] == "RATE_LIMITED"
    assert fake.eval_calls >= limit, "the limiter never attempted the Redis window"
