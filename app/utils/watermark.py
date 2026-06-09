"""Watermark utilities — text steganography and LSB image watermarking.

Text: Embeds watermark bits as zero-width Unicode characters (U+200B/U+200C/U+200D/U+FEFF).
Image: Embeds watermark bits into pixel least-significant bits (LSB).
"""
import hashlib
import struct
from io import BytesIO
from typing import Literal

# Zero-width characters used for text steganography
# Each encodes 2 bits: 00→U+200B, 01→U+200C, 10→U+200D, 11→U+FEFF
_ZWC = ["​", "‌", "‍", "﻿"]
_ZWC_MAP = {ch: i for i, ch in enumerate(_ZWC)}


def generate_watermark(user_id: str, session_id: str) -> str:
    """Generate a 16-char hex watermark identifier."""
    return hashlib.md5(f"{user_id}:{session_id}".encode()).hexdigest()[:16]


def _bits_from_bytes(data: bytes) -> list[int]:
    """Convert bytes to a flat list of bits (MSB first)."""
    bits = []
    for byte in data:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    return bits


def _bytes_from_bits(bits: list[int]) -> bytes:
    """Convert a flat list of bits back to bytes."""
    out = []
    for i in range(0, len(bits), 8):
        byte = 0
        for b in bits[i:i + 8]:
            byte = (byte << 1) | b
        out.append(byte)
    return bytes(out)


# ── Text Steganography ─────────────────────────────────────────────

# Sentinel: U+2060 (Word Joiner) — zero-width, not in _ZWC set
_SENTINEL = "⁠"


def embed_text_watermark(text: str, watermark: str) -> str:
    """Embed watermark into text using zero-width Unicode characters.

    Format: text + U+2060 + 2-byte big-endian length + ZWC-encoded data.
    Each ZWC char encodes 2 bits. Extraction reads length header first,
    then exactly that many bytes of encoded data.
    """
    data = watermark.encode("utf-8")
    length_header = struct.pack(">H", len(data))  # 2 bytes = 16 bits = 8 ZWC chars
    full = length_header + data
    bits = _bits_from_bytes(full)

    # Pad to even length
    if len(bits) % 2:
        bits.append(0)

    encoded = []
    for i in range(0, len(bits), 2):
        pair = (bits[i] << 1) | bits[i + 1]
        encoded.append(_ZWC[pair])

    return text + _SENTINEL + "".join(encoded)


def extract_text_watermark(text: str) -> str | None:
    """Extract a watermark embedded by embed_text_watermark.

    Returns the watermark string, or None if no valid watermark found.
    """
    idx = text.rfind(_SENTINEL)
    if idx == -1:
        return None

    encoded = text[idx + len(_SENTINEL):]
    if not encoded:
        return None

    # Collect all ZWC characters (skip any non-ZWC that might be adjacent)
    zwc_chars = [ch for ch in encoded if ch in _ZWC_MAP]
    if len(zwc_chars) < 8:  # Need at least 8 ZWC chars for the 2-byte length header
        return None

    # Decode ZWC → bits
    bits = []
    for ch in zwc_chars:
        val = _ZWC_MAP[ch]
        bits.append((val >> 1) & 1)
        bits.append(val & 1)

    # Parse 2-byte length header (16 bits → 8 ZWC chars)
    header_bits = bits[:16]
    data_len = struct.unpack(">H", _bytes_from_bits(header_bits))[0]
    if data_len == 0 or data_len > 1024:
        return None

    # Extract exactly data_len bytes
    data_bits = bits[16:16 + data_len * 8]
    if len(data_bits) < data_len * 8:
        return None

    data = _bytes_from_bits(data_bits)
    try:
        return data[:data_len].decode("utf-8")
    except UnicodeDecodeError:
        return None


def verify_text_watermark(text: str, user_id: str, session_id: str) -> bool:
    """Verify that the text contains the expected watermark."""
    expected = generate_watermark(user_id, session_id)
    extracted = extract_text_watermark(text)
    return extracted is not None and expected in extracted


# ── LSB Image Watermarking ─────────────────────────────────────────

def _load_image_bytes(image_bytes: bytes):
    """Load a PIL Image from raw bytes. Returns (Image, format)."""
    from PIL import Image
    img = Image.open(BytesIO(image_bytes))
    fmt = img.format or "PNG"
    return img, fmt


def embed_image_watermark(image_bytes: str | bytes, watermark: str, bits_per_channel: int = 1) -> bytes:
    """Embed watermark into image using LSB steganography.

    Args:
        image_bytes: Raw image file bytes.
        watermark: Watermark string to embed.
        bits_per_channel: How many LSBs to use per channel (1-4). Higher = more capacity.

    Returns:
        Watermarked image bytes (PNG).
    """
    from PIL import Image

    if isinstance(image_bytes, str):
        image_bytes = image_bytes.encode()

    img, fmt = _load_image_bytes(image_bytes)
    img = img.convert("RGBA")
    pixels = img.load()
    width, height = img.size

    # Prepare watermark payload: 4-byte length prefix + data
    wm_bytes = watermark.encode("utf-8")
    payload = struct.pack(">I", len(wm_bytes)) + wm_bytes
    bits = _bits_from_bytes(payload)

    # Total capacity: width * height * 3 channels * bits_per_channel
    capacity = width * height * 3 * bits_per_channel
    if len(bits) > capacity:
        raise ValueError(
            f"Watermark too large for image: {len(bits)} bits needed, "
            f"{capacity} available ({width}x{height}, {bits_per_channel}bpc)"
        )

    mask = (0xFF << bits_per_channel) & 0xFF  # e.g. 0xFE for 1 bit
    bit_idx = 0
    total_bits = len(bits)

    for y in range(height):
        for x in range(width):
            if bit_idx >= total_bits:
                break
            r, g, b, a = pixels[x, y]
            channels = [r, g, b]
            for c in range(3):
                if bit_idx >= total_bits:
                    break
                # Extract bits_per_channel bits from the payload
                val = 0
                for b_idx in range(bits_per_channel):
                    if bit_idx < total_bits:
                        val = (val << 1) | bits[bit_idx]
                        bit_idx += 1
                    else:
                        val = val << 1
                channels[c] = (channels[c] & mask) | val
            pixels[x, y] = (channels[0], channels[1], channels[2], a)
        if bit_idx >= total_bits:
            break

    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def extract_image_watermark(image_bytes: str | bytes, bits_per_channel: int = 1) -> str | None:
    """Extract watermark from an LSB-watermarked image.

    Returns the watermark string, or None if extraction fails.
    """
    from PIL import Image

    if isinstance(image_bytes, str):
        image_bytes = image_bytes.encode()

    img, fmt = _load_image_bytes(image_bytes)
    img = img.convert("RGBA")
    pixels = img.load()
    width, height = img.size

    # First extract the 4-byte length (32 bits)
    length_bits = []
    bit_idx = 0
    needed = 32

    for y in range(height):
        for x in range(width):
            if bit_idx >= needed:
                break
            r, g, b, a = pixels[x, y]
            for ch in (r, g, b):
                if bit_idx >= needed:
                    break
                for b_idx in range(bits_per_channel - 1, -1, -1):
                    length_bits.append((ch >> b_idx) & 1)
                    bit_idx += 1
        if bit_idx >= needed:
            break

    if len(length_bits) < 32:
        return None

    wm_len = struct.unpack(">I", _bytes_from_bits(length_bits))[0]
    if wm_len == 0 or wm_len > 1024:
        return None

    # Now extract wm_len bytes worth of bits
    total_bits = (4 + wm_len) * 8
    all_bits = []
    bit_idx = 0

    for y in range(height):
        for x in range(width):
            if bit_idx >= total_bits:
                break
            r, g, b, a = pixels[x, y]
            for ch in (r, g, b):
                if bit_idx >= total_bits:
                    break
                for b_idx in range(bits_per_channel - 1, -1, -1):
                    if bit_idx >= 32:
                        all_bits.append((ch >> b_idx) & 1)
                    bit_idx += 1
        if bit_idx >= total_bits:
            break

    data = _bytes_from_bits(all_bits)
    try:
        return data[:wm_len].decode("utf-8")
    except (UnicodeDecodeError, struct.error):
        return None


def verify_image_watermark(image_bytes: str | bytes, user_id: str, session_id: str) -> bool:
    """Verify that an image contains the expected watermark."""
    expected = generate_watermark(user_id, session_id)
    extracted = extract_image_watermark(image_bytes)
    return extracted is not None and expected in extracted
