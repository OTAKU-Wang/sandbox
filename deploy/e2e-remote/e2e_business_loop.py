#!/usr/bin/env python3
"""CDS remote e2e business loop (SS-14, requirement 2).

Boots the platform from zero against a deployed instance: admin bootstrap,
SS-12 API key issuance, scope negatives, and the full data closed-loop
(create product -> submit/approve/publish -> session -> upload -> execute ->
gated download -> audit/usage/proof-bundle) driven by a platform API key.

Usage:
    python3 e2e_business_loop.py BASE_URL [--out EVIDENCE.json]

Writes a JSON evidence report (one record per step: status, status_code,
note, response excerpt) and exits non-zero on any FAIL.
"""
import argparse
import json
import os
import secrets
import sys
import time

import httpx

EVIDENCE = []


def step(name, status, status_code=None, note="", excerpt=""):
    rec = {
        "step": name,
        "status": status,
        "status_code": status_code,
        "note": note,
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    if excerpt:
        rec["excerpt"] = str(excerpt)[:600]
    EVIDENCE.append(rec)
    print(f"[{status:5}] {name}" + (f" ({note})" if note else ""))
    return status == "PASS"


def _as_items(payload):
    """Pure extractor for list-or-{items|nodes:[...]} envelopes.

    The old inline chain (`rows if isinstance(rows, list) else
    rows.get("items", [])`) raised AttributeError whenever the gateway
    returned an error envelope, None, or {"items": null} — i.e. exactly on the
    branches where FAIL evidence was needed.
    """
    if isinstance(payload, dict):
        payload = payload.get("items", payload.get("nodes", []))
    if not isinstance(payload, list):
        return []
    return [x for x in payload if isinstance(x, dict)]


def _access_token(resp):
    try:
        body = resp.json()
    except Exception:
        return ""
    return body.get("access_token", "") if isinstance(body, dict) else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("--out", default="e2e_business_loop_evidence.json")
    ap.add_argument("--admin-user", default="cdsadmin")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")
    # No committed default credential: a lane booted with the wrong password
    # must fail here, not silently probe a stack that was never ours.
    admin_pass = os.environ.get("CDS_E2E_ADMIN_PASS")
    if not admin_pass:
        step("admin.login", "FAIL", note="CDS_E2E_ADMIN_PASS is required (no default)")
        return finish(args.out)

    http = httpx.Client(base_url=base, timeout=120)

    s1 = http.get("/health")
    step("health", "PASS" if s1.status_code == 200 else "FAIL", s1.status_code,
         note=json.dumps(s1.json())[:200] if s1.status_code == 200 else s1.text[:200])
    if s1.status_code != 200:
        return finish(args.out)

    s2 = http.post(f"{base}/api/v1/auth/login",
                   json={"username": args.admin_user, "password": admin_pass})
    if s2.status_code == 200:
        token = _access_token(s2)
        if not token:
            step("admin.login", "FAIL", 200, note="200 login without access_token")
            return finish(args.out)
        step("admin.login", "PASS", 200, "existing admin")
    else:
        s2 = http.post(f"{base}/api/v1/auth/register",
                       json={"username": args.admin_user,
                             "email": f"{args.admin_user}@cds-e2e.cn",
                             "password": admin_pass, "role": "admin"})
        if s2.status_code not in (200, 201):
            step("admin.register", "FAIL", s2.status_code, note=s2.text[:300])
            return finish(args.out)
        token = _access_token(s2)
        if not token:
            step("admin.register", "FAIL", s2.status_code,
                 note=f"{s2.status_code} register without access_token")
            return finish(args.out)
        step("admin.register", "PASS", s2.status_code, "bootstrap admin (role=admin)")
    ah = {"Authorization": f"Bearer {token}"}

    s3 = http.post(f"{base}/api/v1/api-keys", headers=ah,
                   json={"name": "e2e-full", "description": "SS-14 e2e full preset",
                         "preset": "full", "expires_in_days": 0})
    if s3.status_code != 201:
        step("apikey.create.full", "FAIL", s3.status_code, note=s3.text[:300])
        return finish(args.out)
    full_key = s3.json().get("key", "")
    if not full_key:
        step("apikey.create.full", "FAIL", 201, note="201 without key material")
        return finish(args.out)
    step("apikey.create.full", "PASS", 201,
         note=f"prefix={s3.json().get('key_prefix')} len={len(full_key)}")

    s4 = http.post(f"{base}/api/v1/api-keys", headers=ah,
                   json={"name": "e2e-readonly", "preset": "readonly",
                         "expires_in_days": 30})
    # The readonly leg feeds the scope-negative probes: continuing with an
    # empty key turned a 401 into a fake "denied" PASS and poisoned three
    # unrelated steps — fail the leg at its source instead.
    ro_key = s4.json().get("key", "") if s4.status_code == 201 else ""
    if not ro_key:
        step("apikey.create.readonly", "FAIL", s4.status_code, note=s4.text[:200])
        return finish(args.out)
    step("apikey.create.readonly", "PASS", 201)

    kh = {"Authorization": f"Bearer {full_key}"}
    r = http.get(f"{base}/api/v1/meta/capability-matrix", headers=kh)
    step("apikey.auth.meta", "PASS" if r.status_code == 200 else "FAIL", r.status_code,
         note="API key subject authenticates on /meta")

    r = http.post(f"{base}/api/v1/data-products",
                  headers={"Authorization": f"Bearer {ro_key}"},
                  json={"name": "scope-probe"})
    ok = r.status_code == 403 and "API_KEY_SCOPE_DENIED" in r.text
    step("apikey.scope.write_denied", "PASS" if ok else "FAIL", r.status_code,
         note=f"readonly key on write endpoint -> {r.status_code}", excerpt=r.text[:200])

    r = http.get(f"{base}/api/v1/api-keys", headers=kh)
    ok = r.status_code == 403 and "API_KEY_SCOPE_DENIED" in r.text
    step("apikey.scope.human_only_denied", "PASS" if ok else "FAIL", r.status_code,
         note="full key on human-only /api-keys -> 403", excerpt=r.text[:200])

    r = http.get(f"{base}/api/v1/users/options", headers=kh)
    ok = r.status_code == 403 and "API_KEY_SCOPE_DENIED" in r.text
    step("apikey.scope.users_denied", "PASS" if ok else "FAIL", r.status_code,
         note="full key on human-only /users -> 403", excerpt=r.text[:200])

    # Resource -> product linkage (SS-14 requirement: 资源→产品→契约→会话).
    # Upload with the machine key: exercises require_roles on the ISSUER's
    # role (D-e2e-09) — DATA_PROVIDER/ADMIN gate must accept delegated keys.
    csv = "city,amount\nShanghai,100\nBeijing,200\n"
    r = http.post(f"{base}/api/v1/data-resources/upload", headers=kh,
                  files={"file": ("e2e-loop.csv", csv.encode("utf-8"), "text/csv")},
                  data={"name": "e2e-loop resource"})
    if r.status_code != 200:
        step("loop.resource.upload", "FAIL", r.status_code, note=r.text[:300])
        return finish(args.out)
    resource_id = r.json()["id"]
    step("loop.resource.upload", "PASS", 200, note=resource_id)

    r = http.post(f"{base}/api/v1/data-products", headers=kh,
                  json={"name": f"e2e-loop-{secrets.token_hex(3)}", "resource_id": resource_id})
    if r.status_code != 201:
        step("loop.product.create", "FAIL", r.status_code, note=r.text[:300])
        return finish(args.out)
    product_id = r.json()["id"]
    step("loop.product.create", "PASS", 201, note=product_id)

    r = http.post(f"{base}/api/v1/data-products/{product_id}/submit", headers=kh)
    step("loop.product.submit", "PASS" if r.status_code in (200, 201) else "FAIL",
         r.status_code, excerpt=r.text[:200])
    r = http.post(f"{base}/api/v1/data-products/{product_id}/approve", headers=ah)
    step("loop.product.approve", "PASS" if r.status_code in (200, 201) else "FAIL",
         r.status_code, note="human admin approves", excerpt=r.text[:200])
    r = http.post(f"{base}/api/v1/data-products/{product_id}/publish", headers=ah)
    step("loop.product.publish", "PASS" if r.status_code in (200, 201) else "FAIL",
         r.status_code, excerpt=r.text[:200])

    # SS-13 single path: creation accepts l2c only (retired levels 400).
    # Node-less remote: honest fail-closed 400 RUNTIME_UNAVAILABLE is PASS.
    r = http.post(f"{base}/api/v1/sandbox-sessions", headers=kh,
                  json={"data_product_id": product_id, "sandbox_level": "l2c"})
    session_id = None
    session_live = False
    if r.status_code == 201:
        body = r.json()
        sess = body if isinstance(body, dict) else {}
        sid = sess.get("id") or ""
        status0 = sess.get("status")
        session_id = sid or None
        step("loop.session.create", "PASS" if sid else "FAIL", 201,
             note=f"level=l2c status={status0}" + ("" if sid else " — 201 WITHOUT session id"))
        session_live = bool(sid) and status0 in ("ready", "running")
        if session_live:
            payload = b"name,Alice\nname,Bob\nname,Carol\n"
            r = http.post(f"{base}/api/v1/sandbox-sessions/{sid}/files",
                          headers=kh,
                          files={"file": ("loop.csv", payload, "text/csv")})
            step("loop.file.upload", "PASS" if r.status_code in (200, 201) else "FAIL",
                 r.status_code, excerpt=r.text[:200])

            r = http.post(
                f"{base}/api/v1/sandbox-sessions/{sid}/execute", headers=kh,
                json={"language": "python", "code": "print('ss14-hello')"})
            body = r.text[:300]
            ok = r.status_code == 200 and "ss14-hello" in r.text
            step("loop.execute", "PASS" if ok else "FAIL", r.status_code,
                 note="python print via sandbox", excerpt=body)

            r = http.get(f"{base}/api/v1/sandbox-sessions/{sid}/files/loop.csv",
                         headers=kh)
            review = r.headers.get("X-CDS-Output-Review", "")
            ok = r.status_code == 200 and review.startswith(("passed", "redacted"))
            step("loop.file.download.gated", "PASS" if ok else "FAIL", r.status_code,
                 note=f"X-CDS-Output-Review={review!r}", excerpt=r.text[:150])

            r = http.get(f"{base}/api/v1/sandbox-sessions/{sid}/usage", headers=kh)
            ok = r.status_code == 200
            step("loop.usage", "PASS" if ok else "FAIL", r.status_code,
                 excerpt=r.text[:200])

            r = http.get(f"{base}/api/v1/sandbox-sessions/{sid}/proof-bundle",
                         headers=kh)
            meta_ok = False
            ev_ok = False
            if r.status_code == 200:
                b = r.json()
                ev_ok = bool(b.get("integrity", {}).get("evidence_hash"))
                bm = b.get("bundle_meta", {})
                meta_ok = all(k in bm for k in
                              ("signing_backend", "aead_algorithm", "trust_root",
                               "chain_anchoring"))
            step("loop.proof_bundle", "PASS" if (ev_ok and meta_ok) else "FAIL",
                 r.status_code,
                 note=f"evidence_hash={ev_ok} bundle_meta={meta_ok}")
    else:
        # Node-less remote (no KVM/nested virt): SS-13 fail-closed contract —
        # session creation MUST return 400 RUNTIME_UNAVAILABLE, not a fake
        # session. That 400 IS the correct behavior here; the live execution
        # leg (upload/execute/download/proof) is exercised on the local node
        # track (RT-07). Anything other than the honest 400 is a FAIL.
        honest_400 = (r.status_code == 400
                      and "RUNTIME_UNAVAILABLE" in r.text)
        step("loop.session.create", "PASS" if honest_400 else "FAIL",
             r.status_code,
             note=("node-less remote: honest fail-closed 400 "
                   "(execution leg runs on local node track)")
                 if honest_400
             else f"expected 201 or honest 400, got {r.status_code}",
             excerpt=r.text[:300])

    # Terminate is NOT gated on session_live: a session created but stuck in
    # provisioning used to leak (no terminate call, no evidence step) while the
    # harness still printed its green summary.
    if session_id:
        r = http.post(f"{base}/api/v1/sandbox-sessions/{session_id}/terminate",
                      headers=kh)
        step("loop.session.terminate",
             "PASS" if r.status_code in (200, 202) else "FAIL", r.status_code,
             excerpt=r.text[:150])

    r = http.get(f"{base}/api/v1/audit/records", headers=ah,
                 params={"action": "api_key.created", "limit": 10})
    ok = r.status_code == 200 and "e2e-full" in r.text
    step("audit.api_key_created", "PASS" if ok else "FAIL", r.status_code,
         note="admin audit trail carries api_key.created")

    r = http.get(f"{base}/api/v1/api-keys", headers=ah)
    rows = _as_items(r.json() if r.status_code == 200 else None)
    field_leak = any(row.get("key") for row in rows)
    ok = r.status_code == 200 and not field_leak and full_key not in r.text
    step("apikey.list.no_bare_key", "PASS" if ok else "FAIL", r.status_code,
         note="list exposes key_prefix only (no bare key, no key field)")

    # Credential hygiene: both keys this run issued (the full preset key is
    # non-expiring) used to survive the lane forever — a finished e2e must not
    # leave live full-scope credentials on the target.
    by_name = {row.get("name"): row.get("id", "") for row in rows}
    cleanup = []
    for label, key_name in (("full", "e2e-full"), ("readonly", "e2e-readonly")):
        kid = by_name.get(key_name, "")
        if not kid:
            cleanup.append(f"{label}:no-id")
            continue
        d = http.patch(f"{base}/api/v1/api-keys/{kid}", headers=ah,
                       json={"status": "disabled"})
        cleanup.append(f"{label}:{d.status_code}")
    ok = all(c.endswith(":200") for c in cleanup) and len(cleanup) == 2
    step("apikey.cleanup.disabled", "PASS" if ok else "FAIL", None,
         note=" ".join(cleanup))

    return finish(args.out)


def finish(out):
    fails = [e for e in EVIDENCE if e["status"] == "FAIL"]
    report = {
        "started": EVIDENCE[0]["time"] if EVIDENCE else "",
        "finished": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "total": len(EVIDENCE),
        "passed": len(EVIDENCE) - len(fails),
        "failed": len(fails),
        "evidence": EVIDENCE,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\n== {report['passed']}/{report['total']} passed, "
          f"{report['failed']} failed -> {out}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
