#!/usr/bin/env bash
# install.sh — SS-10 CB-02：Cube 计算节点安装/升级幂等 wrapper
#
# 流程（spec §4 CB-02 步骤 1，幂等：可重复执行；节点重装/上游升级后重跑即恢复，§5.4）：
#   1. preflight.sh 硬门禁（失败即退出，不继续）
#   2. 官方 install-compute.sh（DEPLOY_ROLE=compute；auto = 既有安装走配置保留升级）
#   3. 三个 CDS 组件安装（均为独立 systemd 单元/进程，不触碰 stock 单元——红线 1/2）：
#      ② TLS 前置代理（stunnel）：证书缺失 → 硬失败（无静默明文，§2.10）；
#         仅当显式 CDS_CUBE_NODE_GRPC_TLS=false（本地低风险环境）才允许跳过并大声告警
#      ③ 审计 tailer 单元 + env（tailer 二进制本体由 CB-06 交付；二进制缺席时不 enable，如实记录）
#      ① cds-volume-plugin（CB-03 交付）：二进制在场 → 幂等向 stock cubelet config.toml
#         增量注册 cds-encrypted（diff 清单归档 stock-config-diff.log，红线 1「diff 留档」落地处）；
#         缺席 → 跳过并告警
#   4. 结果目录（host_dir 允许前缀，CB-01 侧校验）
#   5. 安装状态落盘 /var/lib/cds/cube-node/INSTALL_STATE.json
#      （节点 capabilities JSON 同目录：NODE_CAPABILITIES.json，preflight 的 TEE 探测
#       （SS-13 RT-03）将 tee=tdx|sev_snp|none upsert 进该文件，随节点注册上报）
#
# 所需权限：root（上游 installer 自带 require_root；本脚本不代跑 sudo，非 root 直接拒绝）。
#
# 环境变量（CDS_CUBE_* 前缀，默认值见各行；测试钩子 CDS_CUBE_MOCK_ROOT 同 preflight，
# mock 模式下 /var/lib、/etc/cds、/etc/systemd/system 重定向到 MOCK_ROOT 下，非 root 可演练）：
#   CUBE_UPSTREAM_DIR        上游 CubeSandbox 干净工作副本，默认 /home/kratos/Documents/workspace/CubeSandbox
#   CDS_CUBE_INSTALL_MODE    上游 --mode，默认 auto（install|upgrade|auto）
#   CDS_CUBE_NODE_GRPC_TLS   默认 true；显式 false = 本地低风险明文回退（大声告警，生产禁止，M-08）
#   CDS_CUBE_TLS_CERT/KEY/CA certificate_service 签发的节点 sidecar 证书（PEM 路径，必配，除非显式明文回退）
#   CDS_CUBE_TLS_PROXY_PORT  默认 9443（§6.1 CDS_CUBE_NODE_TLS_PROXY_PORT）
#   CDS_CUBE_GRPC_PORT       默认 9999（cubelet gRPC tcp_address）
#   CDS_CUBE_SANDBOX_CIDR    写入上游安装器的沙箱网段，默认 10.100.0.0/18（上游默认
#                            192.168.0.0/18 会盖掉常见 LAN，preflight 第 10 项硬拦）
#   CDS_CUBE_RESULTS_DIR     默认 /var/lib/cds/sandbox-results（§6.1）
#   CDS_CUBE_AUDIT_DIR       默认 /data/log/cube-egress（§6.1，egress 审计 JSONL 所在）
#   CDS_CUBE_VOLUME_PLUGIN_BIN   默认 /usr/local/bin/cds-volume-plugin（CB-03）
#   CDS_CUBE_VOLUME_PLUGIN_SOCKET 默认 /run/cds-volume-plugin/cds.sock
#   CDS_CUBE_AUDIT_TAILER_BIN    默认 /usr/local/bin/cds-audit-tailer（CB-06）
#   CDS_CUBE_CDS_API_URL     CDS 控制面地址（写入 tailer env）
#   CDS_CUBE_NODE_ID         节点标识（写入 tailer env；默认取 hostname）
#   CDS_CUBE_CUBELET_CONFIG  默认 /usr/local/services/cubetoolbox/Cubelet/config/config.toml
#   CDS_CUBE_CAPABILITIES_FILE 节点 capabilities JSON（默认 ${STATE_DIR}/NODE_CAPABILITIES.json，
#                              SS-13 RT-03 TEE 探测落存位）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOCK_ROOT="${CDS_CUBE_MOCK_ROOT:-}"
if [[ -n "${MOCK_ROOT}" ]]; then
  export PATH="${MOCK_ROOT}/bin:${PATH}"
  STATE_DIR="${MOCK_ROOT}/var/lib/cds/cube-node"
  TLS_DIR="${MOCK_ROOT}/etc/cds/tls-proxy"
  SYSTEMD_DIR="${MOCK_ROOT}/etc/systemd/system"
  ETC_CDS_DIR="${MOCK_ROOT}/etc/cds"
else
  STATE_DIR="/var/lib/cds/cube-node"
  TLS_DIR="/etc/cds/tls-proxy"
  SYSTEMD_DIR="/etc/systemd/system"
  ETC_CDS_DIR="/etc/cds"
fi

log()  { printf '[cube-node-install] %s\n' "$*"; }
die()  { printf '[cube-node-install] ERROR: %s\n' "$*" >&2; exit 1; }

# ---- 0. 权限与上游目录 ----
if [[ "${EUID}" -ne 0 && -z "${MOCK_ROOT}" ]]; then
  die "需要 root 权限运行本脚本（请用 sudo bash deploy/cube-node/install.sh）；脚本本身不代跑 sudo"
fi

UPSTREAM_DIR="${CUBE_UPSTREAM_DIR:-/home/kratos/Documents/workspace/CubeSandbox}"
UPSTREAM_INSTALL="${UPSTREAM_DIR}/deploy/one-click/install-compute.sh"
[[ -x "${UPSTREAM_INSTALL}" || -f "${UPSTREAM_INSTALL}" ]] || die "上游安装器不存在: ${UPSTREAM_INSTALL}（CUBE_UPSTREAM_DIR=${UPSTREAM_DIR}）"
# 红线 7：上游仓库唯一合法写操作是 fetch/reset --hard；此处只读执行其官方安装器
log "上游 CubeSandbox: ${UPSTREAM_DIR}"

STATE_FILE="${STATE_DIR}/INSTALL_STATE.json"
DIFF_LOG="${STATE_DIR}/stock-config-diff.log"

# ---- 1. preflight ----
log "步骤 1/5: preflight 硬门禁"
bash "${SCRIPT_DIR}/preflight.sh"

# ---- 2. 上游 compute 安装/升级 ----
INSTALL_MODE="${CDS_CUBE_INSTALL_MODE:-auto}"
# 沙箱网段一律显式落到独立私网段（不用上游默认 192.168.0.0/18）；同时交给上游自带的
# CIDR 冲突预检复核。已在外部显式设上游键时尊重它。
export CUBE_SANDBOX_NETWORK_CIDR="${CUBE_SANDBOX_NETWORK_CIDR:-${CDS_CUBE_SANDBOX_CIDR:-10.100.0.0/18}}"
log "沙箱网段 CUBE_SANDBOX_NETWORK_CIDR=${CUBE_SANDBOX_NETWORK_CIDR}"
log "步骤 2/5: 上游 install-compute.sh（ONE_CLICK_DEPLOY_ROLE=compute，mode=${INSTALL_MODE}）"
if [[ -n "${MOCK_ROOT}" ]]; then
  log "（mock 模式：跳过上游安装器，测试钩子）"
else
  ONE_CLICK_ASSUME_YES=1 ONE_CLICK_MODE="${INSTALL_MODE}" bash "${UPSTREAM_INSTALL}"
fi

# ---- 3. CDS 三组件 ----
mkdir -p "${STATE_DIR}"
export STATE_DIR TLS_DIR SYSTEMD_DIR ETC_CDS_DIR STATE_FILE DIFF_LOG SCRIPT_DIR MOCK_ROOT \
  UPSTREAM_DIR INSTALL_MODE \
  CDS_CUBE_BUNDLE_MANIFEST="${CDS_CUBE_BUNDLE_MANIFEST:-}" \
  CDS_CUBE_NODE_GRPC_TLS="${CDS_CUBE_NODE_GRPC_TLS:-true}" \
  CDS_CUBE_TLS_CERT="${CDS_CUBE_TLS_CERT:-}" CDS_CUBE_TLS_KEY="${CDS_CUBE_TLS_KEY:-}" CDS_CUBE_TLS_CA="${CDS_CUBE_TLS_CA:-}" \
  CDS_CUBE_TLS_PROXY_PORT="${CDS_CUBE_TLS_PROXY_PORT:-9443}" \
  CDS_CUBE_GRPC_PORT="${CDS_CUBE_GRPC_PORT:-9999}" \
  CDS_CUBE_VOLUME_PLUGIN_BIN="${CDS_CUBE_VOLUME_PLUGIN_BIN:-/usr/local/bin/cds-volume-plugin}" \
  CDS_CUBE_VOLUME_PLUGIN_SOCKET="${CDS_CUBE_VOLUME_PLUGIN_SOCKET:-/run/cds-volume-plugin/cds.sock}" \
  CDS_CUBE_CUBELET_CONFIG="${CDS_CUBE_CUBELET_CONFIG:-/usr/local/services/cubetoolbox/Cubelet/config/config.toml}" \
  CDS_CUBE_AUDIT_DIR="${CDS_CUBE_AUDIT_DIR:-/data/log/cube-egress}" \
  CDS_CUBE_AUDIT_TAILER_BIN="${CDS_CUBE_AUDIT_TAILER_BIN:-/usr/local/bin/cds-audit-tailer}" \
  CDS_CUBE_CDS_API_URL="${CDS_CUBE_CDS_API_URL:-}" \
  CDS_CUBE_AUDIT_SERVICE_TOKEN="${CDS_CUBE_AUDIT_SERVICE_TOKEN:-}" \
  CDS_CUBE_NODE_ID="${CDS_CUBE_NODE_ID:-$(hostname)}" \
  CDS_CUBE_CAPABILITIES_FILE="${CDS_CUBE_CAPABILITIES_FILE:-${STATE_DIR}/NODE_CAPABILITIES.json}"
bash - <<'INSTALL_CDS_COMPONENTS'
set -euo pipefail
log()  { printf '[cube-node-install] %s\n' "$*"; }
die()  { printf '[cube-node-install] ERROR: %s\n' "$*" >&2; exit 1; }
state_sidecar="not_installed"
state_tailer="not_installed"
state_plugin="skipped_binary_absent(CB-03)"
run() { if [[ -n "${MOCK_ROOT}" ]]; then log "（mock：$*）"; else "$@"; fi; }

# ---- ② TLS 前置代理（CDS 组件②，§2.10）----
CERT="${CDS_CUBE_TLS_CERT}"; KEY="${CDS_CUBE_TLS_KEY}"; CA="${CDS_CUBE_TLS_CA}"
if [[ -n "${CERT}" && -n "${KEY}" && -n "${CA}" && -f "${CERT}" && -f "${KEY}" && -f "${CA}" ]]; then
  mkdir -p "${TLS_DIR}"
  if [[ -z "${MOCK_ROOT}" ]]; then
    run install -m 0644 "${CERT}" "${TLS_DIR}/tls.crt"
    run install -m 0600 "${KEY}"  "${TLS_DIR}/tls.key"
    run install -m 0644 "${CA}"   "${TLS_DIR}/ca.crt"
  fi
  sed -e "s|__TLS_PROXY_PORT__|${CDS_CUBE_TLS_PROXY_PORT}|g" \
      -e "s|__CUBELET_GRPC_PORT__|${CDS_CUBE_GRPC_PORT}|g" \
      "${SCRIPT_DIR}/tls-proxy.stunnel.conf" > "${TLS_DIR}/stunnel.conf"
  run install -m 0644 "${SCRIPT_DIR}/tls-proxy.service" "${SYSTEMD_DIR}/cds-tls-proxy.service"
  if [[ -z "${MOCK_ROOT}" ]]; then
    run systemctl daemon-reload
    run systemctl enable cds-tls-proxy.service
    run systemctl restart cds-tls-proxy.service
  fi
  state_sidecar="installed_port_${CDS_CUBE_TLS_PROXY_PORT}"
  log "② TLS sidecar 已安装（${CDS_CUBE_TLS_PROXY_PORT} → 127.0.0.1:${CDS_CUBE_GRPC_PORT}，mTLS）"
else
  if [[ "${CDS_CUBE_NODE_GRPC_TLS}" != "false" ]]; then
    die "缺少 sidecar 证书（CDS_CUBE_TLS_CERT/KEY/CA 未就绪）。红线：不静默明文——\
证书由 CDS certificate_service 签发后重跑本脚本；仅本地低风险环境可显式 CDS_CUBE_NODE_GRPC_TLS=false（生产禁止，M-08 告警）"
  fi
  state_sidecar="skipped_plaintext_fallback(CDS_CUBE_NODE_GRPC_TLS=false)"
  log "⚠⚠⚠ 显式明文回退（CDS_CUBE_NODE_GRPC_TLS=false）：TLS sidecar 未安装。仅限本地低风险环境；生产环境禁止（M-08 告警纪律）"
fi

# ---- ③ 审计 tailer 单元 + env（CDS 组件③，二进制 CB-06）----
mkdir -p "${ETC_CDS_DIR}"
# 红线：缺服务令牌 = 审计链路 100% 401（/internal/audit/egress fail-closed），不静默交付坏链路
if [[ -z "${CDS_CUBE_AUDIT_SERVICE_TOKEN}" ]]; then
  die "缺少 CDS_CUBE_AUDIT_SERVICE_TOKEN（与 CDS 控制面同值的服务令牌）。红线：不静默安装注定 401 的审计链路——令牌由控制面 /root/cds.env 下发后重跑本脚本"
fi
sed -e "s|__CDS_CUBE_AUDIT_DIR__|${CDS_CUBE_AUDIT_DIR}|g" \
    -e "s|__CDS_CUBE_CDS_API_URL__|${CDS_CUBE_CDS_API_URL}|g" \
    -e "s|__CDS_CUBE_NODE_ID__|${CDS_CUBE_NODE_ID}|g" \
    -e "s|__CDS_CUBE_AUDIT_TAILER_BIN__|${CDS_CUBE_AUDIT_TAILER_BIN}|g" \
    -e "s|__CDS_CUBE_AUDIT_SERVICE_TOKEN__|${CDS_CUBE_AUDIT_SERVICE_TOKEN}|g" \
    "${SCRIPT_DIR}/audit-tailer.env.template" > "${ETC_CDS_DIR}/audit-tailer.env"
if [[ -z "${MOCK_ROOT}" ]]; then
  run chmod 0640 "${ETC_CDS_DIR}/audit-tailer.env"
fi
# 单元模板内 ExecStart 占位符由 tailer 二进制约定路径渲染（CB-06 交付）
RENDERED_UNIT="${ETC_CDS_DIR}/.audit-tailer.service.rendered"
sed -e "s|__CDS_CUBE_AUDIT_TAILER_BIN__|${CDS_CUBE_AUDIT_TAILER_BIN}|g" "${SCRIPT_DIR}/audit-tailer.service" > "${RENDERED_UNIT}"
run install -m 0644 "${RENDERED_UNIT}" "${SYSTEMD_DIR}/cds-audit-tailer.service"
if [[ -x "${CDS_CUBE_AUDIT_TAILER_BIN}" ]]; then
  if [[ -z "${MOCK_ROOT}" ]]; then
    run systemctl daemon-reload
    run systemctl enable cds-audit-tailer.service
    run systemctl restart cds-audit-tailer.service
  fi
  state_tailer="installed_active"
  log "③ 审计 tailer 已安装并启用（${CDS_CUBE_AUDIT_TAILER_BIN}）"
else
  state_tailer="installed_unit_pending_binary(CB-06)"
  log "③ 审计 tailer 单元已安装；二进制 ${CDS_CUBE_AUDIT_TAILER_BIN} 未交付（CB-06），未 enable——如实记录，不伪造"
fi

# ---- ① 卷插件注册（CDS 组件①，二进制 CB-03；此处只引用 socket 路径）----
if [[ -x "${CDS_CUBE_VOLUME_PLUGIN_BIN}" ]]; then
  CFG="${CDS_CUBE_CUBELET_CONFIG}"
  if [[ ! -f "${CFG}" ]]; then
    die "stock cubelet 配置不存在: ${CFG}（上游安装未完成？）"
  fi
  if grep -q 'name *= *"cds-encrypted"' "${CFG}"; then
    log "① 卷插件 cds-encrypted 已注册（幂等跳过）"
    state_plugin="registered(idempotent_skip)"
  else
    cp -a "${CFG}" "${CFG}.cds-backup-$(date +%s)"
    cat >> "${CFG}" <<TOML

# ==== CDS increment (SS-10 CB-02, idempotent; diff archived in ${DIFF_LOG}) ====
[plugins."io.cubelet.internal.v1.storage"]
volume_plugin_base_dir = "/data/cube-shared/volume"

[[plugins."io.cubelet.internal.v1.storage".volume_plugins]]
name        = "cds-encrypted"
type        = "rpc"
socket_path = "${CDS_CUBE_VOLUME_PLUGIN_SOCKET}"
# ==== end CDS increment ====
TOML
    echo "[$(date -Is)] CDS increment appended to ${CFG}: volume_plugins[cds-encrypted] (backup=${CFG}.cds-backup-*)" >> "${DIFF_LOG}"
    if [[ -z "${MOCK_ROOT}" ]] && systemctl list-unit-files cube-sandbox-cubelet.service >/dev/null 2>&1; then
      run systemctl restart cube-sandbox-cubelet.service
      log "① 卷插件已注册，cubelet 已重启加载配置"
    fi
    state_plugin="registered_socket_${CDS_CUBE_VOLUME_PLUGIN_SOCKET}"
  fi
else
  log "① 卷插件二进制 ${CDS_CUBE_VOLUME_PLUGIN_BIN} 未交付（CB-03）——跳过 stock 配置注册（不伪造）；socket 约定: ${CDS_CUBE_VOLUME_PLUGIN_SOCKET}"
  state_plugin="skipped_binary_absent(CB-03)"
fi

# 状态落盘（JSON，供 runbook/巡检读取）
cat > "${STATE_FILE}" <<JSON
{
  "installed_at": "$(date -Is)",
  "bundle_manifest": "${CDS_CUBE_BUNDLE_MANIFEST}",
  "install_mode": "${INSTALL_MODE}",
  "upstream_dir": "${UPSTREAM_DIR}",
  "components": {
    "tls_sidecar": "${state_sidecar}",
    "audit_tailer": "${state_tailer}",
    "volume_plugin": "${state_plugin}"
  },
  "ports": {"tls_proxy": ${CDS_CUBE_TLS_PROXY_PORT}, "cubelet_grpc": ${CDS_CUBE_GRPC_PORT}},
  "node_capabilities": "${CDS_CUBE_CAPABILITIES_FILE}",
  "paths": {
    "state_dir": "${STATE_DIR}",
    "stock_diff_archive": "${DIFF_LOG}",
    "results_dir": "${CDS_CUBE_RESULTS_DIR:-/var/lib/cds/sandbox-results}",
    "audit_dir": "${CDS_CUBE_AUDIT_DIR}"
  }
}
JSON
log "状态已写入 ${STATE_FILE}"
INSTALL_CDS_COMPONENTS
log "步骤 3/5: CDS 组件安装完成"

# ---- 4. 结果目录（host_dir 允许前缀；CB-01 侧做路径校验）----
RESULTS_DIR="${CDS_CUBE_RESULTS_DIR:-/var/lib/cds/sandbox-results}"
if [[ -n "${MOCK_ROOT}" ]]; then
  log "（mock：mkdir ${RESULTS_DIR}）"
else
  mkdir -p "${RESULTS_DIR}"
  chmod 0750 "${RESULTS_DIR}"
fi
log "步骤 4/5: 结果目录就绪 ${RESULTS_DIR}（mode 0750）"

# ---- 5. 完成 ----
log "步骤 5/5: 完成。下一步："
log "  - 运行 bash ${SCRIPT_DIR}/verify.sh（--json 可出机读结果）"
log "  - 节点注册 runtime_version 写入 sandbox_nodes（CB-01 客户端，§5.2 钉住版本）；"
log "    tee 能力由 preflight 探测落存 ${CDS_CUBE_CAPABILITIES_FILE}（SS-13 RT-03，随 capabilities 上报）"
exit 0
