"""Tests for Streaming Inspection Proxy (SS-05 §5)."""
import pytest
import time

from app.services.streaming_inspector import (
    StreamingInspector,
    StreamingInspectorPool,
    pii_regex_scan,
    auto_redact,
    PIIType,
    PIIHit,
    ChunkInspectionResult,
    RedactionEvent,
    WindowCheckResult,
    InspectionDecision,
    inspector_pool,
)


# ── PII Regex Scanning ────────────────────────────────────────

class TestPIIRegexScan:
    def test_detect_chinese_id(self):
        text = "用户身份证号：110101199001011234"
        hits = pii_regex_scan(text)
        assert any(h.pii_type == PIIType.CHINESE_ID for h in hits)

    def test_detect_phone(self):
        text = "联系电话：13812345678"
        hits = pii_regex_scan(text)
        assert any(h.pii_type == PIIType.PHONE for h in hits)

    def test_detect_email(self):
        text = "邮箱：user@example.com"
        hits = pii_regex_scan(text)
        assert any(h.pii_type == PIIType.EMAIL for h in hits)

    def test_detect_bank_card(self):
        text = "卡号：6222021234567890123"
        hits = pii_regex_scan(text)
        assert any(h.pii_type == PIIType.BANK_CARD for h in hits)

    def test_detect_ipv4(self):
        text = "服务器：192.168.1.100"
        hits = pii_regex_scan(text)
        assert any(h.pii_type == PIIType.IPV4 for h in hits)

    def test_no_pii(self):
        text = "这是一段没有敏感信息的普通文本"
        hits = pii_regex_scan(text)
        assert len(hits) == 0

    def test_multiple_pii(self):
        text = "手机13812345678，邮箱test@example.com"
        hits = pii_regex_scan(text)
        assert len(hits) >= 2

    def test_hit_positions(self):
        text = "电话13812345678"
        hits = pii_regex_scan(text)
        phone_hit = next(h for h in hits if h.pii_type == PIIType.PHONE)
        assert text[phone_hit.start:phone_hit.end] == "13812345678"


# ── Auto Redaction ────────────────────────────────────────────

class TestAutoRedact:
    def test_redact_phone(self):
        text = "电话13812345678"
        hits = pii_regex_scan(text)
        result = auto_redact(text, hits)
        assert "13812345678" not in result
        assert "1*********8" in result

    def test_redact_preserves_context(self):
        text = "联系人：张三，电话13812345678，地址北京"
        hits = pii_regex_scan(text)
        result = auto_redact(text, hits)
        assert "张三" in result
        assert "北京" in result
        assert "13812345678" not in result

    def test_redact_no_hits(self):
        text = "普通文本"
        hits = []
        result = auto_redact(text, hits)
        assert result == text

    def test_redact_short_value(self):
        text = "AB"
        hit = PIIHit(pii_type=PIIType.PHONE, value="AB", start=0, end=2)
        result = auto_redact(text, [hit])
        assert result == "**"

    def test_redact_email(self):
        text = "邮箱user@example.com"
        hits = pii_regex_scan(text)
        result = auto_redact(text, hits)
        assert "user@example.com" not in result

    def test_redact_chinese_id(self):
        text = "身份证110101199001011234"
        hits = pii_regex_scan(text)
        result = auto_redact(text, hits)
        assert "110101199001011234" not in result
        # First and last char preserved
        assert result.startswith("身份证1")


# ── Chunk Inspection ──────────────────────────────────────────

class TestChunkInspection:
    def test_inspect_clean_chunk(self):
        inspector = StreamingInspector(session_id="s1")
        output, result = inspector.inspect_chunk(b"Hello, world!")
        assert result.pii_hits == []
        assert result.redacted is False
        assert result.chunk_index == 0

    def test_inspect_pii_chunk(self):
        # OCR-A105 新契约：短 chunk 处于边界保留带内，只有 final=True 才定局。
        # 旧形状（不传 final 就期望立即命中）正是 4KB 切块逃逸的温床，故显式定局。
        inspector = StreamingInspector(session_id="s1")
        output, result = inspector.inspect_chunk(b"Phone: 13812345678", final=True)
        assert len(result.pii_hits) > 0
        assert result.redacted is True
        assert b"13812345678" not in output

    def test_inspect_increments_index(self):
        inspector = StreamingInspector(session_id="s1")
        _, r1 = inspector.inspect_chunk(b"chunk1")
        _, r2 = inspector.inspect_chunk(b"chunk2")
        assert r1.chunk_index == 0
        assert r2.chunk_index == 1

    def test_inspect_tracks_stats(self):
        # 流尾以 final=True 收尾（新契约）：定局时才计命中，stats 语义不变
        inspector = StreamingInspector(session_id="s1")
        inspector.inspect_chunk(b"Phone: 13812345678")
        inspector.inspect_chunk(b"Clean text", final=True)
        stats = inspector.stats
        assert stats["chunks_inspected"] == 2
        assert stats["total_pii_hits"] > 0

    def test_inspect_creates_audit_events(self):
        inspector = StreamingInspector(session_id="s1")
        inspector.inspect_chunk(b"Phone: 13812345678", final=True)
        log = inspector.get_audit_log()
        assert len(log) > 0
        assert isinstance(log[0], RedactionEvent)

    def test_inspect_result_sizes(self):
        inspector = StreamingInspector(session_id="s1")
        data = b"Phone: 13812345678"
        _, result = inspector.inspect_chunk(data, final=True)
        assert result.original_size == len(data)
        assert result.output_size > 0


class TestBoundaryCarryContract:
    """OCR-A105（2026-10-30）新契约钉：未 final 不定局 + flush 必收口.

    旧缺陷（4KB 字节切块 + errors="ignore"）让跳边界的 PII 永不命中、跨块多字节
    被静默吞掉。修复引入 64B 边界保留带后，短 chunk 的匹配在定局前**不得外发**
    ——这里钉住「不外发未定局字节」与「flush 收口」「截断≠坏字节」。
    """

    def test_sub_carry_pii_not_emitted_before_final(self):
        inspector = StreamingInspector(session_id="s1")
        output, result = inspector.inspect_chunk(b"Phone: 13812345678")  # 19B < carry
        assert result.pii_hits == []
        assert result.redacted is False
        assert b"13812345678" not in output  # 保留带字节绝不以未脱敏形态外发
        assert output == b""

    def test_flush_resolves_carried_tail_pii(self):
        inspector = StreamingInspector(session_id="s1")
        _, r1 = inspector.inspect_chunk(b"Phone: 1381234567")  # 残段：不许半号外发/半号计hit
        _, r2 = inspector.inspect_chunk(b"8")                   # 号码拼齐但仍在保留带
        tail_out, r3 = inspector.flush()                        # 流尾定局
        assert r1.pii_hits == [] and r2.pii_hits == []
        assert r3.pii_hits and b"13812345678" not in tail_out
        assert inspector.stats["total_pii_hits"] >= 1

    def test_multibyte_split_across_chunks_lossless(self):
        payload = "结论：中文输出αβ".encode("utf-8")
        insp = StreamingInspector(session_id="s1")
        out = b""
        for i in range(0, len(payload), 3):  # 3B 切块必然劈开多字节
            chunk_out, res = insp.inspect_chunk(payload[i:i + 3])
            assert res.undecodable is False  # 半截序列应被缓存而非判坏
            out += chunk_out
        tail_out, _ = insp.flush()
        out += tail_out
        assert out == payload  # 零丢失

    def test_truly_broken_bytes_flag_undecodable(self):
        insp = StreamingInspector(session_id="s1")
        _, res = insp.inspect_chunk(b"abc\xff\xfe\xfd")  # 中段坏字节：不是流截断
        assert res.undecodable is True  # fail-closed 交调用方阻断，绝不吞字节


# ── Rolling Window Check ──────────────────────────────────────

class TestRollingWindow:
    def test_window_clean(self):
        inspector = StreamingInspector(session_id="s1", window_seconds=60)
        inspector.inspect_chunk(b"Clean text without PII")
        check = inspector.check_window()
        assert check.exceeded is False
        assert check.action == "continue"

    def test_window_with_pii(self):
        inspector = StreamingInspector(
            session_id="s1",
            window_seconds=60,
            pii_density_threshold=0.01,
        )
        # Large clean text + small PII
        inspector.inspect_chunk(b"x" * 10000)
        inspector.inspect_chunk(b"Phone: 13812345678")
        check = inspector.check_window()
        # Should not exceed with low density
        assert isinstance(check.pii_density, float)

    def test_window_exceeded(self):
        inspector = StreamingInspector(
            session_id="s1",
            window_seconds=60,
            pii_density_threshold=0.0001,  # Very low threshold
        )
        # High PII density
        for _ in range(10):
            inspector.inspect_chunk(b"Phone: 13812345678, ID: 110101199001011234")
        check = inspector.check_window()
        assert check.exceeded is True
        assert check.action == "suspend"
        assert inspector.window_exceeded is True

    def test_window_check_result_fields(self):
        inspector = StreamingInspector(session_id="s1", window_seconds=30)
        inspector.inspect_chunk(b"test")
        check = inspector.check_window()
        assert check.session_id == "s1"
        assert check.window_seconds == 30
        assert check.threshold == 0.01  # default


# ── Inspector Pool ────────────────────────────────────────────

class TestInspectorPool:
    def test_get_or_create(self):
        pool = StreamingInspectorPool()
        insp1 = pool.get_or_create("s1")
        insp2 = pool.get_or_create("s1")
        assert insp1 is insp2

    def test_remove(self):
        pool = StreamingInspectorPool()
        pool.get_or_create("s1")
        assert pool.remove("s1") is True
        assert pool.active_sessions == 0

    def test_remove_nonexistent(self):
        pool = StreamingInspectorPool()
        assert pool.remove("nonexistent") is False

    def test_get_all_stats(self):
        pool = StreamingInspectorPool()
        pool.get_or_create("s1")
        pool.get_or_create("s2")
        stats = pool.get_all_stats()
        assert len(stats) == 2

    def test_active_sessions(self):
        pool = StreamingInspectorPool()
        assert pool.active_sessions == 0
        pool.get_or_create("s1")
        assert pool.active_sessions == 1
        pool.get_or_create("s2")
        assert pool.active_sessions == 2


# ── Enums ─────────────────────────────────────────────────────

class TestEnums:
    def test_pii_type_values(self):
        assert PIIType.CHINESE_ID.value == "chinese_id"
        assert PIIType.PHONE.value == "phone"
        assert PIIType.EMAIL.value == "email"

    def test_inspection_decision_values(self):
        assert InspectionDecision.ALLOW.value == "allow"
        assert InspectionDecision.BLOCK.value == "block"


# ── Dataclass Construction ────────────────────────────────────

class TestDataclasses:
    def test_pii_hit(self):
        hit = PIIHit(pii_type=PIIType.PHONE, value="138", start=0, end=3)
        assert hit.redacted_value == ""

    def test_chunk_inspection_result_defaults(self):
        r = ChunkInspectionResult(chunk_index=0, pii_hits=[])
        assert r.redacted is False

    def test_redaction_event_defaults(self):
        e = RedactionEvent(event_id="e1", session_id="s1", chunk_index=0, pii_type=PIIType.PHONE)
        assert e.timestamp is not None

    def test_window_check_result(self):
        r = WindowCheckResult(
            session_id="s1", window_seconds=60, total_chunks=1,
            total_pii_hits=0, pii_density=0.0, threshold=0.01,
            exceeded=False, action="continue",
        )
        assert r.action == "continue"
