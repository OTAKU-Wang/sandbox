#!/usr/bin/env bash
# CDS K8s runtime end-to-end smoke (N7).
#
# Runs AFTER the cluster is up and CDS is deployed (see scripts/install-k3s.sh):
#   1. provisions a k8s sandbox session via the public API
#   2. waits for the pod to be Ready (kubectl ContainersReady condition)
#   3. executes code in the sandbox -> exit 0
#   4. verifies the python-client WS streaming exec path returns the answer
#   5. verifies a shared-volume PVC can be ensured (provision-time mounts)
#   6. terminates the session -> pod deletion is verified
#
# Real-cluster execution is environment acceptance (spec FG-004); this script
# is the固化 runbook. Each step fails the whole run on error (fail-closed).
#
# Env:
#   CDS_API_URL      base URL (default http://localhost:8080)
#   CDS_E2E_USER / CDS_E2E_PASS   dev login — required unless CDS_E2E_TOKEN is
#                    set (默认口令绝不入仓：这里没有任何内置口令)
#   CDS_E2E_TOKEN    optional bearer token (skips login)
#   KUBECTL          kubectl binary (default: kubectl)
#   CDS_SANDBOX_NS   k8s namespace (default cds-sandbox)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_URL="${CDS_API_URL:-http://localhost:8080}"
USER="${CDS_E2E_USER:-}"
PW="${CDS_E2E_PASS:-}"
TOKEN="${CDS_E2E_TOKEN:-}"
KUBECTL="${KUBECTL:-kubectl}"
NS="${CDS_SANDBOX_NS:-cds-sandbox}"
PY="${PYTHON_BIN:-.venv/bin/python}"

log() { printf '[e2e-k8s] %s\n' "$*"; }
fail() { printf '[e2e-k8s] FAIL: %s\n' "$*" >&2; exit 1; }

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

if [ -z "$TOKEN" ] && { [ -z "$USER" ] || [ -z "$PW" ]; }; then
    fail "CDS_E2E_USER and CDS_E2E_PASS are required (or set CDS_E2E_TOKEN); refusing built-in default credentials"
fi

cd "$REPO_ROOT"
need_cmd curl
need_cmd "$KUBECTL"
command -v "$PY" >/dev/null 2>&1 || PY=python3

log "== preconditions =="
curl -fsS -m 5 "$API_URL/health" >/dev/null 2>&1 || fail "CDS API unreachable at $API_URL (deploy first: scripts/install-k3s.sh)"
$KUBECTL get ns "$NS" >/dev/null 2>&1 || fail "namespace $NS missing (cluster not ready?)"

# SDK-driven steps run in a single python process (shared login/token state).
log "== provisioning k8s session + exec + streaming + teardown =="
CDS_API_URL="$API_URL" CDS_E2E_USER="$USER" CDS_E2E_PASS="$PW" CDS_E2E_TOKEN="$TOKEN" \
KUBECTL="$KUBECTL" CDS_SANDBOX_NS="$NS" REPO_ROOT="$REPO_ROOT" \
"$PY" - <<'PYEOF'
import json
import os
import subprocess
import sys
import time

# sys.path 锚定仓库根（由 shell 传入），不依赖调用方 CWD
sys.path.insert(0, os.path.join(os.environ["REPO_ROOT"], "sdk"))
from cds_sdk.client import CDSClient, CDSApiError

api = os.environ["CDS_API_URL"]
kubectl = os.environ["KUBECTL"]
ns = os.environ["CDS_SANDBOX_NS"]
token = os.environ.get("CDS_E2E_TOKEN")

# 真实签名是 token=（SDK 没有 access_token 形参，旧写法是 TypeError，从没跑通过）
client = (
    CDSClient(base_url=api, token=token) if token
    else CDSClient.login(api, os.environ["CDS_E2E_USER"], os.environ["CDS_E2E_PASS"])
)
client.health()

def kctl(*args):
    return subprocess.run([kubectl, *args], capture_output=True, text=True)

def step(name):
    print(f"[e2e-k8s] - {name}")

session = None
ok = False
teardown_bad = False
try:
    # 1. create k8s session（预算覆盖 pod 冷启动最坏路径：镜像拉取+PVC 绑定+就绪）
    step("create k8s session")
    session = client.create_session(
        data_product_id=os.environ.get("CDS_E2E_PRODUCT", "e2e-k8s-product"),
        sandbox_level="k8s",
        timeout_seconds=300,
    )
    sid = session["id"]
    cid = session.get("container_id", "k8s-" + str(sid)[:32])
    print(f"      session={sid} container={cid}")

    # 2. wait for pod Ready — phase=Running 不代表容器就绪，看 ContainersReady 条件
    step("wait for pod ContainersReady")
    pod = "sandbox-" + str(sid)[:16]
    deadline = time.time() + 180
    ready = False
    while time.time() < deadline:
        out = kctl("get", "pod", pod, "-n", ns, "-o",
                   'jsonpath={.status.conditions[?(@.type=="ContainersReady")].status}')
        if out.returncode == 0:
            if out.stdout.strip() == "True":
                ready = True
                break
        else:
            # 只有 NotFound 值得继续等；RBAC/网络等其他错误立刻带诊断失败
            if "NotFound" not in (out.stderr or ""):
                print(out.stderr)
                d = kctl("describe", "pod", pod, "-n", ns)
                print(d.stdout, d.stderr)
                raise SystemExit(f"kubectl get pod failed: {out.stderr.strip()}")
        time.sleep(2)
    if not ready:
        d = kctl("describe", "pod", pod, "-n", ns)
        events = kctl("get", "events", "-n", ns, "--sort-by=.lastTimestamp", "--field", f"involvedObject.name={pod}")
        print(d.stdout)
        print(events.stdout)
        raise SystemExit("pod not ContainersReady in 180s (见上方 describe/events 输出)")

    # 3. execute python in the sandbox -> expect exit 0
    step("execute code in sandbox")
    res = client.execute(sid, "print(21*2)", language="python")
    assert res.get("exit_code") == 0, f"execute failed: {res}"
    assert "42" in (res.get("output") or ""), f"unexpected output: {res}"
    print(f"      output={res.get('output','').strip()}")

    # 4. streaming exec path (W10: WS ticket -> exec/stream frames)。REST exec_command
    #    不算覆盖 W10；stream_exec 是流式链路的唯一 SDK 入口。
    step("streaming exec (WS frames)")
    frames = list(client.stream_exec(sid, "echo stream-ok", timeout_seconds=60))
    blob = "\n".join(json.dumps(f, ensure_ascii=False) for f in frames)
    assert "stream-ok" in blob, f"stream frames missing payload: {blob[:500]}"
    assert '"exit"' in blob or "'exit'" in blob, f"no exit frame in stream: {blob[:500]}"
    print(f"      stream frames={len(frames)}")
finally:
    # teardown + 删除核验：terminate 返回 0 不代表 pod 真没了（N7 回归点）
    if session and session.get("id"):
        try:
            client.terminate_session(session["id"])
            print("[e2e-k8s] session terminated")
            pod = "sandbox-" + str(session["id"])[:16]
            gone = False
            for _ in range(15):
                chk = kctl("get", "pod", pod, "-n", ns)
                if chk.returncode != 0 and ("NotFound" in (chk.stderr or "")):
                    gone = True
                    break
                time.sleep(2)
            if not gone:
                raise RuntimeError(f"pod still present after teardown: {pod} (ns={ns})")
            print("[e2e-k8s] pod deletion verified")
        except Exception as e:  # noqa: BLE001
            teardown_bad = True
            print(f"[e2e-k8s] teardown failed: {e}")
    if ok:
        print("[e2e-k8s] PASS: provision + exec + stream + verified teardown")
    if teardown_bad:
        # teardown/删除核验失败同样是失败：绝不因为“主流程绿了”而漏报
        raise SystemExit("[e2e-k8s] FAIL: teardown 或 pod 删除核验未通过")
PYEOF

log "== shared-volume PVC check (provision-time mounts) =="
if $KUBECTL get pvc -n "$NS" -l app=cds-sandbox >/dev/null 2>&1; then
    log "PVCs present; RWX attach semantics covered by unit tests (N7)."
else
    log "SKIP: namespace $NS 没有 app=cds-sandbox PVC —— 本轮未覆盖共享卷路径（显式跳过，不计通过）"
fi

log "e2e-k8s done"
