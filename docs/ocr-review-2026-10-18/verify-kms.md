# KMS / MPC / Scenario-engine finding adjudication

Repo `D:/workspace/sandbox`, branch `feat/ss-11-productization`. Read-only; `git status --porcelain` empty before and after.
Claims source: `ocr-findings.json` filtered on `path` ∈ {kms_service.py, mpc_service.py, scenario_engine.py} → **31 claims** (kms 11, mpc 9, scenario 11).
Runtime evidence: `C:/Users/清彧/AppData/Local/Temp/ocr-cds/vk_kms.py`, `vk_mpc.py`, `vk_scenario.py`, `vk_scen6.py`, run with `.venv-gate/Scripts/python.exe` (3.12.10), `PYTHONPATH=D:/workspace/sandbox`. No Vault/HSM/network contact: `CDS_VAULT_ADDR=""` → `SoftwareHSMAdapter` (hsm_adapter.py:96-101 falls back when `VAULT_ADDR` empty and `HSM_SOFTWARE_FALLBACK_ALLOWED=true` default, config.py:198).

## Reachability map

| Symbol / surface | Reached from | Status |
|---|---|---|
| `KMSService.generate_data_key/get_key/distribute_key/export_wrapped/destroy_key` | `app/api/kms.py:71,355`; `app/api/sandbox_sessions.py:553,586,1258`; `app/api/connectors.py:470-481`; `app/services/contract_fulfillment.py:141-154`; `app/services/sandbox_runtime.py:730,945,2573`; `task_pipeline.py:441`; `session_lifecycle.py:108`; `task_code_security.py:15-16,33` (← `api/agent.py:103`, `api/inference.py:147`, `api/sandbox_tasks.py:155,314`, `delivery_service.py:119`) | **LIVE** — routers mounted `main.py:341` (`/api/v1/kms`, `require_group("infra")`), `/api/v1/sandbox-sessions`, `/api/v1/connectors` |
| `KMSService.rotate_key` | tests only: `test_kms.py:31`, `test_kms_lifecycle.py:74`, `test_kms_service.py:51`. The DEK-rotate route `api/kms.py:163-178` calls `generate_data_key` instead | **LATENT** (tests-only importer) |
| `KMSService.import_wrapped` | `kms_recovery.py:38` ← `main.py:113-116` (boot) | **LIVE** |
| `KMSService.cleanup_expired_distributions` / `_ttl_cleanup_loop` | loop started `main.py:107-109` (unconditional, even under TESTING) | **LIVE loop, no-op in effect** — `KeyDistribution` rows are never inserted anywhere: `KeyDistribution(` matches only `models/kms.py:107`; `key_distributions` appears only in `models/kms.py:113` + `alembic/versions/0000_baseline_all_tables.py:151`. Sole reader besides the loop: `api/sandbox_sessions.py:842-848`. Already logged as STUB: `docs/spec-implementation-analysis.md:794` |
| `_verify_attestation` | `distribute_key` → `api/sandbox_sessions.py:586`, `api/connectors.py:475`, `contract_fulfillment.py:148`, `task_pipeline.py:441`; gate `KMS_REQUIRE_ATTESTATION` default **true** (config.py:181) | **LIVE** |
| `hsm_adapter.generate_kek` | `_ensure_kek` (kms_service.py:50) from every key op; runs on the event-loop thread **and** on `to_thread` workers (`api/sandbox_sessions.py:507` → `sandbox_runtime.provision` → `:945`) | **LIVE, multi-thread** |
| `KMSService()` second instance | `task_worker.py:116` — inside `_execute_task`, reachable only via `start_worker()` fallback (`main.py:99-103`, fires when `task_pipeline.start()` raises) | **LATENT behind fallback** |
| `mpc_service.*` | `api/mpc.py:103,142,166,187,207,231,236,263,284` → `main.py:339` `/api/v1/mpc` (`require_group("pipeline")`) | **LIVE** |
| `scenario_engine` (all of it) | `tests/test_scenario_engine.py` only. No router, no service importer, no `cds-frontend/src` reference, no `docs/`/`ci/` mention; spec `specs/advanced-scenarios-spec.md` exists but is unimplemented | **LATENT** — same unwired-service family as `policy_evaluator.py`/`field_acl.py`/`encrypted_db.py`/`audit_partition.py` (`app/services/AGENTS.md`), but *not* in that documented list. `render_sql` only produces a string — it never executes SQL |

**Cross-language KMS contract (step 5) — Python and Go still AGREE.**
`app/schemas/kms.py:24-27` `volume_id/name/dek_hex/cipher` ↔ `cds-volume-plugin/kms.go:98-103` (same four keys); `schemas/kms.py:32` `key_id` ↔ `kms.go:96`; `schemas/kms.py:37` `session_id` ↔ `kms.go:120`; `schemas/kms.py:43` `key_hex` ↔ `kms.go:117`; revoke 204 (`api/kms.py:374`) ↔ `kms.go:78` (`do` errors only at `>= 300`). No field on one side is missing on the other. Key-version semantics: the wrapped blob **never crosses the wire** (`api/kms.py:300` wraps for at-rest, `:371` returns plaintext `key_hex`), and Go carries no version at all — so no KMS-side blob-format or key-version change can break Go. The one Python knob that *can* break Go is key size: Go pins `dekLen = 16` at compile time (`cds-volume-plugin/cipher.go:25`) and fail-closes on mismatch (`kms.go:125` `if err != nil || len(dek) != dekLen`), while Python's is a runtime setting (`config.py:356` `CUBE_DEK_LENGTH: int = 16`, enforced `api/kms.py:295`). Nothing asserts the two are equal. See K5.

---

## kms_service.py

### K1 — `rotate_key` returns a `new_key_id` it never stored when `key_id` is unknown
**TRUE / LATENT (tests-only caller).**

    app/services/kms_service.py:165-178
        new_key_id = f"{key_id}-rotated-{uuid.uuid4().hex[:8]}"
        if key_id in self._wrapped_keys:
            ...
            self._wrapped_keys[new_key_id] = wrapped
            ...
        return new_key_id

Runtime: `rotate_key("dek-does-not-exist")` → `"dek-does-not-exist-rotated-78ffee42"`, `get_key(new)` → `None`, old key untouched.
Reachability: no production caller (grep `.rotate_key(`: `api/mpc.py:236` is `mpc_service`, `hsm_adapter.py:158` is Vault). The DEK rotation route (`api/kms.py:163-178`) does not use it. The claim's second half (old DEK never marked/destroyed) is true but moot for the same reason.
Minimal fix: `if key_id not in self._wrapped_keys: raise ValueError(f"Key {key_id} not found; cannot rotate")`. Safe: `test_kms_service.py:48-59`, `test_kms.py:28-32`, `test_kms_lifecycle.py:68-76` all rotate an existing key.

### K2 — attestation is unbound and replayable; whitelist/TCB/freshness checks claimed in the docstring never run
**TRUE / LIVE — and materially worse than the claim states.**

    app/services/kms_service.py:258-262   (docstring)
        1. Quote structure and signature
        2. MRENCLAVE measurement against whitelist
        3. Quote freshness (within max_quote_age_seconds)
        4. TCB version check
    app/services/kms_service.py:294            report_data=parsed.get("report_data", ""),   # assigned, never compared
    app/services/kms_service.py:314-316
            policy = AttestationPolicy(require_fresh_quote=True, max_quote_age_seconds=300)
            service = AttestationService(policy=policy)
            result = service.verify_quote(tee_quote)
    app/services/kms_service.py:329            return result.verified                        # trust_root ignored

Consequences of constructing the policy inline instead of using the `attestation_service` singleton:

    app/services/remote_attestation.py:127        allowed_measurements: set[str] = field(default_factory=set)  # Empty = allow all
    app/services/remote_attestation.py:503-504    if policy is None:
                                                      policy = AttestationPolicy()          # ← only here is CDS_ATTESTATION_ALLOWED_MEASUREMENTS read
    app/services/remote_attestation.py:601        if self._policy.allowed_measurements:      # empty ⇒ check skipped entirely
    app/services/remote_attestation.py:586-588    age = (datetime.now(...) - quote.timestamp).total_seconds()
    app/services/remote_attestation.py:97         timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))   # parser (kms_service.py:288-296) never supplies one
    app/services/remote_attestation.py:130        min_tcb_version: str = ""                  # only occurrence in the file — never enforced

Runtime (`vk_kms.py`, `CDS_ALLOW_SIMULATION=false`, `CDS_KMS_REQUIRE_ATTESTATION=true`):
* a locally-crafted quote with `"type":"sgx_ecdsa"` signed by the repo's own `_LOCAL_ATTESTATION_ROOT` (remote_attestation.py:36) is **accepted**;
* the **same quote** then unlocks a second `key_id` and a second `session_id` (`report_data` never compared) → cross-key/cross-session replay has no time bound (freshness is vacuous per `:97`);
* with `CDS_ATTESTATION_ALLOWED_MEASUREMENTS` set, the default service picks it up while the KMS's inline policy stays empty → the whitelist is bypassed even though `config.py:549-555` makes it **mandatory in production** ("an empty allow-all policy is not a production posture", PZ-05/MR-B1). The prod gate validates a config value the KMS path then ignores.
* NEW: because a fresh `AttestationService` is built per call, KMS verifications never land in the singleton `_verification_log` (`:623`), so `get_stats()/get_verification_log()` under-count every distribution, and `KeyDistribution.tee_quote_hash` is never written (no rows, see K3) — a replayed quote leaves no attribution trail.

Caller chain: `POST /api/v1/sandbox-sessions` → `api/sandbox_sessions.py:586` · `api/connectors.py:475` · `contract_fulfillment.py:148` · `task_pipeline.py:441` → `distribute_key` (kms_service.py:198) → `_verify_attestation`.
Harm: whoever can present a locally-signed quote blob obtains the session DEK in hex for any `key_id`/`session_id`, indefinitely; the prod-mandated enclave whitelist does not apply on this path.
Minimal fix: use the singleton `attestation_service` (so `allowed_measurements` comes from settings), and bind the quote: hash `key_id + session_id + nonce` into `report_data` at enrolment and compare it in `_verify_attestation` (reject when absent); populate `tee_quote.timestamp` from the quote payload and reject raw quotes with no timestamp.

### K3 — TTL expiry never crypto-erases; key material never reclaimed; dict grows unbounded
**TRUE in code / LATENT in effect, with one LIVE sub-part and one FALSE sub-part.**

    app/services/kms_service.py:403-411
        for dist in expired:
            dist.status = DistributionStatus.EXPIRED.value
            # Terminate associated session if exists
            if dist.session_id:
                ...
                    await sandbox_runtime.terminate_sessions_by_key(
                        dist.key_id, db, reason="key_ttl_expired",
                    )
    app/services/kms_service.py:103-107   get_key() → self._wrapped_keys.get(key_id)     # no distribution status/expires_at lookup
    app/services/kms_service.py:335-348   destroy_key() → del self._wrapped_keys[...]    # no HSM call, despite docstring :32 "delete from store + hsm revoke"

* "Expired DEKs stay unwrappable" — code-true, effect-less: the query at `:393-398` can never return a row (see reachability map; already a documented STUB, `docs/spec-implementation-analysis.md:794`).
* "The `models/kms.py` crypto-erase convention is violated" — **FALSE for the paths that exist**: `session_lifecycle.py:114-118` nulls `wrapped_payload`/`sm2_encrypted_payload` on destroy (pinned by `tests/test_session_lifecycle.py:95`), and volume-key revoke nulls its blob at `api/kms.py:389`.
* "The dict grows without bound" — **TRUE and LIVE, via a different route than claimed**: the workspace DEK minted at `sandbox_runtime.py:945` (`generate_data_key(f"workspace-{session_id}")`, recorded at `:963`) is read back at `_workspace_dek_env` (`:723-732`) and is in **no** destroy path (only `session.session_key_id` is destroyed: `session_lifecycle.py:108`, `sandbox_sessions.py:1058`, `sandbox_runtime.py:2573`). One permanently-retained wrapped DEK entry per provisioned session, plus `data_encryption_keys.encrypted_key` which is never cleared (write-only column, `api/kms.py:78,171`).
* NEW: `destroy_key` promises an HSM revoke in the class docstring (`:32`) that cannot happen — `hsm_adapter` exposes no revoke operation at all (`generate_kek/wrap_dek/unwrap_dek/rotate_kek/sign/generate_signing_keypair`).

Minimal fix: destroy the workspace DEK alongside the session key (`session_lifecycle.py:106-108`); drop the "hsm revoke" sentence from `:32`; until KeyDistribution is actually written, do not advertise TTL enforcement in the docstring (or write the row in `distribute_key`, which is what `specs/ss-01-kms-identity.md:377` specifies).

### K4 — `_ensure_kek` is an unlocked check-then-act over a KEK id that `generate_kek` overwrites
**TRUE / LIVE (narrow race) — plus a deterministic variant the claim did not name; the Vault half is UNVERIFIABLE-HERE.**

    app/services/kms_service.py:45-51
        def _ensure_kek(self):
            if self._kek_ready:
                return
            try:
                self._hsm.generate_kek(_KEK_ID)
                self._kek_ready = True
    app/services/hsm_adapter.py:200-203      kek = os.urandom(32)
                                               self._keys[key_id] = kek          # unconditional overwrite
    app/services/kms_service.py:21           _KEK_ID = "cds-master-kek"          # hard-coded at :86,:109,:122,:132

Thread exposure is real, not theoretical: `api/sandbox_sessions.py:507` runs `runtime.provision` in `asyncio.to_thread`, and provision calls `kms_service.generate_data_key` (`sandbox_runtime.py:945`) while API routes on the loop thread do the same.
NEW, deterministic (runtime-proven): a second `KMSService()` shares the module-level `hsm_adapter` singleton but starts with `_kek_ready = False`, so its first key operation re-mints `cds-master-kek` and every DEK wrapped before it stops unwrapping:
`before is not None → after is None | same plaintext: False`. That instance exists at `task_worker.py:116 kms = KMSService()`, reachable when `task_pipeline.start()` fails and `main.py:100-101` starts the legacy worker.
Vault half: `hsm_adapter.py:113` `client.secrets.transit.create_key(name=key_id, key_type="aes256-gcm96")` inside a `try` whose `except` collapses everything into `raise RuntimeError("HSM unavailable — cannot generate KEK")` (`:118`) → `_ensure_kek` → `CDSError` → 503. Whether Vault rejects `create_key` for an existing key cannot be settled without a Vault server → **UNVERIFIABLE-HERE**; code-wise there is no "already exists" tolerance. Note the claimed *unwrap* breakage does **not** apply to Vault: it stores the versioned ciphertext verbatim (`hsm_adapter.py:134-136`), so old blobs survive; only the software HSM loses them.
Minimal fix: guard `_ensure_kek` with a `threading.Lock` + double-check; make `SoftwareHSMAdapter.generate_kek` idempotent (`if key_id in self._keys: return self._keys[key_id]`); make `VaultTransitAdapter.generate_kek` tolerate an existing key instead of raising.

### K5 — `os.urandom(32)  # SM4-256 key`
**TRUE (mislabel + would break a real SM4 backend) / LIVE; the claimed downstream-plugin harm is FALSE.**

    app/services/kms_service.py:83           key_bytes = os.urandom(32)  # SM4-256 key      (also :167 rotate, :191 session key)
    app/models/kms.py:42                     algorithm ... default=KeyAlgorithm.SM4_GCM     # SM4 keys are 128-bit (GM/T 0002)

Runtime: `len(key_bytes) == 32`, model default `SM4-GCM`. Today it works only because `app/utils/crypto.py:2,67,72` implements SM4-GCM as AES-256-GCM (documented, per `app/services/AGENTS.md` — the mislabel itself is the new part, since switching to Tongsuo SM4 rejects a 32-byte key).
FALSE sub-part: "下游（沙箱侧/插件侧按 16 字节 SM4 实现）不一致" — the volume plugin's DEK is a different key class: generated plugin-side at 16 bytes (`cipher.go:25 dekLen = 16`, `plugin.go:63`), registered through `api/kms.py:295-299` against `CUBE_DEK_LENGTH`, stored in `volume_keys`, and it never touches `generate_data_key`. The 32-byte product/session DEK never reaches the plugin.
NEW consequence: the only Python-side knob that can break the Go side is `CUBE_DEK_LENGTH` (a runtime setting; deploy pins it to `"16"` in `docker-compose.yml:166`, `helm/cds/values.yaml:106`, `deploy/e2e-remote/remote_deploy.sh:160`) versus Go's compile-time `dekLen = 16`; setting `CDS_CUBE_DEK_LENGTH=32` yields a control plane that happily hands the plugin a key it always rejects (`kms.go:125`, fail-closed). No test asserts the two match.
Minimal fix: rename the comment to `# 256-bit DEK (AES-256-GCM today)`, and when real SM4 lands branch key length on `algorithm`; add a test asserting `settings.CUBE_DEK_LENGTH == 16` to lock the Go constant.

### K6 — `decrypt_with_key` slices fixed offsets with no length validation
**TRUE / LIVE (diagnostics only, no integrity impact).**

    app/services/kms_service.py:152          nonce, tag, ct = ciphertext[:12], ciphertext[12:28], ciphertext[28:]

Runtime: `decrypt_with_key(key, b"\x01\x02\x03")` → `ValueError: SM4-GCM authentication tag mismatch` — the AEAD tag message hides "truncated blob". It still fails closed.
Live chain: `api/sandbox_tasks.py:314`, `task_pipeline.py:420`, `task_worker.py:81`, `delivery_service.py:119` → `task_code_security.decrypt_task_code` → `decrypt_with_key`. Same slicing exists in `hsm_adapter.py:218`.
Minimal fix: `if len(ciphertext) < 28: raise ValueError("ciphertext too short for nonce+tag header")`. `tests/test_task_code_security.py:22-27` only asserts `pytest.raises(Exception)`, so it keeps passing.

### K7 — unknown quote `type` silently downgrades to SOFTWARE_HASH + FIRECRACKER (asserted fail-open)
**FALSE as stated (it fails closed) / the mapping gap itself is TRUE.**

    app/services/kms_service.py:278-291          quote_type_map = {"sgx_ecdsa":…, "sev_snp":…, "software_hash":…}
                                                 tee_type_map   = {…same three…}
                                                 quote_type=quote_type_map.get(quote_type_str, QuoteType.SOFTWARE_HASH),
                                                 tee_type=tee_type_map.get(quote_type_str, _TT.FIRECRACKER),

`remote_attestation.py:82,85` do define `SGX_EPID` and `GPU_NVIDIA`, so the map is incomplete — that part is true. The asserted harm is not: the downgrade routes the quote to `FirecrackerProvider`, which still validates its own software hash, and the `SOFTWARE_HASH` gate (`kms_service.py:303-312`) is checked first.
Runtime: type `"sgx_epid"` → `ALLOW_SIMULATION=false` → `rejected`; the same quote with `ALLOW_SIMULATION=true` → **also rejected** (`status=invalid_measurement, error=Software component hash mismatch`); case-typo `"SGX_ECDSA"` → rejected. So a mis-typed/tampered `type` string causes a false *rejection* (a real SGX/GPU quote can be misrouted into the wrong verifier), not acceptance.
Minimal fix: map `sgx_epid`/`gpu_nvidia` explicitly, and `if quote_type_str not in quote_type_map: return False` with a warning — that also removes the misrouting for genuine hardware types.

### K8 — TTL loop guards on `dist.session_id` but terminates by `dist.key_id`; N+1
**FALSE as stated / LATENT.**

    app/services/kms_service.py:406-411          if dist.session_id:  … terminate_sessions_by_key(dist.key_id, db, …)

The code shape is as described, but the harm premise ("one DEK serving several sessions, one expires → all die") does not hold on this data model: `terminate_sessions_by_key` matches `SandboxSession.session_key_id == key_id` (`sandbox_runtime.py:2538-2545`) and every session key id is session-scoped (`generate_session_key`, `kms_service.py:189-191`: `session-{session_id}-{hex8}`), i.e. 1:1 by construction. And no `KeyDistribution` row can exist at all (reachability map), so the branch is unreachable; the N+1 concern is therefore academic too.
Minimal fix: none required for correctness; if `KeyDistribution` ever gets written, pass `dist.session_id` and de-duplicate `key_id`s before terminating.

### K9 — `import_wrapped` reports success for blobs that can never be unwrapped
**TRUE / LIVE.**

    app/services/kms_service.py:372-374          if not key_id or not wrapped:
                                                     return False
                                                 self._wrapped_keys[key_id] = bytes(wrapped)

Runtime: `import_wrapped("dek-ghost", b"\x00"*60)` → `True`, `get_key("dek-ghost")` → `None` (log: `Failed to unwrap key dek-ghost: SM4-GCM authentication tag mismatch`).
Caller chain: `main.py:113-116` → `kms_recovery.py:38-43` → `import_wrapped`; the boot log at `main.py:119` then reports "Restored N wrapped keys" for blobs that are dead. The module docstring is honest about the cause (`kms_recovery.py:9-12`: "with the software HSM fallback the KEK is process-local, so cross-replica recovery requires a real Vault/HSM backend"), but the return value still asserts success.
Minimal fix: probe once before storing — `try: self._hsm.unwrap_dek(_KEK_ID, wrapped) except Exception: return False` (discard the plaintext) — and have `restore_wrapped_keys` count/report rejects so the boot line cannot overstate recovery.

### K10 — dead SM2 helpers, write-only `_cert_public_keys`, unused `sm3_hash` import
**TRUE / LIVE file, dead symbols.**

    app/services/kms_service.py:64-70            def _sm2_encrypt(...) / def _sm2_decrypt(...)
    app/services/kms_service.py:270              from app.utils.crypto import sm3_hash      # sole occurrence in the file — unused
    app/services/kms_service.py:42,95,175,347    self._cert_public_keys: written (:95,:175), deleted (:347-348), never read

Verified by occurrence counts: `_sm2_decrypt` = 1 occurrence (its own `def`); `_sm2_encrypt` is never called (call sites use `crypto_service.sm2_encrypt` directly at `:93,:173`); `sm3_hash` = 1 (the import); `_cert_public_keys` has no read. Consequence beyond tidiness (agrees with the claim): `import_wrapped` restores `_sm2_encrypted_keys` (`:375-376`) but has no public key to restore, so the SM2 layer is unrecoverable after a restart by construction.
Minimal fix: delete `_sm2_decrypt` and the `sm3_hash` import, and either drop `_cert_public_keys` or persist the public key next to the ciphertext so recovery can use it. Caution: `tests/test_p0_8_9_10.py:24` references `hasattr(svc, '_sm2_encrypt')` inside an `or` chain — keep one of the listed attributes or update that assertion.

### K11 — f-string logging on the KMS hot path
**TRUE / LIVE (style/perf, low).**

    app/services/kms_service.py:99               logger.info(f"[KMS] Generated and wrapped DEK {key_id}")
    (same pattern at :52, :97, :111, :177, :196, :320-321, :325-326, :332, :350, :413, :415, :426, :449;
     placeholder style used at :308-310 → the file is internally inconsistent)

Minimal fix: convert to `logger.info("[KMS] … %s", key_id)`; no behaviour change.

---

## mpc_service.py

### M1 — secret is reduced mod `_PRIME` and re-expanded to 32 bytes, silently changing the key
**TRUE / LIVE (critical).**

    app/services/mpc_service.py:167              secret_int = int.from_bytes(secret, "big") % _PRIME
    app/services/mpc_service.py:241              return secret_int.to_bytes(32, "big")
    app/api/mpc.py:58                            secret_hex: str = Field(..., min_length=2, max_length=128, pattern="^[0-9a-fA-F]+$")   # ≤ 64 bytes accepted

Runtime: a 64-byte secret splits fine; `reconstruct_key` returns 32 bytes and `got == secret` is **False** — no error anywhere. A 32-byte all-`0xFF` secret (value ≥ `_PRIME`, `_PRIME.bit_length() == 256`) also round-trips to a different value.
Caller chain: `POST /api/v1/mpc/split` (`api/mpc.py:103`) then `POST /api/v1/mpc/reconstruct` (`:142`) — both `require_group("pipeline")` + admin/operator roles.
Harm: a caller escrowing a ≥32-byte key material or any 32-byte value above the prime gets back a *different* key with HTTP 200 and an audit line; the custodianship promise (recover the key you stored) is silently violated. The Shamir math is correct — only the byte↔field conversion is lossy.
Minimal fix: in `split_key`, `if len(secret) != 32: raise ValueError("Secret must be exactly 32 bytes")` and `if int.from_bytes(secret,"big") >= _PRIME: raise ValueError("Secret value exceeds the Shamir field prime")` — both surface as 400 through `api/mpc.py:111-112`.

### M2 — duplicate `share_ids` produce a wrong secret instead of an error
**TRUE / LIVE.**

    app/services/mpc_service.py:224-238
        if len(share_ids) < key_rec.threshold: …
        found = {r.share_id: r for r in result.scalars().all()}
        if len(found) < key_rec.threshold: …
        for sid in share_ids:                      # iterates the raw request list, duplicates included
            …
            points.append((rec.share_index, y))
    app/services/mpc_service.py:61,63              denominator = (denominator * (xi - xj)) % prime
                                                   lagrange_coef = (numerator * _mod_inverse(denominator, prime)) % prime

Runtime: threshold 2, shares `[s1, s2]`; `reconstruct([s1, s2])` → `0000000100…`, `reconstruct([s1, s1, s2])` → `a6c588370a…`, no exception. The claim's own analysis is right: pure duplicates are stopped by the `len(found)` gate, one extra duplicate slips through and yields a zero denominator.
Caller chain: `POST /api/v1/mpc/reconstruct`, `ReconstructRequest.share_ids: list[str] = Field(..., min_length=2)` (`api/mpc.py:67`) — no dedup at the boundary.
Harm: a caller (or a UI that double-selects a share) gets a plausible but wrong key and no failure signal; downstream it looks like key corruption, not a request error.
Minimal fix: dedup + index-collision guard before interpolating: `for sid in dict.fromkeys(share_ids): … if rec.share_index in seen: raise ValueError(...)`.

### M3 — `rotate_key` commits the destructive half before generating the new shares
**TRUE / LIVE.**

    app/services/mpc_service.py:296-313
        old_key.status = "rotated"
        old_key.rotated_at = now
        # Crypto-erase old shares (drop the encrypted blobs).
        await db.execute(delete(MPCKeyShareRecord).where(MPCKeyShareRecord.key_id == key_id))
        if new_secret is None:
            new_secret = os.urandom(32)
        await db.commit()                          # ← rotated + zero shares is now durable
        return await self.split_key(...)

Runtime: with `split_key` stubbed to raise, the row is left `status = rotated`, `shares = 0`, and `reconstruct_key` answers `Key <id> is rotated and cannot be reconstructed` — permanent key loss, no rollback.
Caller chain: `POST /api/v1/mpc/keys/{key_id}/rotate` (`api/mpc.py:236`).
Sub-part **FALSE**: the `expire_on_commit` concern does not apply — both session factories pin `expire_on_commit=False` (`core/database.py:28`, `:74`).
Minimal fix: generate the new shares first (`split_key` into new rows), then flip `rotated` + delete old share rows + commit — or keep the old key `active` and mark it rotated only after the new rows commit, all in one transaction.

### M4 — `_load_holders` returns an unordered, `None`-filtered list used for positional holder binding
**TRUE / LIVE.**

    app/services/mpc_service.py:350-354
        select(MPCKeyShareRecord.holder_id).where(MPCKeyShareRecord.key_id == key_id)
        return [h for h in result.scalars().all() if h]
    app/services/mpc_service.py:179                holder = holders[i - 1] if holders and i <= len(holders) else None
    app/services/mpc_service.py:306-312            return await self.split_key(..., holders=old_holders)

Runtime: split with `holders=["ALICE", None, "CAROL"]` → `_load_holders` → `['ALICE', 'CAROL']`; after rotation the binding becomes `[('ALICE',1),('CAROL',2),(None,3)]` — CAROL silently moved from index 3 to index 2 and index 3 has no holder. No `ORDER BY`, so ordering is not even stable across backends.
Harm: after a rotation, holders receive the wrong share index (share/holder misbinding across custodians); with fewer holders than shares, positions shift for everyone after the first `None`.
Minimal fix: `select(...).order_by(MPCKeyShareRecord.share_index)` and keep the `None`s (`return [r[0] for r in result.all()]`, typed `list[str | None]`), or pass an explicit `{holder: index}` mapping into `split_key`.

### M5 — `_mod_inverse` returns 0 for a non-invertible argument
**TRUE / LIVE (root cause of M2).**

    app/services/mpc_service.py:39-41              def _mod_inverse(a, m): return pow(a, m - 2, m)

Runtime: `_mod_inverse(0, _PRIME)` → `0`; `_lagrange_interpolate([(1,11),(1,11)], _PRIME)` → `0` instead of raising.
Minimal fix: `if a % m == 0: raise ValueError("non-invertible denominator in Lagrange interpolation")` (a duplicate x-coordinate is caller-supplied data, so this is input validation, not a defensive check).

### M6 — `get_share` dereferences `_load_key`'s `None` result
**TRUE in code / LIVE on SQLite, LATENT on the production backend.**

    app/services/mpc_service.py:257-258                key_rec = await self._load_key(db, rec.key_id)
                                                       return _share_from_record(rec, key_rec.threshold, key_rec.total_shares)

Runtime (in-memory SQLite): after deleting the parent row the share row survives ("orphan share row survived key deletion" = True) and `get_share` raises `AttributeError`; `api/mpc.py:253-258` has no handler for it → 500 instead of 404.
Caveat the claim omits: production runs PostgreSQL (`app/services/AGENTS.md`: 39 models, PG in prod), where `ForeignKey(..., ondelete="CASCADE")` (`models/mpc_key.py:39-41`) is enforced and cascade-deletes the shares — so the orphan requires SQLite (dev/CI) or an out-of-band write. The missing `None` check is still a real defect, just lower-probability in prod.
Minimal fix: `if not key_rec: return None` (route already returns 404 on `None`).

### M7 — share-at-rest KEK is `SHA256(AUDIT_ENCRYPTION_KEY or JWT_SECRET_KEY)`, shared with egress audit, no domain separation or key version
**TRUE / LIVE.**

    app/services/mpc_service.py:97-101             secret = settings.AUDIT_ENCRYPTION_KEY or settings.JWT_SECRET_KEY
                                                     return hashlib.sha256(secret.encode("utf-8")).digest()
    app/services/egress_audit.py:52-53             secret = settings.AUDIT_ENCRYPTION_KEY or settings.JWT_SECRET_KEY
                                                     return SM4Cipher(key=hashlib.sha256(secret.encode("utf-8")).digest())

Runtime: `_share_cipher_key() == egress_audit's key` → True. The sharing is deliberate but undocumented as a risk: `mpc_service.py:98` says "same chain as egress_audit".
Harm: one leaked `AUDIT_ENCRYPTION_KEY` (or `JWT_SECRET_KEY` when the former is unset) decrypts both escrowed Shamir shares and the egress audit trail; rotating/losing that secret makes every stored share permanently unreadable with only a GCM-tag error to show for it (`_decrypt_share_value` failure surfaces as `reconstruct_key` ValueError), and the `nonce+tag+ct` header (`:89`) carries no key id, so no overlap period is possible.
Minimal fix: HKDF with domain separation (`HKDF(settings…, info=b"cds-mpc-share-v1")`) plus a leading version byte, and refuse the `JWT_SECRET_KEY` fallback for custody data (raise instead) so an identity secret can never become an encryption key.

### M8 — `list_keys` decrypts every share of every key for a listing that only counts them
**TRUE / LIVE.**

    app/services/mpc_service.py:275                    shares = [_share_from_record(r, k.threshold, k.total_shares) for r in recs]
    app/services/mpc_service.py:76                     share_value=_decrypt_share_value(rec.share_value).decode("ascii"),
    app/api/mpc.py:166-180                             keys = await mpc_service.list_keys(db)   # ShareSummary uses share_count only

Runtime: 8 keys → **21 SM4-GCM decryptions** for one `GET /api/v1/mpc/keys`; every plaintext y-coordinate is materialised in the process at once. `_share_from_record` also rebuilds `SM4Cipher` and re-derives the KEK per share (`:71-76` → `:97-101`).
Minimal fix: count shares with one `select(key_id, func.count()).group_by(key_id)` and build `KeyShare` objects without `share_value` for the list path.

### M9 — `key_id = str(uuid.uuid4())[:8]` (32 bits) as the primary key
**TRUE / LIVE, low frequency.**

    app/services/mpc_service.py:164              key_id = str(uuid.uuid4())[:8]
    app/models/mpc_key.py:23-25                  # String PK … the service generates short hex key_ids
                                                   key_id: Mapped[str] = mapped_column(String(64), primary_key=True)

The column fits a full UUID, and `share_id = f"{key_id}-s{i}"` (`:178`) inherits the same space. On collision the insert raises `IntegrityError`, which `api/mpc.py:111-112` does not catch (only `ValueError`) → unhandled 500 on `POST /split`. (M1b above also shows `[:8]`-style truncation is a live source of value loss in this module.)
Minimal fix: `key_id = str(uuid.uuid4())`; optionally catch `IntegrityError` → 409. No migration needed (`String(64)`).

---

## scenario_engine.py — **all 11 claims LATENT**

Only importer: `tests/test_scenario_engine.py:3`. No router in `app/api/`, no service importer, no `cds-frontend/src` reference, no `docs/`/`ci/` entry; `specs/advanced-scenarios-spec.md` describes the feature but nothing wires it. `render_sql` returns a string and never executes it (`:214-218`), so today there is no path from any of these defects to a database. Verdicts below assume the module gets wired as specified.

### S1 — keyword blacklist + `str.format` concatenation instead of parameterisation
**TRUE / LATENT (critical if wired).**

    app/services/scenario_engine.py:199              sql = template.sql_template.format(**sanitized)
    app/services/scenario_engine.py:228-229          cleaned = re.sub(r'(?:DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE)\b', '', cleaned, …)
    app/services/scenario_engine.py:235              cleaned = cleaned.replace("'", "''")

Runtime: template `WHERE id = {user_id}` + param `1 OR 1=1` → `… WHERE id = 1 OR 1=1 LIMIT 500`; `1 UNION SELECT a,b,c FROM secrets` passes verbatim (no `UNION`/`SELECT`/`OR` in the blacklist). Accurate nuance the claim gets right in point 3: inside a single-quoted context the escaping does hold (`x' OR '1'='1` → `'x'' OR ''1''=''1'`) and `;` is stripped at `:233`, so classic stacking/`pg_sleep` payloads are blocked (`x'; SELECT pg_sleep(10) --` → rejected, by the default `network_call` rule at `:276`).
Minimal fix: keep only named placeholders, resolve them into a bind-parameter list (`text(sql).bindparams(...)`) and forbid unquoted/identifier/`ORDER BY`/`LIMIT` substitution; a blacklist over a formatted string is not fixable by adding keywords.

### S2 — keywords stripped before comment markers; silent mutation of legitimate values
**TRUE / LATENT.**

    app/services/scenario_engine.py:228-231          cleaned = re.sub(r'(?:DROP|…)\b', '', cleaned, …)
                                                     cleaned = cleaned.replace('--', '').replace('/*', '').replace('*/', '')

Runtime: `DR--OP TABLE t` renders as `'DROP TABLE t'` (the comment strip reassembles a blacklisted keyword). Business data is silently rewritten: `delete user report` → `' user report'`, `A;B Corp` → `'AB Corp'` — and `sanitized` is echoed back to the caller (`:217`).
Minimal fix: normalise (strip comments) *before* validation, and reject rather than mutate — return `success=False` when a value matches a dangerous pattern.

### S3 — `max_output_rows` enforced by a `"LIMIT" not in sql.upper()` substring test
**TRUE / LATENT.**

    app/services/scenario_engine.py:211-212              if "LIMIT" not in sql.upper():
                                                             sql = f"{sql} LIMIT {template.max_output_rows}"

Runtime, three ways: a template `LIMIT 1000000` is kept verbatim; a column named `delivery_limit` suppresses the append entirely; and an attacker-controlled *parameter value* containing the word "limit" (`see the limit docs`) suppresses it too. Appending is also naive about a trailing `;`/comment (which `:233` happens to neutralise for string params only).
Minimal fix: parse a trailing `LIMIT n [OFFSET m]` with a regex and clamp to `min(n, max_output_rows)`, appending `LIMIT` when absent — and compute it from the template, never from the rendered string.

### S4 — `forbidden_operations` are declared but not enforced
**TRUE / LATENT.**

    app/services/scenario_engine.py:270-277
            for op in template.forbidden_operations:
                if op == "subquery_leak":
                    if re.search(r'WHERE\s+.*?\(SELECT\b', sql_upper):
                        logger.warning("Subquery detected in WHERE — potential data leak")   # no return False
                elif op == "network_call":
                    if re.search(r'\b(pg_sleep|dblink|http)', sql_upper, re.IGNORECASE):
                        return False, "Network calls are forbidden in scenarios"
    app/services/scenario_engine.py:55-57            forbidden_operations … ["row_level_export", "network_call", "subquery_leak"]

Runtime: with the default forbidden list, `WHERE b = (SELECT max(c) FROM u)` renders `success=True` (the warning fires), a select-list subquery is not even detected, `pg_read_file('/etc/passwd')` renders, `UTL_HTTP.REQUEST(...)` renders, and `row_level_export` has no branch at all.
Minimal fix: make `subquery_leak` return `(False, …)`; enumerate the file/exec primitives for the target engine (or refuse anything outside a parsed SELECT allowlist); return an explicit error for an unrecognised op name instead of falling through.

### S5 — `table_count = join_count + 1`
**TRUE / LATENT.**

    app/services/scenario_engine.py:250-254              join_count = len(re.findall(r'\bJOIN\b', sql_upper))
                                                         table_count = join_count + 1
                                                         if table_count > template.max_tables: return False, …

Runtime: `FROM a, b, c, d, e, f` with `max_tables=4` → accepted; `t1 UNION t2 UNION t3` with `max_tables=2` → accepted. (Comma joins are also cartesian products and dodge the CROSS JOIN/ON checks below.)
Minimal fix: count relations from a parsed token stream after stripping literals/comments, and include comma-separated FROM items and UNION branches.

### S6 — `"ON" not in section.split("WHERE")[0]` substring test
**TRUE / LATENT, in both directions.**

    app/services/scenario_engine.py:264-267                  join_sections = re.split(r'\bJOIN\b', sql_upper, flags=re.IGNORECASE)
                                                             for i, section in enumerate(join_sections[1:], 1):
                                                                 if "ON" not in section.split("WHERE")[0]:
                                                                     return False, f"JOIN #{i} missing ON clause …"

Runtime: false accepts — `JOIN commission c WHERE a.id = c.id` and `JOIN b POSITION(x) …` are accepted because `COMMISSION`/`POSITION` contain `ON`; both are unconditioned joins. False rejects — `NATURAL JOIN` and `JOIN … USING (id)` are refused. `JOIN … ON 1=1` is accepted.
Minimal fix: parse each JOIN clause for an `ON`/`USING` condition token, accept `USING`, reject `NATURAL` and comma joins; never test membership on a raw substring.

### S7 — `validate_parameters` mutates the caller's dict
**TRUE / LATENT.**

    app/services/scenario_engine.py:120-122                if value is None and param_def.default is not None:
                                                               params[param_def.name] = param_def.default
    app/services/scenario_engine.py:136,143                params[param_def.name] = value            # coerced value written back

Runtime: `caller = {"n": "7"}` → `is_valid=False` with `errors=['Parameter n must be >= 10']`, yet the caller's dict is now `{'n': 7, 'q': 'dflt'}` — a default injected and a coercion applied on a *failed* validation.
Minimal fix: work on `validated = dict(params)` and return it (`(is_valid, errors, validated)`), using it in `render_sql`.

### S8 — `str(value) not in param_def.allowed_values`
**TRUE / LATENT (false rejects proven; false accept not reproduced).**

    app/services/scenario_engine.py:164                    if param_def.allowed_values and str(value) not in param_def.allowed_values:

Runtime: `flag=True` vs `["true","false"]` → rejected; `n=1` (coerced to `1.0` at `:135`) vs `["1","2"]` → rejected (`"1.0"`); `"active"` vs `["Active"]` → rejected. A `list` value is rejected too, so the claimed accidental *accept* did not reproduce — the observable defect is wholesale false rejection of legal boolean/numeric/case variants.
Minimal fix: compare on a normalised, type-aware value (numbers numerically, booleans via `{"true","false"}` mapping, strings case-folded per an explicit rule), and coerce `allowed_values` to the declared type at registration.

### S9 — only `KeyError` caught around `.format()`; template gets attribute/index access
**TRUE / LATENT.**

    app/services/scenario_engine.py:198-203                try:
                                                               sql = template.sql_template.format(**sanitized)
                                                           except KeyError as e:

Runtime: template `{0}` → uncaught `IndexError: Replacement index 0 out of range…`; malformed `'{q'` → uncaught `ValueError: expected '}' before end of string`; and `'{q.__class__.__name__}'` renders to `'str'` — attribute probing through a provider-supplied template.
Minimal fix: `except (KeyError, IndexError, ValueError)` returning `ScenarioExecution(success=False, …)` with the template id logged, and pre-validate templates with `string.Formatter().parse()` (named, no `.`/`[`/format-spec) at `register_template`.

### S10 — `allowed_operations` never read; `row_count`/`elapsed_ms` never set
**TRUE / LATENT.**

    app/services/scenario_engine.py:52-54                  allowed_operations: list[str] = field(default_factory=lambda: ["filter", "aggregate", "multi_table_join"])
    app/services/scenario_engine.py:67-68                  row_count: int = 0 / elapsed_ms: int = 0

`allowed_operations` appears exactly once in the file (the field); `_check_join_constraints` iterates only `forbidden_operations` (`:270`). Runtime: a template with `allowed_operations=[]` still renders, and both metrics stay `0`.
Minimal fix: implement the check or delete the field; if the engine stays render-only, drop `row_count`/`elapsed_ms` (they imply execution happened).

### S11 — f-string logging + silent duplicate-template overwrite
**TRUE / LATENT.**

    app/services/scenario_engine.py:85-88                  def register_template(self, template):
                                                               self._templates[template.id] = template
                                                               logger.info(f"Registered scenario template: {template.id} ({template.name})")

Runtime: registering a second template with id `dup` silently replaces the first (`sql_template` becomes the new one), on the module-level singleton (`:283`) shared by all callers; and the log line is eagerly formatted regardless of level.
Minimal fix: `if template.id in self._templates: raise ValueError(...)` (or warn), and switch to `logger.info("Registered scenario template: %s (%s)", template.id, template.name)`.

---

## Summary

| File | TRUE+LIVE | TRUE+LATENT | FALSE as stated | UNVERIFIABLE-HERE |
|---|---|---|---|---|
| kms_service.py | K2, K4(race), K5, K6, K9 | K1, K3(effect), K8(→FALSE), K10, K11 | K3(crypto-erase sub-claim), K7(fail-open), K8 | K4 Vault `create_key` idempotency |
| mpc_service.py | M1, M2, M3, M4, M5, M7, M8, M9 | M6(SQLite-only) | M3(expire_on_commit sub-claim) | — |
| scenario_engine.py | — | S1–S11 (whole module unwired) | — | — |

Priority, if anything gets fixed: **K2** (attestation unbound + prod-mandated whitelist bypassed on a live key-release path), **M1/M2/M3** (silent key corruption and unrecoverable rotation on a live custody API), **K4** (make KEK init idempotent + locked; kill the second `KMSService()`), then **M7/M8**, then the scenario module as a whole before it is ever wired.

Known-and-documented facts not re-reported as defects: `mpc_service` = Shamir custody not compute (`api/mpc.py:36-46`, `docs/error-codes.md:73`), SM4-labelled AEAD = AES-256-GCM (`app/utils/crypto.py:2`), `sm3()` SHA-256 fallback, `KeyDistribution` STUB (`docs/spec-implementation-analysis.md:794`), `kms_recovery` process-local-KEK caveat (`kms_recovery.py:9-12`).
