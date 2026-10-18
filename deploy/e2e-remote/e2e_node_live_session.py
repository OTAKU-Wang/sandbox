#!/usr/bin/env python3
"""SS-14 RT-07 node-track: live-session isolation + egress deny + execution leg.

Runs against a stack that has at least one SCHEDULABLE Cube node (local
workstation bring-up). Remote (node-less) runs are refused at N1.
Steps:
  N1  node.schedulable        registry has >=1 schedulable node
  N2  session.create          l2c live session 201 (real MicroVM)
  N3  exec.basic              python print executes, result carries output
  N4  files.roundtrip         upload -> list -> download, bytes identical
  N5  egress.deny             outbound HTTP to blocked external host fails inside sandbox
  N6  isolation.file_escape   reading /etc/passwd inside sandbox is blocked
  N7  isolation.cross_party   second user cannot list/execute in first user's session (403/404)
  N8  proof_bundle            GET proof-bundle 200 with integrity fields (no secrets)
  N9  audit.session_actions   session audit trail carries exec/file actions
  N10 session.terminate       clean terminate, terminal state
"""
import argparse
import json
import base64
import secrets
import sys
import time

import httpx

EVIDENCE = []


def step(name, status, code=None, note="", excerpt=""):
    EVIDENCE.append({"step": name, "status": status, "status_code": code,
                     "note": note, "excerpt": excerpt,
                     "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    print(f"[{status:5s}] {name} ({note})")


def admin_login(base, user, password):
    s = httpx.post(f"{base}/api/v1/auth/login",
                   json={"username": user, "password": password}, timeout=30)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--admin-user", default="cdsadmin")
    ap.add_argument("--admin-pass", default=None)
    ap.add_argument("--out", default="e2e_node_live_session_evidence.json")
    ap.add_argument("--node-endpoint", default=None,
                    help="cubelet endpoint (host:port); if no schedulable node is registered, register one via POST /sandbox-nodes")
    ap.add_argument("--node-runtime-version", default=None,
                    help="cubelet runtime commit to declare at registration (must be the node's real commit; omitted = not declared)")
    ap.add_argument("--node-id", default="node-local-1")
    args = ap.parse_args()
    admin_pass = args.admin_pass or ""

    import os
    admin_pass = admin_pass or os.environ.get("E2E_ADMIN_PASS", "")
    base = args.base.rstrip("/")
    http = httpx.Client(base_url=base, timeout=120)

    s = admin_login(base, args.admin_user, admin_pass)
    if s.status_code != 200:
        step("admin.login", "FAIL", s.status_code, s.text[:200])
        return finish(args.out)
    try:
        token = s.json().get("access_token", "")
    except Exception:
        token = ""
    if not token:
        step("admin.login", "FAIL", 200, "200 login without access_token")
        return finish(args.out)
    ah = {"Authorization": f"Bearer {token}"}
    step("admin.login", "PASS", 200)

    # N1: schedulable node — envelope-tolerant: an error body / {"nodes": null}
    # must yield an empty list (fail toward registration), not an AttributeError
    # traceback with no evidence file.
    r = http.get("/api/v1/sandbox-nodes", headers=ah)
    nodes = r.json() if r.status_code == 200 else {}
    if isinstance(nodes, dict):
        nodes = nodes.get("nodes", nodes.get("items", []))
    nodes = [n for n in nodes if isinstance(n, dict)] if isinstance(nodes, list) else []
    schedulable = [n for n in nodes if n.get("schedulable")]
    if not schedulable and args.node_endpoint:
        caps = {"cubelet_endpoint": args.node_endpoint, "tee": "none"}
        if args.node_runtime_version:
            caps["runtime_version"] = args.node_runtime_version
        r = http.post("/api/v1/sandbox-nodes", headers=ah, json={
            "node_id": args.node_id,
            "hostname": "local-node",
            "ip_address": "127.0.0.1",
            "capabilities": caps,
        })
        if r.status_code in (200, 201):
            step("N0.node.register", "PASS", r.status_code,
                 f"probe_ok={r.json().get('probe_ok')}")
            r = http.get("/api/v1/sandbox-nodes", headers=ah)
            nodes = r.json() if r.status_code == 200 else {}
            nodes = nodes.get("nodes") if isinstance(nodes, dict) else nodes
            nodes = nodes if isinstance(nodes, list) else nodes.get("items", [])
            schedulable = [n for n in nodes if n.get("schedulable")]
        else:
            step("N0.node.register", "FAIL", r.status_code, r.text[:250])
            return finish(args.out)
    if not schedulable:
        step("N1.node.schedulable", "FAIL", r.status_code,
             f"no schedulable node ({len(nodes)} registered) — run node bring-up first")
        return finish(args.out)
    step("N1.node.schedulable", "PASS", 200, f"{len(schedulable)} schedulable node(s)")

    # Read-after-write: create endpoints commit in get_db teardown (after the
    # 2xx response), so the immediate follow-up can 404 in a sub-second window.
    # Bounded retry with explicit final failure — no silent fallback.
    def post_with_404_retry(url, **kw):
        for attempt in range(6):
            resp = http.post(url, **kw)
            if resp.status_code != 404:
                return resp
            time.sleep(0.5 * (attempt + 1))
        return resp

    # Product lifecycle (admin): resource -> product -> publish
    csv = "city,amount\nShanghai,100\nBeijing,200\n"
    r = http.post("/api/v1/data-resources/upload", headers=ah,
                  files={"file": ("node-live.csv", csv.encode(), "text/csv")},
                  data={"name": "node-live resource"})
    if r.status_code != 200:
        step("setup.product_published", "FAIL", r.status_code, r.text[:250])
        return finish(args.out)
    rid = r.json().get("id")
    r = http.post("/api/v1/data-products", headers=ah,
                  json={"name": f"node-live-{secrets.token_hex(3)}", "resource_id": rid})
    if r.status_code != 201:
        step("setup.product_published", "FAIL", r.status_code, r.text[:200])
        return finish(args.out)
    pid = r.json()["id"]
    for action in ("submit", "approve", "publish"):
        r = post_with_404_retry(f"/api/v1/data-products/{pid}/{action}", headers=ah)
        if r.status_code not in (200, 201):
            step(f"setup.{action}", "FAIL", r.status_code, r.text[:200])
            return finish(args.out)
    step("setup.product_published", "PASS", 201, pid)

    # N2: live session
    r = http.post("/api/v1/sandbox-sessions", headers=ah,
                  json={"data_product_id": pid, "level": "l2c"})
    if r.status_code != 201:
        step("N2.session.create", "FAIL", r.status_code, r.text[:300])
        return finish(args.out)
    sid = r.json()["id"]
    status = r.json().get("status")
    # session may take a moment to report running
    for _ in range(20):
        if status in ("running", "RUNNING"):
            break
        time.sleep(3)
        st = http.get(f"/api/v1/sandbox-sessions/{sid}", headers=ah)
        if st.status_code == 200:
            status = st.json().get("status")
    if status not in ("running", "RUNNING"):
        step("N2.session.create", "FAIL", 201, f"session not running (status={status})")
        return finish(args.out)
    step("N2.session.create", "PASS", 201, f"live l2c session {sid} running")

    # N3: basic exec（create 后的瞬态 404 重试一次 —— v32 实证过一次性竞态）
    r = http.post(f"/api/v1/sandbox-sessions/{sid}/execute", headers=ah,
                  json={"code": "print(6*7)", "language": "python"})
    if r.status_code == 404:
        time.sleep(3)
        r = http.post(f"/api/v1/sandbox-sessions/{sid}/execute", headers=ah,
                      json={"code": "print(6*7)", "language": "python"})
    body = r.json() if r.status_code == 200 else {}
    out = json.dumps(body, ensure_ascii=False)
    if r.status_code != 200 or "42" not in out:
        step("N3.exec.basic", "FAIL", r.status_code, "exec did not return 42", out[:300])
    else:
        step("N3.exec.basic", "PASS", 200, "print(6*7) -> 42")

    # N4: files roundtrip
    payload = os.urandom(512)
    r = http.post(f"/api/v1/sandbox-sessions/{sid}/files", headers=ah,
                  files={"file": ("rt.bin", payload, "application/octet-stream")})
    if r.status_code not in (200, 201):
        step("N4.files.roundtrip", "FAIL", r.status_code, "upload failed", r.text[:200])
    else:
        r2 = http.get(f"/api/v1/sandbox-sessions/{sid}/files/rt.bin", headers=ah)
        if r2.status_code == 200 and r2.content == payload:
            step("N4.files.roundtrip", "PASS", 200, "512B upload/download byte-identical")
        else:
            step("N4.files.roundtrip", "FAIL", r2.status_code, "download mismatch")

    # N5: egress deny — outbound HTTP to an external host must fail inside the sandbox.
    # 走 /exec 通道（/execute 的代码扫描器正确地拒绝 urllib 白名单外模块,
    # egress 面由 shell 通道探测）; 脚本经 base64 传输, 免 shell 引号/换行问题。
    n5_script = ("import urllib.request\n"
                 "try:\n"
                 "    urllib.request.urlopen('http://93.184.216.34/', timeout=5)\n"
                 "    print('EGRESS_OPEN')\n"
                 "except Exception as e:\n"
                 "    print('EGRESS_BLOCKED', type(e).__name__)\n")
    n5_b64 = base64.b64encode(n5_script.encode()).decode()
    r = http.post(f"/api/v1/sandbox-sessions/{sid}/exec", headers=ah,
                  json={"command": f"echo {n5_b64} | base64 -d | python3",
                      "timeout_seconds": 30})
    out = json.dumps(r.json() if r.status_code == 200 else r.text, ensure_ascii=False)
    if r.status_code == 200 and "EGRESS_BLOCKED" in out and "EGRESS_OPEN" not in out:
        step("N5.egress.deny", "PASS", 200, "outbound HTTP from sandbox refused")
    else:
        step("N5.egress.deny", "FAIL", r.status_code,
             "egress result unexpected (EGRESS_OPEN present or exec failed)", out[:300])

    # N6: 隔离 — 镜像自带 /etc/passwd 属容器正常内容（非宿主文件）;
    # 真正的隔离断言 = 宿主独有内容不可见: 宿主用户 kratos 不在 VM passwd,
    # 宿主 /home/kratos 不存在。
    n6_script = ("try:\n"
                 "    c = open('/etc/passwd').read()\n"
                 "    import os\n"
                 "    if 'kratos' in c or os.path.exists('/home/kratos'):\n"
                 "        print('HOST_VISIBLE')\n"
                 "    else:\n"
                 "        print('ESCAPE_BLOCKED')\n"
                 "except Exception as e:\n"
                 "    print('ESCAPE_BLOCKED', e)\n")
    n6_b64 = base64.b64encode(n6_script.encode()).decode()
    r = http.post(f"/api/v1/sandbox-sessions/{sid}/exec", headers=ah,
                  json={"command": f"echo {n6_b64} | base64 -d | python3",
                      "timeout_seconds": 30})
    out = json.dumps(r.json() if r.status_code == 200 else r.text, ensure_ascii=False)
    if r.status_code == 200 and "ESCAPE_BLOCKED" in out and "HOST_VISIBLE" not in out:
        step("N6.isolation.file_escape", "PASS", 200, "host-unique content not visible in sandbox")
    else:
        step("N6.isolation.file_escape", "FAIL", r.status_code,
             "host file visible from sandbox or exec failed", out[:300])

    # N7: cross-party isolation — a second user gets 403/404 on this session
    # （用户名契约: 仅字母数字下划线, UserCreate 验证器实证）。
    iso_tag = secrets.token_hex(3).replace("-", "")
    u2 = f"iso_{iso_tag}"
    r = http.post("/api/v1/auth/register", headers=ah,
                  json={"username": u2,
                        "email": f"{u2}@cds-e2e.cn",
                        "password": "IsoE2e!Pass1"})
    second = None
    if r.status_code in (200, 201):
        # register 返回 TokenResponse（无 username 字段）—— 用请求时的用户名。
        second = u2
        ls = admin_login(base, second, "IsoE2e!Pass1")
        if ls.status_code == 200:
            bh = {"Authorization": f"Bearer {ls.json()['access_token']}"}
            r1 = http.get(f"/api/v1/sandbox-sessions/{sid}/files", headers=bh)
            r2 = http.post(f"/api/v1/sandbox-sessions/{sid}/execute", headers=bh,
                           json={"code": "print(1)", "language": "python"})
            if r1.status_code in (403, 404) and r2.status_code in (403, 404):
                step("N7.isolation.cross_party", "PASS", None,
                     f"foreign session: files={r1.status_code} exec={r2.status_code}")
            else:
                step("N7.isolation.cross_party", "FAIL", None,
                     f"foreign access NOT denied: files={r1.status_code} exec={r2.status_code}")
        else:
            step("N7.isolation.cross_party", "FAIL", ls.status_code, "second user login failed")
    else:
        step("N7.isolation.cross_party", "FAIL", r.status_code,
             f"could not create second user: {r.text[:150]}")

    # N8: proof bundle
    r = http.get(f"/api/v1/sandbox-sessions/{sid}/proof-bundle", headers=ah)
    if r.status_code == 200:
        b = r.json()
        has_integrity = any(k in json.dumps(b) for k in ("integrity", "evidence_hash", "merkle"))
        no_secret = "key" not in {k.lower() for k in (b.keys() if isinstance(b, dict) else [])}
        if has_integrity and no_secret:
            step("N8.proof_bundle", "PASS", 200, "proof bundle with integrity, no raw keys")
        else:
            step("N8.proof_bundle", "FAIL", 200,
                 f"integrity={has_integrity} no_secret={no_secret}", json.dumps(b)[:250])
    else:
        step("N8.proof_bundle", "FAIL", r.status_code, r.text[:200])

    # N9: audit trail
    r = http.get(f"/api/v1/sandbox-sessions/{sid}/audit", headers=ah)
    acts = []
    if r.status_code == 200:
        data = r.json()
        items = data if isinstance(data, list) else data.get("items", data.get("events", []))
        acts = [a.get("action") or a.get("event_type", "") for a in items if isinstance(a, dict)]
    joined = " ".join(acts)
    if r.status_code == 200 and any("exec" in a or "execute" in a for a in acts):
        step("N9.audit.session_actions", "PASS", 200, f"{len(acts)} audit actions incl exec")
    else:
        step("N9.audit.session_actions", "FAIL", r.status_code, f"actions: {joined[:200]}")

    # N10: terminate
    r = http.post(f"/api/v1/sandbox-sessions/{sid}/terminate", headers=ah)
    ok = r.status_code in (200, 201) and "terminat" in json.dumps(r.json() if r.status_code == 200 else {}, ensure_ascii=False).lower()
    step("N10.session.terminate", "PASS" if ok else "FAIL", r.status_code,
         "session terminated" if ok else r.text[:200])

    return finish(args.out)


def finish(out):
    fails = [e for e in EVIDENCE if e["status"] == "FAIL"]
    report = {"finished": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "total": len(EVIDENCE), "passed": len(EVIDENCE) - len(fails),
              "failed": len(fails), "evidence": EVIDENCE}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n== {report['passed']}/{report['total']} passed, {report['failed']} failed -> {out}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
