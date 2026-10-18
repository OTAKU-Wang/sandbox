## remote_attestation.py — medium

### C15 [remote_attestation.py] medium/bug — `SGXProvider.verify_quote` crashes on a JSON quote that is not an object

**TRUE·LIVE** (crash + audit gap reproduced; the "500" part is refuted — both live callers catch it)

Evidence read:
- `app/services/remote_attestation.py:210` `parsed = json.loads(quote.raw_bytes)` then immediately `parsed.get("type")` at `:211` (also `:220`); the handler is `except (json.JSONDecodeError, UnicodeDecodeError):` at `:249` → no `isinstance(parsed, dict)` guard anywhere in the provider.
- The claim's suggested patch is literally the missing check.

Executed: `raw_bytes` = `[]` / `null` / `42` / `"str"` → **AttributeError: 'list' object has no attribute 'get'** (and the NoneType/int/str equivalents) escaping `AttestationService.verify_quote`. `_verification_log` gained no entry for the crashing call (verified: log length unchanged) — the audit-gap half of the claim is confirmed.

Impact correction: no HTTP 500 on either live path. `kms_service.py:331-334` wraps the whole attestation block in `except Exception` → `logger.error` + `return False` (executed: `_verify_attestation(b'[]')` logged `TEE attestation verification error: 'list' object has no attribute 'get'` and returned False); `result_verifier.py:316-317` catches `Exception` and falls through to the DCAP path. So the real consequence is a fail-closed rejection plus a **missing audit record**, not an availability/auth bypass. Severity low-medium, not a security hole.

### C16 [remote_attestation.py] medium/bug — same non-dict crash in `SEVProvider.verify_quote`

**TRUE·LIVE**

Evidence read: `app/services/remote_attestation.py:326` `parsed = json.loads(quote.raw_bytes)` → `parsed.get("type")` at `:327` (and `:336`), handler `except (json.JSONDecodeError, UnicodeDecodeError):` at `:365`.
Executed: all four non-object blobs → `AttributeError: 'list'/'NoneType'/'int'/'str' object has no attribute 'get'` with `tee_type=sev_snp`, same missing-audit-log behavior as C15.

### C17 [remote_attestation.py] medium/bug — same for `FirecrackerProvider`, plus no `tee_type` guard and no `type` field check

**TRUE·LIVE**

Evidence read:
- Non-dict risk: `app/services/remote_attestation.py:434` `parsed = json.loads(quote.raw_bytes)` → `parsed.get("components")` at `:436` (and `:447`), handler `except (json.JSONDecodeError, UnicodeDecodeError):` at `:465`.
- Type guard absent: SGX has `if quote.tee_type != TEEType.SGX:` at `:198` and SEV `if quote.tee_type != TEEType.SEV_SNP:` at `:315`; `FirecrackerProvider.verify_quote` (`:429`) goes straight from `start = time.monotonic()` to the `try`/`json.loads` at `:433-434` with no equivalent, and it never asserts `parsed.get("type") == "software_hash"` (SGX/SEV do at `:211`/`:327`). Confirmed by reading the whole method (`:429-489`).
- Practical reach: `AttestationService.verify_quote` dispatches by `quote.tee_type` (`:619`), so through the service the guard is implicit; the gap bites on direct provider use — which is what `kms_service.py:315-316` does per request (`AttestationService(policy=...)`) and what a caller reusing `FirecrackerProvider` would hit.
Executed: non-object blobs raise `AttributeError` from the firecracker provider identically to C15/C16.

### C18 [remote_attestation.py] medium/security — measurement derived from the random `quote_id`

**TRUE·LIVE**

Evidence read:
- `app/services/remote_attestation.py:405` `components = f"kernel:v5.15-hardened|rootfs:gvisor-ext4|config:isolated"` — f-string with zero placeholders (the "此行 f-string 无占位符" note confirmed), `:407` `measurement = sm3_hash(f"{components}|{quote_id}".encode())`; SGX `:170` `sm3_hash(f"sgx-enclave-{quote_id}".encode())`; SEV `:288` `sm3_hash(f"sev-snp-{quote_id}".encode())`. `quote_id` embeds `uuid4().hex[:12]` (`:167`, `:286`, `:402`).
- Verify just recomputes the same formula (`:230`, `:346`, `:446`), so measurement carries no image/kernel/firmware fact.
- Consequence (1) confirmed: with a non-empty whitelist the check at `:601-612` can never hit.

Executed: two consecutive `generate_quote(TEEType.SGX)` → measurements `ffe042ec89a2573d…` vs `cfb252fd68401454…` (unpredictable per call); with `AttestationPolicy(allowed_measurements={'deadbeef'})` a freshly generated legit quote → `invalid_measurement`. So configuring the whitelist as the comment at `:505-507` demands makes every local quote fail — the operator is pushed to leave it empty, which is the fail-open of C9.

### C19 [remote_attestation.py] medium/bug — `min_tcb_version` and `require_signed` are never read

**TRUE·LIVE**

Evidence read:
- Declared at `app/services/remote_attestation.py:130-131` (`min_tcb_version: str = ""`, `require_signed: bool = True`). Repo-wide grep for `min_tcb_version` and `require_signed` returns **only those two lines** across `app/`, `tests/` and `docs/` — no read, no test.
- `tcb_status` is a hardcoded literal per provider: `:268` `tcb_status="up_to_date"`, `:384` `tcb_status="up_to_date"`, `:482` `tcb_status="software_l2"`, and `policy_checks={"signature_valid": True, "measurement_match": True, "tcb_current": True}` (`:271`, `:387`, `:485`) is written unconditionally for the success path.
- Docstrings advertise it anyway: module docstring step 3 "signature chain, measurement, version, TCB status" (`:6`-ish, and `verify_quote` docstring `5. TCB version check` at `:565`).

Executed: `AttestationPolicy(min_tcb_version='99.99', require_signed=False)` + a quote whose `tcb_version` is `2.23` → `verified`, `tcb_status=up_to_date`. Neither field changes any outcome.

### C20 [remote_attestation.py] medium/bug — `register_provider` overwrite leaves `_local_root_types` stale

**TRUE·LATENT** (defect reproduced; nothing in `app/` calls `register_provider`)

Evidence read:
- `app/services/remote_attestation.py:534-537` `def register_provider(self, provider)` → `self._providers[provider.tee_type] = provider` + `logger.info`; it never touches `self._local_root_types`, which is fixed at construction (`:529-531` `{TEEType.SGX, TEEType.SEV_SNP, TEEType.FIRECRACKER}`).
- Consequence at `:632-633` `if quote.tee_type in self._local_root_types: result.trust_root = "local-simulated"` — a decision keyed on the **TEE type**, not on which provider produced the result, exactly as claimed.
Reachability: grep `register_provider` → only the definition plus `tests/test_remote_attestation.py:283` and `tests/test_pz05_federation_attestation.py:228`. LATENT today, but it is the exact hook the real-DCAP integration is supposed to use, and getting it wrong silently mislabels hardware attestation as simulated (and the reverse: a hardware root registered for a non-listed type would be labeled `external` by the `:118` default, so the mislabel is bidirectional).
Executed: registered a custom SGX provider returning `verified=True` → result `trust_root = local-simulated`.

### C21 [remote_attestation.py] medium/bug — unbounded `_verification_log`, unsynchronized

**TRUE·LIVE**

Evidence read:
- `app/services/remote_attestation.py:532` `self._verification_log: list[AttestationResult] = []` — plain list, no `maxlen`, no trimming operation anywhere in the file (grep `_verification_log` → `:532` declaration, appends at `:582`, `:597`, `:611`, `:625`, `:640`, reads at `:650`, `:657`, `:658`).
- Appended on every verification including every policy rejection; `AttestationResult` retains `measurement`, `signer_chain`, `policy_checks`, `error_message` (`:103-120`).
- No lock: imports at `:23-32` contain no `threading`/`collections`; `get_verification_log` (`:650-653`) and `get_stats` (`:657-658`) iterate the same list that appends mutate.
- Singleton scope: `# Singleton` / `attestation_service = AttestationService()` at `:668-669`, imported by `result_verifier.py:304`. (Note the busiest caller, `kms_service.py:315`, builds a throwaway service per request, so *its* logs are dropped entirely — the opposite failure: no accumulation, but also no audit trail for KMS attestations.)
Executed: `get_stats()['total_verifications']` grew 1:1 with calls and `_verification_log.maxlen` is absent.
Impact: real but slow-leak hygiene; `list.append` and the reads are individually atomic under CPython, so the "读到中间状态" wording overstates it — the honest issue is unbounded growth plus lock-free read/modify interleaving (a compaction or `deque` change would need the lock the claim asks for).

