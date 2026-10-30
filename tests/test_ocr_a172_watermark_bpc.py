"""A172 — 图片水印 bits_per_channel 只写不读，验证器永远按 1 bit 解；文本/图片验证用子串判等。

`embed/extract_image_watermark` 都收 bits_per_channel（文档 1-4），`verify_image_watermark`
签名里没有 ⇒ 任何 bpc>1 嵌入的图永远返 False，而"水印已植入"是对外权属/溯源结论。
bpc 取值范围还要 fail-closed：0/负数/≥8 会让掩码静默吃掉整像素。
"""
import pytest
from io import BytesIO

from app.utils.watermark import (
    embed_image_watermark,
    embed_text_watermark,
    extract_text_watermark,
    generate_watermark,
    verify_image_watermark,
    verify_text_watermark,
)

USER = "user-a172"
SESSION = "sess-a172"


def _image(width: int = 100, height: int = 100) -> bytes:
    from PIL import Image

    img = Image.new("RGBA", (width, height), color=(10, 200, 30, 255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.parametrize("bpc", [1, 2, 3, 4])
def test_image_verify_roundtrip_for_every_documented_bpc(bpc: int):
    """文档承诺 1-4 bit/通道：每一档都必须 embed→verify 成立。"""
    expected = generate_watermark(USER, SESSION)
    marked = embed_image_watermark(_image(), expected, bits_per_channel=bpc)
    assert verify_image_watermark(marked, USER, SESSION, bits_per_channel=bpc) is True
    assert verify_image_watermark(marked, "other-user", SESSION, bits_per_channel=bpc) is False


def test_image_bpc_out_of_range_is_rejected():
    """bpc 越界（0/负数/≥8）会把像素高位静默清零 ⇒ 必须拒，不接受就报错。"""
    image = _image()
    for bad in (0, -1, 8, 9):
        with pytest.raises(ValueError):
            embed_image_watermark(image, "ab" * 8, bits_per_channel=bad)


def test_text_verify_requires_exact_match():
    """文本水印同样必须等值判定：夹带该水印片段的外部载荷不得被归因。"""
    expected = generate_watermark(USER, SESSION)
    smuggled = f"prefix-{expected}-suffix"
    text = embed_text_watermark("一份长文档正文", smuggled)
    assert extract_text_watermark(text) == smuggled
    assert verify_text_watermark(text, USER, SESSION) is False
    assert verify_text_watermark(embed_text_watermark("正文", expected), USER, SESSION) is True
