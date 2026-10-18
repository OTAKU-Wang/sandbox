#!/usr/bin/env python3
"""CDS sandbox security e2e (SS-14, requirement 3) — REMOTE posture.

With CDS_CUBE_DATAPLANE_ENABLED=false and no cube node, the platform must
fail CLOSED and disclose honestly (SS-13 single-path contract):
  - l2c create -> 400 RUNTIME_UNAVAILABLE (no bwrap/process fallback, ever)
  - legacy levels (L0/L1/L2) -> 400 (retired levels, explicit message)
  - capability meta -> trust_root=none + sandbox_engine=cube_microvm
    (honest degradation, no fake TEE evidence)
  - node registry -> no schedulable nodes

Usage: python3 e2e_sandbox_security.py BASE_URL [--admin-user U] [--out E.json]
Exits non-zero on any FAIL. Evidence JSON written alongside.
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
    return status == "PASS"


def _as_items(payload):
    """Pure extractor for every envelope shape the gateway may answer with.

    The old inline chain (`nodes.get("nodes") ... nodes.get("items", [])`)
    raised AttributeError on {}, {"nodes": null}, string items etc. — i.e.
    exactly on the error branches where FAIL evidence must be recorded, and
    the harness died with a traceback instead of writing the report.
    """
    if isinstance(payload, dict):
        payload = payload.get("nodes", payload.get("items", []))
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
    ap.add_argument("--admin-user", default="cdsadmin")
    ap.add_argument("--out", default="e2e_sandbox_security_evidence.json")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")
    # Required env, no committed default (the literal default password lived
    # in git history — claims batch-87).
    admin_pass = os.environ.get("CDS_E2E_ADMIN_PASS")
    if not admin_pass:
        step("admin.login", "FAIL", note="CDS_E2E_ADMIN_PASS is required (no default)")
        return finish(args.out)
    http = httpx.Client(base_url=base, timeout=60)

    s = http.post(f"{base}/api/v1/auth/login",
                  json={"username": args.admin_user, "password": admin_pass})
    if s.status_code != 200:
        step("admin.login", "FAIL", s.status_code, note=s.text[:200])
        return finish(args.out)
    token = _access_token(s)
    if not token:
        step("admin.login", "FAIL", 200, note="200 login without access_token")
        return finish(args.out)
    ah = {"Authorization": f"Bearer {token}"}
    step("admin.login", "PASS", 200)

    # Probe product must be PUBLISHED for the l2c node gate to be the first
    # blocking check (product-publication precedes the runtime gate). Strict:
    # every leg's status is checked — a silent 400/500 here would mask the
    # real fail-closed signal (Round-C false-positive lesson).
    csv = "city,amount\nShanghai,100\nBeijing,200\n"
    r = http.post(f"{base}/api/v1/data-resources/upload", headers=ah,
                  files={"file": ("sec-probe.csv", csv.encode("utf-8"), "text/csv")},
                  data={"name": "sec-probe resource"})
    if r.status_code != 200:
        step("probe.resource_upload", "FAIL", r.status_code, note=r.text[:250])
        return finish(args.out)
    step("probe.resource_upload", "PASS", 200, note=r.json().get("id", ""))

    r = http.post(f"{base}/api/v1/data-products", headers=ah,
                  json={"name": f"sec-probe-{secrets.token_hex(3)}",
                        "resource_id": r.json()["id"]})
    if r.status_code != 201:
        step("probe.product", "FAIL", r.status_code, note=r.text[:200])
        return finish(args.out)
    product_id = r.json()["id"]
    for action in ("submit", "approve", "publish"):
        r = http.post(f"{base}/api/v1/data-products/{product_id}/{action}", headers=ah)
        if r.status_code not in (200, 201):
            step(f"probe.{action}", "FAIL", r.status_code, note=r.text[:200])
            return finish(args.out)
    step("probe.product", "PASS", 201, note=f"published probe product {product_id}")

    r = http.post(f"{base}/api/v1/sandbox-sessions", headers=ah,
                  json={"data_product_id": product_id})
    ok = r.status_code == 400 and "RUNTIME_UNAVAILABLE" in r.text
    step("failclosed.l2c_no_node", "PASS" if ok else "FAIL", r.status_code,
         note="l2c create with dataplane off -> 400 RUNTIME_UNAVAILABLE (no fallback)",
         excerpt=r.text[:300])

    for lvl in ("L0", "L1", "L2"):
        r = http.post(f"{base}/api/v1/sandbox-sessions", headers=ah,
                      json={"data_product_id": product_id, "sandbox_level": lvl})
        # A bare 400 is not the contract: the W2 body must say WHY (SS-13
        # "legacy_level_retired"), or any unrelated validation 400 passes.
        ok = r.status_code == 400 and "retired" in r.text.lower()
        step(f"failclosed.legacy_{lvl.lower()}", "PASS" if ok else "FAIL", r.status_code,
             note="retired level -> 400 with legacy_level_retired reason",
             excerpt=r.text[:200])

    # D-e2e-10: meta path is /capability-matrix (bare /api/v1/meta is 404).
    # Three-surface TEE/engineness consistency (remote, node-less):
    #   1) meta sandbox_engine = cube_microvm (platform engine disclosure)
    #   2) meta trust_root = local-simulated|external (attestation root,
    #      honestly simulated — never a fabricated hardware claim)
    #   3) session-level trust_root default = "none" without TEE hardware
    #      (code-verified: sandbox_sessions.py:365; live surface = node
    #      registry empty + fail-closed create, both checked below)
    r = http.get(f"{base}/api/v1/meta/capability-matrix", headers=ah)
    meta = r.json() if r.status_code == 200 else {}
    engine_ok = meta.get("sandbox_engine") == "cube_microvm"
    trust_ok = meta.get("trust_root") in ("local-simulated", "external")
    step("disclosure.meta_engine", "PASS" if engine_ok else "FAIL", r.status_code,
         note="capability matrix carries sandbox_engine=cube_microvm",
         excerpt=json.dumps(meta, ensure_ascii=False)[:300])
    step("disclosure.meta_trust_root", "PASS" if trust_ok else "FAIL", r.status_code,
         note="attestation trust_root disclosed honestly (local-simulated, no fake hardware)",
         excerpt=json.dumps(meta, ensure_ascii=False)[:300])

    r = http.get(f"{base}/api/v1/sandbox-nodes", headers=ah)
    ok = r.status_code == 200
    nodes = _as_items(r.json() if ok else None)
    schedulable = [n for n in nodes if n.get("status") in ("ready", "active", "available")]
    step("nodes.registry", "PASS" if (ok and not schedulable) else "FAIL", r.status_code,
         note=f"registry lists {len(nodes)} nodes, {len(schedulable)} schedulable (expect 0 on remote)",
         excerpt=json.dumps(nodes)[:200])

    return finish(args.out)


def finish(out):
    fails = [e for e in EVIDENCE if e["status"] == "FAIL"]
    report = {"finished": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "total": len(EVIDENCE), "passed": len(EVIDENCE) - len(fails),
              "failed": len(fails), "evidence": EVIDENCE}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n== {report['passed']}/{report['total']} passed, "
          f"{report['failed']} failed -> {out}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
