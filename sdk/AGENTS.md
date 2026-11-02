# sdk/ — cds-sdk, standalone Python REST client (scored 10: distinct domain — own pyproject.toml, pip-installable deliverable with its own version, decoupled from app/ conventions; 8 tracked files, 87% code, 26 test modules import it)

Sync httpx client for the CDS `/api/v1` surface. Python ≥3.10; only dependency httpx (bounded `>=0.27,<1.0`, A207); streaming extra `[stream]` = websocket-client `>=1.6,<2.0`.

## STRUCTURE
```
sdk/
├── cds_sdk/
│   ├── __init__.py   # __all__ = CDSClient, CDSError, CDSApiError, CDSResponse; version resolver (60 L)
│   ├── client.py     # 1314 L / 103 public methods — the entire API surface
│   ├── stream.py     # 277 L WS streaming exec (optional dep)
│   ├── _encoding.py  # 34 L — path_segment / query_value (A215: a value with `/` or `?` must not traverse)
│   └── py.typed      # shipped via [tool.setuptools.package-data]
├── pyproject.toml    # name cds-sdk, version 0.3.0 (single source)
└── README.md         # 207 L Chinese usage doc
```
(`build/` and `cds_sdk.egg-info/` exist on disk but are gitignored artifacts — never read them as truth.)

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Any endpoint call | `client.py` | session lifecycle + files/snapshots + contracts + tasks + training/inference |
| Auth (SS-12 §4.6, exactly one of three) | `client.py` `CDSClient.__init__` (L277+) | `token` (human JWT) \| `app_id`+`app_secret` (`X-App-*` headers, PZ-11⑥) \| `api_key` (`cds_ak_…`, sent as Bearer); mixing them raises at construction |
| Flow surface (SS-15) | `client.py` L1023-1314 | `# ── flow orchestration` banners: notebooks (L1023) / workloads (L1086) / delivery (L1149) + endpoints & webhooks (~40 ops) |
| Error semantics | `client.py` `CDSApiError` | W2 envelope `{code,message,detail,request_id}` + `.retry_after` parsed from the header |
| Honest-degradation fields | `client.py:19` `DISCLOSURE_KEYS` | `is_simulation, tee_mode, backend, engine, trust_root, signing_backend, aead_algorithm, dp_applied` — extracted additively from any response carrying them |
| Cross-language parity constants | `client.py:145-147` | `PAYLOAD_MAX_LEN=64 / _ROLE=16 / _SCOPE=64` mirror `app/services/crypto_service.py`; `tests/test_ocr_a255_xlang_parity.py` extracts the FE parity values |
| Streaming exec | `stream.py` | `SUPPORTED_STREAM_LEVELS = ("L0", "k8s")` — mirrors the backend capability matrix, NOT an exhaustive level list (L2 is deprecated, `l2c` is the current high-isolation level and does not stream) |

## CONVENTIONS
- Responses are `CDSResponse(dict)` subclasses — dict-compatible, plus `.disclosure` (honest-degradation keys, PZ-06 family).
- Retries ONLY 429/503 **and** only idempotent methods (GET/HEAD/OPTIONS, A213/A88): a gateway can answer 429/503 *after* the backend committed the write, so writes raise instead — with `.retry_after` populated so the caller can replay deliberately.
- The wait honours `Retry-After`, clamped to `[0, 60]` s with a 1.0 s fallback for a missing/unparseable header (`_RETRY_AFTER_CAP_SECONDS`, `client.py:94`). The old 5 s clip is gone (MAJOR-7) — the 60 s ceiling exists only because this client is synchronous.
- `create_task`'s script/dataset refs default to the body (`task_payload_in_body=True`, A87/A216); pass `False` only to talk to a pre-body server.
- Legacy `CDSError` kept for back-compat alongside `CDSApiError`.
- Chinese README; consumed by 26 repo test modules (`tests/test_sdk_client.py`, `test_ss15_sdk.py`, `test_error_contract.py`, `test_ocr_a213_retry_idempotency.py`, `test_ocr_a88_write_no_retry.py`, `test_sdk_retry_after_cap.py`, `test_ocr_a78_sdk_version_single_source.py`, `test_ocr_a207_sdk_packaging.py`, `test_ocr_a255_xlang_parity.py`) — the repo pytest suite is the SDK's test home, it has no `sdk/tests/`.

## ANTI-PATTERNS
- `SESSION_TERMINATED` (410) is deliberately EXCLUDED from the retry set — terminal by W2 contract; never add it back (and don't add writes to the retry set either).
- Never fake streaming: missing `websocket-client` raises `CDSError` with an install hint; a `blocked` WS frame means T5 DLP killed the stream — treat output as untrusted. Terminal frames are `exit`/`blocked`/`error`; a socket that dies before one raises `CDSError(504)` with the structured marker `.truncated = True` (never report truncated output as complete).
- Every interpolated path/query value goes through `_encoding.path_segment`/`query_value` (A215) — no raw f-string path building.
- Version single source (A78, 2026-10-20): `sdk/pyproject.toml` `[project].version` is the only truth; `cds_sdk/__init__.py` resolves it at import (tree pyproject → `importlib.metadata` → `UNKNOWN_VERSION = "0.0.0+unknown"`) and a test forbids a literal `__version__ = "` from coming back. The old 0.2.0-vs-0.3.0 drift is closed.
- The SDK is published as `cds-sdk` (pip name) but imports as `cds_sdk`; `sdk/` is not an importable package — CI does `pip install -e ./sdk`.

## COMMANDS
```bash
pip install -e ./sdk            # dev (also run by CI: pip install -r requirements.txt && pip install -e ./sdk)
pip install 'cds-sdk[stream]'   # + WS
python -m pytest -q tests/test_sdk_client.py tests/test_ss15_sdk.py
python -m pytest -q tests/test_ocr_a78_sdk_version_single_source.py tests/test_ocr_a207_sdk_packaging.py tests/test_ocr_a213_retry_idempotency.py tests/test_ocr_a255_xlang_parity.py
```
