"""A186 — 选位密钥 ``key_seed`` 明文入列且原样回给 API 调用方（high）.

Ledger fix requirement (docs/ocr-review-2026-10-18/ocr-verified-findings.md
### A186): 「拿到 seed 的人能精确算出每一位位置，对已发模型做定向翻转/擦除而不被发现」
must stop being true, and the API/audit surface must carry a SM3 fingerprint of
the seed instead of the seed itself.

The half of that which lives in ``app/services/model_watermark.py``:
* position selection must NOT be a function of ``key_seed`` alone (the old
  ``random.Random(key_seed)`` schedule was fully reproducible from the stored
  seed, and Mersenne Twister is not a cryptographic PRNG);
* the schedule must be derived from a server-side secret as well, so reading
  the ``watermark_records.key_seed`` column (or the API echo of it) is not
  enough to locate the bits;
* metadata must publish a non-reversible fingerprint for reconciliation.

The DB column (``app/models/watermark_record.py``) and the API response field
(``app/api/training.py``) are outside this test's file scope.
"""
import random
import re

import numpy as np

from app.services.model_watermark import ModelWatermarkService

svc = ModelWatermarkService()

LAYER = "fc1.weight"
TOTAL = 4096
BITS = 64


def _state_dict() -> dict:
    rng = np.random.default_rng(7)
    return {LAYER: (rng.random(TOTAL) * 0.5).astype(np.float64)}


def _bits() -> list[int]:
    return [i % 2 for i in range(BITS)]


def test_positions_are_not_the_plain_key_seed_prng_schedule():
    """The documented attack: read the seed, replay the PRNG, find every bit."""
    key_seed = 1234567
    result = svc.embed_watermark(_state_dict(), _bits(), [LAYER], key_seed)
    recorded = sorted(idx for _layer, idx in result.metadata.ordered_positions)

    attacker_replay = sorted(random.Random(key_seed).sample(range(TOTAL), BITS))
    assert recorded != attacker_replay, (
        "watermark positions are still reproducible from key_seed alone: anyone "
        "who reads watermark_records.key_seed (or the training API echo) can "
        "locate and surgically erase every embedded bit"
    )


def test_schedule_needs_a_server_side_secret_beyond_key_seed():
    """key_seed + layer names WITHOUT the deployment pepper must not reproduce it."""
    from app.services.model_watermark import POSITION_CONTEXT, _Sm3CounterDrbg

    key_seed = 987654
    result = svc.embed_watermark(_state_dict(), _bits(), [LAYER], key_seed)
    recorded = sorted(idx for _layer, idx in result.metadata.ordered_positions)

    no_pepper = f"{POSITION_CONTEXT}||{key_seed}|{LAYER}".encode("utf-8")
    guess = sorted(
        ModelWatermarkService._select_positions(
            _Sm3CounterDrbg(no_pepper), TOTAL, BITS
        )
    )
    assert recorded != guess, (
        "positions are derivable from key_seed + layer names alone; the schedule "
        "has no server-side secret, so a leaked row is a full erase recipe"
    )


def test_schedule_is_uniform_and_in_range_not_mt_seeded():
    """Every recorded position is a distinct in-range index (schedule sanity)."""
    result = svc.embed_watermark(_state_dict(), _bits(), [LAYER], 555)
    positions = [idx for _layer, idx in result.metadata.ordered_positions]
    assert len(positions) == BITS
    assert len(set(positions)) == BITS, "positions must be unique per bit"
    assert all(0 <= idx < TOTAL for idx in positions)


def test_metadata_publishes_irreversible_fingerprint_and_kdf():
    """A reconciliation token that is not the seed itself (A186 修法 3rd clause)."""
    key_seed = 4242
    result = svc.embed_watermark(_state_dict(), _bits(), [LAYER], key_seed)
    fingerprint = result.metadata.key_seed_fingerprint
    assert re.fullmatch(r"[0-9a-f]{16}", str(fingerprint)), (
        f"key_seed_fingerprint must be a hex SM3 prefix for API/audit use, got {fingerprint!r}"
    )
    assert fingerprint == ModelWatermarkService.key_seed_fingerprint(key_seed)
    assert str(result.metadata.position_kdf), (
        "metadata must disclose how positions were derived (honest disclosure)"
    )
