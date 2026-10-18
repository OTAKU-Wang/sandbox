### app/services/mutual_tls.py

Reachability baseline (grep over app/ sdk/ cds-volume-plugin/ tests/): the only production import of this module is `app/services/federation_connector.py:671,681` → `load_federation_mtls_material()`. `mtls_manager` (`mutual_tls.py:357`) has **zero** references outside its own definition, and `SM2CertificateAuthority` / `MTLSManager` / `MTLSIdentity` / `CertificateInfo` are imported only by `tests/test_mtls.py:5-10`. Live chain for the loader: `app/main.py:344` (`include_router(federation.router, prefix="/api/v1/federation", Depends(require_group("federation")))`) → `app/api/federation.py:287` `POST /request` → `federation_connector.send_request` (`app/api/federation.py:297`) → `_execute_remote_request` (`federation_connector.py:618` → def at `:659`) → `load_federation_mtls_material()` (`federation_connector.py:681`). Gate: `app/core/config.py:212` `FEDERATION_ENABLED: bool = True`; prod fail-closed at `app/core/config.py:498-505` (`if self.FEDERATION_ENABLED and not (self.FEDERATION_CA_BUNDLE and self.FEDERATION_CERT and self.FEDERATION_KEY): raise`), mirrored at `federation_connector.py:682-689`. The SM2/CA/manager half of the module is unwired (repo's recurring "impressive service module" pattern) — hence LATENT, not production defect.

**C1 — `_cert_to_pem` emits fake PEM and `get_tls_context` names the fields `cert_pem`/`key_der` as if usable TLS material**
- verdict: CLOSED-BY-DOC ; reachability: LATENT (tests-only — `get_tls_context` called only from `tests/test_mtls.py:204,232,207`)
- code: `app/services/mutual_tls.py:288-297` — `lines = [` / `"-----BEGIN CERTIFICATE-----",` / `f"PublicKey: {cert.public_key[:64]}...",` / `f"Signature: {cert.signature[:64]}...",` / `"-----END CERTIFICATE-----",` / `]` (consecutive lines; only key-value text between the headers); and `:277-280` — `"cert_pem": self._cert_to_pem(identity.certificate),` / `"key_der": identity.private_key,` / `"ca_cert_pem": self._cert_to_pem(identity.ca_certificate) if identity.ca_certificate else None,` / `"verify": True,`
- impact: technically accurate (gate venv: `ssl.load_verify_locations(cadata=cert_pem)` → `SSLError: no start line: cadata does not contain a certificate`; `key_der` is the 64-char hex SM2 private key, not DER) but no caller exists — the behaviour is self-disclosed (`:284` "PEM-like string representation"), tracked as an open ledger item (docs/mock-remediation-plan.md:202-214 M-08/MR-A8 step 2 "改为真实 ASN.1 证书签发"), pinned by `tests/test_mtls.py:229-238` (`assert "Subject:" in pem`), and superseded for real TLS by `load_federation_mtls_material` (comment `mutual_tls.py:301-304`).
- fix: rename the keys to `cert_text`/`key_hex` (or drop `key_der`) so no future caller mistakes them for handshake material; real DER/PEM stays under MR-A8.

**C2 — signed payload is built by unescaped `|`/`=` concatenation in three places, so different field sets can share one byte string**
- verdict: LATENT ; reachability: LATENT (tests-only — `issue_certificate`/`verify_certificate` reached only through `MTLSManager`, which no app code imports)
- code: `app/services/mutual_tls.py:100` / `:140` / `:189` — `payload_str = "|".join(f"{k}={v}" for k, v in sorted(cert_data.items()))`
- impact: collision reproduced at runtime in the py3.12 gate venv (`probe_d.py`): a cert issued for `space_id="victim-space"` and a mutated copy with `serial_number="<serial>|space_id=victim-space"`, `space_id=""` render byte-identical payloads and **both** verify `True` — the signature no longer pins one field set. Needs a CA-issued cert plus the manager to be wired; today nothing consumes it.
- fix: one shared `def _payload(d): return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()` used by all three call sites.

**C3 — `verify_certificate` checks only signature+time window (no issuer==CA subject, no is_ca/basicConstraints, no space binding, no path building) and falls back to minting a CA when `ca_cert` is omitted**
- verdict: PARTLY ; reachability: LATENT (tests-only)
- code: `app/services/mutual_tls.py:168` — `ca = ca_cert or self.generate_ca_certificate()`; `:192-196` — `return self._crypto.verify(` / `payload_str.encode(),` / `cert.signature,` / `ca.public_key,` / `)` — the reconstructed dict at `:177-187` carries `issuer`/`is_ca` but nothing compares them to the CA, and no chain/space check follows.
- impact: the missing-binding half is true (gate venv: a cert verifies `True` against a CA cert whose subject is `"Custom CA"`, and `verify_certificate(cert)` with no CA returns `True`). The alarming half is not: only the CA private key can sign, so a re-typed `issuer` (`issuer="Totally Unrelated CA"`) verifies `False`, and a foreign CA returns `False`; the `or` fallback re-uses `self._ca_keypair` (the same key that issued), so it is silent misconfiguration, not a trust bypass, and `verify_peer_certificate` (`:260-263`) does fail closed. Layer is unwired anyway.
- fix: `if ca_cert is None: logger.warning(...); return False`, plus `if cert.issuer != ca.subject or not ca.is_ca: return False`.

**C4 — `verify_peer_certificate(cert)` takes no expected space, so any space's CA-signed cert authenticates as any other space**
- verdict: LATENT ; reachability: LATENT (tests-only — `tests/test_mtls.py:182,187,192,202,226,227`)
- code: `app/services/mutual_tls.py:258-263` — `def verify_peer_certificate(self, cert: CertificateInfo) -> bool:` … `return self._ca.verify_certificate(cert, self._ca_cert)`
- impact: accurate in code — `cert.space_id` is never compared to anything, and `tests/test_mtls.py:226-227` exercises exactly that (both `space-a` and `space-b` certs verify against the same manager). No peer-authentication call site exists; the live federation channel authenticates with real PEM mTLS + bearer token (`federation_connector.py:579` `token = self._idp.create_federated_token(...)`, `:681,708-710`), not with these objects.
- fix: `def verify_peer_certificate(self, cert, expected_space_id: str)` → after the signature check `return cert.space_id == expected_space_id` (log on mismatch).

**C5 — `load_federation_mtls_material` only proves cert/key load; it never checks the client cert against the CA bundle, and catches only `ssl.SSLError`**
- verdict: PARTLY ; reachability: LIVE (`POST /api/v1/federation/request` → `send_request` → `_execute_remote_request` → `load_federation_mtls_material`)
- code: `app/services/mutual_tls.py:347-351` — `ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)` / `try:` / `ctx.load_cert_chain(certfile=cert, keyfile=key)` / `except ssl.SSLError as e:` / `raise ValueError(f"Federation mTLS cert/key pair is invalid: {e}")`
- impact: reproduced — an unrelated self-signed CA bundle plus an **already-expired** client cert/key pair passes the loader (returns the dict). It is not a security hole: the failure is only deferred to `httpx.Client(verify=ca_bundle, cert=(cert,key))` (`federation_connector.py:708-710`), which rejects the handshake and yields 502/500 instead of a config-time error, so the channel degrades honestly rather than accepting an untrusted peer. The narrower docstring at `mutual_tls.py:310-312` promises exactly "validates that the files exist, are PEM, and the cert/key pair / loads".
- fix: `except (ssl.SSLLError, OSError, ValueError) as e: raise ValueError(...) from e`, and parse the client cert + bundle with `cryptography` to assert `not_valid_after` and issuer/AKI-SKI match.

**C6 — issue/verify disagree on `space_id` (issue always includes it, verify includes it only when truthy), and remote-supplied datetimes/signatures raise instead of returning False**
- verdict: LATENT ; reachability: LATENT (tests-only)
- code: `app/services/mutual_tls.py:186-187` — `if cert.space_id:` / `cert_data["space_id"] = cert.space_id` vs `:136` — `"space_id": space_id,`; `:172` — `if now < cert.not_before or now > cert.not_after:`; `:192-196` — `return self._crypto.verify(` / `payload_str.encode(),` / `cert.signature,` / `ca.public_key,` / `)` (no exception guard around it)
- impact: all three reproduced in the gate venv (`probe_a.py`): `issue_certificate("")` → `verify_certificate(...) is False` (round-trip break); naive `not_before` → `TypeError: can't compare offset-naive and offset-aware datetimes`; `signature="not-hex"` → `ValueError: invalid literal for int() with base 16`. Would be a 500 on a federation endpoint if this layer were wired; it is not. `tests/test_mtls.py:76-77` even names the ValueError (`# Tamper with signature — invalid hex causes ValueError…`) but dodges it with `cert.signature = "ff" * 64` instead of pinning it.
- fix: share one payload builder that always emits `space_id`, and wrap the whole verify body in `except (TypeError, ValueError) as e: logger.warning(...); return False`.

**C7 — `issuer` is hardcoded to `"CDS Federation CA"` while `generate_ca_certificate` accepts any subject**
- verdict: LATENT ; reachability: LATENT (tests-only)
- code: `app/services/mutual_tls.py:131` — `"issuer": "CDS Federation CA",` and `:149` — `issuer="CDS Federation CA",` (vs `:80` — `def generate_ca_certificate(self, subject: str = "CDS Federation CA")`)
- impact: reproduced — a CA created with `subject="Regional Health Space CA"` issues a leaf whose `issuer == "CDS Federation CA"`. Zero current bite: nothing compares issuer to CA subject (C3), no production caller, and no code path passes a custom subject (`initialize_ca` at `:217-221` uses the default). Purely a trap for whoever adds the issuer check.
- fix: in `issue_certificate`, take `issuer = (self._ca_cert.subject if self._ca_cert else "CDS Federation CA")` via a `_DEFAULT_CA_SUBJECT` class constant.

**C8 — `_read_pem` lets `OSError`/`UnicodeDecodeError` escape instead of `ValueError`, and its PEM check is a bare substring test**
- verdict: PARTLY ; reachability: LIVE (same chain as C5)
- code: `app/services/mutual_tls.py:333-339` — `p = Path(path)` / `if not p.is_file():` / `raise ValueError(f"Federation {label} not found: {path}")` / `text = p.read_text()` / `if must_contain not in text:`
- impact: the `OSError` half holds — with `Path.read_text` raising `PermissionError`, `load_federation_mtls_material()` propagates `PermissionError` (`isinstance(e, ValueError)` → False) up through `federation_connector.py:681` → `app/api/federation.py:287` as an unhandled 500 instead of the "always ValueError" contract of `:312`. Two sub-claims are wrong: `UnicodeDecodeError` **is** a `ValueError` subclass (measured: `isinstance(..., ValueError)` → True, so already conformant), and no startup consumer exists — `validate_security_config` (`app/core/config.py:498-505`) only checks the three settings are non-empty and never calls this function (only importer is `federation_connector.py:671`). The weak substring check is real (`tests/test_pz05_federation_attestation.py:55-56` feeds `-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----`) but the pair check at `:349` catches bogus cert/key; only the CA bundle is never parsed.
- fix: `try: text = p.read_text(encoding="utf-8")` `except OSError as e: raise ValueError(f"Federation {label} unreadable: {path}") from e`.

**C9 — module-level singleton mutates `_ca_cert`/`_identities` with check-then-act and no lock**
- verdict: LATENT ; reachability: LATENT (tests-only — `mtls_manager` is referenced nowhere but its own definition at `mutual_tls.py:357`; `tests/test_mtls.py` builds a fresh `MTLSManager()` per test, lines 141-231)
- code: `app/services/mutual_tls.py:219-220` — `if self._ca_cert is None:` / `self._ca_cert = self._ca.generate_ca_certificate()` and `:232-233` — `if self._ca_cert is None:` / `self.initialize_ca()`
- impact: the race is real as written but unreachable — no router, worker or lifespan hook imports the singleton, so no threadpool ever calls it; the identity layer is unwired (M-08/MR-A8 still plans to replace it). It becomes a live defect the day someone wires `mtls_manager`.
- fix: `_lock = threading.Lock()` and guard `initialize_ca`/`create_identity` (`with self._lock:` re-checking `_ca_cert is None` inside).

**C10 — `create_identity` silently overwrites an existing identity (no rotation flag, no revocation) and reaches into `_ca._crypto`**
- verdict: LATENT ; reachability: LATENT (tests-only)
- code: `app/services/mutual_tls.py:250-251` — `self._identities[space_id] = identity` / `logger.info(f"[mTLS] Created identity for space {space_id}")`; `:236` — `keypair = self._ca._crypto.generate_keypair()`
- impact: accurate, but the module has no persistence or revocation at all (identity material lives in a process dict, nothing reads it in production), so "对端认证突然失败" has no surface today. `tests/test_mtls.py` never re-creates the same `space_id`, so the overwrite is unpinned either way. The `_crypto` privacy break is style.
- fix: `if space_id in self._identities and not rotate: raise ValueError(f"identity for {space_id} exists")`; expose `SM2CertificateAuthority.generate_keypair()` and call that.

**C11 — `hashlib`, `time` and `dataclasses.field` are imported but never used**
- verdict: TRUE ; reachability: LIVE (module-level import executed by `federation_connector.py:671` and both test modules)
- code: `app/services/mutual_tls.py:19` — `import hashlib`; `:21` — `import time`; `:23` — `from dataclasses import dataclass, field`
- impact: grep over the whole file returns exactly one hit each (`:19`, `:21`, `:23`) — no `hashlib.`, no `time.`, no `field(`. Cosmetic: no runtime harm, only a misleading reader (and lint noise).
- fix: `import hashlib` / `import time` deleted and `from dataclasses import dataclass`.
