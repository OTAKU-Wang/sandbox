"""A80 — verify_image_watermark 丢弃 bits_per_channel 且用子串包含判等。

嵌入/提取两侧都收 bits_per_channel，只有验证器没有：任何 `bits_per_channel > 1`
嵌入的图永远验证失败（泄密溯源里"查不到水印"= 静默无法归因）；
`expected in extracted` 是子串包含，携带该 16 hex 作片段的外部水印会被误归因。
"""
from io import BytesIO

from app.utils.watermark import (
    embed_image_watermark,
    extract_image_watermark,
    generate_watermark,
    verify_image_watermark,
)

USER = "user-a80"
SESSION = "sess-a80"


def _image(width: int = 100, height: int = 100) -> bytes:
    from PIL import Image

    img = Image.new("RGBA", (width, height), color=(128, 64, 32, 255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_verify_accepts_bits_per_channel():
    """验证器必须能按嵌入时的 bpc 解码，而不是固定 1 bit。"""
    expected = generate_watermark(USER, SESSION)
    image = _image()
    for bpc in (2, 3, 4):
        marked = embed_image_watermark(image, expected, bits_per_channel=bpc)
        assert extract_image_watermark(marked, bits_per_channel=bpc) == expected
        assert verify_image_watermark(marked, USER, SESSION, bits_per_channel=bpc) is True


def test_multi_bpc_not_false_negative_with_default():
    """按默认 1 bit 解 2 bit 嵌入的图：不得返回 True（真阴性方向也要钉住）。"""
    expected = generate_watermark(USER, SESSION)
    marked = embed_image_watermark(_image(), expected, bits_per_channel=2)
    assert verify_image_watermark(marked, USER, SESSION) is False


def test_substring_payload_is_not_attributed():
    """包含该水印作片段但整体不相等的外部载荷，不得被判为该用户的水印。"""
    expected = generate_watermark(USER, SESSION)
    smuggled = f"ff{expected}ff"
    marked = embed_image_watermark(_image(), smuggled)
    assert extract_image_watermark(marked) == smuggled
    assert verify_image_watermark(marked, USER, SESSION) is False
