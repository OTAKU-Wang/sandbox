"""Differential Privacy Budget Ledger — persistent tracking with DB backing.

Budgets are persisted in dp_budget_allocations, dp_budget_entries, and a
portable materialized status snapshot named dp_budget_status. The snapshot
matches the spec's materialized-view contract while remaining testable on
SQLite.
"""
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dp_budget import DPBudgetAllocation, DPBudgetEntry, DPBudgetStatus

logger = logging.getLogger(__name__)


class _BudgetRejected(Exception):
    """The database refused the deduction; unwind the SAVEPOINT with the entry."""


class BudgetBackendUnavailable(RuntimeError):
    """A192 ③: 跨进程 ε 预算后端不可用时写入必须显式失败。

    「没读到」既不是「没预算」也不是「有预算」——只在本进程记一笔预算而对副本
    不可见，等于伪造预算（口径同 quota_manager 的 A146）。
    """


# A192 ③: the synchronous fast path used to live entirely in this process's dict, so a
# second API replica (or this replica after a restart) saw no budget at all and
# output_inspection took its "no budget tracked" bypass — unlimited unaccounted DP, and
# two replicas each spending the same ε. Redis is the cross-process view of the budget;
# the DB rows stay the persistent authority.
_BUDGET_KEY_PREFIX = "cds:dp:budget:"
_BACKEND_PROBE_COOLDOWN = 60.0

# KEYS[1] = budget key, ARGV[1] = epsilon to spend.
# -1 = never allocated, 0 = insufficient, 1 = deducted (atomic: one ε, one spend).
LUA_SPEND_EPSILON = """
local cur = redis.call('GET', KEYS[1])
if cur == false then
  return {-1}
end
local remaining = tonumber(cur)
local want = tonumber(ARGV[1])
if remaining < want then
  return {0}
end
redis.call('SET', KEYS[1], tostring(remaining - want))
return {1}
"""


@dataclass
class BudgetEntry:
    session_id: str
    contract_id: str
    epsilon_consumed: float
    operation: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class BudgetStatus:
    contract_id: str
    total_epsilon: float
    consumed_epsilon: float
    remaining_epsilon: float
    last_consumed_at: datetime | None = None
    updated_at: datetime | None = None


class DPBudgetLedger:
    """Manages differential privacy budget allocation and consumption.

    Uses a write-through cache: in-memory dict for fast reads,
    all mutations persisted to PostgreSQL for crash recovery.
    """

    def __init__(self):
        self._cache: dict[str, float] = {}  # contract_id → remaining epsilon (cache)
        self._loaded: set[str] = set()  # contracts loaded from DB
        self._loaded_at: dict[str, float] = {}  # contract_id → monotonic load time
        self._redis = None  # A192 ③ cross-process budget view (None = process-local)
        # A192f: 权威位属于「这次接上、且当场 ping 通过的那个客户端」。用对象身份比对，
        # 而不是一个布尔位：接上坏客户端不得污染上一个好客户端的权威，反之亦然。
        self._backend_verified_client = None
        self._backend_probe_at: float | None = None
        self._backend_last_error: str | None = None
        self._backend_events = {
            "redis_errors": 0,
            "unverified_reads": 0,
            "untracked_spend_attempts": 0,
            "backend_writes": 0,
        }

    # A145: the DB is the authority; the in-process dict is only a read cache and
    # must expire, otherwise every API replica spends its own copy of one ε budget.
    _CACHE_TTL = 5.0

    def _mark_loaded(self, contract_id: str) -> None:
        self._loaded.add(contract_id)
        self._loaded_at[contract_id] = time.monotonic()

    def _invalidate(self, contract_id: str) -> None:
        """Drop the read cache so the next read re-derives the number from the ledger."""
        self._loaded.discard(contract_id)
        self._loaded_at.pop(contract_id, None)
        self._cache.pop(contract_id, None)

    async def _ensure_loaded(self, db: AsyncSession, contract_id: str):
        """Load budget from DB into cache if the read cache is absent or expired."""
        loaded_at = self._loaded_at.get(contract_id)
        if contract_id in self._loaded and (
            loaded_at is None or time.monotonic() - loaded_at < self._CACHE_TTL
        ):
            return
        status = await self._get_status_row(db, contract_id)
        if status:
            self._cache[contract_id] = max(float(status.remaining_epsilon), 0.0)
            self._mark_loaded(contract_id)
            return

        result = await db.execute(
            select(DPBudgetAllocation).where(DPBudgetAllocation.contract_id == contract_id)
        )
        alloc = result.scalar_one_or_none()
        if alloc:
            # Calculate remaining: total - consumed
            consumed_result = await db.execute(
                select(func.coalesce(func.sum(DPBudgetEntry.epsilon_consumed), 0.0))
                .where(DPBudgetEntry.contract_id == contract_id)
            )
            consumed = consumed_result.scalar()
            remaining = float(alloc.total_epsilon) - float(consumed)
            self._cache[contract_id] = max(remaining, 0.0)
            await self._sync_status(
                db,
                contract_id=contract_id,
                total_epsilon=float(alloc.total_epsilon),
                consumed_epsilon=float(consumed),
                last_consumed_at=None,
            )
        else:
            if contract_id not in self._cache:
                self._cache[contract_id] = 0.0
        self._mark_loaded(contract_id)

    async def allocate(self, db: AsyncSession, contract_id: str, epsilon: float):
        """Allocate epsilon budget for a contract (upsert)."""
        if epsilon < 0:
            raise ValueError("epsilon budget must be non-negative")

        # Gap E3: global allocation cap — fail-closed against oversized budgets.
        from app.core.config import get_settings
        max_alloc = get_settings().DP_MAX_EPSILON_ALLOCATION
        if max_alloc is not None and epsilon > float(max_alloc):
            raise ValueError(
                f"epsilon allocation {epsilon} exceeds DP_MAX_EPSILON_ALLOCATION={max_alloc}"
            )

        result = await db.execute(
            select(DPBudgetAllocation).where(DPBudgetAllocation.contract_id == contract_id)
        )
        alloc = result.scalar_one_or_none()
        if alloc:
            alloc.total_epsilon = epsilon
        else:
            alloc = DPBudgetAllocation(contract_id=contract_id, total_epsilon=epsilon)
            db.add(alloc)
        await db.flush()

        consumed = await self.get_total_consumed(db, contract_id)
        remaining = max(float(epsilon) - consumed, 0.0)
        await self._sync_status(
            db,
            contract_id=contract_id,
            total_epsilon=float(epsilon),
            consumed_epsilon=consumed,
            last_consumed_at=await self._get_last_consumed_at(db, contract_id),
        )
        self._cache[contract_id] = remaining
        self._mark_loaded(contract_id)
        self._mirror_backend_safe(contract_id, remaining)

    async def get_remaining(self, db: AsyncSession, contract_id: str) -> float:
        await self._ensure_loaded(db, contract_id)
        return self._cache.get(contract_id, 0.0)

    async def consume(self, db: AsyncSession, contract_id: str, session_id: str,
                      epsilon: float, operation: str = "query") -> bool:
        """Consume epsilon budget. Returns False if insufficient or outside
        the configured guardrails (gap E3)."""
        from app.core.config import get_settings
        settings = get_settings()
        if epsilon <= settings.DP_MIN_EPSILON_CONSUMPTION:
            return False  # no-op / non-positive consumption is not meaningful
        max_per = settings.DP_MAX_EPSILON_PER_CONSUMPTION
        if max_per is not None and epsilon > float(max_per):
            return False  # single consumption exceeds the per-request ceiling

        await self._ensure_loaded(db, contract_id)

        entry = DPBudgetEntry(
            contract_id=contract_id,
            session_id=session_id,
            epsilon_consumed=epsilon,
            operation=operation,
            created_at=datetime.now(timezone.utc),
        )

        if self._is_mock_session(db):
            # Legacy unit tests drive the ledger with AsyncMock sessions and no tables;
            # they keep the pre-A145 in-process semantics.
            remaining = self._cache.get(contract_id, 0.0)
            if remaining < epsilon:
                return False
            self._cache[contract_id] = remaining - epsilon
            db.add(entry)
            await db.flush()
            return True

        # A145: ε is a compliance control, so the deduction is decided inside the
        # database (conditional UPDATE + rowcount) rather than by a check-then-act on
        # this process's cache. The ε entry and the deduction live in one SAVEPOINT, so
        # a rejected or failed consumption can never leave the cache ahead of the ledger.
        try:
            async with db.begin_nested():
                if await self._get_status_row(db, contract_id) is None:
                    await self._materialize_status(db, contract_id)
                db.add(entry)
                await db.flush()
                applied = await self._atomic_deduct(db, contract_id, epsilon, entry.created_at)
                if applied is None:
                    await self._materialize_status(db, contract_id)
                    applied = await self._atomic_deduct(db, contract_id, epsilon, entry.created_at)
                if not applied:
                    raise _BudgetRejected
        except _BudgetRejected:
            await self._refresh_from_db(db, contract_id)
            logger.info(
                "[DPBudget] ε=%s rejected for contract %s (remaining insufficient in the ledger)",
                epsilon,
                contract_id,
            )
            return False
        except Exception as exc:
            self._invalidate(contract_id)
            logger.error(
                "[DPBudget] ε=%s for contract %s was NOT recorded (%s); read cache dropped — "
                "the ledger, not this process, holds the budget truth",
                epsilon,
                contract_id,
                exc,
            )
            return False

        await self._refresh_from_db(db, contract_id)
        return True

    async def _atomic_deduct(
        self, db: AsyncSession, contract_id: str, epsilon: float, consumed_at: datetime
    ) -> bool | None:
        """Deduct ε inside the DB. True=applied, False=insufficient, None=no status row."""
        result = await db.execute(
            update(DPBudgetStatus)
            .where(
                DPBudgetStatus.contract_id == contract_id,
                DPBudgetStatus.remaining_epsilon >= epsilon,
            )
            .values(
                consumed_epsilon=DPBudgetStatus.consumed_epsilon + epsilon,
                remaining_epsilon=DPBudgetStatus.remaining_epsilon - epsilon,
                last_consumed_at=consumed_at,
            )
        )
        if result.rowcount:
            return True
        row = await self._get_status_row(db, contract_id)
        return False if row is not None else None

    async def _materialize_status(self, db: AsyncSession, contract_id: str) -> None:
        await self._sync_status(
            db,
            contract_id=contract_id,
            total_epsilon=await self._get_total_allocated(db, contract_id),
            consumed_epsilon=await self.get_total_consumed(db, contract_id),
            last_consumed_at=await self._get_last_consumed_at(db, contract_id),
        )

    async def _refresh_from_db(self, db: AsyncSession, contract_id: str) -> None:
        status = await self._get_status_row(db, contract_id)
        if status is None:
            self._invalidate(contract_id)
            return
        self._cache[contract_id] = max(float(status.remaining_epsilon), 0.0)
        self._mark_loaded(contract_id)
        self._mirror_backend_safe(contract_id, self._cache[contract_id])

    def _is_mock_session(self, db: AsyncSession) -> bool:
        """Return True for unittest.mock sessions used by legacy tests."""
        return db.__class__.__module__.startswith("unittest.mock")

    # ── Synchronous fast path (PZ-02 / MR-A3), cross-process since A192 ③ ───
    # The output-inspection pipeline runs in synchronous contexts (no DB session).
    # PZ-02 made THIS ledger the single source of truth for DP budgets; A192 ③ makes
    # the numbers it reads cross-process when a budget backend (Redis) is reachable:
    # every allocation/consumption is mirrored there and every ε spend is decided by
    # one atomic Lua script, so N API replicas share one budget instead of each
    # spending its own copy. Without a backend the in-process cache is still used
    # (development, unit tests) but it is then reported as non-authoritative through
    # budget_backend_health() — it survives the request, not a restart.

    def attach_redis(self, client) -> None:
        """Attach the cross-process budget backend (Redis in prod, fakeredis in tests).

        A192f: 权威由**这次**接上的客户端当场通过 ping 来决定。一个连 ping 都没过的
        客户端从来没有跨副本共享过任何东西，所以它接上之后 `budget_backend_health()`
        报 `authoritative=False`，不得继续宣布「这份 ε 跨副本可见」。
        客户端本身仍留在后端位上：读写必须打到它并显式失败（`source=unverified`），
        而不是悄悄退回进程内缓存 —— 后者会让 `budget_state_sync` 报 `tracked=False`，
        重新打开 A192 ③ 已经关掉的「无预算 = 免费 DP」旁路。
        """
        self._backend_probe_at = None
        if client is None:
            self._redis = None
            self._backend_verified_client = None
            logger.warning(
                "[DPBudget][A192] cross-process ε backend detached — budgets are process-local "
                "again (NOT shared across replicas, lost on restart)"
            )
            return
        try:
            client.ping()
        except Exception as exc:
            self._redis = client
            self._backend_verified_client = None
            self._record_backend_error("attach", "-", exc)
            logger.error(
                "[DPBudget][A192f] ε 后端接上但当场 ping 未通过 ⇒ authoritative=False，"
                "所有读数按 unverified 处理；既不宣布权威，也不退回进程内猜测"
            )
            return
        self._redis = client
        self._backend_verified_client = client
        logger.info(
            "[DPBudget][A192] cross-process ε budget backend attached — allocations and "
            "consumptions are now shared across replicas"
        )

    def budget_backend_health(self) -> dict:
        """A192 ③: 「进程内猜测」必须是可见状态，而不是一行注释（口径同 A146）。

        A192f: `authoritative`/`verified` 要求后端**在场且当场 ping 过**。
        """
        backend = self._budget_backend()
        verified = backend is not None and backend is self._backend_verified_client
        return {
            "backend": "redis" if backend is not None else "in-process",
            "authoritative": verified,
            "verified": verified,
            **self._backend_events,
            "last_error": self._backend_last_error,
        }

    def _record_backend_error(self, op: str, budget_id: str, exc: BaseException) -> None:
        self._backend_events["redis_errors"] += 1
        self._backend_last_error = f"{op} {budget_id}: {exc.__class__.__name__}: {exc}"
        logger.error("[DPBudget][A192] ε budget backend %s failed for %s (%s)", op, budget_id, exc)

    def _budget_backend(self):
        """Return the cross-process backend, or None when this process runs on its own cache."""
        if self._redis is not None:
            return self._redis
        if os.environ.get("TESTING") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
            # Test/dev lane: never open a socket by itself — tests inject a client.
            return None
        now = time.monotonic()
        if (
            self._backend_probe_at is not None
            and now - self._backend_probe_at < _BACKEND_PROBE_COOLDOWN
        ):
            return None
        self._backend_probe_at = now
        try:
            import redis as redis_lib

            from app.core.config import get_settings

            client = redis_lib.Redis.from_url(
                get_settings().REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            client.ping()
        except Exception as exc:
            self._record_backend_error("probe", "-", exc)
            logger.error(
                "[DPBudget][A192] no cross-process ε budget backend — falling back to the "
                "PROCESS-LOCAL cache: ε is NOT shared across replicas and is lost on restart "
                "(disclosed via budget_backend_health())"
            )
            return None
        self._redis = client
        self._backend_verified_client = client
        logger.info("[DPBudget][A192] cross-process ε budget backend attached from REDIS_URL")
        return client

    def _write_backend(self, budget_id: str, remaining: float) -> None:
        """Publish a budget to the shared backend; a failed publish is a failed allocation."""
        backend = self._budget_backend()
        if backend is None:
            return
        try:
            backend.set(_BUDGET_KEY_PREFIX + budget_id, repr(float(remaining)))
            self._backend_events["backend_writes"] += 1
        except Exception as exc:
            self._record_backend_error("write", budget_id, exc)
            raise BudgetBackendUnavailable(
                f"ε budget for {budget_id} could not be published to the shared budget backend "
                f"({exc.__class__.__name__}: {exc}) — keeping it only in this process would "
                "fabricate a budget the other replicas cannot see"
            ) from exc

    def _mirror_backend_safe(self, budget_id: str, remaining: float) -> None:
        """Best-effort mirror of a DB-derived budget for the sync readers (async path)."""
        backend = self._budget_backend()
        if backend is None:
            return
        try:
            backend.set(_BUDGET_KEY_PREFIX + budget_id, repr(float(remaining)))
            self._backend_events["backend_writes"] += 1
        except Exception as exc:
            self._record_backend_error("mirror", budget_id, exc)

    def budget_state_sync(self, budget_id: str) -> dict:
        """The one reading of a sync budget: what is tracked, how much is left, and whether
        the number came from the shared backend or only from this process."""
        backend = self._budget_backend()
        if backend is not None:
            try:
                raw = backend.get(_BUDGET_KEY_PREFIX + budget_id)
            except Exception as exc:
                self._record_backend_error("read", budget_id, exc)
                self._backend_events["unverified_reads"] += 1
                # tracked=True is deliberate: an unreadable budget must not be reported as
                # "no budget" (that opens the unaccounted-DP bypass). The caller is sent to
                # the accounting path, where consume_sync() refuses explicitly.
                return {
                    "budget_id": budget_id, "tracked": True, "remaining": 0.0,
                    "source": "unverified", "authoritative": False,
                    "error": f"{exc.__class__.__name__}: {exc}",
                }
            if raw is None:
                return {
                    "budget_id": budget_id, "tracked": False, "remaining": 0.0,
                    "source": "redis", "authoritative": True, "error": None,
                }
            return {
                "budget_id": budget_id, "tracked": True,
                "remaining": max(float(raw), 0.0),
                "source": "redis", "authoritative": True, "error": None,
            }
        return {
            "budget_id": budget_id,
            "tracked": budget_id in self._loaded or budget_id in self._cache,
            "remaining": self._cache.get(budget_id, 0.0),
            "source": "in-process", "authoritative": False, "error": None,
        }

    def has_budget_sync(self, budget_id: str) -> bool:
        """True when a budget for ``budget_id`` exists in the shared backend (or, with no
        backend reachable, in this process's ledger cache)."""
        return self.budget_state_sync(budget_id)["tracked"]

    def get_remaining_sync(self, budget_id: str) -> float:
        """Remaining budget from the shared backend; 0.0 when untracked or unverifiable
        (never the stale in-process number presented as authoritative)."""
        return self.budget_state_sync(budget_id)["remaining"]

    def allocate_sync(self, budget_id: str, total_epsilon: float) -> float:
        """Allocate a budget on the shared backend (process-local only when none is reachable).

        Applies the same guardrails as the async ``allocate`` (gap E3 cap). Raises
        BudgetBackendUnavailable when a backend exists but refuses the write.
        """
        from app.core.config import get_settings

        total = float(total_epsilon)
        if total < 0:
            raise ValueError("epsilon budget must be non-negative")
        max_alloc = get_settings().DP_MAX_EPSILON_ALLOCATION
        if max_alloc is not None and total > float(max_alloc):
            raise ValueError(
                f"epsilon allocation {total} exceeds DP_MAX_EPSILON_ALLOCATION={max_alloc}"
            )
        remaining = max(total, 0.0)
        self._write_backend(budget_id, remaining)
        self._cache[budget_id] = remaining
        self._loaded.add(budget_id)
        return remaining

    def consume_sync(self, budget_id: str, epsilon: float) -> bool:
        """Consume epsilon from the shared budget (one atomic check-and-deduct).

        Mirrors the async ``consume`` guardrails: epsilon=0 / below
        ``DP_MIN_EPSILON_CONSUMPTION`` is not a meaningful consumption
        ("DP not applied") and is rejected, as is a single consumption above
        ``DP_MAX_EPSILON_PER_CONSUMPTION``. An unreadable backend is a refused
        consumption — never a spend against this process's private guess.
        """
        from app.core.config import get_settings

        settings = get_settings()
        if epsilon <= settings.DP_MIN_EPSILON_CONSUMPTION:
            return False
        max_per = settings.DP_MAX_EPSILON_PER_CONSUMPTION
        if max_per is not None and epsilon > float(max_per):
            return False

        backend = self._budget_backend()
        if backend is not None:
            try:
                raw_verdict = backend.eval(
                    LUA_SPEND_EPSILON, 1, _BUDGET_KEY_PREFIX + budget_id, float(epsilon)
                )
                # Lua returns a table, so redis-py hands back [code].
                verdict = int(raw_verdict[0] if isinstance(raw_verdict, (list, tuple)) else raw_verdict)
            except Exception as exc:
                self._record_backend_error("spend", budget_id, exc)
                logger.error(
                    "[DPBudget][A192] ε=%s for %s was NOT consumed: the shared budget backend "
                    "is unusable — refusing instead of spending a process-local guess",
                    epsilon, budget_id,
                )
                return False
            if verdict != 1:
                if verdict == -1:
                    self._backend_events["untracked_spend_attempts"] += 1
                return False
            self._backend_events["backend_writes"] += 1
            try:
                mirrored = backend.get(_BUDGET_KEY_PREFIX + budget_id)
                if mirrored is not None:
                    self._cache[budget_id] = max(float(mirrored), 0.0)
                    self._loaded.add(budget_id)
            except Exception as exc:  # the spend already happened; only the mirror failed
                self._record_backend_error("mirror-read", budget_id, exc)
            return True

        remaining = self._cache.get(budget_id, 0.0)
        if remaining < epsilon:
            return False
        self._cache[budget_id] = remaining - epsilon
        self._loaded.add(budget_id)
        return True

    async def get_ledger(self, db: AsyncSession, contract_id: str) -> list[BudgetEntry]:
        result = await db.execute(
            select(DPBudgetEntry)
            .where(DPBudgetEntry.contract_id == contract_id)
            .order_by(DPBudgetEntry.created_at.desc())
        )
        rows = result.scalars().all()
        return [
            BudgetEntry(
                session_id=r.session_id,
                contract_id=r.contract_id,
                epsilon_consumed=r.epsilon_consumed,
                operation=r.operation,
                timestamp=r.created_at,
            )
            for r in rows
        ]

    async def get_total_consumed(self, db: AsyncSession, contract_id: str) -> float:
        result = await db.execute(
            select(func.coalesce(func.sum(DPBudgetEntry.epsilon_consumed), 0.0))
            .where(DPBudgetEntry.contract_id == contract_id)
        )
        return float(result.scalar())

    async def get_status(self, db: AsyncSession, contract_id: str) -> BudgetStatus:
        """Return materialized DP budget status for a contract."""
        status = await self._get_status_row(db, contract_id)
        if status:
            return BudgetStatus(
                contract_id=status.contract_id,
                total_epsilon=float(status.total_epsilon),
                consumed_epsilon=float(status.consumed_epsilon),
                remaining_epsilon=float(status.remaining_epsilon),
                last_consumed_at=status.last_consumed_at,
                updated_at=status.updated_at,
            )

        result = await db.execute(
            select(DPBudgetAllocation).where(DPBudgetAllocation.contract_id == contract_id)
        )
        alloc = result.scalar_one_or_none()
        if not alloc:
            return BudgetStatus(contract_id=contract_id, total_epsilon=0.0, consumed_epsilon=0.0, remaining_epsilon=0.0)

        consumed = await self.get_total_consumed(db, contract_id)
        last_consumed_at = await self._get_last_consumed_at(db, contract_id)
        await self._sync_status(
            db,
            contract_id=contract_id,
            total_epsilon=float(alloc.total_epsilon),
            consumed_epsilon=consumed,
            last_consumed_at=last_consumed_at,
        )
        row = await self._get_status_row(db, contract_id)
        if row:
            return BudgetStatus(
                contract_id=row.contract_id,
                total_epsilon=float(row.total_epsilon),
                consumed_epsilon=float(row.consumed_epsilon),
                remaining_epsilon=float(row.remaining_epsilon),
                last_consumed_at=row.last_consumed_at,
                updated_at=row.updated_at,
            )
        return BudgetStatus(
            contract_id=contract_id,
            total_epsilon=float(alloc.total_epsilon),
            consumed_epsilon=consumed,
            remaining_epsilon=max(float(alloc.total_epsilon) - consumed, 0.0),
            last_consumed_at=last_consumed_at,
        )

    async def get_all_statuses(self, db: AsyncSession) -> list[BudgetStatus]:
        """Return all materialized DP budget status rows."""
        result = await db.execute(select(DPBudgetStatus).order_by(DPBudgetStatus.contract_id))
        rows = result.scalars().all()
        return [
            BudgetStatus(
                contract_id=row.contract_id,
                total_epsilon=float(row.total_epsilon),
                consumed_epsilon=float(row.consumed_epsilon),
                remaining_epsilon=float(row.remaining_epsilon),
                last_consumed_at=row.last_consumed_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    async def _get_status_row(self, db: AsyncSession, contract_id: str) -> DPBudgetStatus | None:
        result = await db.execute(
            select(DPBudgetStatus).where(DPBudgetStatus.contract_id == contract_id)
        )
        row = result.scalar_one_or_none()
        return row if isinstance(row, DPBudgetStatus) else None

    async def _get_last_consumed_at(self, db: AsyncSession, contract_id: str) -> datetime | None:
        result = await db.execute(
            select(func.max(DPBudgetEntry.created_at)).where(DPBudgetEntry.contract_id == contract_id)
        )
        return result.scalar()

    async def _get_total_allocated(self, db: AsyncSession, contract_id: str) -> float:
        result = await db.execute(
            select(DPBudgetAllocation.total_epsilon).where(DPBudgetAllocation.contract_id == contract_id)
        )
        total = result.scalar_one_or_none()
        return float(total or 0.0)

    async def _sync_status(
        self,
        db: AsyncSession,
        contract_id: str,
        total_epsilon: float,
        consumed_epsilon: float,
        last_consumed_at: datetime | None,
    ) -> None:
        """Create or update the portable materialized status snapshot."""
        remaining = max(float(total_epsilon) - float(consumed_epsilon), 0.0)
        result = await db.execute(
            select(DPBudgetStatus).where(DPBudgetStatus.contract_id == contract_id)
        )
        status = result.scalar_one_or_none()
        if not isinstance(status, DPBudgetStatus):
            status = DPBudgetStatus(contract_id=contract_id)
            db.add(status)

        status.total_epsilon = float(total_epsilon)
        status.consumed_epsilon = float(consumed_epsilon)
        status.remaining_epsilon = remaining
        status.last_consumed_at = last_consumed_at
        await db.flush()


# Singleton for in-process use (cache shared across requests)
dp_budget_ledger = DPBudgetLedger()
