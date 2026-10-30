#!/usr/bin/env bash
# verify.sh — SS-10 CB-02：Cube 计算节点安装后验收（post-install acceptance）
#
# 检查项（spec §4 CB-02 步骤 1 验收 + §2.10）：
#   1. cubelet_grpc   cubelet 管理 gRPC 端口应答（HTTP/2 preface + SETTINGS 帧探测，
#                     python3 标准库实现，不依赖 grpcio——依赖轻）
#   2. tls_sidecar    TLS 前置代理（CDS 组件②）出示 CDS 证书（subject/issuer 含 CDS 标记）
#   3. volume_sock    CDS 卷插件 socket 在场（-S inode 判定；证据强度见检查内声明：
#                     未做活性探测，陈旧的 socket 文件无法由此发现）
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

usage() { printf 'usage: verify.sh [--json]\n' >&2; }

JSON_OUT=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --json) JSON_OUT=1 ;;
    -h | --help) usage; exit 0 ;;
    # 未知参数硬拒：旧版只看 $1，`verify.sh --json=1` 或拼错的参数会被静默当成
    # 默认调用——CI 里就是一份口径不同的「验收通过」
    *)
      printf 'FATAL: 未知参数: %s\n' "$1" >&2
      usage
      exit 2
      ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOCK_ROOT="${CDS_CUBE_MOCK_ROOT:-}"
if [[ -n "${MOCK_ROOT}" ]]; then
  # MOCK 钩子把 PATH/socket/证书/capabilities 全部指向 fixture 目录，全部验收项都能
  # 被垫片接管：不设显式确认就带着它跑到验收，说明演练变量被带进了真验收（禁生产）
  if [[ "${CDS_CUBE_ACCEPT_MOCK:-}" != "1" ]]; then
    printf 'FATAL: CDS_CUBE_MOCK_ROOT=%s 已设置但未显式确认演练（需 CDS_CUBE_ACCEPT_MOCK=1）——MOCK 结果不构成节点验收\n' "${MOCK_ROOT}" >&2
    exit 2
  fi
  export PATH="${MOCK_ROOT}/bin:${PATH}"
  # 演练横幅走 stderr：--json 模式下 stdout 必须只有 JSON（机器消费者直接 json.load）
  printf '== MOCK 演练模式：CDS_CUBE_MOCK_ROOT=%s（测试/演练专用，结果不构成节点验收）==\n' "${MOCK_ROOT}" >&2
fi

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
# detail 必须压成单行：内嵌换行/制表会把一行 TSV 裂成两行（下游解析器会静默丢行），
# 现场日志里的多行探针输出就是典型来源
record() { LAST_ST="$2"; LAST_DET="$(printf '%s' "$3" | tr '\t\n\r' '   ')"; printf '%s\t%s\t%s\n' "$1" "$2" "${LAST_DET}" >> "${RESULT_FILE}"; }
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
try:
    s = socket.create_connection((host, port), timeout=3)
    # HTTP/2 客户端前导：连接前导串 + 零长度 SETTINGS 帧
    s.sendall(b"PRI * HTTP/2.0\r\n\r\nSM\r\n" + b"\x00\x00\x00\x04\x00\x00\x00\x00\x00")
    hdr = b""
    while len(hdr) < 9:
        chunk = s.recv(9 - len(hdr))
        if not chunk:
            break
        hdr += chunk
    s.close()
except Exception as e:
    print("connect_or_read_failed:%s" % e.__class__.__name__)
    sys.exit(2)
if len(hdr) < 9:
    print("short_header:%d" % len(hdr))
    sys.exit(2)
length = int.from_bytes(hdr[0:3], "big")
ftype = hdr[3]
flags = hdr[4]
stream = int.from_bytes(hdr[5:9], "big") & 0x7FFFFFFF
# 合法首帧必须是 SETTINGS：类型==4、flags==0、stream==0、长度自洽
# （<= 16384 = DEFAULT_MAX_FRAME_SIZE 且为 6 的整数倍）。旧判据
# `0 <= hdr[3] <= 8` 会把一个 TLS 记录头（0x16 0x03 0x03 ...）当成 HTTP/2
# 帧而假 pass：TLS 记录的类型/长度/流号全部对不上，必须 fail。
if ftype != 4 or flags != 0 or stream != 0 or length > 16384 or length % 6 != 0:
    print("not_http2_settings:type=%d_flags=%d_len=%d_stream=%d" % (ftype, flags, length, stream))
    sys.exit(2)
print("http2_settings_frame_len=%d" % length)
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
expect = os.environ["CDS_TLS_EXPECT"].lower()
ca = os.environ["CDS_TLS_CA"]


def _tlv(der, i):
    """DER TLV：返回 (tag, 值起始, 值长度)。不定长编码不受理。"""
    tag = der[i]
    ln = der[i + 1]
    if ln & 0x80:
        n = ln & 0x7F
        if n == 0 or n > 4:
            raise ValueError("unsupported DER length")
        ln = int.from_bytes(der[i + 2:i + 2 + n], "big")
        return tag, i + 2 + n, ln
    return tag, i + 2, ln


STRINGS = (0x0c, 0x13, 0x16, 0x1e, 0x0b)  # UTF8String/PrintableString/IA5/Teletex/UTF8


def _names(der, start, end):
    """收集一个 Name 子树内的全部字符串（O= / OU= / CN=）。"""
    out, i = [], start
    while i < end:
        tag, vs, ln = _tlv(der, i)
        if tag in (0x30, 0x31):
            out += _names(der, vs, vs + ln)
        elif tag in STRINGS:
            s = der[vs:vs + ln].decode("utf-8", "ignore")
            if s:
                out.append(s)
        i = vs + ln
    return out


def _subject_issuer_names(der):
    """按 X.501 结构取 issuer 与 subject（不扫全文）。

    旧实现把整个 DER 按 latin-1 解码后正则扫串：签名/SAN/CA Issuer URI 等
    任意位置的字符序列都能凑出标记，于是一张与 CDS 无关的证书也能 pass。
    """
    _, p, _ = _tlv(der, 0)              # Certificate ::= SEQUENCE
    cls, tbs, _tbs_len = _tlv(der, p)   # TBSCertificate ::= SEQUENCE
    if cls != 0x30:
        raise ValueError("证书结构异常（TBSCertificate 非 SEQUENCE）")
    j = tbs
    if der[j] == 0xA0:                  # [0] version（可选）
        _, vs, vl = _tlv(der, j)
        j = vs + vl
    _, vs, vl = _tlv(der, j)            # serialNumber
    j = vs + vl
    _, vs, vl = _tlv(der, j)            # signature AlgorithmIdentifier
    j = vs + vl
    _, ivs, ilen = _tlv(der, j)         # issuer Name
    j = ivs + ilen
    _, vs, vl = _tlv(der, j)            # validity
    j = vs + vl
    cls, svs, slen = _tlv(der, j)       # subject Name
    if cls != 0x30:
        raise ValueError("证书结构异常（Subject 非 SEQUENCE）")
    return _names(der, ivs, ivs + ilen) + _names(der, svs, svs + slen)


ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE  # 无 CA 时的只看证书内容口径（不校验链，下方 pass 文案已声明）
try:
    s = socket.create_connection((host, port), timeout=3)
    w = ctx.wrap_socket(s, server_hostname=host)
    der = w.getpeercert(binary_form=True)
    w.close()
except Exception as e:
    print("tls_handshake_failed:%s" % e.__class__.__name__)
    sys.exit(2)
if not der:
    print("no_cert")
    sys.exit(2)
try:
    names = _subject_issuer_names(der)
except ValueError as e:
    print("cert_parse_failed:%s" % e)
    sys.exit(2)
text = " ".join(names).lower()
if expect not in text:
    print("cert_missing_marker(subject/issuer=%s)" % " ".join(names)[:120])
    sys.exit(2)
if ca:
    ctx2 = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # 默认 verify_mode=CERT_REQUIRED
    ctx2.load_verify_locations(cafile=ca)           # CA 在场：校验链
    ctx2.check_hostname = True                      # 且校验主机名
    try:
        s2 = socket.create_connection((host, port), timeout=3)
        w2 = ctx2.wrap_socket(s2, server_hostname=host)
    except Exception as e:
        print("ca_verify_failed:%s" % e)
        sys.exit(2)
    w2.close()
    print("ok_ca_verified")
else:
    print("ok_marker_only_no_chain_check")
EOF
)"; then
    record tls_sidecar fail "TLS sidecar ${TLS_PROXY_HOST}:${TLS_PROXY_PORT} 未出示带 CDS 标记的证书（${out:-连接失败}）"
  else
    record tls_sidecar pass "证书 subject/issuer 含 CDS 标记（${out}）"
  fi
  say_last tls_sidecar
}

# ---- 3. 卷插件 socket ----
check_volume_sock() {
  # 证据强度如实声明（诚实降级铁律）：`-S` 只看 inode 类型——插件崩溃/未启动后
  # 残留的陈旧 socket 文件同样“在场”，本项发现不了。这里故意不做活性探测：
  # 向插件的 rpc unix socket 发 connect 本身就是一次业务动作（可能被计为一次卷请求），
  # 验收脚本不得拿它去碰生产插件；真实可用性由 CreateVolume 健康检查（CB-04）承担。
  if [[ -S "${SOCK_PATH}" ]]; then
    record volume_sock pass "socket 在场: ${SOCK_PATH}（仅 -S inode 判定，未做活性探测：陈旧 socket 无法由此发现）"
  else
    record volume_sock fail "socket 不存在: ${SOCK_PATH}（CB-03 插件未部署/未启动）"
  fi
  say_last volume_sock
}

# ---- 4. 审计 tailer 单元 ----
check_audit_tailer() {
  if ! command -v systemctl >/dev/null 2>&1; then
    record audit_tailer skipped "systemctl 不可用（非 systemd 环境）"
  else
    local state
    state="$(systemctl is-active "${TAILER_UNIT}" 2>/dev/null || true)"
    if [[ "${state}" == "active" ]]; then
      record audit_tailer pass "unit ${TAILER_UNIT} active"
    else
      if [[ ! -e "${TAILER_BIN}" ]]; then
        # CB-06 二进制未交付：如实降级披露（unit 在场但依赖未就绪），不计失败
        record audit_tailer skipped "unit=${state:-unknown}; 二进制 ${TAILER_BIN} 未交付（CB-06 前置）"
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
    record cds_node_probe skipped "CDS_CUBE_CDS_API_URL 未设置（CB-01 客户端就绪后启用；诚实跳过，非失败）"
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
  # 设备根必须跟着 mock 根走：否则宿主机真装的 TEE 设备/工具会把演练场景探成
  # 「有真硬件」（伪造硬件信号，铁律禁止）；真节点仍为 "/"
  local tee_root err_file tee_err warn
  tee_root="${MOCK_ROOT:-/}"
  err_file="$(mktemp)"
  # stderr 必须留档：tee_probe.py 的 capabilities 写入告警只走 stderr，直接丢弃
  # 就把「能力上报没落盘」的实情给静掉了
  if ! TEE_OUT="$(CDS_CUBE_TEE_DEVICE_ROOT="${tee_root}" python3 "${SCRIPT_DIR}/tee_probe.py" --capabilities-file "${CAPABILITIES_FILE}" 2>"${err_file}")"; then
    tee_err="$(head -n1 "${err_file}" 2>/dev/null)"
    rm -f "${err_file}"
    record tee_probe fail "tee_probe.py 探测失败（stderr: ${tee_err:-<无输出>}）"
    say_last tee_probe
    return
  fi
  tee_err="$(cat "${err_file}" 2>/dev/null)"
  rm -f "${err_file}"
  warn=""
  if grep -q 'capabilities 写入失败' <<<"${tee_err}"; then
    warn="；警告: $(head -n1 <<<"${tee_err}")"
  fi
  TEE_LINE="$(head -n1 <<<"${TEE_OUT}")"
  case "${TEE_LINE}" in
    tee=none)    record tee_probe pass "无 TEE 硬件——如实披露 tee=none（SS-13 TEE 可选，裸 MicroVM 合法；capabilities 已记录 ${CAPABILITIES_FILE}）${warn}" ;;
    tee=tdx)     record tee_probe pass "TEE 硬件在场（tee=tdx；capabilities 已记录 ${CAPABILITIES_FILE}）${warn}" ;;
    tee=sev_snp) record tee_probe pass "TEE 硬件在场（tee=sev_snp；capabilities 已记录 ${CAPABILITIES_FILE}）${warn}" ;;
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
import json, os, sys
rows = []
bad = []
with open(os.environ["RESULT_FILE"]) as f:
    for lineno, line in enumerate(f, 1):
        parts = line.rstrip("\n").split("\t")
        if len(parts) == 3:
            rows.append(parts)
        else:
            bad.append((lineno, line.rstrip("\n")))
if bad:
    # 丢一行 = 丢一项验收：静默丢弃可能正好丢掉唯一的 fail，把验收变成假 pass
    print("FATAL: 结果文件有 %d 行列数 != 3，拒绝出报告: %s" % (len(bad), bad[:3]), file=sys.stderr)
    sys.exit(2)
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
