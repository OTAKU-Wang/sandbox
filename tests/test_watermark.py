"""Tests for watermark utilities — text steganography and LSB image watermarking."""
import pytest
from PIL import Image
from io import BytesIO

from app.utils.watermark import (
    generate_watermark,
    embed_text_watermark,
    extract_text_watermark,
    verify_text_watermark,
    embed_image_watermark,
    extract_image_watermark,
    verify_image_watermark,
)


def test_generate_watermark_deterministic():
    wm1 = generate_watermark("user1", "session1")
    wm2 = generate_watermark("user1", "session1")
    assert wm1 == wm2
    assert len(wm1) == 16


def test_generate_watermark_different_inputs():
    wm1 = generate_watermark("user1", "session1")
    wm2 = generate_watermark("user1", "session2")
    wm3 = generate_watermark("user2", "session1")
    assert wm1 != wm2
    assert wm1 != wm3


# ── Text Steganography ─────────────────────────────────────────────

def test_text_watermark_embed_extract():
    text = "Hello, this is a test output."
    wm = "abcdef1234567890"
    watermarked = embed_text_watermark(text, wm)
    # Watermarked text should look identical to the original (zero-width chars are invisible)
    assert watermarked.startswith(text)
    # But internally it should differ
    assert len(watermarked) > len(text)
    extracted = extract_text_watermark(watermarked)
    assert extracted == wm


def test_text_watermark_roundtrip():
    text = "Sensitive query result: 42 rows returned."
    wm = generate_watermark("user-abc", "session-xyz")
    watermarked = embed_text_watermark(text, wm)
    extracted = extract_text_watermark(watermarked)
    assert extracted == wm


def test_text_watermark_verify():
    text = "Some output data."
    user_id = "user1"
    session_id = "sess1"
    watermarked = embed_text_watermark(text, generate_watermark(user_id, session_id))
    assert verify_text_watermark(watermarked, user_id, session_id) is True
    assert verify_text_watermark(watermarked, "user2", session_id) is False
    assert verify_text_watermark(watermarked, user_id, "sess2") is False


def test_text_watermark_no_watermark():
    text = "Plain text with no watermark"
    assert extract_text_watermark(text) is None
    assert verify_text_watermark(text, "user1", "sess1") is False


def test_text_watermark_unicode_content():
    text = "中文输出结果：数据查询成功"
    wm = "deadbeef12345678"
    watermarked = embed_text_watermark(text, wm)
    extracted = extract_text_watermark(watermarked)
    assert extracted == wm
    # Original text should still be readable
    assert watermarked.startswith(text)


def test_text_watermark_empty_string():
    wm = "abc123"
    watermarked = embed_text_watermark("", wm)
    extracted = extract_text_watermark(watermarked)
    assert extracted == wm


# ── LSB Image Watermarking ─────────────────────────────────────────

def _make_test_image(width: int = 100, height: int = 100) -> bytes:
    """Create a simple test PNG image."""
    img = Image.new("RGBA", (width, height), color=(128, 64, 32, 255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_image_watermark_embed_extract():
    image = _make_test_image()
    wm = "abcdef1234567890"
    watermarked = embed_image_watermark(image, wm)
    assert isinstance(watermarked, bytes)
    assert len(watermarked) > 0
    extracted = extract_image_watermark(watermarked)
    assert extracted == wm


def test_image_watermark_roundtrip():
    image = _make_test_image()
    wm = generate_watermark("user-abc", "session-xyz")
    watermarked = embed_image_watermark(image, wm)
    extracted = extract_image_watermark(watermarked)
    assert extracted == wm


def test_image_watermark_verify():
    image = _make_test_image()
    user_id = "user1"
    session_id = "sess1"
    wm = generate_watermark(user_id, session_id)
    watermarked = embed_image_watermark(image, wm)
    assert verify_image_watermark(watermarked, user_id, session_id) is True
    assert verify_image_watermark(watermarked, "user2", session_id) is False
    assert verify_image_watermark(watermarked, user_id, "sess2") is False


def test_image_watermark_visual_similarity():
    """Watermarked image should be visually similar to the original."""
    image = _make_test_image()
    wm = "test1234"
    watermarked_bytes = embed_image_watermark(image, wm)

    orig = Image.open(BytesIO(image)).convert("RGBA")
    wm_img = Image.open(BytesIO(watermarked_bytes)).convert("RGBA")
    assert orig.size == wm_img.size

    # Pixel difference should be very small (LSB only changes last bit)
    orig_px = orig.load()
    wm_px = wm_img.load()
    max_diff = 0
    for y in range(orig.height):
        for x in range(orig.width):
            for c in range(3):
                diff = abs(orig_px[x, y][c] - wm_px[x, y][c])
                max_diff = max(max_diff, diff)
    # LSB change means max 1-bit difference per channel
    assert max_diff <= 1


def test_image_watermark_too_large():
    """Should raise if watermark doesn't fit in image."""
    image = _make_test_image(4, 4)  # Very small: 4*4*3*1 = 48 bits capacity
    # 32-bit length prefix + payload needs to exceed 48 bits
    long_wm = "x" * 10  # 80 bits payload + 32 prefix = 112 bits > 48
    with pytest.raises(ValueError, match="Watermark too large"):
        embed_image_watermark(image, long_wm)


def test_image_watermark_no_watermark():
    image = _make_test_image()
    assert extract_image_watermark(image) is None
