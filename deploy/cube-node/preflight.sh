#!/usr/bin/env bash
# preflight.sh — SS-10 CB-02：Cube 计算节点 bring-up 硬门禁（逐项 PASS/FAIL，任一 FAIL → exit 1，不降级）
#
# 检查项（spec ss-10 §2.1 节点前置 + §4 CB-02 步骤 1）：
#   1. kvm            /dev/kvm 存在且可读写
#   2. data_fs        数据盘（CDS_CUBE_DATA_DIR，默认 /data/cubelet）位于 XFS 且 reflink 可用（FICLONE 实测）
#   3. ebpf           bpftool prog list 可用 + 内核 bpf 支持（syscall 探针 / /proc/filesystems）
#   4. cubelet_bin    cubelet 二进制在场 + 版本/ sha 与钉住 bundle 一致（§5.2）
#   5. volume_sock    CDS 加密卷插件 socket（CB-03 交付；本任务只引用其路径）
#   6. tls_sidecar    CDS TLS 前置代理（CDS 组件②）端口 TCP 可达（§2.10：sidecar 不可达 = 节点 down）
#   7. containerd     containerd 版本可读
#   8. resources      内存/磁盘水位
#   9. tee_probe      TEE 硬件探测（SS-13 RT-03，信息性检查：无硬件 = tee=none 如实上报，
#                     永不 FAIL——TEE 可选；探测本身出错才 FAIL）。结果写入节点
#                     capabilities JSON（CDS_CUBE_CAPABILITIES_FILE，默认 /var/lib/cds/cube-node/
#                     NODE_CAPABILITIES.json），stdout 输出单行 tee=...
#
# 所需权限：只读检查，普通用户可运行；但 /dev/kvm 的读写探针需要当前用户具备 kvm 设备访问权
# （通常 root 或 kvm 组成员）。本脚本不假设也不调用 sudo——权限不足时对应检查如实 FAIL。
#
# 测试钩子（仅测试/演练使用，生产不应设置）：
#   CDS_CUBE_MOCK_ROOT=DIR  将以下路径重定向到 DIR 下，并把 DIR/bin 前置到 PATH：
#     /dev/kvm, /proc/meminfo, /proc/filesystems, /sys/fs/bpf,
#     DIR/etc/fs_type（数据盘文件系统类型）, DIR/etc/disk_pct（数据盘已用百分比）,
#     DIR/etc/ebpf_syscall（1=内核支持 bpf syscall）, DIR/bin/<工具名>（xfs_info/bpftool/cubelet/... 垫片）
#
# 可覆盖环境变量（CDS_* 前缀，纪律沿用 6.1 矩阵；节点侧键不依赖代码默认值即可运行）：
#   CDS_CUBE_KVM_DEVICE          默认 /dev/kvm
#   CDS_CUBE_DATA_DIR            默认 /data/cubelet
#   CDS_CUBE_CUBELET_BIN         默认 /usr/local/services/cubetoolbox/bin/cubelet
#   CDS_CUBE_EXPECTED_VERSION    默认取 bundle manifest（v0.7.3-rc2）
#   CDS_CUBE_EXPECTED_SHA        默认取 bundle manifest（9b75d339...）
#   CDS_CUBE_BUNDLE_MANIFEST     默认 <repo>/scripts/cube-bundle-manifest.json
#   CDS_CUBE_VOLUME_PLUGIN_SOCKET 默认 /run/cds-volume-plugin/cds.sock
#   CDS_CUBE_TLS_PROXY_HOST      默认 127.0.0.1
#   CDS_CUBE_TLS_PROXY_PORT      默认 9443（§6.1 CDS_CUBE_NODE_TLS_PROXY_PORT）
#   CDS_CUBE_MIN_MEM_KB          默认 7500000（≈8GB，与上游 install.sh 对齐）
#   CDS_CUBE_MIN_FREE_DISK_PCT   默认 10
#   CDS_CUBE_REFLINK_PROBE_CMD   默认 "cp --reflink=always"（FICLONE 实测探针）
#   CDS_CUBE_CAPABILITIES_FILE   默认 <state>/NODE_CAPABILITIES.json（state 目录 mock 模式为
#                                ${MOCK_ROOT}/var/lib/cds/cube-node，生产 /var/lib/cds/cube-node）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOCK_ROOT="${CDS_CUBE_MOCK_ROOT:-}"
if [[ -n "${MOCK_ROOT}" ]]; then
  export PATH="${MOCK_ROOT}/bin:${PATH}"
fi

# 路径重定向：mock 模式下 /dev、/proc、/sys 下的固定路径指向 fixture
syspath() {
  if [[ -n "${MOCK_ROOT}" ]]; then echo "${MOCK_ROOT}$1"; else echo "$1"; fi
}

KVM_DEVICE="${CDS_CUBE_KVM_DEVICE:-/dev/kvm}"
[[ -n "${MOCK_ROOT}" ]] && KVM_DEVICE="${MOCK_ROOT}/dev/kvm"
DATA_DIR="${CDS_CUBE_DATA_DIR:-/data/cubelet}"
MEMINFO="$(syspath /proc/meminfo)"
PROC_FS="$(syspath /proc/filesystems)"
BPF_DIR="$(syspath /sys/fs/bpf)"
SOCK_PATH="${CDS_CUBE_VOLUME_PLUGIN_SOCKET:-/run/cds-volume-plugin/cds.sock}"
[[ -n "${MOCK_ROOT}" && -z "${CDS_CUBE_VOLUME_PLUGIN_SOCKET:-}" ]] && SOCK_PATH="${MOCK_ROOT}${SOCK_PATH}"
TLS_PROXY_HOST="${CDS_CUBE_TLS_PROXY_HOST:-127.0.0.1}"
TLS_PROXY_PORT="${CDS_CUBE_TLS_PROXY_PORT:-9443}"
MIN_MEM_KB="${CDS_CUBE_MIN_MEM_KB:-7500000}"
MIN_FREE_DISK_PCT="${CDS_CUBE_MIN_FREE_DISK_PCT:-10}"
REFLINK_PROBE_CMD="${CDS_CUBE_REFLINK_PROBE_CMD:-cp --reflink=always}"
CAPABILITIES_FILE="${CDS_CUBE_CAPABILITIES_FILE:-}"
if [[ -z "${CAPABILITIES_FILE}" ]]; then
  if [[ -n "${MOCK_ROOT}" ]]; then CAPABILITIES_FILE="${MOCK_ROOT}/var/lib/cds/cube-node/NODE_CAPABILITIES.json"; else CAPABILITIES_FILE="/var/lib/cds/cube-node/NODE_CAPABILITIES.json"; fi
fi

# ---- 钉住版本解析（§5.2：版本单位 = 上游 git sha） ----
EXPECTED_VERSION="${CDS_CUBE_EXPECTED_VERSION:-}"
EXPECTED_SHA="${CDS_CUBE_EXPECTED_SHA:-}"
MANIFEST="${CDS_CUBE_BUNDLE_MANIFEST:-${SCRIPT_DIR}/../../scripts/cube-bundle-manifest.json}"
if [[ -f "${MANIFEST}" ]] && command -v python3 >/dev/null 2>&1; then
  read -r _ver _sha < <(python3 - "${MANIFEST}" <<'EOF'
import json, sys
m = json.load(open(sys.argv[1]))
cs = m["cubesandbox"]
print(cs["version"], cs["git_sha"])
EOF
)
  EXPECTED_VERSION="${EXPECTED_VERSION:-${_ver}}"
  EXPECTED_SHA="${EXPECTED_SHA:-${_sha}}"
fi
# 兜底：与 scripts/cube-bundle-manifest.json 保持一致的内置钉住值
EXPECTED_VERSION="${EXPECTED_VERSION:-v0.7.3-rc2}"
EXPECTED_SHA="${EXPECTED_SHA:-9b75d33907ed7336b1eef73a935ae2d1990d9b35}"

FAILURES=0
pass() { printf 'PASS: %s (%s)\n' "$1" "$2"; }
fail() { printf 'FAIL: %s: %s\n' "$1" "$2"; FAILURES=$((FAILURES + 1)); }

# ---- 1. KVM ----
check_kvm() {
  if [[ ! -e "${KVM_DEVICE}" ]]; then
    fail kvm "/dev/kvm 不存在（内核未启用 KVM 或设备节点缺失）"
    return
  fi
  if [[ ! -r "${KVM_DEVICE}" || ! -w "${KVM_DEVICE}" ]]; then
    fail kvm "/dev/kvm 权限不足（当前用户需 kvm 组或 root；权限：$(stat -c '%A' "${KVM_DEVICE}")）"
    return
  fi
  # 实际打开探针：O_RDWR（python3 在场时；无 python3 时以 stat 权限判定为准）
  if command -v python3 >/dev/null 2>&1; then
    if ! python3 - "${KVM_DEVICE}" <<'EOF'
import os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
os.close(fd)
EOF
    then
      fail kvm "/dev/kvm 打开失败（O_RDWR）"
      return
    fi
  fi
  pass kvm "KVM 设备可读写"
}

# ---- 2. 数据盘 XFS + reflink ----
check_data_fs() {
  local fs_type=""
  if [[ -n "${MOCK_ROOT}" && -f "${MOCK_ROOT}/etc/fs_type" ]]; then
    fs_type="$(tr -d '[:space:]' < "${MOCK_ROOT}/etc/fs_type")"
  else
    fs_type="$(df -T "${DATA_DIR}" 2>/dev/null | awk 'NR==2 {print $2}')"
  fi
  if [[ "${fs_type}" != "xfs" ]]; then
    fail data_fs "数据盘文件系统为 '${fs_type:-unknown}'（要求 XFS，CubeCoW FICLONE 依赖）"
    return
  fi
  local xfs_out=""
  if command -v xfs_info >/dev/null 2>&1; then
    if ! xfs_out="$(xfs_info "${DATA_DIR}" 2>&1)"; then
      fail data_fs "xfs_info ${DATA_DIR} 失败: $(head -n1 <<<"${xfs_out}")"
      return
    fi
    if ! grep -q 'reflink=1' <<<"${xfs_out}"; then
      fail data_fs "xfs_info 未显示 reflink=1（CubeCoW O(1) 快照需要）"
      return
    fi
  else
    fail data_fs "xfs_info 不存在（xfsprogs 未安装）"
    return
  fi
  # FICLONE 实测：在数据盘上做 refllink 复制（探针命令可覆盖，测试用垫片）
  local probe_dir
  probe_dir="$(mktemp -d "${DATA_DIR}/.cds-preflight.XXXXXX")" || { fail data_fs "无法在 ${DATA_DIR} 建临时目录"; return; }
  local a="${probe_dir}/a" b="${probe_dir}/b"
  dd if=/dev/zero of="${a}" bs=1k count=64 2>/dev/null
  if ! ${REFLINK_PROBE_CMD} "${a}" "${b}" 2>/dev/null; then
    rm -rf "${probe_dir}"
    fail data_fs "FICLONE 实测失败（${REFLINK_PROBE_CMD}）"
    return
  fi
  rm -rf "${probe_dir}"
  pass data_fs "XFS + reflink（xfs_info reflink=1 + FICLONE 实测）"
}

# ---- 3. eBPF ----
check_ebpf() {
  if ! command -v bpftool >/dev/null 2>&1; then
    fail ebpf "bpftool 不存在（安装 bpftrace/libbpf 工具集或内核头包）"
    return
  fi
  if ! bpftool prog list >/dev/null 2>&1; then
    fail ebpf "bpftool prog list 失败（内核 bpf 系统调用被拒，检查 seccomp/安全模块）"
    return
  fi
  local probe="1"
  if [[ -n "${MOCK_ROOT}" && -f "${MOCK_ROOT}/etc/ebpf_syscall" ]]; then
    probe="$(tr -d '[:space:]' < "${MOCK_ROOT}/etc/ebpf_syscall")"
  elif command -v python3 >/dev/null 2>&1; then
    probe="$(python3 - <<'EOF'
import ctypes, ctypes.util, os
libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
try:
    libc.bpf(5, None, 0)  # BPF_PROG_GET_NEXT_ID
except Exception:
    pass
err = ctypes.get_errno()
# ENOSYS(38) = 内核无 bpf syscall；其它错误码 = 系统调用存在
print("0" if err == 38 else "1")
EOF
)"
  elif grep -qw bpf "${PROC_FS}"; then
    probe="1"
  else
    probe="0"
  fi
  if [[ "${probe}" != "1" ]]; then
    fail ebpf "内核不支持 bpf（/proc/filesystems 无 bpf 或 bpf() syscall 为 ENOSYS）"
    return
  fi
  [[ -d "${BPF_DIR}" ]] || { fail ebpf "/sys/fs/bpf 未挂载（bpffs 缺失，Cubelet 内嵌网络运行时需要）"; return; }
  pass ebpf "bpftool 可用 + 内核 bpf 支持 + bpffs 已挂载"
}

# ---- 4. cubelet 二进制 + 钉住版本 ----
check_cubelet_bin() {
  local bin="${CDS_CUBE_CUBELET_BIN:-/usr/local/services/cubetoolbox/bin/cubelet}"
  if [[ ! -x "${bin}" ]]; then
    fail cubelet_bin "cubelet 二进制不存在或不可执行: ${bin}"
    return
  fi
  local out
  if ! out="$("${bin}" -v 2>&1)"; then
    fail cubelet_bin "cubelet -v 执行失败"
    return
  fi
  if grep -qF "${EXPECTED_SHA}" <<<"${out}" || grep -qF "${EXPECTED_VERSION}" <<<"${out}"; then
    pass cubelet_bin "版本匹配钉住 bundle（${EXPECTED_VERSION} / ${EXPECTED_SHA:0:12}）"
  else
    fail cubelet_bin "版本不匹配：钉住 ${EXPECTED_VERSION} (${EXPECTED_SHA:0:12})，实际: $(head -n1 <<<"${out}")"
  fi
}

# ---- 5. CDS 卷插件 socket（CB-03 交付，此处只引用路径） ----
check_volume_sock() {
  if [[ -S "${SOCK_PATH}" ]]; then
    pass volume_sock "socket 在场: ${SOCK_PATH}"
  else
    fail volume_sock "CDS 卷插件 socket 不存在: ${SOCK_PATH}（CB-03 二进制未部署或插件未启动）"
  fi
}

# ---- 6. TLS 前置代理可达（CDS 组件②，§2.10） ----
check_tls_sidecar() {
  local host="${TLS_PROXY_HOST}" port="${TLS_PROXY_PORT}"
  if command -v python3 >/dev/null 2>&1; then
    if ! python3 - "${host}" "${port}" <<'EOF'
import socket, sys
s = socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=3)
s.close()
EOF
    then
      fail tls_sidecar "TLS sidecar ${host}:${port} 不可达（红线：sidecar 不可达 = 节点 down，不做明文回退）"
      return
    fi
  else
    if ! (exec 3<>"/dev/tcp/${host}/${port}") 2>/dev/null; then
      fail tls_sidecar "TLS sidecar ${host}:${port} 不可达"
      return
    fi
  fi
  pass tls_sidecar "TCP 可达 ${host}:${port}"
}

# ---- 7. containerd ----
check_containerd() {
  local out
  if out="$(containerd --version 2>&1)"; then
    pass containerd "$(head -n1 <<<"${out}")"
  else
    fail containerd "containerd 不可用（${out}）"
  fi
}

# ---- 8. 资源水位 ----
check_resources() {
  local mem_total_kb
  mem_total_kb="$(awk '/^MemTotal/ {print $2; exit}' "${MEMINFO}" 2>/dev/null || echo 0)"
  if [[ "${mem_total_kb}" -lt "${MIN_MEM_KB}" ]]; then
    fail resources "内存不足：需 ≥ $((MIN_MEM_KB / 1024 / 1024))GB，实测 $((mem_total_kb / 1024 / 1024))GB"
    return
  fi
  local used_pct=""
  if [[ -n "${MOCK_ROOT}" && -f "${MOCK_ROOT}/etc/disk_pct" ]]; then
    used_pct="$(tr -d '[:space:]\%' < "${MOCK_ROOT}/etc/disk_pct")"
  else
    used_pct="$(df -P "${DATA_DIR}" 2>/dev/null | awk 'NR==2 {gsub(/%/,""); print $5}')"
  fi
  if [[ -z "${used_pct}" || ! "${used_pct}" =~ ^[0-9]+$ ]]; then
    fail resources "无法读取数据盘使用率: ${DATA_DIR}"
    return
  fi
  local free_pct=$((100 - used_pct))
  if [[ "${free_pct}" -lt "${MIN_FREE_DISK_PCT}" ]]; then
    fail resources "数据盘余量不足：${free_pct}% < ${MIN_FREE_DISK_PCT}%"
    return
  fi
  pass resources "内存 $((mem_total_kb / 1024 / 1024))GB / 数据盘余量 ${free_pct}%"
}

# ---- 9. TEE 硬件探测（SS-13 RT-03；信息性：无硬件 = none，不 FAIL） ----
# 放在 KVM 之后尽早执行：能力上报行不受后续资源门禁失败截断（preflight 硬门禁
# 在任一 FAIL 后继续跑完全部检查，但在资源门禁失败的节点上提前探测可稳定出 tee 行）
check_tee_probe() {
  local out
  if ! command -v python3 >/dev/null 2>&1; then
    fail tee_probe "python3 不存在，无法执行 TEE 探测（tee_probe.py 依赖标准库）"
    return
  fi
  local TEE_DEVICE_ROOT="${MOCK_ROOT}"
  local err_file
  err_file="$(mktemp)"
  if ! out="$(CDS_CUBE_TEE_DEVICE_ROOT="${TEE_DEVICE_ROOT}" python3 "${SCRIPT_DIR}/tee_probe.py" --capabilities-file "${CAPABILITIES_FILE}" 2>"${err_file}")"; then
    fail tee_probe "tee_probe.py 探测失败: $(head -n1 "${err_file}" 2>/dev/null || echo '<无输出>')"
    rm -f "${err_file}"
    return
  fi
  local tee_err
  tee_err="$(cat "${err_file}" 2>/dev/null)"
  rm -f "${err_file}"
  local tee_line
  tee_line="$(head -n1 <<<"${out}")"
  case "${tee_line}" in
    tee=none|tee=tdx|tee=sev_snp) ;;
    *) fail tee_probe "tee_probe.py 输出格式异常: ${out}"; return ;;
  esac
  local warn=""
  if grep -q 'capabilities 写入失败' <<<"${tee_err}"; then
    warn="；警告: $(head -n1 <<<"${tee_err}")"
  fi
  printf '%s\n' "${tee_line}"
  case "${tee_line}" in
    tee=tdx)     pass tee_probe "TEE 硬件在场（${CAPABILITIES_FILE} 已记录 tee=tdx）${warn}" ;;
    tee=sev_snp) pass tee_probe "TEE 硬件在场（${CAPABILITIES_FILE} 已记录 tee=sev_snp）${warn}" ;;
    tee=none)    pass tee_probe "无 TEE 硬件——如实上报 tee=none（SS-13 TEE 可选，裸 MicroVM 合法；已记录 ${CAPABILITIES_FILE}）${warn}" ;;
  esac
}

echo "== CDS Cube 节点 preflight（SS-10 CB-02）钉住: ${EXPECTED_VERSION} ${EXPECTED_SHA:0:12} =="
check_kvm
check_tee_probe
check_data_fs
check_ebpf
check_cubelet_bin
check_volume_sock
check_tls_sidecar
check_containerd
check_resources

echo "== 结果: ${FAILURES} 项失败 =="
if [[ "${FAILURES}" -gt 0 ]]; then
  exit 1
fi
exit 0
