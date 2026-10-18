# Verification of static-review findings — certificate_service / column_encryption / deterministic_sm4

Date 2026-10-18 · repo `D:/workspace/sandbox` @ branch `feat/ss-11-productization` · read-only (no repo file touched).
Source of claims: `ocr-findings.json`, filtered to the three paths → **37 findings** (the brief estimated ~18).

## Method / environment

* Every claim was checked against the file itself (line numbers below are from the current working tree).
* Executed checks ran on `D:/workspace/sandbox/.venv-gate/Scripts/python.exe` — **Python 3.12.10**, `cryptography 46.0.5`, **`gmssl 3.2.2` importable** (so `_HAS_GMSSL is True` in this venv; both modules run the real SM4/SM3 path). Logs: `chk_cert.log`, `chk_crypto.log`, `chk3.log`, `chk4.log`; scripts `verify_checks*.py` in this directory.
* `requirements.txt:18 gmssl>=3.2.2` and `Dockerfile.api:11 pip install -r requirements.txt` → the AES fallback is **not** the production-image posture.
* `openssl` used for DER truth: `C:/Program Files/Git/mingw64/bin/openssl.exe` (1.1.1l, no SM2 curve → it prints `Unable to load Public Key` for the SPKI; structure parsing/asn1parse succeeded, which is what matters).

### Reachability map (grep + import graph, executed)

| Entry point | Reaches | Live? |
|---|---|---|
| `POST /api/v1/certificates/generate` (`main.py:338`, group `certs`, ADMIN/OPERATOR) → `tlcp_service.generate_certificate` → `tlcp_service.py:348` → `certificate_service._build_cert_pem` | all DER-encoding helpers, `_build_extensions`, `crypto_service.sign` | **LIVE** — PEM returned in the API response, written to `/tmp/cds-certs/<id>/cert.pem`, and the documented source of `CDS_CUBE_TLS_CERT` (`deploy/cube-node/install.sh:26`) |
| `POST /api/v1/auth/login/cert` (`auth.py:520`), `tlcp_service.verify_certificate` (`tlcp_service.py:233`), `result_verifier._parse_certificate/_extract_public_key` (`result_verifier.py:205,229`) | `certificate_service._parse_x509_der` | **LIVE** |
| `GET /api/v1/certificates/{id}/verify-chain` (`certificates.py:182`) | `verify_chain` | live route, but the `certificates` table is **never written** by app code (only `issue_certificate` inserts, `certificate_service.py:211`, and it has no caller) → always `{"valid": false, "error": "Certificate … not found"}` |
| `certificate_service.issue_certificate / revoke_certificate / suspend_sessions_by_cert / validate_certificate / generate_crl / verify_certificate` | — | **NO caller in `app/`, `sdk/`, `cds-volume-plugin/`** — only `tests/test_certificate_service.py`, `tests/test_sprint4.py`, `tests/test_p0_8_9_10.py` (xfail ledger) |
| `DataEncryptionKey.encryption_cert_id` (read by `_cascade_revoke`, `certificate_service.py:314`) | — | grep: column exists in `app/models/kms.py:66` + `alembic/versions/0000_baseline_all_tables.py:551`; **no writer anywhere in the repo** → cascade always sees 0 DEKs |
| `app/api/sandbox_db.py` router (+ `gateway_service.py:379`) → `secure_duckdb.py:459,789` → `ColumnEncryption.encrypt_deterministic / encrypt_randomized / decrypt_column` | column_encryption | **LIVE** |
| `ColumnEncryption.encrypt_row / decrypt_row / encrypt_siv / decrypt_siv / encrypt_gcm / decrypt_gcm / hash_for_index / derive_column_key` | — | test-only (`tests/test_column_encryption.py`, `tests/test_sm4_duckdb_roundtrip.py`). The `encrypt_gcm/decrypt_gcm` hits in `kms_service`, `storage_service`, `egress_audit`, `policy_compiler`, `mpc_service`, `hsm_adapter`, `sandbox_security` are `app/utils/crypto.SM4Cipher` — a different class |
| `deterministic_sm4.*` | `query_rewriter.py:14` only; `query_rewriter` is imported **only** by `tests/test_deterministic_sm4.py` | **no live caller at all** |

---

# app/services/certificate_service.py

## [0] DER INTEGER encoding loses leading zero bytes → "certificates are corrupt"

**Verdict: FALSE** (core claim) — the negative-integer parenthetical is a real but unreachable defect (**LATENT**).
**My severity: Info** (non-issue as written) / Info (negative case).

Evidence:
* `certificate_service.py:26-27` `def _int_to_bytes(n): return n.to_bytes((n.bit_length()+7)//8,'big') if n else b'\x00'` — this is the *minimal* magnitude encoding; DER INTEGER **requires** minimality, so dropping leading `0x00` bytes is correct, not a bug.
* `certificate_service.py:53-62` `_encode_integer` prepends the sign byte **after** `_int_to_bytes`, so the `0x00` can never be "stripped again" as the finding claims.
* `certificate_service.py:826-828` builds `serial_int` from `b"CDS-…"` → the first byte is `0x43`, high bit clear, so no sign byte is needed at all.

Executed check (`chk_cert.log` §1, §4):
```
serial='CDS-0080A1B2C3D4E5F6' bitlen=159 _int_to_bytes=20B first=0x43 DER=02144344..4636 der_value_len=20 high_bit_set=False
[_encode_integer(-1)] RAISED OverflowError: can't convert negative int to unsigned
$ openssl asn1parse -inform der -in ee.der
   13:d=2  hl=2 l=  20 prim:   INTEGER  :4344532D30303830413142324333443445354636
$ openssl x509 -inform der -in ee.der -text -noout
   Serial Number: 43:44:53:2d:30:30:38:30:41:31:42:32:43:33:44:34:45:35:46:36
cryptography.load_der_x509_certificate(ee) -> parsed (only my attribute name was wrong)
```
OpenSSL and `cryptography` both parse the serial (and the whole certificate). The predicted `nested ASN.1 - invalid encoding` does **not** occur.
**Reachability:** `_encode_integer` is live (TLCP cert + key PEM), but only ever called with non-negative ints (`version 2`, `serial_int`, `CA=TRUE`), so the `OverflowError` branch has no input that reaches it.
**Impact:** none on stored/issued data. **Fix:** only the robustness half — `if n < 0: raise ValueError(...)` in `_encode_integer` (the finding's suggested body is otherwise a no-op change).

## [1] KeyUsage bit order wrong (EE cert gets keyCertSign); missing `critical`

**Verdict: TRUE. My severity: Medium** (conformance + wrong key-usage assertion; no direct privilege gain because no X.509 verifier is wired to these certs).

Evidence: `certificate_service.py:910-911`
```python
else:
    # digitalSignature + keyEncipherment = bits 0+2 = 0x05
    ku_value = _encode_tlv(0x03, b'\x01\x05')
```
and `:904-916` — both extensions are built as `SEQUENCE{OID, OCTET STRING}` with **no** `critical BOOLEAN`.
Executed (`chk4.log`, `chk_cert.log` §6):
```
is_ca=False (EE, live TLCP path): BIT STRING=03020105 unused_bits=1 content=0x05 -> ['keyCertSign']
is_ca=True  (CA)                : BIT STRING=03020106 unused_bits=1 content=0x06 -> ['keyCertSign','cRLSign']
$ openssl x509 -in ee.der -text : X509v3 Key Usage: Certificate Sign
                                  X509v3 Basic Constraints: CA:FALSE
$ openssl x509 -in ca.der -text : X509v3 Key Usage: Certificate Sign, CRL Sign
                                  X509v3 Basic Constraints: CA:FALSE, pathlen:1
```
The CA branch is bit-correct; the EE branch grants **keyCertSign** to an end-entity cert (bit5=0x04 + bit7 with unused_bits=1 → the trailing bit is illegal to set). Neither extension is critical.
**Additional defect the finding missed** (same executed proof): `_build_extensions` encodes `CA=TRUE` as `_encode_integer(1)` (`:902` → `30 03 02 01 01`), but `cA` is a **BOOLEAN**; OpenSSL decodes a CA certificate as `CA:FALSE, pathlen:1`. Every certificate this module mints mis-declares its CA flag.
**Reachability:** LIVE — `POST /api/v1/certificates/generate` → `tlcp_service.py:348` (always `is_ca=False`) → PEM returned to the caller and written to disk for cube-node sidecar mTLS.
**Impact:** third-party TLS stacks see an EE cert asserting `keyCertSign`, a non-critical keyUsage (RFC 5280 §4.2.1.3 MUST be critical), and a CA cert claiming `CA:FALSE`. Rejection or wrong path-length reasoning downstream; the `is_ca` claim in the emitted PEM is always false.
**Fix:** `ku_value = _encode_tlv(0x03, b'\x00\xa0')` for EE (`0x00` unused-bits, digitalSignature|keyEncipherment) and `_encode_tlv(0x03, b'\x00\x06')` for CA; `bc_value = _encode_sequence(b'\x01\x01\xff')` for CA; add `_encode_boolean(True)` before the OCTET STRING for basicConstraints (CA) and keyUsage.

## [2] CA private keys held in a plaintext in-process dict

**Verdict: TRUE as described, but LATENT** (the only writer/reader is `issue_certificate`).
**My severity: Medium-if-wired** (currently Info).

Evidence: `certificate_service.py:145-148` `self._local_private_keys: dict[str, str] = {}`; written at `:230-232`, read at `:187-195` with the `Issuer private key is not available …` raise; no TTL, no lock, no persistence → multi-worker/restart loss is real.
**Reachability:** `issue_certificate` has **no caller** in `app/`/`sdk/`/plugin (grep; only `tests/test_certificate_service.py`). The live cert path (`tlcp_service`) keeps its CA key in `TLCPService._ca_key_pair` (`tlcp_service.py:141`), a separate in-memory field with the same class of problem.
**Impact:** none today. **Fix:** keep sign operations behind `crypto_service`/HSM (`hsm_adapter`) and delete the vault; if kept, at minimum document the single-worker assumption and clear entries after issuance.

## [3] `signatureValue` is raw `r||s`, not DER `ECDSA-Sig-Value`

**Verdict: TRUE. My severity: Medium.**

Evidence: `certificate_service.py:873-876`
```python
signature = crypto_service.sign(tbs_cert, signing_key, signing_pub)
sig_bytes = bytes.fromhex(signature.signature)
sig_value = _encode_bitstring(sig_bytes)
```
`crypto_service.py:31-34` `class SM2Signature: signature: str  # 128 hex chars (r: 64 + s: 64)` — a flat r||s.
Executed (`chk_cert.log` §5): `parsed signature hex len: 128 -> bytes: 64`; OpenSSL prints the signature as 64 raw octets (`b6:3b:47:b8:…`) instead of a nested `SEQUENCE { INTEGER, INTEGER }`.
**Reachability:** LIVE (same path as [1]).
**Impact:** GB/T 32918.2 / RFC 3279 require ASN.1 `ECDSA-Sig-Value` in `signatureValue`. Tongsuo/OpenSSL-with-SM2/Java/browser verifiers cannot consume these certificates; only this repo's own `_parse_x509_der` can. Combined with [1] the PEM is currently a *self-consistent internal token*, not an X.509 certificate — and the repo already records that consequence (`docs/defect-ledger-2026-10-16-portal-e2e.md:47`: re-verifying a self-issued cert fails, needs re-issuance).
**Fix:** `sig_value = _encode_bitstring(_encode_sequence(_encode_integer(r), _encode_integer(s)))` with `r,s = int.from_bytes(sig_bytes[:32],'big'), int.from_bytes(sig_bytes[32:],'big')`; make `crypto_service.verify` accept both forms.

## [4] Revocation cascade swallows session-termination failure, still audits `cascade.complete`

**Verdict: TRUE (code) / LATENT (unreachable in practice). My severity: Low today, Medium-if-wired.**

Evidence: `certificate_service.py:345-354`
```python
total_terminated = 0
try:
    from app.services.sandbox_runtime import sandbox_runtime
    for key_id in suspended_key_ids:
        count = await sandbox_runtime.terminate_sessions_by_key(...)
        total_terminated += count
except Exception as e:
    logger.error(f"[CertRevoke] Session termination failed: {e}")
```
followed unconditionally by `audit_service.log(action="certificate.cascade.complete", … "sessions_terminated": total_terminated)` (`:356-366`). DEKs were already marked `REVOKED` and flushed at `:337`.
Executed (`chk3.log` §N): `asyncio.CancelledError.__mro__ = [CancelledError, BaseException, object]` → **not** swallowed, so the finding's parenthetical worry about cancellation is wrong; a partial loop (some keys terminated, then a raise) *is* real and `total_terminated` is then an undercount presented as "complete".
**Reachability:** blocked twice — `revoke_certificate` has no caller, and the DEK query at `:312-317` filters on `DataEncryptionKey.encryption_cert_id`, a column with **no writer** anywhere in the repo (grep) → the function returns at `:320-322` ("No DEKs linked") before reaching the try-block.
**Impact:** today: none. Once KMS populates `encryption_cert_id`: revoked-key sessions keep running while the audit says complete.
**Fix:** return the counts and record `partial_failure=True`; move the import out of the try, keep the per-key loop failure inside it, and let the DEK status change + audit share the transaction outcome.

## [5] CRL signed with a throwaway key per call; failures yield an unsigned CRL

**Verdict: TRUE. My severity: Medium-if-wired (Low today — no caller).**

Evidence: `certificate_service.py:455-465`
```python
try:
    keypair = crypto_service.generate_keypair()
    import json
    crl_data = json.dumps(crl, sort_keys=True).encode()
    signature = crypto_service.sign(crl_data, keypair.private_key, keypair.public_key)
    crl["signature"] = signature.signature
except Exception as e:
    logger.warning(f"CRL signing failed: {e}")
return crl
```
A fresh, unpublished keypair signs; on failure the `signature` key is simply absent and the caller cannot tell "unsigned" from "unverifiable".
**Reachability:** `generate_crl` is referenced only by `tests/test_certificate_service.py:161` (with `crypto_service` mocked). No router, no SDK, no plugin, and the CRL is not DER/ASN.1 (`docs/spec-implementation-analysis.md:437` says the same).
**Impact:** none today; would be a false-assurance primitive if consumed. **Fix:** sign with the issuer key from `crypto_service`/HSM, raise on failure, add `signature_algorithm` + issuer key id to the payload, move `import json` to module scope.

## [6] Trust anchor = any DB row with `is_ca and issuer == subject`

**Verdict: TRUE (code). My severity: High-as-designed, Medium-now** (route is live but the table is never populated).

Evidence: `certificate_service.py:519-522`
```python
# Self-signed CA (issuer == subject) = root of trust
if cert.is_ca and cert.issuer == cert.subject:
    chain_entry["is_root"] = True
    break
```
No trust-anchor set, no fingerprint allow-list, no self-signature check.
**Reachability:** live route `GET /api/v1/certificates/{cert_id}/verify-chain` (`certificates.py:169-182`), but `Certificate` rows are created **only** by `issue_certificate` (`:211`) which has no caller → the endpoint cannot currently return `valid: True`. The precondition the finding needs (attacker-minted PLATFORM cert with `subject` == `CN=CDS Root CA,O=CDS,C=CN`) is reachable only if `issue_certificate` is wired with a user-supplied `subject`, which is exactly its signature (`:150-157`).
**Impact:** latent PKI-trust collapse the moment issuance is exposed. **Fix:** a configured anchor set (`CDS_TRUST_ANCHOR_SERIALS`/fingerprints) plus `crypto_service.verify(tbs, sig, anchor_pub)` before `is_root = True`.

## [7] `verify_chain` never verifies the parent's signature

**Verdict: TRUE — the most severe design defect in the file. My severity: High (blocked in practice today).**

Evidence: `certificate_service.py:526-537` — the whole "verification" is
```python
for i in range(len(chain) - 1):
    child = chain[i]; parent = chain[i + 1]
    if child["issuer"] != parent["subject"]:
        return {"valid": False, "error": f"Issuer mismatch at position {i} …"}
return {"valid": True, "chain_length": len(chain), "chain": chain}
```
No `crypto_service.verify` call anywhere in the method; no `parent.is_ca` check (issuance checks it at `:182-185`, verification does not); and because the walk exits when `parent_cert_id is None` (`:524`) without ever setting `is_root`, a lone leaf with no parent returns **`valid: True`**.
Executed proof that the missing key is the *only* thing standing between this and a working check (`chk_cert.log` §5): `verify_certificate(CA-signed EE) signature_valid = False` while `same signature re-verified with the ISSUER public key -> True`.
**Reachability:** live endpoint, data-starved (see [6]). `docs/spec-implementation-analysis.md:37` already states it ("仅检查 DB 记录，不验证密码学签名").
**Impact:** string equality is not a chain of trust: any row with a matching `issuer` string validates. **Fix:** per hop, parse `parent.certificate_pem` → SPKI → `crypto_service.verify(child_tbs_der, child_sig_der, parent_pub)`, require `parent.is_ca`, and require the terminal node to be an anchor with a verified self-signature.

## [8] `verify_certificate` uses the subject's own key; `startswith("04")` corrupts keys

**Verdict: first half TRUE, second half FALSE. Overall LATENT (no caller). My severity: Medium.**

Evidence: `certificate_service.py:562-571`
```python
tbs_der = parsed["tbs_certificate_der"]; sig_hex = parsed["signature"]; pub_key = parsed["public_key"]
if pub_key.startswith("04"): pub_key = pub_key[2:]
valid = crypto_service.verify(tbs_der, sig_hex, pub_key)
```
`parsed["public_key"]` comes from the SPKI (`:670-677`), i.e. the **subject's** key, while `_build_cert_pem:869` signs with `signing_key = issuer_private_key or private_key`.
Executed (`chk_cert.log` §5):
```
verify_certificate(self-signed CA) signature_valid = True
verify_certificate(CA-signed EE)  signature_valid = False
same signature re-verified with the ISSUER public key -> True
parsed public_key len: 130 prefix: 04
```
So the wrong-key claim is confirmed exactly. The `startswith("04")` claim is **not**: certificates built here always carry the uncompressed-point prefix, so `parsed["public_key"]` is always 130 hex starting `04` and the strip is the intended behaviour (the finding's "x 坐标以 04 开头就误删" scenario cannot arise from this parser). `app/api/auth.py:523-524` does the same strip with the length guard (`len(cert_pub) == 130`), and `result_verifier.py:203-206,227-230` uses the bare `startswith` form.
**Reachability:** `certificate_service.verify_certificate` is **never called** (`tests/test_sprint4.py:48` only asserts it is callable); `certificates.py:149` calls `tlcp_service.verify_certificate`, which does expiry checks and no signature check at all.
**Impact:** when wired, every CA-issued certificate reports `signature_valid: False` (fail-closed if honoured, false trust if ignored). **Fix:** verify with the issuer's key (or branch on `issuer == subject`), and use the `len == 130` guard for consistency with `auth.py`.

## [9] Extension-parsing block is dead code (operator precedence)

**Verdict: TRUE. My severity: Medium** (live consumer, always-wrong result).

Evidence: `certificate_service.py:680` `if idx < len(tbs_buf) and tbs_buf[idx] & 0xa0 == 0xa3:` and `:688-689` `if oid == "2.5.29.19": result["is_ca"] = True  # Simplified: presence = CA`.
Executed (`chk_cert.log` §2, §5):
```
tag=0xa3: (tag & 0xa0) == 0xa3 -> False | (tag == 0xa3) -> True
parsed is_ca (CA cert!): False      <- cert built with is_ca=True
```
`&` binds tighter than `==`, `x & 0xa0 ∈ {0x00,0x20,0x80,0xa0}`, so the condition is constant-false and `parsed["is_ca"]` is permanently `False` (its initial value, `:627`). Note line `:635` (`tbs_buf[0] & 0xa0 == 0xa0`) is the same style but happens to work for tag `0xa0`.
**Reachability:** `_parse_x509_der` is **live** — `auth.py:520`, `tlcp_service.py:233`, `result_verifier.py:205,229`; the last two feed `CertificateInfo.is_ca` (`result_verifier.py:237`) and `_extract_public_key`.
**Impact:** CA certificates are always reported as non-CA; the "presence = CA" rule it never reaches would be wrong in the other direction (every certificate here carries basicConstraints). **Fix:** `tbs_buf[idx] == 0xA3`, then decode basicConstraints properly: expect `30 [03 01 01 FF]` / after fixing [1], read the BOOLEAN.

## [18] `_decode_oid` mishandles the first content octet

**Verdict: TRUE as a conformance bug, but the finding's arithmetic and impact are wrong → LATENT. My severity: Low.**

Evidence: `certificate_service.py:704-705` `first = data[0]; parts = [first // 40, first % 40]`.
Executed (`chk_cert.log` §3):
```
2a -> '1.2'   55 -> '2.5'   50 -> '2.0'   60 -> '2.16'   2a8648 -> '1.2.840'
7f -> '3.7'   (invalid arc 3; correct is 2.47)
80 -> '3.8'   (0x80 is an incomplete multi-byte first component)
round-trip 1.2.156.10197.1.501 / .301 / .301.1 / 2.5.29.19 / 2.5.29.15 : match=True (all)
```
So the decoder is wrong only for first octets ≥ 120 and for multi-byte first components. The finding's two examples are incorrect: `0x50` (decimal 80) correctly yields `2.0`, and `200` yields `5.0`, not `parts[1]=40`.
**Reachability:** every OID this module emits or parses (`1.2.156.10197.*`, `2.5.4.*`, `2.5.29.*`) round-trips correctly, so no current input triggers the bug; only a third-party certificate with e.g. `{2,47}…` or `{2,16,840,…}` in an AlgorithmIdentifier/extension would.
**Impact:** mis-parsed `signature_algorithm` / extension OIDs when parsing foreign certs. **Fix:** branch on `first < 40 / < 80 / else` and support the two-octet form for `first >= 0x80`.

## [19] Default `issuer` ≠ `subject` while signing with the subject key

**Verdict: TRUE (code) / LATENT (no caller). My severity: Medium-if-wired.**

Evidence: `certificate_service.py:169-173`
```python
if not subject: subject = f"CN={user_id},O=CDS,C=CN"
issuer = "CN=CDS Root CA,O=CDS,C=CN"
issuer_priv_key = None
```
with `_build_cert_pem:869` `signing_key = issuer_private_key or private_key`. The certificate is cryptographically self-signed yet its DB row and its DER claim an unrelated issuer → it is neither a valid self-signed root ([6] requires `issuer == subject`) nor CA-signed. `is_ca` is true for `CertType.PLATFORM` (`:205,222`), so such a row could later be used as a parent (`:182-196` would then raise on the missing key).
**Reachability:** `issue_certificate` only. The live `tlcp_service` path is consistent: `tlcp_service.py:347` passes `issuer_private_key, issuer_public_key = self._ca_key_pair or (private_key, public_key)`, so with gmssl present the TLCP CA key really does sign the `CN=CDS TLCP CA` issuer (only if CA init failed — `_init_ca` swallows the error at `tlcp_service.py:139-141` — does it degrade to the same mismatch, and then `crypto_service.sign` raises).
**Fix:** require an explicit issuer (or force `issuer = subject` when self-signing) and reject `cert_type=PLATFORM` without a parent.

## [20] `validity_days or DEFAULT_VALIDITY_DAYS` swallows 0/negative

**Verdict: TRUE (code) / LATENT. My severity: Low.**
Evidence: `certificate_service.py:166` `days = validity_days or self.DEFAULT_VALIDITY_DAYS`. `0` → 365; `-1` → `not_after < not_before` (no validation anywhere in `_build_cert_pem`).
**Reachability:** `issue_certificate` only; the live API's `validity_days` (`certificates.py:22`) is `Field(default=365, ge=1, le=3650)` and goes to `tlcp_service`, so no caller can pass 0 today.
**Fix:** `days = DEFAULT if validity_days is None else int(validity_days)` + range check.

## [21] Serial-number derivation collides

**Verdict: FALSE (overstated). My severity: Info.**
Evidence: `certificate_service.py:164` `serial = f"CDS-{uuid.uuid4().hex[:16].upper()}"` (64 bits of entropy) and `:826-828` `serial_int = int.from_bytes(serial.encode()[:20].rjust(20, b'\x00'), 'big')`.
Executed: the DER serial is a clean 20-byte INTEGER (`0214 4344 532d …`), no truncation, first byte `0x43`. Uniqueness comes from the 16 hex chars regardless of the constant `CDS-` prefix — the birthday bound is ~5×10⁹ certificates, and `app/models/certificate.py` declares `serial_number` **unique** (executed: `serial_number unique: True`), so a collision raises instead of silently aliasing a CRL entry.
**Fix (optional):** use `secrets.randbits(128)` for the DER serial and keep the human-readable `CDS-…` as a separate column.

## [22] Validity always UTCTime (2-digit year)

**Verdict: TRUE (code) / LATENT until 2050. My severity: Info.**
Evidence: `certificate_service.py:98-99` `_encode_utctime` always `%y`; `:847-850` emits UTCTime for both fields; `_decode_time:787-792` maps `year < 50 → +2000`.
Executed (`chk_crypto.log` §L): `_encode_utctime(2050-01-01) = "500101000000Z" -> decodes 1950-01-01`; `(2126-05-05) -> 2026-05-05`. Also: `CA_VALIDITY_DAYS occurrences in the module: 1` → the 10-year constant (`:143`) is **never applied**, so the "CA cert crosses 2049" premise does not hold today (openssl showed my hand-built 3650-day CA cert as `361015083636Z` → 2036, still inside the UTCTime window).
**Fix:** `_encode_time()` → GeneralizedTime (`0x18`, `%Y%m%d%H%M%SZ`) for years ≥ 2050 (the decoder already handles 15-char GeneralizedTime).

## [23] `suspend_sessions_by_cert` always returns 1

**Verdict: TRUE (code) / LATENT. My severity: Low.**
Evidence: `certificate_service.py:290-301` — docstring "Returns the number of sessions affected", body `await self._cascade_revoke(db, cert, reason); return 1`; `_cascade_revoke` returns `None` (`:303`) and keeps the counts locally (`:345,364`).
**Reachability:** no caller (grep; `tests/test_p0_8_9_10.py:144-162` xfail-asserts the method's existence and `inspect.getsource(cert_svc._cascade_revoke)`).
**Fix:** `return await self._cascade_revoke(...)` with a `(deks, sessions)` tuple, and return `0` only for "no cert".

## [24] Naive/aware datetime comparison in `validate_certificate`

**Verdict: FALSE as quoted (the quoted code does not exist); the underlying risk is real but LATENT. My severity: Low.**
Evidence: the finding's `existing_code` contains `if now > cert.after if False else cert.not_after:` — `chk_cert.log` §8: `"if False else" in src_txt → False`. The actual code is `certificate_service.py:412-416` (`if now < cert.not_before: … if now > cert.not_after:`) against `app/models/certificate.py:58-59` `DateTime(timezone=True)`.
Executed (`chk4.log`): on **sqlite**, SQLAlchemy round-trips that column as a **naive** datetime and the comparison raises:
```
written aware UTC datetime -> read back: datetime(2026,1,1,0,0) tzinfo: None
now_aware > value -> TypeError: can't compare offset-naive and offset-aware datetimes
```
On PostgreSQL (the production and remote-harness DB) the column is `TIMESTAMP WITH TIME ZONE` → aware → no error.
**Reachability:** `validate_certificate` has no caller in `app/` (only `tests/test_certificate_service.py`), so even the sqlite failure is test-only.
**Fix:** normalise before comparing (`v if v.tzinfo else v.replace(tzinfo=timezone.utc)`), which also protects the two live chain-walk comparisons at `:492-494`.

---

# app/services/column_encryption.py

## [10] Silent SM4→AES fallback changes the on-disk format

**Verdict: LATENT (real format fork, proven; but disclosed and not the deployment posture). My severity: Medium.**

Evidence: `column_encryption.py:20-26`
```python
try:
    from gmssl import sm4 as _sm4; from gmssl import sm3 as _sm3; _HAS_GMSSL = True
except ImportError:
    _HAS_GMSSL = False
    logger.warning("gmssl not installed — column encryption will use AES fallback")
```
`:96-111` AES-CBC branch vs `:113-127` SM4-CBC branch; no algorithm/version tag in the ciphertext.
So the fallback **is disclosed** (import-time warning, plus `docs/cubesandbox-parity-plan.md:627` records the split) — the project's honest-degradation rule is only half met: the warning exists, but the ciphertext carries no marker, so the *data* never says which backend produced it.
Executed cross-backend proof (`chk_crypto.log` §D):
```
gmssl ct 44b92499… (64B)   aes ct ba120445… (48B)   equal=False
gmssl idx 9db96df6…        aes  idx cb5c9be7…       equal=False
gmssl IV  9db96df6fe…      aes  IV  cb5c9be71f…     equal=False
read gmssl-written ciphertext with AES backend -> ValueError: Corrupted PKCS7 padding
read AES-written ciphertext with gmssl backend -> IndexError: index out of range
```
**Reachability:** live module (`column_encryption = build_column_encryption()`, `:372`) used by `secure_duckdb` → `app/api/sandbox_db.py` router. But `gmssl>=3.2.2` is pinned in `requirements.txt:18` and installed by `Dockerfile.api:11`, so a write/read backend switch requires a non-image deployment (venv drift — the repo's own note that the local venv is py3.14) or a failed wheel install.
**Impact:** all deterministic columns + index hashes become unreadable/unqueryable across such a transition; nothing detects it. **Fix:** one-byte scheme tag in the ciphertext header (`0x01=sm4-cbc-keyed-iv`, `0x02=aes`) validated on decrypt, or fail-fast when `_HAS_GMSSL` is false in a GM/T deployment (mirrors `CDS_SM3_FALLBACK_ALLOWED`).

## [11] No ciphertext structure validation in `decrypt_deterministic`

**Verdict: TRUE. My severity: Low** (wrong exception type / oracle-shaped errors, no confidentiality break).
Evidence: `column_encryption.py:134-139` (AES branch) and `:148-153` (gmssl branch) — only `if len(encrypted.ciphertext) < 16`, then `iv = [:16]`, `ct = [16:]`, no emptiness/alignment check.
Executed (`chk_crypto.log` §C):
```
gmssl backend decrypt len==16 (IV only)              -> IndexError: list index out of range
gmssl backend decrypt len==20 (not block aligned)     -> IndexError: index out of range
gmssl backend decrypt len==15                          -> ValueError: Ciphertext too short for SIV mode
AES fallback decrypt len==16                           -> IndexError: index out of range
AES fallback decrypt len==20                           -> ValueError: … not a multiple of the block length
gmssl pkcs7_unpadding source: pkcs7_unpadding = lambda data: data[:-data[-1]]
```
The finding's `IndexError` prediction is confirmed for both branches; `cryptography`'s unpad (`_pkcs7_unpad`, `:85-91`) raises `ValueError`, so the failure type depends on the backend — and gmssl's library unpadding does **no** validation at all (`data[:-data[-1]]`), so a corrupted last byte silently truncates the plaintext instead of erroring (that part belongs to [15] too).
**Reachability:** LIVE (secure_duckdb decrypt path catches `Exception` → `[DECRYPT_ERROR]`, `secure_duckdb.py:808-810`). The caller converts everything to one visible sentinel, so the padding-oracle angle is largely mitigated by the caller, not by this function.
**Fix:** the finding's suggested guard (`len(ct)==0 or len(ct)%16 → ValueError`), plus validate gmssl's unpadding instead of trusting it.

## [12] AEAD without context binding (`associated_data=None`)

**Verdict: TRUE. My severity: Medium** (live, unauthenticated ciphertext substitution).
Evidence: `column_encryption.py:167` `ct_and_tag = aesgcm.encrypt(nonce, data, None)` (and `:252` for the simplified API); nothing else in the envelope binds `column_name` / `key_id` / mode (`EncryptedColumn` carries them, but they are never fed to the AEAD, and `secure_duckdb.py:800-804` re-supplies them from its own metadata).
Executed (`chk_crypto.log` §E):
```
ciphertext minted for column 'tax_amount' replayed onto 'loan_amount' decrypts to: 'amount=999999'
adata actually passed to AESGCM.encrypt: None
```
**Reachability:** LIVE — `secure_duckdb.py:470-477` encrypts randomized columns into DuckDB BLOBs with the per-table DEK; anyone able to write BLOBs (a compromised/curious data path inside the same tenant) can copy a value between rows or between two RANDOMIZED columns of the same table and authentication still passes.
**Impact:** undetectable cross-row / cross-column substitution of high-sensitivity fields (amounts, names) inside an encrypted column store — the exact threat column-level encryption is supposed to make detectable.
**Fix:** `adata = f"{key_id}|{column_name}|aes-128-gcm".encode()` in both `encrypt_randomized` and `decrypt_randomized` (and in `encrypt_gcm`/`decrypt_gcm`), versioned so old blobs can be identified.

## [13] `decrypt_row` swallows errors and returns ciphertext as plaintext

**Verdict: TRUE (code) / LATENT (function has no production caller). My severity: Medium-if-wired.**
Evidence: `column_encryption.py:284-305`
```python
try:
    ct = bytes.fromhex(result[col]); enc = EncryptedColumn(ciphertext=ct, mode=mode, …)
    result[col] = self.decrypt_column(enc)
except Exception:
    pass  # Skip if not decryptable
```
Executed (`chk_crypto.log` §F): with the wrong `mode` (or the wrong DEK) the row comes back **unchanged**, i.e. the hex ciphertext is handed to the caller as the plaintext, with no exception and no log:
```
decrypt_row(WRONG mode) -> {'phone': '9db96df6fe2f8ca5822d9153653cd7a9…', …}
== ciphertext echoed back as plaintext: True | no exception, no log -> silent
decrypt_row with the WRONG DEK -> {'phone': '9db96df6fe2f8ca5822d…', …}
```
Also confirmed: a legitimately all-hex plaintext (`"deadbeef"*6`) round-trips normally, so the `isinstance(str)` + `fromhex` heuristic itself is not the practical hazard — the silence is.
**Reachability:** `encrypt_row`/`decrypt_row` are referenced only by `tests/test_column_encryption.py:128-148`. The live equivalent (`secure_duckdb.py:808-810`) logs and substitutes `"[DECRYPT_ERROR]"`. `db_access_proxy.py:188` (which does decrypt via `decrypt_column`) is imported only by tests.
**Fix:** `logger.warning("column %s decryption failed", col, exc_info=True)` and set `None` (or raise) instead of leaving ciphertext in a plaintext-typed field.

## [25] `_pkcs7_unpad` raises `IndexError` on empty input

**Verdict: TRUE. My severity: Low.** (Same defect family as [11]; separate function.)
Evidence: `column_encryption.py:84-91` `pad_len = data[-1]` with no length guard. Executed: `ce._pkcs7_unpad(b'') -> IndexError: index out of range`.
**Reachability:** live (called by `decrypt_deterministic`, both branches), caller catches `Exception`. **Fix:** the finding's guard (`if not data or len(data) % 16 != 0: raise ValueError`).

## [26] `decrypt_randomized` mislabels every failure as tampering

**Verdict: TRUE. My severity: Low.**
Evidence: `column_encryption.py:179-188` — guard is `len(...) < 12` (a legal blob is ≥ 28), `try` wraps both `aesgcm.decrypt` **and** `plaintext.decode("utf-8")`, `except Exception: raise ValueError("Authentication tag mismatch — ciphertext tampered")` with no `from err`. Executed: `chk_crypto.log` §H — 16-byte blob → "ciphertext tampered"; `'raise ... from' used anywhere in the module: False`.
**Reachability:** LIVE (secure_duckdb randomized columns). Operational impact only: implementation bugs / truncated BLOBs / non-UTF8 payloads get reported as a security event.
**Fix:** `except InvalidTag as err: raise ValueError(...) from err`; move `decode` out of the `try`; guard `len < 28`.

## [27] `decrypt_gcm` — same mislabel, plus no length checks at all

**Verdict: TRUE (code) / LATENT (no production caller). My severity: Low.**
Evidence: `column_encryption.py:263-268` (`aesgcm.decrypt(nonce, ciphertext + tag, None)` inside a bare `except Exception`). Executed: `decrypt_gcm(tag[:8])` → "ciphertext tampered"; reversed argument order → "ciphertext tampered" (the real InvalidTag/length problem is masked).
**Reachability:** the identically named `encrypt_gcm/decrypt_gcm` in `kms_service`, `storage_service`, `egress_audit`, `policy_compiler`, `mpc_service`, `hsm_adapter`, `sandbox_security` belong to `app/utils/crypto.SM4Cipher`, **not** this class; grep finds no caller of `ColumnEncryption.decrypt_gcm`.
**Fix:** as [26] + assert `len(nonce)==12 and len(tag)==16`.

## [28] No domain separation in the keyed derivations

**Verdict: TRUE. My severity: Medium.**
Evidence: `column_encryption.py:73` `data = dek + column_name.encode("utf-8") + plaintext` (also `:214` for `hash_for_index`, `:309` for `derive_column_key`) — unseparated variable-length concatenation, raw MD-style hash, no HMAC.
Executed (`chk_crypto.log` §G):
```
_derive_iv_siv(dek,'ab',b'cX') == _derive_iv_siv(dek,'a',b'bcX') : True
hash_for_index(dek,'ab','cX')  == hash_for_index(dek,'a','bcX')  : True
IV/hash is the keyed digest, and the IV is transmitted in the ciphertext header:
   9db96df6fe2f8ca5822d9153653cd7a9 == first16 of SM3(dek||column||plaintext)
```
Consequences: (a) the boundary ambiguity makes two *different columns* derive the same IV and the same index digest, so ciphertext prefixes and index values are linkable across columns; (b) `hash_for_index` publishes a keyed digest (the full SM3/SHA-256 hex) while the same keyed-hash value is also transmitted in the ciphertext header (`iv` prefix) — with a Merkle–Damgård hash and a public `H(key‖m)`, the digest for `m‖padding‖extra` is computable without the DEK (stated as a standard property; not executed here), which is why the raw-construction "SIV" is not an SIV.
**Reachability:** `_derive_iv_siv` LIVE via deterministic encryption in secure_duckdb; `hash_for_index`/`derive_column_key` are test-only.
**Fix:** length-prefixed or `|`-separated pre-image plus an HMAC structure (`hmac.new(dek, msg, hashlib.sha256)` / HMAC-SM3), and index with `HMAC(dek, f"{table}.{column}|{plaintext}")`.

## [29] Hard-coded default DEK in the repository

**Verdict: TRUE but already gated (MR-A9/PZ-04); residual risk is a DEBUG misconfiguration. My severity: Medium.**
Evidence: `column_encryption.py:322` `_default_dek = hashlib.sha256(b"cds-column-encryption-default-key").digest()[:16]`; `:357-369` `build_column_encryption()` raises when `is_production(s)` and no key is configured, otherwise logs one warning and uses the default.
Executed (`chk_crypto.log` §K):
```
singleton key_id = cds-default (default DEK active: True)
def is_production(settings): return (not settings.DEBUG and os.environ.get("TESTING") != "1"
                                    and not os.environ.get("PYTEST_CURRENT_TEST"))
is_production(DEBUG=True) = False
config.py:479: if not self.COLUMN_ENCRYPTION_KEY: raise …  (inside validate_security_config)
```
So the claim "no configuration gate" (as still worded in `docs/mock-remediation-plan.md:32`) is outdated — there are two prod gates — but the finding is right that prod posture hinges on `not DEBUG`, and that with `DEBUG=True` the module silently encrypts with a key derivable from public source. The finding's other point is also correct: `encrypt_row` persists only `enc.ciphertext.hex()` (`:281`), so `key_id`/`mode` never reach storage and the ciphertext carries no key provenance.
**Reachability:** LIVE singleton, imported by secure_duckdb; production compose/helm set `CDS_*` env (the key must be provided or startup raises).
**Fix:** make the production check independent of `DEBUG` (explicit `CDS_ENV=production`), and refuse to encrypt with `key_id == "cds-default"` unless `DEBUG` is on *and* the caller opted in.

## [35] `sm4-gcm` / `sm4-siv` labels vs AES-GCM / keyed-IV-CBC

**Verdict: TRUE (label/compliance), partially mitigated by in-code disclosure. My severity: Low.**
Evidence: `column_encryption.py:1-7` module docstring "SM4-SIV … SM4-GCM … Both use SM4 per GM/T 0002 national standard"; `:30-31` `DETERMINISTIC = "sm4-siv"`, `RANDOMIZED = "sm4-gcm"`. Honest counter-evidence in the same file: `:158-161` and `:240-246` state "Uses AES-128-GCM … gmssl does not support SM4-GCM, so AES-GCM provides real AEAD". The deterministic mode is keyed-hash-IV + CBC with no S2V and no integrity (verified by reading `:96-127` + executed §D IV behaviour).
**Reachability:** the enum *value* is a wire/persisted string: `secure_duckdb.py:470` `EncryptionMode(encrypted_columns[col])` stores it in table metadata → it does end up in audit/compliance-visible metadata.
**Fix:** rename the values (`aes-128-gcm`, `sm4-cbc-keyed-iv`) with a migration alias, or wire real SM4-GCM (Tongsuo) before claiming `sm4-gcm` — the repo's own W19 task (`docs/cubesandbox-parity-plan.md:627`) says exactly this.

## [36] Unused `import struct`

**Verdict: TRUE. My severity: Info.**
Evidence: `column_encryption.py:13`. Executed: `occurrences of 'struct' in column_encryption.py: ['struct']` (the import only). **Fix:** delete the line.

---

# app/services/deterministic_sm4.py

## [14] Silent AES fallback makes historical ciphertext unreadable

**Verdict: LATENT. My severity: Medium-if-wired** (the mechanism is proven; the module has no live caller).
Evidence: `deterministic_sm4.py:16-21` (import-time `_HAS_GMSSL` + `logger.warning("gmssl not installed — SM4 encryption will use AES-CBC fallback")` — i.e. **disclosed**, contrary to "silent"), `:68-78` (AES branch vs SM4 branch), `:59-64` (`_derive_iv` flips SM3↔SHA-256), `:81-92` (decrypt mirrors it). No algorithm tag in the ciphertext.
Executed (`chk_crypto.log` §D):
```
deterministic_sm4: gmssl ct 5bd94936…  aes ct 65abea0f…  equal=False
deterministic_sm4 decrypt gmssl-ct under AES backend -> ValueError: Invalid PKCS7 padding: 167
IV derivation also flips: gmssl 45b9024964…  aes 17f01739d0…
```
So the failure is an explicit `ValueError`/`IndexError` rather than silent garbage in this pair of backends — the "silently corrupted plaintext" variant depends on gmssl's unchecked `data[:-data[-1]]` unpadding (see [15]).
**Reachability:** `query_rewriter.py:14` is the only importer and `query_rewriter` is imported only by `tests/test_deterministic_sm4.py`; no router/service/SDK/plugin reference. Production image installs gmssl anyway (`requirements.txt:18`, `Dockerfile.api:11`).
**Fix:** same scheme-tag/fail-fast as [10]; the finding's suggested `raise RuntimeError` in `encrypt` is the right shape once a live writer exists (it would break existing dev ciphertexts, so pair it with the tag).

## [15] gmssl path relies on library padding; unvalidated plaintext; two incompatible paths

**Verdict: TRUE on the padding-validation and incompatibility points; FALSE on "补块实现不同". LATENT reachability. My severity: Low-Medium.**
Evidence: `deterministic_sm4.py:73-78` encrypt passes **unpadded** `data` to `crypt_cbc`; `:86-88` the AES branch pads explicitly with `_pkcs7_pad`; `:90-92` gmssl decrypt then `plaintext.decode("utf-8")` with no check, while `:84-85` the AES branch validates via `_pkcs7_unpad`.
Library truth (`gmssl/func.py:12,16`, `gmssl/sm4.py:172-196`):
```
pkcs7_padding   = lambda data, block=16: data + [(16 - len(data) % block) for _ in range(16 - len(data) % block)]
pkcs7_unpadding = lambda data: data[:-data[-1]]
```
→ gmssl *does* apply PKCS7 with the same full-block semantics, so the two paths agree in framing (the finding's "补块实现都不同" is wrong); but its unpadding performs **zero validation** and strips `last_byte` bytes blindly — a corrupted ciphertext can silently return a truncated/garbage string, or `IndexError` on empty input (executed: `IndexError: list index out of range` via `column_encryption` gmssl branch).
**Reachability:** `query_rewriter` only (test-only) — see [14].
**Fix:** decrypt through the module's own `_pkcs7_unpad` (call gmssl with `padding_mode=ZERO` or unpad manually) and verify the recovered bytes decode as UTF-8 before returning.

## [16] `RandomizedSM4` / module docstring claim SM4, implementation is AES-128-GCM

**Verdict: TRUE. My severity: Medium (compliance labelling), not a confidentiality defect.**
Evidence: `deterministic_sm4.py:6` `Both use SM4-CBC per GM/T 0002 national standard.` vs `:107-126`
```python
class RandomizedSM4:
    """Randomized AES-GCM encryption … Uses AES-128-GCM with the SM4 key …"""
    …
    ct_and_tag = aesgcm.encrypt(nonce, data, None)
```
So the class docstring is honest, the module docstring is not (and "Both … SM4-CBC" is doubly wrong: the randomized path is GCM, and it is AES). The registry at `:190-192,207-216` marks `ent_name`, `legal_person`, `tax_amount`, `loan_amount` as `rand` → those high-sensitivity columns are AES-GCM under an "SM4" module name.
**Reachability:** no live writer (see [14]); the same AES-GCM-under-SM4-label pattern in `app/utils/crypto.SM4Cipher` *is* live and is already tracked (`docs/cubesandbox-parity-plan.md` G20/W19; `app/AGENTS.md` anti-pattern list).
**Fix:** rename to `RandomizedAEAD`/`AESGCMColumnCipher`, correct the module docstring, and register the algorithm per column so reports state the truth.

## [17] Over-broad `except` in `RandomizedSM4.decrypt`, wrong message, lost chain

**Verdict: TRUE. LATENT reachability. My severity: Low.**
Evidence: `deterministic_sm4.py:131-141`. Executed (`chk_crypto.log` §H): truncated 20 B, 12 B and 27 B inputs all raise `ValueError: Authentication tag mismatch — ciphertext tampered`; `decode("utf-8")` sits inside the `try`; no `raise … from`.
**Reachability:** `query_rewriter.py:221` (`ResultDecryptor.decrypt_rows`) — test-only.
**Fix:** the finding's patch is correct (`except InvalidTag as err: raise ValueError(...) from err`, decode outside).

## [30] `_pkcs7_unpad` empty input / hardcoded 16 vs `block_size`

**Verdict: TRUE. My severity: Low.**
Evidence: `deterministic_sm4.py:25-36`. Executed: `d4._pkcs7_unpad(b'') -> IndexError`; `d4._pkcs7_unpad(b'\x01') -> b''` (accepted as a valid 1-byte pad and returns an empty string, i.e. a short-data case that silently yields `""`); `_pkcs7_pad(b'x', block_size=8)` → `_pkcs7_unpad` round-trips to `b'x'` only because the default block size coincides — the hardcoded `16` in `pad_len > 16` is a real inconsistency with the `block_size` parameter of `_pkcs7_pad`.
**Reachability:** AES-fallback branch only (no live caller).
**Fix:** the finding's version (empty/alignment guard, `pad_len > block_size`, `pad_len > len(data)`, pass `block_size` through).

## [31] `hash_for_index` backend switch breaks index equality

**Verdict: TRUE (code) / LATENT reachability. My severity: Medium-if-wired.**
Evidence: `deterministic_sm4.py:94-104`. Executed: `gmssl hash f952d7734bc79b2d48b318544460e6f0` vs `aes hash e00fbe6b86f24b192cc1f19fb946aa29` — `equal=False`, both 32 hex chars, so a rewritten query simply matches nothing.
**Reachability:** `query_rewriter.py:99` (`hash_value = self.det.hash_for_index(plain_value)`) — test-only; the sibling `column_encryption.hash_for_index` has no caller either.
**Fix:** one backend or a tagged hash algorithm column; fail fast on mismatch.

## [32] Index hash lacks table/column domain separation

**Verdict: TRUE. My severity: Medium-if-wired** (worse than the finding states: the pre-image has no column name at all).
Evidence: `deterministic_sm4.py:101` `data = self.dek + plaintext.encode("utf-8")` — no table, no column, no key version; the sibling module at least includes `column_name` (`column_encryption.py:214`).
Executed: `hash_for_index` signature carries no table/column (`True`), and with one DEK the digest for a given plaintext is identical wherever it is used.
**Reachability:** test-only (see [31]).
**Impact:** with the registry columns (`ent_id_hash`, `phone_hash`, `tax_id_hash`, `credit_id_hash`), one index value proves "same plaintext in two different columns/tables" — cross-column correlation and a fixed dictionary target. **Fix:** `HMAC-SM3(dek, f"{table}.{column}|{key_version}|{plaintext}")`.

## [33] `len(data) < 12` should be `< 28`

**Verdict: TRUE. My severity: Low.**
Evidence: `deterministic_sm4.py:131-133`; format is `nonce(12) ‖ ct ‖ tag(16)` (`:113`), so 12–27 bytes are structurally impossible. Executed: a 27-byte input passes the guard and is reported as "ciphertext tampered".
**Reachability:** test-only. **Fix:** `if len(data) < 28: raise ValueError("Ciphertext too short")`.

## [34] `ColumnEncryptionRegistry.register` validates nothing

**Verdict: TRUE. LATENT reachability. My severity: Low-Medium.**
Evidence: `deterministic_sm4.py:163-176`. Executed (`chk_crypto.log` §I):
```
register(level='Det') typo                     -> no error
register(level='det') without enc_column       -> no error
re-register silently overwrites                -> EncryptionColumnMeta(table='t', column='c3', level='plain', enc_column=None, …)
registry is the query rewriter's only 'what must be encrypted' source: True
```
**Reachability:** the registry that matters for fail-open is the one the query rewriter reads, and that whole module is test-only; the 15 default registrations (`:188-216`) are all valid levels with matching `enc_column`, so nothing is mis-registered today.
**Fix:** the finding's guard (`level not in ("plain","det","rand") → ValueError`; require `enc_column` for det/rand; warn on overwrite).

---

# Summary

| # | verdict | my severity | live? |
|---|---|---|---|
| 0 DER INTEGER encoding | **FALSE** (+ LATENT negative-int) | Info | yes (no impact) |
| 1 KeyUsage bits / non-critical | **TRUE** | Medium | **LIVE** |
| 2 in-process CA key vault | TRUE → LATENT | Info now / Med if wired | no |
| 3 r‖s signatureValue | **TRUE** | Medium | **LIVE** |
| 4 cascade swallow | TRUE → LATENT | Low | no |
| 5 CRL throwaway key | TRUE → LATENT | Low | no |
| 6 weak trust anchor | **TRUE** | Med now / High if issued | route live, data-starved |
| 7 no signature check in chain | **TRUE** | High (blocked by missing rows) | route live, data-starved |
| 8 wrong verify key / `startswith("04")` | half TRUE, half FALSE → LATENT | Med | no |
| 9 dead extension block (precedence) | **TRUE** | Medium | **LIVE** |
| 18 `_decode_oid` first octet | TRUE-but-misanalysed → LATENT | Low | no |
| 19 issuer ≠ subject self-signed | TRUE → LATENT | Med if wired | no |
| 20 `validity_days or 365` | TRUE → LATENT | Low | no |
| 21 serial collision | **FALSE** | Info | — |
| 22 UTCTime 2-digit year | TRUE → LATENT (2050) | Info | no |
| 23 `return 1` | TRUE → LATENT | Low | no |
| 24 naive/aware compare (quoted code absent) | **FALSE as quoted** / LATENT | Low | no |
| 10 SM4→AES format fork | **LATENT** (disclosed, gmssl pinned) | Medium | live module, stable backend |
| 11 SIV length validation | **TRUE** | Low | **LIVE** |
| 12 AEAD without AAD | **TRUE** | Medium | **LIVE** |
| 13 `decrypt_row` echo-back | TRUE → LATENT | Med if wired | no |
| 25 unpad IndexError | **TRUE** | Low | **LIVE** |
| 26 decrypt_randomized mislabel | **TRUE** | Low | **LIVE** |
| 27 decrypt_gcm mislabel | TRUE → LATENT | Low | no |
| 28 no domain separation | **TRUE** | Medium | **LIVE** (IV path) |
| 29 default DEK | TRUE (already gated) | Medium | live, prod-gated |
| 35 sm4-* labels vs AES | TRUE | Low | **LIVE** metadata |
| 36 unused `struct` | **TRUE** | Info | — |
| 14 SM4→AES data loss | **LATENT** | Med if wired | no |
| 15 gmssl padding trust | TRUE (except "padding differs") → LATENT | Low-Med | no |
| 16 SM4 label vs AES-GCM | **TRUE** | Medium (compliance) | no live writer |
| 17 broad except / mislabel | TRUE → LATENT | Low | no |
| 30 unpad empty/short | **TRUE** → LATENT | Low | no |
| 31 index hash backend fork | TRUE → LATENT | Med if wired | no |
| 32 no table/column in index hash | **TRUE** → LATENT | Med if wired | no |
| 33 `< 12` vs `< 28` | **TRUE** → LATENT | Low | no |
| 34 registry unvalidated | TRUE → LATENT | Low-Med | no |

**Three most severe TRUE findings**
1. `verify_chain` never performs a cryptographic signature check (`certificate_service.py:526-537`) — chain validity is string equality on `issuer`/`subject`, and a chain that ends without any root still returns `valid: True`; blocked today only because nothing populates the `certificates` table.
2. The live certificate emitter produces non-conformant X.509 (`:910-911` KeyUsage → end-entity certs get `keyCertSign`; `:902` `CA=TRUE` encoded as INTEGER → OpenSSL reads `CA:FALSE, pathlen:1`; `:875` raw `r‖s` instead of DER `ECDSA-Sig-Value`) — proven with `openssl x509`/`asn1parse` on certificates built through `POST /api/v1/certificates/generate`.
3. `encrypt_randomized`/`decrypt_randomized` pass `associated_data=None` (`column_encryption.py:167`), so a RANDOMIZED-column ciphertext replays across rows and columns of the same DEK with a valid tag — executed: a blob minted for `tax_amount` decrypts under `loan_amount` to `amount=999999`. Reached live through `secure_duckdb` ← `app/api/sandbox_db.py`.

**Notable corrections to the input set:** [0] and [21] are wrong (DER serial encoding is minimal and parses fine; serial uniqueness has 64 bits of entropy plus a UNIQUE constraint); [24] quotes code that is not in the file; [18]'s arithmetic is wrong (the bug only bites for first octets ≥ 120, none of which this module emits); the two "silent SM4→AES fallback" criticals ([10], [14]) are **disclosed** by an import-time `logger.warning`, gmssl is pinned in `requirements.txt`/`Dockerfile.api`, and `deterministic_sm4` has no live caller at all — real format-fork risk, wrong severity and wrong "silent" characterisation.
