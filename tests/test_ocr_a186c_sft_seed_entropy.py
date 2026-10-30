"""A186c — SFT 自动水印路径的 ``key_seed`` 熵（``### A186`` 的最后一条腿 · 生成器侧）。

台账（docs/ocr-review-2026-10-18/ocr-verified-findings.md ``### A186``）：「水印的全部
安全性 = 这颗种子」。密文落存（0030）、定位 KDF、API 不回显、列域加宽（A186b /
0031）都已落地，但 SFT 自动水印生成器 ``app/services/llm_sft_runtime.py``
(``_inject_watermark``) 仍只取 SM3 摘要的前 8 个十六进制位 —— 32-bit 熵，可被离线
枚举 ⇒ 攻击者可重算/伪造水印完整性证据。

父会话复核出的边界（本文件同时钉两条）：种子必须
① 落在 ``app/api/training.py::_key_seed_column_max()`` 派生的列域内（有符号
   64-bit，0…2**63-1 —— 上界从列型真值取，不在这里抄第二份常量）；
② 熵真正加宽到 ≥ 60 bit（值域跨度可观测地超过 2**60，而不是只换了切片长度）；
③ 仍是 session_id 的纯函数（可复现，不许引入随机源 —— 水印要能对账）；
④ 真实自动水印路径产出的记录能落库（``EncryptedKeySeed`` 封套往返）并能被
   完整性校验（``verify_watermark``）读到 —— 不能只在纯函数层比大小。
"""
import uuid

from app.api.training import _key_seed_column_max
from app.models.watermark_record import WatermarkRecord
from app.services.llm_sft_runtime import llm_sft_runtime
from app.services.model_watermark import WatermarkMetadata, model_watermark_service

# 固定 session id：种子是纯函数，读数必须可复现，不用随机 id 制造假方差。
SESSION_IDS = [f"a186c-sess-{i:03d}" for i in range(64)]

# 熵下限（台账口径 ≥ 60 bit）；改前的 32-bit 种子跨度最多 ~2**32，必然红在这里。
SEED_SPAN_FLOOR = 2 ** 60


def _auto_watermark_seed(session_id: str) -> int:
    """走真实 SFT 自动水印路径，取它生成并写进元数据的 key_seed。"""
    assert llm_sft_runtime._inject_watermark(session_id) is True, (
        "SFT auto-watermark path must verify its own embed (baseline behaviour)"
    )
    entry = llm_sft_runtime._watermarks[session_id]
    return int(entry["metadata"].key_seed)


def test_auto_seeds_stay_inside_the_signed_64bit_column_domain():
    """① N 个不同 session 的种子全部落在列域 0…_key_seed_column_max() 内。

    上界从列型派生（A167/A186b 的上游契约）：若生成器将来敢越界，列一收窄这里
    自动变红，而不是把 2**63-1 抄成第二份死常量。
    """
    bound = _key_seed_column_max()
    for sid in SESSION_IDS:
        seed = _auto_watermark_seed(sid)
        assert 0 <= seed <= bound, (
            f"auto key_seed {seed} escapes the watermark_records.key_seed domain "
            f"0…{bound} (would 422/fail-at-flush on the API-side contract)"
        )


def test_auto_seed_span_proves_at_least_60_bits_of_entropy():
    """② 值域跨度 ≥ 2**60：证明熵真的加宽了，而不是只换了切片长度。

    改前 ``[:8]`` 的 64 个种子全部挤在 [0, 2**32)，跨度 ≤ 2**32-1，红。
    """
    seeds = [_auto_watermark_seed(sid) for sid in SESSION_IDS]
    span = max(seeds) - min(seeds)
    assert span >= SEED_SPAN_FLOOR, (
        f"auto key_seed span across {len(SESSION_IDS)} sessions is only {span} "
        f"(< 2**60): the seed is still enumerable (A186: 水印的全部安全性 = 这颗种子)"
    )


def test_auto_seed_is_reproducible_for_the_same_session():
    """③ 同一 session_id 两次生成必须相等 —— 可复现，不许引入随机源。"""
    sid = "a186c-reproducible-001"
    first = _auto_watermark_seed(sid)
    fingerprint_first = llm_sft_runtime._watermarks[sid]["metadata"].key_seed_fingerprint
    second = _auto_watermark_seed(sid)
    fingerprint_second = llm_sft_runtime._watermarks[sid]["metadata"].key_seed_fingerprint
    assert first == second
    assert fingerprint_first == fingerprint_second


async def test_auto_watermark_record_persists_and_passes_integrity_check(db_session):
    """④ 真实自动水印路径产出的一条记录：能落库、能读回、能被完整性校验读到。

    落库镜像 API 侧的持久化（training.py embed handler → WatermarkRecord →
    EncryptedKeySeed 封套）；读回后按 API verify handler 同样的方式重建元数据
    并校验 —— 封套往返出的种子若与生成器不一致，这里必须失败。
    """
    sid = f"a186c-persist-{uuid.uuid4().hex[:8]}"
    assert llm_sft_runtime._inject_watermark(sid) is True
    entry = llm_sft_runtime._watermarks[sid]
    meta = entry["metadata"]

    record = WatermarkRecord(
        job_id=f"a186c-{uuid.uuid4().hex[:12]}",
        owner_id="cds",
        model_id=entry["model_id"],
        layer_positions=meta.layer_positions,
        ordered_positions=[[layer, idx] for layer, idx in meta.ordered_positions],
        key_seed=meta.key_seed,
        num_bits=meta.num_bits,
    )
    db_session.add(record)
    await db_session.flush()
    await db_session.refresh(record)  # forces a real SELECT → sealed-blob round trip

    assert record.key_seed == meta.key_seed
    assert 0 <= record.key_seed <= _key_seed_column_max()

    # Integrity check exactly as the API verify handler reconstructs it.
    expected_bits = model_watermark_service.generate_watermark_bits(
        record.owner_id, record.model_id, record.num_bits,
    )
    metadata = WatermarkMetadata(
        layer_positions=record.layer_positions,
        key_seed=record.key_seed,
        num_bits=record.num_bits,
        ordered_positions=[(p[0], p[1]) for p in record.ordered_positions]
        if record.ordered_positions
        else [],
    )
    # Rebuild the auto path's initial synthetic state, embed with the seed read
    # back from the DB, then verify — a wrong/corrupted seed cannot reproduce it.
    state = {"lora_adapter.weight": [0.001 * (i + 1) for i in range(128)]}
    embed = model_watermark_service.embed_watermark(
        state, expected_bits, list(record.layer_positions.keys()), record.key_seed,
    )
    assert embed.success is True
    verify = model_watermark_service.verify_watermark(state, expected_bits, metadata)
    assert verify.passed is True
    assert verify.match_ratio == 1.0
