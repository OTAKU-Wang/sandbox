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
- Externally influenceable field: `subject` via `:128` `subj = subject or f"CDS Space {space_id}"` with caller-supplied `space_id` / `subject` (`:116-118`).
- Drift between the three copies is proven by C10 (issue always writes `space_id`, verify writes it only when truthy) — the "三份重复逻辑不同步" half of the claim is not hypothetical, it is already broken.

Executed: `issue_certificate("x|subject=y", kp)` produces a signed payload with a duplicate `subject=` term (`...|space_id=x|subject=y|subject=CDS Space x|subject=y`) — injectable, ambiguous to any parser. But I could NOT construct two distinct field sets that serialize to identical bytes: `subject` is emitted last and always non-empty (`:128`, `:178`), so an injected tail can never be byte-equal to the honest payload, and the claim's own example cannot collide because `is_ca` sorts before `subject` (`sorted(cert_data.items())`, `:140`). No signature-reuse attack demonstrated.

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

Impact correction: the fallback does not "把配置错误变成校验通过" for arbitrary certs — `generate_ca_certificate()` self-signs with the same local `_ca_keypair`, so it substitutes the local key as trust anchor rather than rubber-stamping foreign certs (and `:161-162` `if not self.available: return False` already fail-closes the no-crypto case). What is genuinely lost is explicit trust-anchor pinning, plus the fail-closed inconsistency with `verify_peer_certificate` (`:259-261` returns False when `_ca_cert` is None) that the claim correctly spots.

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

