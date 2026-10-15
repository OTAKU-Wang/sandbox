#!/usr/bin/env bash
# verify.sh — SS-10 CB-02：Cube 计算节点安装后验收（post-install acceptance）
#
# 检查项（spec §4 CB-02 步骤 1 验收 + §2.10）：
#   1. cubelet_grpc   cubelet 管理 gRPC 端口应答（HTTP/2 preface + SETTINGS 帧探测，
#                     python3 标准库实现，不依赖 grpcio——依赖轻）
#   2. tls_sidecar    TLS 前置代理（CDS 组件②）出示 CDS 证书（subject/issuer 含 CDS 标记）
#   3. volume_sock    CDS 卷插件 socket 存活（-S）
#   4. audit_tailer   审计 tailer 单元 active（CDS 组件③；二进制本体由 CB-06 交付）
#   5. cds_node_probe CDS 控制面注册探测——CB-01 客户端未就绪前，未配置端点时如实
#                     SKIPPED（诚实降级，不计失败，spec 6.3 步骤 5 纪律）
#   6. tee_probe      TEE 硬件探测（SS-13 RT-03）：无硬件 = pass(tee=none，如实披露，TEE 可选）；
#                     探测失败 = fail（探测本身出错，不是硬件缺失）
#
# 用法：verify.sh [--json]
#   --json  输出机器可读 JSON（契约：{"ok":bool,"checks":{name:{status,detail}},...}，
#           status ∈ pass|fail|skipped；ok = 无 fail）
# 退出码：0 = 无 fail；1 = 任一 fail。
#
# 环境变量与 preflight.sh 共用同一套 CDS_CUBE_* 键（见 preflight.sh 头部注释）；
# 追加：
#   CDS_CUBE_GRPC_PORT      cubelet gRPC TCP 端口，默认 9999（上游 config.toml grpc.tcp_address）
#   CDS_CUBE_TLS_CA         可选；设置后额外做 CA 校验握手
#   CDS_CUBE_TLS_EXPECT_SUBSTR  证书 subject/issuer 必须包含的标记，默认 "cds"
#   CDS_CUBE_AUDIT_TAILER_UNIT  tailer systemd 单元名，默认 cds-audit-tailer
#   CDS_CUBE_AUDIT_TAILER_BIN   tailer 二进制路径，默认 /usr/local/bin/cds-audit-tailer
#   CDS_CUBE_CDS_API_URL    CDS 控制面地址（设置后 cds_node_probe 实际探测 /health）
#   CDS_CUBE_CAPABILITIES_FILE  节点 capabilities JSON，默认 /var/lib/cds/cube-node/
#                               NODE_CAPABILITIES.json（mock 模式重定向 ${MOCK_ROOT} 下；同 preflight.sh）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOCK_ROOT="${CDS_CUBE_MOCK_ROOT:-}"
if [[ -n "${MOCK_ROOT}" ]]; then
  export PATH="${MOCK_ROOT}/bin:${PATH}"
fi

JSON_OUT=0
if [[ "${1:-}" == "--json" ]]; then JSON_OUT=1; fi

GRPC_HOST="${CDS_CUBE_GRPC_HOST:-127.0.0.1}"
GRPC_PORT="${CDS_CUBE_GRPC_PORT:-9999}"
TLS_PROXY_HOST="${CDS_CUBE_TLS_PROXY_HOST:-127.0.0.1}"
TLS_PROXY_PORT="${CDS_CUBE_TLS_PROXY_PORT:-9443}"
SOCK_PATH="${CDS_CUBE_VOLUME_PLUGIN_SOCKET:-/run/cds-volume-plugin/cds.sock}"
[[ -n "${MOCK_ROOT}" && -z "${CDS_CUBE_VOLUME_PLUGIN_SOCKET:-}" ]] && SOCK_PATH="${MOCK_ROOT}${SOCK_PATH}"
TLS_CA="${CDS_CUBE_TLS_CA:-}"
TLS_EXPECT="${CDS_CUBE_TLS_EXPECT_SUBSTR:-cds}"
TAILER_UNIT="${CDS_CUBE_AUDIT_TAILER_UNIT:-cds-audit-tailer}"
TAILER_BIN="${CDS_CUBE_AUDIT_TAILER_BIN:-/usr/local/bin/cds-audit-tailer}"
CAPABILITIES_FILE="${CDS_CUBE_CAPABILITIES_FILE:-}"
if [[ -z "${CAPABILITIES_FILE}" ]]; then
  if [[ -n "${MOCK_ROOT}" ]]; then CAPABILITIES_FILE="${MOCK_ROOT}/var/lib/cds/cube-node/NODE_CAPABILITIES.json"; else CAPABILITIES_FILE="/var/lib/cds/cube-node/NODE_CAPABILITIES.json"; fi
fi

# 结果累积：TSV 行 name<TAB>status<TAB>detail（detail 内不含换行）
RESULT_FILE="$(mktemp)"
trap 'rm -f "${RESULT_FILE}"' EXIT
record() { LAST_ST="$2"; LAST_DET="$3"; printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "${RESULT_FILE}"; }
say_last() {
  if [[ "${JSON_OUT}" -eq 0 ]]; then
    printf '[%s] %s: %s\n' "$(printf '%s' "${LAST_ST}" | tr '[:lower:]' '[:upper:]')" "$1" "${LAST_DET}"
  fi
}

# ---- 1. cubelet gRPC 端口应答 ----
check_cubelet_grpc() {
  local out
  if out="$(python3 - "${GRPC_HOST}" "${GRPC_PORT}" <<'EOF'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
s = socket.create_connection((host, port), timeout=3)
# HTTP/2 客户端前导：连接前导串 + 零长度 SETTINGS 帧
s.sendall(b"PRI * HTTP/2.0\r\n\r\nSM\r\n" + b"\x00\x00\x00\x04\x00\x00\x00\x00\x00")
try:
    hdr = s.recv(9)
except Exception:
    hdr = b""
s.close()
if len(hdr) >= 9 and 0 <= hdr[3] <= 8:
    # 收到合法 HTTP/2 帧（首帧通常为 SETTINGS=4）→ gRPC/HTTP2 服务应答
    print("http2_frame_type=%d" % hdr[3])
else:
    sys.exit(2)
EOF
)"; then
    record cubelet_grpc pass "${GRPC_HOST}:${GRPC_PORT} 应答（${out}）"
  else
    record cubelet_grpc fail "${GRPC_HOST}:${GRPC_PORT} 无 HTTP/2 应答（cubelet 未运行或端口不符）"
  fi
  say_last cubelet_grpc
}

# ---- 2. TLS sidecar 出示 CDS 证书 ----
check_tls_sidecar() {
  local out
  if ! out="$(CDS_TLS_CA="${TLS_CA}" CDS_TLS_EXPECT="${TLS_EXPECT}" python3 - "${TLS_PROXY_HOST}" "${TLS_PROXY_PORT}" <<'EOF'
import os, socket, ssl, sys
host, port = sys.argv[1], int(sys.argv[2])
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
s = socket.create_connection((host, port), timeout=3)
w = ctx.wrap_socket(s, server_hostname=host)
der = w.getpeercert(binary_form=True)
w.close()
if not der:
    print("no_cert")
    sys.exit(2)
# 从 DER 中直接提取可读 ASN.1 UTF8/Printable 串（证书主体名短，足够判定标记）
import re
blob = der.decode("latin-1")
names = re.findall(r"[\x0c\x13][\x01-\x7f]([ -~]{3,60})", blob)
text = " ".join(names) + " " + blob.encode("latin-1").decode("utf-8", "ignore")
expect = os.environ["CDS_TLS_EXPECT"]
ca = os.environ["CDS_TLS_CA"]
if expect not in text.lower():
    print("cert_missing_marker")
    sys.exit(2)
if ca:
    ctx2 = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx2.load_verify_locations(ca)
    s2 = socket.create_connection((host, port), timeout=3)
    try:
        w2 = ctx2.wrap_socket(s2, server_hostname=host)
    except Exception as e:
        print("ca_verify_failed:%s" % e)
        sys.exit(2)
    w2.close()
    print("ok_ca_verified")
else:
    print("ok_marker_only")
EOF
)"; then
    record tls_sidecar fail "TLS sidecar ${TLS_PROXY_HOST}:${TLS_PROXY_PORT} 未出示 CDS 证书（${out:-连接失败}）"
  else
    record tls_sidecar pass "证书含 CDS 标记（${out}）"
  fi
  say_last tls_sidecar
}

# ---- 3. 卷插件 socket ----
check_volume_sock() {
  if [[ -S "${SOCK_PATH}" ]]; then
    record volume_sock pass "socket 存活: ${SOCK_PATH}"
  else
    record volume_sock fail "socket 不存在: ${SOCK_PATH}（CB-03 插件未部署/未启动）"
  fi
  say_last volume_sock
}

# ---- 4. 审计 tailer 单元 ----
check_audit_tailer() {
  if ! command -v systemctl >/dev/null 2>&1; then
    record audit_tailer skip "systemctl 不可用（非 systemd 环境）"
  else
    local state
    state="$(systemctl is-active "${TAILER_UNIT}" 2>/dev/null || true)"
    if [[ "${state}" == "active" ]]; then
      record audit_tailer pass "unit ${TAILER_UNIT} active"
    else
      if [[ ! -e "${TAILER_BIN}" ]]; then
        # CB-06 二进制未交付：如实降级披露（unit 在场但依赖未就绪），不计失败
        record audit_tailer skip "unit=${state:-unknown}; 二进制 ${TAILER_BIN} 未交付（CB-06 前置）"
      else
        record audit_tailer fail "unit ${TAILER_UNIT} 状态=${state:-unknown}"
      fi
    fi
  fi
  say_last audit_tailer
}

# ---- 5. CDS 控制面注册探测 ----
check_cds_node_probe() {
  if [[ -z "${CDS_CUBE_CDS_API_URL:-}" ]]; then
    record cds_node_probe skip "CDS_CUBE_CDS_API_URL 未设置（CB-01 客户端就绪后启用；诚实跳过，非失败）"
    say_last cds_node_probe
    return
  fi
  if python3 - "${CDS_CUBE_CDS_API_URL%/}/health" <<'EOF'
import json, sys, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=5) as r:
    r.read()
EOF
  then
    record cds_node_probe pass "CDS /health 可达: ${CDS_CUBE_CDS_API_URL}"
  else
    record cds_node_probe fail "CDS /health 不可达: ${CDS_CUBE_CDS_API_URL}"
  fi
  say_last cds_node_probe
}

# ---- 6. TEE 硬件探测（SS-13 RT-03；结果 upsert 进节点 capabilities JSON） ----
check_tee_probe() {
  if ! command -v python3 >/dev/null 2>&1; then
    record tee_probe fail "python3 不存在，无法执行 TEE 探测（tee_probe.py 依赖标准库）"
    say_last tee_probe
    return
  fi
  if ! TEE_OUT="$(python3 "${SCRIPT_DIR}/tee_probe.py" --capabilities-file "${CAPABILITIES_FILE}")"; then
    record tee_probe fail "tee_probe.py 探测失败: $(head -n1 <<<"${TEE_OUT:-<无输出>}")"
    say_last tee_probe
    return
  fi
  TEE_LINE="$(head -n1 <<<"${TEE_OUT}")"
  case "${TEE_LINE}" in
    tee=none)    record tee_probe pass "无 TEE 硬件——如实披露 tee=none（SS-13 TEE 可选，裸 MicroVM 合法；capabilities 已记录 ${CAPABILITIES_FILE}）" ;;
    tee=tdx)     record tee_probe pass "TEE 硬件在场（tee=tdx；capabilities 已记录 ${CAPABILITIES_FILE}）" ;;
    tee=sev_snp) record tee_probe pass "TEE 硬件在场（tee=sev_snp；capabilities 已记录 ${CAPABILITIES_FILE}）" ;;
    *)           record tee_probe fail "tee_probe.py 输出格式异常: ${TEE_LINE}" ;;
  esac
  say_last tee_probe
}

check_cubelet_grpc
check_tls_sidecar
check_volume_sock
check_audit_tailer
check_cds_node_probe
check_tee_probe

if [[ "${JSON_OUT}" -eq 1 ]]; then
  RESULT_FILE="${RESULT_FILE}" python3 - <<'EOF'
import json, os
rows = []
with open(os.environ["RESULT_FILE"]) as f:
    for line in f:
        parts = line.rstrip("\n").split("\t")
        if len(parts) == 3:
            rows.append(parts)
checks = {name: {"status": st, "detail": detail} for name, st, detail in rows}
fails = sum(1 for r in rows if r[1] == "fail")
doc = {"ok": fails == 0, "checks": checks}
# SS-13 RT-03：tee 能力（探测值）在 JSON 顶层镜像一份，便于机器读取
if "tee_probe" in checks:
    doc["tee"] = {"status": checks["tee_probe"]["status"], "detail": checks["tee_probe"]["detail"]}
print(json.dumps(doc, ensure_ascii=False, indent=2))
EOF
else
  echo "== CDS Cube 节点 verify 完成 =="
fi
if awk -F'\t' '$2=="fail"{f=1} END{exit f+0}' "${RESULT_FILE}"; then
  exit 0
fi
exit 1
