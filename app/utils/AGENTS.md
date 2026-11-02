# app/utils/

**Score 10 — distinct domain: crypto honesty gates + attribution watermarking** (4 modules / 769 LOC). Small but load-bearing: `sm3_hash` appears in 72 files, `app.utils.crypto` in 47, `app.core.config`-adjacent honesty state in both crypto paths, and both watermarking families feed the delivery attribution path (`api/deliveries.py`, `services/output_inspection.py`). It earns its own file because the rules here are disclosure rules, not style rules.

## WHERE TO LOOK
| Task | Module | Notes |
|------|--------|-------|
| Hashing / symmetric encryption | `crypto.py` (92 L) | `sm3_hash` → gmssl SM3 when installed, else SHA-256 gated by `CDS_SM3_FALLBACK_ALLOWED`; `SM4Cipher.encrypt_gcm/decrypt_gcm` = AES-256-GCM, 12-byte nonce, returns `(ct, nonce, tag)`, tag mismatch → `ValueError` |
| Fallback honesty gate | `crypto.py::_sm3_fallback_allowed` | returns **False on any exception** (A71 — an unreadable config must not flip the gate open); `_sm3_fallback_warned` keeps the warning to once per process |
| Subject watermark id | `watermark.py::generate_watermark` | `md5(f"{user}:{session}")[:16]` — the value every verify compares against |
| Text steganography | `watermark.py` (306 L) | 4 zero-width chars × 2 bits + `U+2060` sentinel + 2-byte big-endian length header (8 ZWC chars); `extract_text_watermark` reads the header before the payload |
| Image LSB watermark | `watermark.py::embed/extract/verify_image_watermark` | `bits_per_channel` defaults to 1 and is validated to 1-4 (`_validate_bits_per_channel`, bool rejected); it is a DECODE parameter and must be threaded through verify |
| OOXML delivery watermarks | `office_watermark.py` (371 L) | docx = VML `#_x0000_t136` WordArt shape injected into the page header; xlsx = `headerFooter/oddHeader` text + `docProps/custom.xml`; csv/tsv = bytes untouched |

## CONVENTIONS
- `__init__.py` is empty (full-path imports only), yet `office_watermark.py` carries the repo's only `__all__`. Its four `WATERMARK_METHOD_*` constants are the sole vocabulary of `MANIFEST.files[].watermark_method`: `embedded_watermark` / `header_text` / `preview_only` / `zero_width_text`.
- `office_watermark.py` is stdlib-only by decision: `zipfile` + string-level XML surgery. No python-docx/openpyxl, no ElementTree round-trip — OOXML namespace prefixes and the `standalone="yes"` declaration must survive byte-for-byte or Word/Excel declare the file corrupt.
- A docx injection counts only when all four agree: `word/headerCdsWm.xml` part + `document.xml.rels` relationship + `[Content_Types].xml` Override + `w:sectPr` → `w:headerReference` (a missing sectPr is created just before `</w:body>`; the `#_x0000_t136` shapetype boilerplate must ship with the shape).
- Verify is equality against `generate_watermark`, never substring (A80/A172) — for text and images alike.
- The honest tiers are the API: xlsx is `header_text`, csv/tsv is `preview_only`, and neither may be reported as a real background watermark.

## ANTI-PATTERNS (THIS PROJECT)
- Never present `SM4Cipher` as real SM4 (it is AES-256-GCM; Tongsuo is vendored, not wired), and never call `sm3_hash` output "SM3" when the SHA-256 fallback ran.
- Never let a config-read failure open the SM3 fallback, never delete the one-per-process warning, never add a second silent fallback path (the Merkle-side twin is `services/merkle_service.py`, fixed in A148).
- Never label an `.xlsx` delivery `embedded_watermark`, and never rewrite `.csv`/`.tsv` bytes to carry a watermark — one byte can break downstream parsing.
- No new `watermark_method` values and no library swap: `api/deliveries.py` manifests + Chinese disclosure strings and `tests/test_watermark_office_delivery.py` lock the current four.
- Never drop `bits_per_channel` between embed and verify: a bpc>1 watermark read at bpc=1 is a silent false negative in the attribution path.

## COMMANDS
```bash
python -m compileall -q app/utils
python -m pytest -q tests/test_crypto.py tests/test_watermark.py tests/test_output_inspection.py \
  tests/test_pz04_honesty_gates.py tests/test_ocr_a71_sm3_fallback_failclosed.py \
  tests/test_ocr_a80_watermark_verify_bits.py tests/test_ocr_a172_watermark_bpc.py \
  tests/test_watermark_office_delivery.py
```
