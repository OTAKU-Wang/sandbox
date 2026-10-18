# Verification of AI-code-review claims — attest/mTLS cluster

Claims file: `ocr-cds/claims-attest.md` (27 claims). Repo: D:/workspace/sandbox. No file under D:/workspace/sandbox was modified.

## Reachability baseline (rev_imports3.py, run in-repo)
- `app.services.remote_attestation` → `main.py reaches it? : True` (direct importers: compliance_report, kms_service, result_verifier, sandbox_runtime)
- `app.services.mutual_tls` → `main.py reaches it? : True` (single importer: `app/services/federation_connector.py:671`, function-local import of `load_federation_mtls_material`)
- `app.core.tlcp_service` → `main.py reaches it? : False` — **the file does not exist** (`wc: app/core/tlcp_service.py: No such file or directory`). The real module is `app/services/tlcp_service.py`. **0 of the 27 claims cite any tlcp file**, so that header path is inert.

Grading: TRUE·LIVE = the cited code sits on a path app.main actually executes (boot or request). TRUE·LATENT = the code is exactly as described but has no production caller (tests only). Both mean the claim is factually correct; the difference is blast radius. Executed probes use a stubbed `app.services.crypto_service` (`gmssl` is declared in `requirements.txt:18` but absent from `.venv-gate`), kept at `ocr-cds/probe_mtls.py` / `probe_attest.py`.

## Verdicts

### C1 [mutual_tls.py] critical/security — `_cert_to_pem` emits non-PEM, `get_tls_context` names it *_pem

**TRUE·LATENT**

Evidence read:
- `app/services/mutual_tls.py:289-296` — between the armor lines the body is plaintext key/value text: `"-----BEGIN CERTIFICATE-----"`, `f"Subject: {cert.subject}"`, `f"Issuer: {cert.issuer}"`, `f"Serial: ..."`, `f"Valid: {cert.not_before.isoformat()} to {cert.not_after.isoformat()}"`, `f"PublicKey: {cert.public_key[:64]}..."`, `f"Signature: {cert.signature[:64]}..."`, `"-----END CERTIFICATE-----"`. No base64(DER); body truncated with a literal `...`. "还截断成 [:64]" confirmed verbatim.
- `:277` / `:279` — outputs published as `"cert_pem"` / `"ca_cert_pem"`; `:280` adds `"verify": True`.
- `:278` `"key_der": identity.private_key` where `private_key` is the hex string from `crypto_service.generate_keypair()` (`:236`, `:244`) → name says DER, content is a hex scalar. "私钥以明文散落在业务 dict 中" confirmed. Executed: `get_tls_context("space-A")["key_der"]` = a raw hex-ish key string, `cert_pem` line 2 = `'Subject: CDS Space space-A'`.

Reachability: the only entry point that could hand this dict to httpx/ssl is `MTLSManager.get_tls_context` (`:265`). Repo-wide grep → inside `app/` only the definition; callers are `tests/test_mtls.py:207,232`. The real federation channel uses file paths instead: `app/services/federation_connector.py:681` → `mutual_tls.py:305`, then `federation_connector.py:708-710` passes `verify=mtls["ca_bundle"]`, `cert=(mtls["cert"], mtls["key"])` to `httpx.Client`.

Impact correction: "critical" does not hold. No production code feeds this pseudo-PEM into a TLS stack, so an attacker gains nothing today; the harm is a booby-trapped API (a future caller gets a parse error, never an auth bypass). The docstring at `:284-286` self-declares "In production, this would produce proper ASN.1 DER → PEM encoding" and `docs/mock-remediation-plan.md:204` already registers this exact range. TRUE on the code fact; severity medium (API-shape trap + plaintext key inside a dict), not critical.

### C2 [mutual_tls.py] high/security — unescaped `|`/`=` payload concatenation

**TRUE·LATENT** (hygiene + drift real; the stated byte-collision is NOT demonstrated — see impact)

Evidence read:
- `app/services/mutual_tls.py:100` (`generate_ca_certificate`), `:140` (`issue_certificate`), `:189` (`verify_certificate`) — three copies of the identical line `payload_str = "|".join(f"{k}={v}" for k, v in sorted(cert_data.items()))`. Zero escaping/quoting; `verify_certificate` re-serializes whatever the peer presented (`:177-189`).
- Externally influenceable field: `subject` via `:127` `subj = subject or f"CDS Space {space_id}"` with caller-supplied `space_id` / `subject` (`:118-120`).
- Drift between the three copies is proven by C10 (issue always writes `space_id`, verify writes it only when truthy) — the "三份重复逻辑不同步" half of the claim is not hypothetical, it is already broken.

Executed: `issue_certificate("x|subject=y", kp)` produces a signed payload with a duplicate `subject=` term (`...|space_id=x|subject=y|subject=CDS Space x|subject=y`) — injectable, ambiguous to any parser. But I could NOT construct two distinct field sets that serialize to identical bytes: `subject` is emitted last and always non-empty (`:127`, `:178`), so an injected tail can never be byte-equal to the honest payload, and the claim's own example cannot collide because `is_ca` sorts before `subject` (`sorted(cert_data.items())`, `:140`). No signature-reuse attack demonstrated.

Grading: TRUE as a correctness/maintainability defect (unnormalized serialization + triplicated logic, one of which already drifted), LATENT (no production caller). Severity: the "签名歧义/字段语义漂移" consequence is overstated; medium, not high.

### C3 [mutual_tls.py] high/security — `verify_certificate` checks only signature + time window

**TRUE·LATENT** (sub-claims 1-4 hold; the fallback wording needs one correction)

Evidence read (`:159-196`, whole function):
- Only two checks exist: expiry `:171-174` (`now = datetime.now(timezone.utc)` / `if now < cert.not_before or now > cert.not_after: logger.warning(...); return False`) and signature `:192-196` (`return self._crypto.verify(payload_str.encode(), cert.signature, ca.public_key)`).
- No `cert.issuer == ca.subject` comparison exists (grep `ca.subject` → 0 hits); `ca.is_ca` never read; no key_usage/basic-constraints, no chain depth or path building. Sub-claims 1, 2, 4 confirmed.
- Fallback verbatim: `:168` `ca = ca_cert or self.generate_ca_certificate()`.

Executed (CA-signed payloads re-signed with the CA key so only the audited field differs):
- cert whose `issuer` = `"Attacker Picks This"` → **accepted (True)** → no subject↔anchor binding, exactly sub-claim 1's substance.
- cert flipped to `is_ca=True` → **accepted (True)** → no CA-constraint check, sub-claim 2's substance.
- `verify_certificate(cert, None)` → **True**, i.e. omitting the trust anchor silently verifies against a freshly minted local CA instead of erroring.
- Counter-evidence for the naive reading of sub-claim 1: tampering `issuer` (or handing a foreign/leaf cert as `ca_cert`) *without* re-signing fails (`C3 issuer-mismatch accepted: False`, `C3 leaf-as-CA accepted: False`), because `issuer` is inside the signed payload (`:131`, `:179`) and verification pins the CA public key (`:195`).

Impact correction: the fallback does not "把配置错误变成校验通过" for arbitrary certs — `generate_ca_certificate()` self-signs with the same local `_ca_keypair`, so it substitutes the local key as trust anchor rather than rubber-stamping foreign certs (and `:165-166` `if not self.available:` / `return False` already fail-closes the no-crypto case). What is genuinely lost is explicit trust-anchor pinning, plus the fail-closed inconsistency with `verify_peer_certificate` (`:259-261` returns False when `_ca_cert` is None) that the claim correctly spots.

### C4 [mutual_tls.py] high/bug — no expected-space binding in `verify_peer_certificate`

**TRUE·LATENT** — the most consequential claim of the mTLS cluster.

Evidence read:
- `:258` `def verify_peer_certificate(self, cert: CertificateInfo) -> bool:` — no expected-space/subject parameter; the body is 4 lines: `if self._ca_cert is None: ... return False` (`:259-261`) then `return self._ca.verify_certificate(cert, self._ca_cert)` (`:263`). `cert.space_id` is used only to rebuild signed bytes (`:186-187`), never compared against the intended peer.
- The project's own test pins the weak behavior: `tests/test_mtls.py:226-227` asserts `mgr.verify_peer_certificate(id_a.certificate) is True` **and** `...id_b.certificate) is True` on one manager.

Executed: `MTLSManager` with identities for `space-A` and `space-B` → `verify_peer_certificate` returns True for both. Cross-space impersonation is one valid certificate away.

Reachability: grep `verify_peer_certificate` inside `app/` → only the definition (`:258`); every other hit is `tests/test_mtls.py:182-227`. The live federation channel authenticates with a bearer token + real client cert instead (`federation_connector.py:697-710`, `headers=request.headers`), so this is LATENT — but it is the reason the SM2 CA layer must not be wired into a handshake before this parameter exists.

### C5 [mutual_tls.py] high/security — `load_federation_mtls_material` validates pairing only, not the CA trust relationship

**TRUE·LIVE** (real gap in the validator; impact operational, not a bypass)

Evidence read:
- `:347-351` — the entire validation is `ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)` / `ctx.load_cert_chain(certfile=cert, keyfile=key)` / `except ssl.SSLError as e: raise ValueError(f"Federation mTLS cert/key pair is invalid: {e}")`. No `load_verify_locations` anywhere in the file (grep → only `load_cert_chain` at `:349`); no CA parse, no `not_valid_until`, no issuer/AKI-SKI match, no key-permission check. Narrow `except ssl.SSLError` confirmed — an unreadable key file surfaces from `load_cert_chain` as `OSError` and escapes the promised `ValueError`.
- LIVE: `app/services/federation_connector.py:681` calls it on every remote federation request (import at `:671`); production hard-fails on missing material (`:682-688`).

Impact correction: the claim's own stated consequence ("错误配置会通过校验，直到真正握手时才在对端失败") is precisely right, and the runtime already enforces the missing check at handshake — `federation_connector.py:708-710`: `client_verify = mtls["ca_bundle"] if mtls is not None else True`, `client_cert = (mtls["cert"], mtls["key"])`, `httpx.Client(timeout=timeout, verify=client_verify, cert=client_cert)`. A foreign-CA or expired client cert is rejected by the peer during the handshake; nothing passes silently on the wire, so attacker gain is nil. Residual defect = late opaque failure instead of a boot-time `ValueError`, plus the exception-type leak (shared with C12). Downgrade to medium.


## remote_attestation.py — critical/high

### C6 [remote_attestation.py] critical/security — plaintext local trust root used for both signing and verifying

**TRUE·LIVE — the strongest claim in the file; the forged-quote path reaches KMS DEK release and the one production gate that exists is bypassable.**

Evidence read:
- `app/services/remote_attestation.py:36` `_LOCAL_ATTESTATION_ROOT = b"cds-local-attestation-root-v1"` — plaintext module constant, no env/KMS override anywhere in the file.
- Same constant on both sides: `:45-49` `_sign_quote_payload` → `return sm3_hash(_LOCAL_ATTESTATION_ROOT + _canonical_quote_payload(payload))`; `:52-54` `_signature_valid` recomputes it. Anyone with the source can mint a quote.
- `:118` `trust_root: str = "external"` default on `AttestationResult`, only overwritten to `"local-simulated"` at `:632-633`.

Executed (`probe_attest.py`, then `probe_kms.py` against the real `KMSService._verify_attestation`):
- Offline forgery with only the public constant → `verify_quote` returns `verified=True` for all three built-in providers (sgx / sev_snp / firecracker), `trust_root=local-simulated`.
- End-to-end through the live KMS path with `CDS_ALLOW_SIMULATION=false`: a forged blob with `"type":"software_hash"` → `_verify_attestation` returns **False** (the Gap B2 gate at `app/services/kms_service.py:304-311` works), but the identical forgery with `"type":"sgx_ecdsa"` returns **True** — i.e. `_verify_attestation` reports attestation valid and `return result.verified` (`kms_service.py:329`) gates DEK release (`kms_service.py:234` inside `distribute_key`, `kms_service.py:198`). The gate keys on `quote_type_str = parsed.get("type", "software_hash")` (`kms_service.py:277`) — a field the forger writes.
- Same forged quote accepted with `CDS_ATTESTATION_ALLOWED_MEASUREMENTS` set to a real 64-hex value, because the KMS path builds its own policy (`kms_service.py:314`) that carries no whitelist (see C9).

Disclosed-vs-silent check against the project iron rule: the module *does* disclose — `trust_root="local-simulated"` (`:632-633`), the comment at `:113-117`, and `app/services/compliance_report.py:48` reports `local-simulated`. The disclosure is honest but **unenforced**: no consumer in `app/` reads `AttestationResult.trust_root` (grep: only `remote_attestation.py:118/633` plus the unrelated `session.trust_root` column in `app/api/sandbox_sessions.py:433` / `app/models/sandbox_session.py:85`). So a locally-signed quote travels into the KMS decision indistinguishable from hardware evidence, which is exactly the claim's "调用方极易把 local-simulated 当成真实硬件证明使用" — and here it demonstrably does.

Verdict: TRUE, severity critical is warranted (not the known "Tongsuo unwired / simulated paths are intentional" caveat: the defect is not that simulation exists, it is that a forger-controlled `type` string defeats the simulation gate on a key-release path).

### C7 [remote_attestation.py] high/security — `==` on hex digests; the "signature" is a plain hash, not a MAC

**TRUE·LIVE** (code facts exact; the practical driver is C6, not the timing side channel)

Evidence read:
- `app/services/remote_attestation.py:54` `return isinstance(signature, str) and signature == _sign_quote_payload(payload)` — Python `==` on str, no `hmac.compare_digest`. No `hmac` import in the file (imports `:23-32` are json/logging/time/uuid/abc/dataclasses/datetime/enum/typing).
- `_sign_quote_payload` (`:45-49`) is `sm3_hash(root ‖ canonical_json)` — an MD-structure hash-of-secret-prefix construction, not an HMAC, and the "key" is a public constant. So unforgeability is nil (see C6) and the length-extension concern of the claim is technically apt (though the payload ends with a JSON `}`, so trivial extension is not directly usable).
- Note the comparison is over a *hash output* that the attacker can compute offline; timing leakage is academic next to that, as the claim itself implies by leading with "更关键的是".

Verdict: TRUE·LIVE; fix as proposed (HMAC + `compare_digest`) plus per-instance key from C6.

### C8 [remote_attestation.py] high/security — freshness relies on an unsigned `timestamp`; `report_data` never bound to the session

**TRUE·LIVE — replay reproduced.**

Evidence read:
- Freshness uses only the caller-supplied dataclass field: `app/services/remote_attestation.py:587-588` `age = (datetime.now(timezone.utc) - quote.timestamp).total_seconds()` / `if age > self._policy.max_quote_age_seconds:`. Upper bound only — no `age < 0` rejection.
- `timestamp` is not in any signed payload: SGX payload keys at `:173-180` (`quote_id/type/measurement/report_data/tcb_version/firmware`), SEV `:291-298` (`:295`), Firecracker `:410-416` (`:414`). `TEEQuote.timestamp` defaults to `datetime.now(timezone.utc)` (`:97`) — i.e. the *caller* sets it, and `kms_service.py:283-292` builds the `TEEQuote` without passing a timestamp, so it is always "now".
- `report_data` is inside the signed payload (`:177`, `:295`, `:414`) but no verify path takes an expected value: provider entry points `:195`, `:312`, `:429` and `AttestationService.verify_quote` `:557` all take only `quote`. The only nonce binding in the repo lives one level up in `result_verifier.py:309-313` (compares `attestation_quote.report_data` to `result_hash`), and the KMS path does not do it at all.
- Naive-datetime crash: no tz check before `:587`.

Executed: a 2020-dated forged quote → `expired` (so the check works when the field is honest); the **same raw_bytes re-wrapped in a fresh `TEEQuote` (timestamp defaults to now) → `verified`, `trust_root=local-simulated`**; a timestamp 365 days in the future → `verified` (no upper bound); a naive `timestamp` → **TypeError** raised out of `verify_quote` (`can't subtract offset-naive and offset-aware datetimes`) — in the KMS path `kms_service.py:331` catches `Exception` and returns False (fail-closed), so it degrades rather than 500s.

### C9 [remote_attestation.py] high/security — settings-load failure only warns; empty whitelist = allow all

**TRUE·LIVE** — and worse than claimed: on the live KMS path the whitelist is empty even when the operator configures it.

Evidence read:
- `app/services/remote_attestation.py:504-518`: the settings read happens **only** under `if policy is None:` (`:503`); the handler is `except Exception as e: logger.warning("Could not load ATTESTATION_ALLOWED_MEASUREMENTS from settings: %s", e)` (`:517-518`) and execution continues with `measurements = []` → `policy.allowed_measurements = set()` (`:519`).
- Empty-means-allow-all is explicit: `:127` `allowed_measurements: set[str] = field(default_factory=set)  # Empty = allow all`, enforced by the guarded check `if self._policy.allowed_measurements:` at `:601` — so an empty set skips the check entirely.
- Partly mitigated at boot: `app/core/config.py:549-553` — in production, `KMS_REQUIRE_ATTESTATION` with a blank `ATTESTATION_ALLOWED_MEASUREMENTS` raises ("an empty measurement whitelist accepts any TEE"). That closes the blank-value case in prod, but not the "settings object exists yet the load raised / caller injected its own policy" case.
- The live caller bypasses it: `app/services/kms_service.py:314` `policy = AttestationPolicy(require_fresh_quote=True, max_quote_age_seconds=300)` → `policy is not None` → the settings branch never runs → `allowed_measurements == set()` (executed: printed `set()`).

Executed: with `CDS_ATTESTATION_ALLOWED_MEASUREMENTS` set to a real 64-hex measurement, `KMSService._verify_attestation` still returned **True** for a forged sgx-shaped quote — the operator-configured whitelist is inert on the DEK-release path. Combined with C6, the "measurement 白名单" control is absent in production regardless of configuration.


## mutual_tls.py — medium (claims 10-14)

### C10 [mutual_tls.py] medium/bug — `space_id` asymmetry breaks the sign/verify round-trip (+ naive datetime / malformed signature)

**TRUE·LATENT — round-trip break and both exception paths reproduced by execution.**

Evidence read:
- Issue side always emits the key: `app/services/mutual_tls.py:136` `"space_id": space_id,` inside `cert_data` (no truthiness guard), serialized at `:140`.
- Verify side emits it only when truthy: `app/services/mutual_tls.py:186-187` `if cert.space_id:` / `cert_data["space_id"] = cert.space_id`, serialized at `:189`.
- No tz/format validation at the top of `verify_certificate`; `return self._crypto.verify(...)` (`:192-196`) has no `try`.

Executed (`probe_mtls.py`, stubbed crypto so SM2 availability is irrelevant):
- `issue_certificate("", kp.public_key)` → `verify_certificate(cert, ca_cert)` = **False**; `issue_certificate("space-1", ...)` = **True**. Exactly the claimed invisible round-trip failure.
- Naive `not_before` (what a JSON round-trip yields) → **TypeError** from the comparison at `:172`.
- Malformed signature: whatever `crypto_service.verify` raises escapes `verify_certificate` untouched. I could not execute gmssl's own `int(sig,16)` parse (`gmssl` is pinned at `requirements.txt:18` but absent from `.venv-gate`); what is locally verifiable is that `mutual_tls.py` installs no handler and that `app/services/crypto_service.py:101-102` itself raises `RuntimeError` when gmssl is missing — either way the exception reaches the caller instead of `False`.

Reachability: LATENT for the same reason as C3/C4 — no production caller of `verify_certificate`/`verify_peer_certificate`, so "500 冒泡到联邦接口" is currently hypothetical.

### C11 [mutual_tls.py] medium/bug — issuer hardcoded to `"CDS Federation CA"`

**TRUE·LATENT — reproduced.**

Evidence read: `app/services/mutual_tls.py:131` (`"issuer": "CDS Federation CA"` inside the signed `cert_data`) and `:149` (`issuer="CDS Federation CA"` on the returned `CertificateInfo`), while `generate_ca_certificate(self, subject: str = "CDS Federation CA")` at `:80` takes any subject. `issue_certificate` never reads `self._ca_cert`/`self._ca.subject` (it holds no reference to the CA certificate at all).
Executed: `generate_ca_certificate(subject="ACME Federation CA")` + `issue_certificate("s2", kp)` → `ca.subject='ACME Federation CA'` vs `cert.issuer='CDS Federation CA'`. Because `issuer` is inside the signed payload (`:131`), the claim's warning is exactly right: adding the `cert.issuer == ca.subject` check that C3 recommends invalidates every already-issued certificate unless they are re-signed.

### C12 [mutual_tls.py] medium/bug — `_read_pem` lets `OSError`/`UnicodeDecodeError` escape a documented `ValueError` contract

**TRUE·LIVE**

Evidence read:
- Promised contract: `app/services/mutual_tls.py:312-313` "…validates that the files exist, are PEM, and the cert/key pair loads — raises ValueError on invalid material (misconfiguration must fail loudly, never degrade silently)".
- Implementation `:334-339`: `p = Path(path)` → `if not p.is_file(): raise ValueError(...)` (`:335-336`) → `text = p.read_text()` (`:337`, no `try`, no explicit encoding → locale-dependent decode) → substring test `if must_contain not in text: raise ValueError(...)` (`:338-339`). Callers `:341-343` pass `"BEGIN CERTIFICATE"`/`"PRIVATE KEY"`, which is the whole structural validation — no PEM/X.509 parse, as claimed.
- Failure modes that escape as `OSError`/`UnicodeDecodeError`: EACCES on a stat-able file, replacement between `is_file()` and `open()` (TOCTOU), a device/fifo, binary content. The same narrow-`except` bug exists on the pairing check (`:350`, see C5).
- Caller shape: `app/services/federation_connector.py:681` invokes it **before** the `try` at `:705`, so a non-`ValueError` propagates out of `_execute_remote_request` (the `except Exception` at `:740` never sees it).

Impact: operator-only inputs, no attacker gain; the defect is exception-type drift out of a documented contract at request/boot time. Accurate as medium.

### C13 [mutual_tls.py] medium/bug — module-level `mtls_manager` with unsynchronized check-then-act

**TRUE·LATENT** (the concurrency premise does not currently hold)

Evidence read:
- `app/services/mutual_tls.py:357` `mtls_manager = MTLSManager()`; construction happens at import (its `__init__` at `:207-211` builds `SM2CertificateAuthority()`, which calls `crypto_service.generate_keypair()`).
- Check-then-act verbatim: `:217-221` `if self._ca_cert is None: self._ca_cert = self._ca.generate_ca_certificate(); return self._ca_cert`; `:229-231` the same guard inside `create_identity`; plain-dict write at `:250`, reads at `:255-256` and `:268`. No `threading` import (imports `:19-24`).
- The lost-CA outcome follows from `:220` + `:263`: two racers each mint a CA certificate, the later store wins, identities signed under the loser then fail `verify_peer_certificate`.
Reachability: grep `mtls_manager` inside `app/` → only `mutual_tls.py:357`. No router/service/lifespan hook touches it, so "在 FastAPI 同步端点（线程池）中被并发调用" is not true today — hence LATENT, not LIVE.

### C14 [mutual_tls.py] medium/bug — `create_identity` silently overwrites; reaches into `_ca._crypto`

**TRUE·LATENT — overwrite reproduced.**

Evidence read:
- `app/services/mutual_tls.py:223-252`: no existence check before `:250` `self._identities[space_id] = identity`; the only trace is `:251` `logger.info(f"[mTLS] Created identity for space {space_id}")`. No revocation/blacklist structure exists in the file (no `revoke`/`crl`/`serial` set), so a leaked old key cannot be retired — confirmed by reading the full class.
- Encapsulation break verbatim at `:236` `keypair = self._ca._crypto.generate_keypair()`; `SM2CertificateAuthority` exposes only `available`/`generate_ca_certificate`/`issue_certificate`/`verify_certificate`, no public keypair factory.
Executed: two `create_identity("space-A")` calls returned two different `identity_id` values; the first identity was dropped with no warning.


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
- `app/services/remote_attestation.py:405` `components = f"kernel:v5.15-hardened|rootfs:gvisor-ext4|config:isolated"` — f-string with zero placeholders (the "此行 f-string 无占位符" note confirmed), `:407` `measurement = sm3_hash(f"{components}|{quote_id}".encode())`; SGX `:170` `sm3_hash(f"sgx-enclave-{quote_id}".encode())`; SEV `:288` `sm3_hash(f"sev-snp-{quote_id}".encode())`. `quote_id` embeds `uuid4().hex[:12]` (`:167`, `:286`, `:403`).
- Verify just recomputes the same formula (`:230`, `:346`, `:446`), so measurement carries no image/kernel/firmware fact.
- Consequence (1) confirmed: with a non-empty whitelist the check at `:601-612` can never hit.

Executed: two consecutive `generate_quote(TEEType.SGX)` → measurements `ffe042ec89a2573d…` vs `cfb252fd68401454…` (unpredictable per call); with `AttestationPolicy(allowed_measurements={'deadbeef'})` a freshly generated legit quote → `invalid_measurement`. So configuring the whitelist as the comment at `:505-507` demands makes every local quote fail — the operator is pushed to leave it empty, which is the fail-open of C9.

### C19 [remote_attestation.py] medium/bug — `min_tcb_version` and `require_signed` are never read

**TRUE·LIVE**

Evidence read:
- Declared at `app/services/remote_attestation.py:130-131` (`min_tcb_version: str = ""`, `require_signed: bool = True`). Repo-wide grep for `min_tcb_version` and `require_signed` returns **only those two lines** across `app/`, `tests/` and `docs/` — no read, no test.
- `tcb_status` is a hardcoded literal per provider: `:268` `tcb_status="up_to_date"`, `:384` `tcb_status="up_to_date"`, `:482` `tcb_status="software_l2"`, and `policy_checks={"signature_valid": True, "measurement_match": True, "tcb_current": True}` (`:269`, `:385`, `:483`) is written unconditionally for the success path.
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

Evidence read: `app/services/remote_attestation.py:636-638`, executed after `result = provider.verify_quote(quote)` (`:628`) with no condition:
`result.policy_checks["tee_type_allowed"] = True` / `["quote_fresh"] = True` / `["measurement_whitelisted"] = True`.
- Freshness may not have run: guarded by `if self._policy.require_fresh_quote:` (`:586`).
- Whitelist may not have run: guarded by `if self._policy.allowed_measurements:` (`:601`) — and on the live KMS path it is always empty (C9), so `measurement_whitelisted=True` is asserted for a check that never happened.
- Worse than "misleading": the provider's own `policy_checks` dict (`269`, `:385`, `:483` — `signature_valid/measurement_match/tcb_current` all literal `True`) is silently merged with these three.
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

1. **C2** — the byte-collision example (`subject="A|is_ca=True"` vs `subject="A"` + extra field) is unrealizable: `sorted(cert_data.items())` (`:140`) puts `is_ca` first and `subject` last, and `subject` is mandatory (`:127`), so an injected tail can never be byte-equal to an honest payload. I could not produce a same-signature collision; the demonstrated damage is the C10 round-trip break.
2. **C3** — a tampered `issuer` or a foreign leaf used as `ca_cert` is rejected unless re-signed with the CA key (measured: `False`/`False`), because `issuer` is inside the signed payload (`:131`) and verification pins `ca.public_key` (`:195`). The fallback at `:168` substitutes the local CA as anchor; it does not rubber-stamp arbitrary certs.
3. **C5** — the CA trust relationship *is* enforced, one layer up: `federation_connector.py:708-710` passes `verify=mtls["ca_bundle"]` and `cert=(cert,key)` to `httpx.Client`. No attacker gain; the defect is late/opaque failure.
4. **C13** — `mtls_manager` has zero production callers, so the "FastAPI thread-pool concurrency" premise is counterfactual.
5. **C15/C16/C17** — no 500: `kms_service.py:331-334` and `result_verifier.py:316-317` catch `Exception` and fail closed. The crash and the missing audit record are real.
6. **C21** — `list.append`/iteration are individually atomic under CPython, so "读到中间状态" overstates it; unbounded growth is the actual defect.
7. **C1** — pseudo-PEM never reaches a TLS stack; medium (API trap + plaintext key in a dict), not critical. Already registered in `docs/mock-remediation-plan.md:204`.

Highest-value findings, in order: **C6** (offline-forgeable quotes reach KMS DEK release and the `type`-field gate at `kms_service.py:304-306` is bypassable with one JSON field — measured `True` under `ALLOW_SIMULATION=false`), **C9+C18** (the measurement whitelist is inert on the live KMS path even when configured, so the control C6 needs in prod does not exist), **C8** (unsigned `timestamp` → replay measured `verified`; no upper bound on future timestamps), **C4** (cross-space certificate acceptance, pinned by `tests/test_mtls.py:226-227`).

