# sdk/ — cds-sdk, standalone Python REST client (scored ~9, distinct domain: own pyproject.toml, pip-installable deliverable with its own version, decoupled from app/ conventions)

Sync httpx client for the CDS `/api/v1` surface. Python ≥3.10; only dependency httpx; streaming extra `[stream]` = websocket-client.

## STRUCTURE
```
sdk/
├── cds_sdk/
│   ├── __init__.py   # __all__ = CDSClient, CDSError, CDSApiError, CDSResponse
│   ├── client.py     # 1132 L / 114 defs — the entire API surface
│   └── stream.py     # 168 L WS streaming (optional dep)
├── pyproject.toml    # version 0.3.0
└── README.md         # Chinese usage doc
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Any endpoint call | `client.py` | session lifecycle + files/snapshots + contracts + tasks + training/inference |
| API-key auth (SS-12) | `client.py` ctor `api_key=` | key subjects vs human JWTs |
| Flow surface (SS-15) | `client.py` ~line 880+ | notebooks/workloads/bundles/OpenAPI endpoints/webhooks (~40 ops) |
| Error semantics | `client.py` `CDSApiError` | W2 envelope `{code,message,detail,request_id}` |

## CONVENTIONS
- Responses are `CDSResponse(dict)` subclasses — dict-compatible, plus `.disclosure` (honest-degradation keys, PZ-06 family).
- Retries ONLY 429/503, honoring `Retry-After` capped at 5 s.
- Legacy `CDSError` kept for back-compat alongside `CDSApiError`.
- Chinese README; consumed by 8 repo test modules (`tests/test_sdk_client.py`, `test_ss15_sdk.py`, `test_error_contract.py`, …) — the repo pytest suite is the SDK's test home.

## ANTI-PATTERNS
- `SESSION_TERMINATED` (410) is deliberately EXCLUDED from the retry set — terminal by W2 contract; never add it back.
- Never fake streaming: missing `websocket-client` raises `CDSError` with an install hint; a `blocked` WS frame means T5 DLP killed the stream — treat output as untrusted.
- Version single source (A78, 2026-10-20): `sdk/pyproject.toml` `[project].version` is the only truth; `cds_sdk/__init__.py` resolves it at import (tree pyproject → `importlib.metadata` → `0.0.0+unknown`) and a test forbids a literal `__version__ = "` from coming back. The old 0.2.0-vs-0.3.0 drift is closed.

## COMMANDS
```bash
pip install -e ./sdk            # dev (also run by CI: pip install -r requirements.txt && pip install -e ./sdk)
pip install 'cds-sdk[stream]'   # + WS
python -m pytest -q tests/test_sdk_client.py tests/test_ss15_sdk.py
```
