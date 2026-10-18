## low-severity claims (22-27)

### C22 [mutual_tls.py] low/maintainability — unused `hashlib`, `time`, `field`

**TRUE·LIVE (cosmetic)**

Evidence read: imports `app/services/mutual_tls.py:19` (`import hashlib`), `:21` (`import time`), `:23` (`from dataclasses import dataclass, field`).
- grep `hashlib\.` → 0 matches; grep `field(` → 0 matches (the three dataclasses at `:28-51` use only plain annotated fields); grep `time\.` → the only three hits are `datetime.now(timezone.utc)` at `:86`, `:126`, `:171`, i.e. the substring inside `datetime.now`, not the module.
The module is imported in production (`federation_connector.py:671`), so the dead imports do execute at boot; zero behavioral impact.

### C23 [remote_attestation.py] low/maintainability — unused `crypto_service` import

**TRUE·LIVE (cosmetic)** — second half of the rationale is speculative.

Evidence read: `app/services/remote_attestation.py:23` `from app.services.crypto_service import crypto_service`. grep `crypto_service` over the file returns **only line 23** — the module uses `app.utils.crypto.sm3_hash` via function-local imports instead (`:47`, `:169`, `:229`, `:287`, `:345`, `:406`, `:445`).
Note: this module already has a top-level import of `app.services.crypto_service` without an existing cycle (`crypto_service` imports `app.utils.crypto`, never `remote_attestation`), so the stated circular-import hazard is prospective, not present. Removing the line is still correct.

### C24 [remote_attestation.py] low/maintainability — policy flags written `True` even on failure

**TRUE·LIVE — reproduced.**

Evidence read: `app/services/remote_attestation.py:636-638`, executed after `result = provider.verify_quote(quote)` (`:629`) with no condition:
`result.policy_checks["tee_type_allowed"] = True` / `["quote_fresh"] = True` / `["measurement_whitelisted"] = True`.
- Freshness may not have run: guarded by `if self._policy.require_fresh_quote:` (`:586`).
- Whitelist may not have run: guarded by `if self._policy.allowed_measurements:` (`:601`) — and on the live KMS path it is always empty (C9), so `measurement_whitelisted=True` is asserted for a check that never happened.
- Worse than "misleading": the provider's own `policy_checks` dict (`:271`, `:387`, `:485` — `signature_valid/measurement_match/tcb_current` all literal `True`) is silently merged with these three.
Executed: a quote that fails with `invalid_measurement` still carries `policy_checks={'tee_type_allowed': True, 'quote_fresh': True, 'measurement_whitelisted': True}` alongside `verified=False`.

### C25 [remote_attestation.py] low/performance — f-string passed to `logger.info`

**TRUE·LATENT** (the line exists, but only `register_provider` reaches it and that has no production caller — see C20)

Evidence read: `app/services/remote_attestation.py:537` `logger.info(f"Registered attestation provider: {provider.tee_type.value}")` — formatted eagerly regardless of level. The claimed inconsistency is real: `:518` uses the lazy form `logger.warning("Could not load ATTESTATION_ALLOWED_MEASUREMENTS from settings: %s", e)`.

### C26 [remote_attestation.py] low/performance — f-string passed to `logger.info` (verification path)

**TRUE·LIVE**

Evidence read: `app/services/remote_attestation.py:641` `logger.info(f"Attestation {quote.quote_id}: {result.status.value} ({result.duration_ms}ms)")` — on the path every `verify_quote` call takes (`kms_service.py:316`, `result_verifier.py:307`), so the eager format is paid per attestation. Same lazy-form fix as the claim proposes.

### C27 [remote_attestation.py] low/bug — `entries[-limit:]` mishandles `limit=0` and negative limits

**TRUE·LIVE — reproduced.**

Evidence read: `app/services/remote_attestation.py:644-653` — `entries = self._verification_log` (`:650`), optional status filter, then `return entries[-limit:]` (`:653`). `limit=0` → `entries[0:]` (everything); `limit=-5` → `entries[5:]` (everything but the first five).
Executed with 6 entries in the log: `limit=0` → **6** entries (should be 0), `limit=-4` → **2** (should be empty or an error), `limit=2` → 2.
Reachability caveat: no `app/` caller passes an external `limit` into `get_verification_log` (grep finds no in-app caller at all — only tests and `get_stats`), so today it is a latent API hazard one `Query(limit)` parameter away from being a data-exposure/log-dump bug, not an active one.

## Score

| file | claims | TRUE·LIVE | TRUE·LATENT | FALSE |
|---|---|---|---|---|
| `app/services/mutual_tls.py` | 11 | 3 (C5, C12, C22) | 8 (C1, C2, C3, C4, C10, C11, C13, C14) | 0 |
| `app/services/remote_attestation.py` | 16 | 14 (C6, C7, C8, C9, C15, C16, C17, C18, C19, C21, C23, C24, C26, C27) | 2 (C20, C25) | 0 |
| `app/core/tlcp_service.py` (nonexistent; real path `app/services/tlcp_service.py`) | 0 | 0 | 0 | 0 |
| **Total** | **27** | **17** | **10** | **0** |

No claim was graded FALSE: every one of the 27 describes code that exists as quoted. What did not survive verification is a set of **impact/severity assertions**, all listed under "Impact correction" above:

1. **C2** — the byte-collision example (`subject="A|is_ca=True"` vs `subject="A"` + extra field) is unrealizable: `sorted(cert_data.items())` (`:140`) puts `is_ca` first and `subject` last, and `subject` is mandatory (`:128`), so an injected tail can never be byte-equal to an honest payload. I could not produce a same-signature collision; the demonstrated damage is the C10 round-trip break.
2. **C3** — a tampered `issuer` or a foreign leaf used as `ca_cert` is rejected unless re-signed with the CA key (measured: `False`/`False`), because `issuer` is inside the signed payload (`:131`) and verification pins `ca.public_key` (`:195`). The fallback at `:168` substitutes the local CA as anchor; it does not rubber-stamp arbitrary certs.
3. **C5** — the CA trust relationship *is* enforced, one layer up: `federation_connector.py:708-710` passes `verify=mtls["ca_bundle"]` and `cert=(cert,key)` to `httpx.Client`. No attacker gain; the defect is late/opaque failure.
4. **C13** — `mtls_manager` has zero production callers, so the "FastAPI thread-pool concurrency" premise is counterfactual.
5. **C15/C16/C17** — no 500: `kms_service.py:331-334` and `result_verifier.py:316-317` catch `Exception` and fail closed. The crash and the missing audit record are real.
6. **C21** — `list.append`/iteration are individually atomic under CPython, so "读到中间状态" overstates it; unbounded growth is the actual defect.
7. **C1** — pseudo-PEM never reaches a TLS stack; medium (API trap + plaintext key in a dict), not critical. Already registered in `docs/mock-remediation-plan.md:204`.

Highest-value findings, in order: **C6** (offline-forgeable quotes reach KMS DEK release and the `type`-field gate at `kms_service.py:304-306` is bypassable with one JSON field — measured `True` under `ALLOW_SIMULATION=false`), **C9+C18** (the measurement whitelist is inert on the live KMS path even when configured, so the control C6 needs in prod does not exist), **C8** (unsigned `timestamp` → replay measured `verified`; no upper bound on future timestamps), **C4** (cross-space certificate acceptance, pinned by `tests/test_mtls.py:226-227`).
