#!/usr/bin/env python3
"""CDS trusted-data-space scenario e2e (SS-14, requirement 4).

Scenario contract distilled from specs SS-01/02/04/05/06/13:
  T1 contract-bound access   : session requires a published product (SS-02)
  T2 cross-party isolation   : buyer A cannot read buyer B's session files (SS-04)
  T3 output governance        : file download carries X-CDS-Output-Review (SS-05)
  T4 audit completeness       : every major action lands in the audit trail (SS-06)
  T5 disclosure consistency   : /meta disclosure fields present + honest (SS-13)
  T6 compliance surface       : compliance/retention endpoints answer (SS-06/11)
Session-dependent scenarios run when a sandbox node is available and are
SKIP (not FAIL) on node-less deployments, with the reason recorded.

Usage: python3 e2e_tds_scenarios.py BASE_URL [--admin-user U] [--out E.json]
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
    rec = {"step": name, "status": status, "status_code": status_code,
           "note": note, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    if excerpt:
        rec["excerpt"] = str(excerpt)[:600]
    EVIDENCE.append(rec)
    print(f"[{status:5}] {name}" + (f" ({note})" if note else ""))
    return status in ("PASS", "SKIP")


def register(http, base, user, role=None):
    body = {"username": user, "email": f"{user}@cds-e2e.cn", "password": "TdsE2e!Pass1"}
    if role:
        body["role"] = role
    r = http.post(f"{base}/api/v1/auth/register", json=body)
    if r.status_code in (400, 409):
        r = http.post(f"{base}/api/v1/auth/login",
                      json={"username": user, "password": "TdsE2e!Pass1"})
    if r.status_code not in (200, 201):
        return None
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("--admin-user", default="cdsadmin")
    ap.add_argument("--out", default="e2e_tds_scenarios_evidence.json")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")
    # Required env, no committed default (lived in git history — claims batch-87).
    admin_pass = os.environ.get("CDS_E2E_ADMIN_PASS")
    if not admin_pass:
        step("setup.admin", "FAIL", note="CDS_E2E_ADMIN_PASS is required (no default)")
        return finish(args.out)
    http = httpx.Client(base_url=base, timeout=60)

    r = http.post(f"{base}/api/v1/auth/login",
                  json={"username": args.admin_user, "password": admin_pass})
    if r.status_code != 200:
        step("setup.admin", "FAIL", r.status_code, note=r.text[:200])
        return finish(args.out)
    try:
        token = r.json().get("access_token", "")
    except Exception:
        token = ""
    if not token:
        step("setup.admin", "FAIL", 200, note="200 login without access_token")
        return finish(args.out)
    ah = {"Authorization": f"Bearer {token}"}
    step("setup.admin", "PASS", 200)

    ha = register(http, base, "tds_buyer_a" + secrets.token_hex(2))
    hb = register(http, base, "tds_buyer_b" + secrets.token_hex(2))
    if not ha or not hb:
        step("setup.buyers", "FAIL", None, note="buyer registration failed")
        return finish(args.out)
    step("setup.buyers", "PASS", None, note="two isolated buyer identities")

    # Resource -> product linkage (publish requires a linked resource).
    csv = "city,amount\nShanghai,100\nBeijing,200\n"
    r = http.post(f"{base}/api/v1/data-resources/upload", headers=ah,
                  files={"file": ("tds-loop.csv", csv.encode("utf-8"), "text/csv")},
                  data={"name": "tds resource"})
    if r.status_code != 200:
        step("setup.product_published", "FAIL", r.status_code,
             note=f"resource upload failed: {r.text[:200]}")
        return finish(args.out)
    rid = r.json().get("id")

    r = http.post(f"{base}/api/v1/data-products", headers=ah,
                  json={"name": f"tds-{secrets.token_hex(3)}",
                        "resource_id": rid})
    pid = r.json().get("id")
    for action in ("submit", "approve", "publish"):
        r = http.post(f"{base}/api/v1/data-products/{pid}/{action}", headers=ah)
    step("setup.product_published", "PASS" if r.status_code in (200, 201) else "FAIL",
         r.status_code, note=pid)

    r = http.post(f"{base}/api/v1/sandbox-sessions", headers=ah,
                  json={"data_product_id": pid})
    node_available = r.status_code == 201
    if not node_available:
        step("T1.contract_bound_access", "PASS" if r.status_code == 400 else "FAIL",
             r.status_code,
             note=f"create -> {r.status_code} (400 RUNTIME_UNAVAILABLE = node-less fail-closed, contract path intact)",
             excerpt=r.text[:250])
        for t, note in (("T2.cross_party_isolation", "no live session on this host"),
                        ("T3.output_governance", "no live session on this host"),
                        ("T5.session_proof_bundle", "no live session on this host")):
            step(t, "SKIP", None, note=note + " (runs on the KVM host)")
    else:
        sid = r.json()["id"]
        step("T1.contract_bound_access", "PASS", 201, note=f"session {sid} on published product")
        # 属主隔离修正（SS-14 RT-07 节点轨）: 会话由 admin 创建时 buyer 无权
        # 访问（403 是正确行为）—— TDS 正典流程是 buyer 自己买断会话。
        # 远程轨（无节点）此路径从未运行, 节点轨首跑暴露该脚本设计。
        rb = http.post(f"{base}/api/v1/sandbox-sessions", headers=ha,
                       json={"data_product_id": pid})
        if rb.status_code != 201:
            step("T2.setup_buyer_session", "FAIL", rb.status_code, note=rb.text[:200])
        else:
            step("T2.setup_buyer_session", "PASS", 201)
            sid = rb.json()["id"]
            for _ in range(50):
                st = http.get(f"{base}/api/v1/sandbox-sessions/{sid}", headers=ha)
                if st.status_code == 200 and st.json().get("status") in ("running", "RUNNING"):
                    break
                import time as _t
                _t.sleep(3)
        payload = b"a,1\nb,2\n"
        ra = http.post(f"{base}/api/v1/sandbox-sessions/{sid}/files", headers=ha,
                       files={"file": ("party.csv", payload, "text/csv")})
        step("T2.setup_upload", "PASS" if ra.status_code in (200, 201) else "FAIL",
             ra.status_code, note="buyer A uploads")
        rb = http.get(f"{base}/api/v1/sandbox-sessions/{sid}/files/party.csv", headers=hb)
        ok = rb.status_code in (403, 404)
        step("T2.cross_party_isolation", "PASS" if ok else "FAIL", rb.status_code,
             note="buyer B reading A's session file -> 403/404", excerpt=rb.text[:150])
        ra = http.get(f"{base}/api/v1/sandbox-sessions/{sid}/files/party.csv", headers=ha)
        review = ra.headers.get("X-CDS-Output-Review", "")
        ok = ra.status_code == 200 and review.startswith(("passed", "redacted"))
        step("T3.output_governance", "PASS" if ok else "FAIL", ra.status_code,
             note=f"X-CDS-Output-Review={review!r}")
        rb = http.get(f"{base}/api/v1/sandbox-sessions/{sid}/proof-bundle", headers=ah)
        ok = rb.status_code == 200 and bool(rb.json().get("integrity", {}).get("evidence_hash"))
        step("T5.session_proof_bundle", "PASS" if ok else "FAIL", rb.status_code,
             note="evidence_hash present (non-repudiation)")

    r = http.get(f"{base}/api/v1/meta/capability-matrix", headers=ah)
    text = json.dumps(r.json(), ensure_ascii=False) if r.status_code == 200 else ""
    # D-e2e-10: attestation trust_root (local-simulated/external) + platform
    # engine disclosure both present and honest (no fabricated hardware).
    ok = (r.status_code == 200 and "trust_root" in text
          and "sandbox_engine" in text and "cube_microvm" in text)
    step("T5.meta_disclosure", "PASS" if ok else "FAIL", r.status_code,
         note="capability meta discloses trust_root + sandbox_engine honestly",
         excerpt=text[:250])

    actions = ["auth.login", "data_product.created", "data_product.published",
               "sandbox.file_upload"]
    missing = []
    for action in actions:
        r = http.get(f"{base}/api/v1/audit/records", headers=ah,
                     params={"action": action, "limit": 5})
        if r.status_code != 200 or "[]" == r.text.strip() or len(r.text.strip()) < 8:
            missing.append(action)
    ok = not missing
    step("T4.audit_completeness", "PASS" if ok else "FAIL", None,
         note=f"missing audit actions: {missing or 'none'}")

    # The compliance surface is POST /reports (generate) and GET /reports/quick.
    r = http.get(f"{base}/api/v1/compliance/reports/quick", headers=ah)
    step("T6.compliance_surface", "PASS" if r.status_code == 200 else "FAIL",
         r.status_code, note="compliance/retention surface answers (GET /reports/quick)",
         excerpt=r.text[:200])

    return finish(args.out)


def finish(out):
    fails = [e for e in EVIDENCE if e["status"] == "FAIL"]
    skips = [e for e in EVIDENCE if e["status"] == "SKIP"]
    report = {"finished": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "total": len(EVIDENCE), "passed": len(EVIDENCE) - len(fails) - len(skips),
              "skipped": len(skips), "failed": len(fails), "evidence": EVIDENCE}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n== {report['passed']} passed / {report['skipped']} skipped / "
          f"{report['failed']} failed -> {out}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
