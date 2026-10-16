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
#  10. sandbox_cidr   沙箱网段与本机已有 IPv4 前缀不重叠（上游默认 192.168.0.0/18 覆盖
#                     192.168.0.0/24 这类常见 LAN，egress 一起来就抓走其下跳 → 整机断网）；
#                     并做路由归属差分探针——段不重叠仍可能被代理/VPN 的策略路由整段吞掉
#                     （透明代理按 route-exclude-address 逐条 `to <段> lookup main`，未列入
#                     排除表的段一律落进 TUN 表，egress 一起来宿主就到不了沙箱）
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
#   CDS_CUBE_CUBELET_BIN         默认 /usr/local/services/cubetoolbox/Cubelet/bin/cubelet
#   CDS_CUBE_EXPECTED_VERSION    默认取 bundle manifest（v0.7.3-rc2）
#   CDS_CUBE_EXPECTED_SHA        默认取 bundle manifest（9b75d339...）
#   CDS_CUBE_BUNDLE_MANIFEST     默认 <repo>/scripts/cube-bundle-manifest.json
#   CDS_CUBE_VOLUME_PLUGIN_SOCKET 默认 /run/cds-volume-plugin/cds.sock
#   CDS_CUBE_TLS_PROXY_HOST      默认 127.0.0.1
#   CDS_CUBE_TLS_PROXY_PORT      默认 9443（§6.1 CDS_CUBE_NODE_TLS_PROXY_PORT）
#   CDS_CUBE_SANDBOX_CIDR        检查项 10 用的沙箱网段；未设时依次读上游键
#                                CUBE_SANDBOX_NETWORK_CIDR → .one-click.env → 上游默认 192.168.0.0/18
#   CDS_CUBE_ROUTE_CONTROL_ADDR  检查项 10 归属探针的对照地址，默认 240.0.0.1（保留段，不可路由）
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
# 发布钩（节点二进制）与源码钩（上游 checkout）分列：旧清单无 release 块时退回源码钩
rel = cs.get("release") or {}
print(rel.get("version") or cs["version"], rel.get("git_sha") or cs["git_sha"])
EOF
)
  EXPECTED_VERSION="${EXPECTED_VERSION:-${_ver}}"
  EXPECTED_SHA="${EXPECTED_SHA:-${_sha}}"
fi
# 兜底：与 scripts/cube-bundle-manifest.json 保持一致的内置钉住值
EXPECTED_VERSION="${EXPECTED_VERSION:-v0.7.3-rc2}"
EXPECTED_SHA="${EXPECTED_SHA:-b0c4cc33fbfccbacc1a72241918ef280e9a32772}"

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
    fs_type="$(df -T "${DATA_DIR}" 2>/dev/null | awk 'NR==2 {print $2}')" || true
  fi
  if [[ ! -d "${DATA_DIR}" ]]; then
    fail data_fs "数据盘目录 ${DATA_DIR} 不存在（先 mkfs.xfs 并挂载，见 §9.1）"
    return
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
  local bin="${CDS_CUBE_CUBELET_BIN:-/usr/local/services/cubetoolbox/Cubelet/bin/cubelet}"
  if [[ ! -x "${bin}" ]]; then
    fail cubelet_bin "cubelet 二进制不存在或不可执行: ${bin}"
    return
  fi
  local out
  if ! out="$("${bin}" -v 2>&1)"; then
    fail cubelet_bin "cubelet -v 执行失败"
    return
  fi
  if grep -qF "${EXPECTED_SHA}" <<<"${out}" && grep -qF "${EXPECTED_VERSION}" <<<"${out}"; then
    pass cubelet_bin "版本匹配发布钉住（${EXPECTED_VERSION} + ${EXPECTED_SHA:0:12}）"
  else
    local why
    if grep -qF "${EXPECTED_VERSION}" <<<"${out}"; then why="commit 非钉住发布构建"; else why="版本号非钉住发布"; fi
    fail cubelet_bin "版本不匹配：钉住 ${EXPECTED_VERSION} (${EXPECTED_SHA:0:12})，实际: $(head -n1 <<<"${out}")（${why}）"
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

# ---- 10. 沙箱网段冲突（2026-10-16 现场教训）----
# 上游默认沙箱网段 192.168.0.0/18 把 192.168.0.0/24 这类常见 LAN 整段吞掉：cube-egress 一
# 起来，网关、本地代理（clash TUN）与 VPN（EasyConnect/OpenVPN）的下一跳全被沙箱网段抢走，
# 表象是整机“无法上网”而与代理/VPN 自身配置无关。因此这一项在安装前硬拦，不降级。

# 本机 IPv4 前缀采集：接口地址 + main 表路由（VPN 推送的静态路由只有路由没有接口地址，必须一并取）。
# cube* 接口跳过：那是沙箱网段自己的桥，与判定同源。末尾 sort -u 兼承担把 grep 无命中的
# 退出码归零（pipefail 下否则会把 set -e 的脚本带倒）。
host_prefixes() {
  {
    ip -o -4 addr show 2>/dev/null | grep -v -E ' dev cube[^ ]*'
    ip -o -4 route show table main 2>/dev/null
  } | awk '{
      for (i = 1; i <= NF; i++)
        if ($i ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\/[0-9]+$/) {
          split($i, o, ".");
          if (o[1] == 127 || (o[1] == 169 && o[2] == 254) || o[1] == 0 || o[1] == 255) next;
          print $i; next
        }
    }' | sort -u
}

ip2int() {
  local a b c d
  IFS='.' read -r a b c d <<<"$1"
  echo $(( (a << 24) + (b << 16) + (c << 8) + d ))
}

int2ip() {
  local n=$1
  echo "$(( (n >> 24) & 255 )).$(( (n >> 16) & 255 )).$(( (n >> 8) & 255 )).$(( n & 255 ))"
}

# 归属探针：把 `ip route get` 的一行拆成 (dev, table)，非 main/local 的表或隧道类设备
# 视为可疑；再用一个不路由的对照地址差分，区分“整段被专门吞”与“整机默认出口本就是隧道”
# ——后者的 link 路由更具体，egress 起来后仍会赢，不该误拦。
route_capture() {
  local addr="$1" line dev table
  line="$(ip route get "${addr}" 2>/dev/null | head -n1)"
  if [[ -z "${line}" ]]; then
    ROUTE_DEV=""; ROUTE_TABLE=""; ROUTE_LINE=""
    return 2
  fi
  dev="$(printf '%s' "${line}" | sed -n 's/.* dev \([^ ]*\).*/\1/p')"
  table="$(printf '%s' "${line}" | sed -n 's/.* table \([^ ]*\).*/\1/p')"
  ROUTE_DEV="${dev}"; ROUTE_TABLE="${table:-main}"; ROUTE_LINE="${line}"
  if printf '%s' "${dev}" | grep -qiE '^(tun|tap|utun|ppp|wg|Meta|ipsec|ec[0-9])'; then
    return 1
  fi
  case "${table}" in
    main | local | 254 | "") return 0 ;;
    *) return 1 ;;
  esac
}

check_route_attribution() {
  local base="$1" src="$2"
  local probe ctrl rc=0 crc=0 pdev ptable pline cdev ctable
  probe="$(int2ip $(( base + 2 )))"
  ctrl="${CDS_CUBE_ROUTE_CONTROL_ADDR:-240.0.0.1}"

  if route_capture "${probe}"; then rc=0; else rc=$?; fi
  pdev="${ROUTE_DEV}"; ptable="${ROUTE_TABLE}"; pline="${ROUTE_LINE}"
  if [[ "${rc}" -ne 1 ]]; then
    if [[ "${rc}" -eq 2 ]]; then
      pass sandbox_cidr "沙箱网段归属（来源: ${src}）：探针 ${probe} 尚无路由（未建站），走宿主默认出口"
    else
      pass sandbox_cidr "沙箱网段归属正常（来源: ${src}，探针 ${probe} → dev ${pdev:-?} table ${ptable}）"
    fi
    return
  fi

  if route_capture "${ctrl}"; then crc=0; else crc=$?; fi
  cdev="${ROUTE_DEV}"; ctable="${ROUTE_TABLE}"
  if [[ "${crc}" -eq 1 && "${cdev}" == "${pdev}" && "${ctable}" == "${ptable}" ]]; then
    pass sandbox_cidr "沙箱网段归属（来源: ${src}）：本机默认出口即隧道（dev ${cdev} table ${ctable}，对照地址同路）——本项无法判定，如实放行（egress 的 link 路由更具体，仍会赢）"
    return
  fi
  fail sandbox_cidr "沙箱网段（来源: ${src}）探针 ${probe} 的路由归属被隧道接管: ${pline}——请把该网段加入代理 TUN 的 route-exclude-address（如 mihomo/clash 的 tun.route-exclude-address）后重载代理，或改选 RFC1918 内的独立段；否则 egress 一起来宿主到不了沙箱"
}

check_sandbox_cidr() {
  if ! command -v ip >/dev/null 2>&1; then
    fail sandbox_cidr "ip 命令不可用，无法判定沙箱网段与本机前缀是否重叠"
    return
  fi
  local oneclick_env="/usr/local/services/cubetoolbox/.one-click.env"
  [[ -n "${MOCK_ROOT}" ]] && oneclick_env="${MOCK_ROOT}/usr/local/services/cubetoolbox/.one-click.env"
  local cidr="${CUBE_SANDBOX_NETWORK_CIDR:-${CDS_CUBE_SANDBOX_CIDR:-}}"
  local src="环境变量"
  if [[ -z "${cidr}" && -r "${oneclick_env}" ]]; then
    cidr="$(sed -n 's/^CUBE_SANDBOX_NETWORK_CIDR=//p' "${oneclick_env}" | head -n1 | tr -d '\040\011\012\015\042\047')"
    [[ -n "${cidr}" ]] && src="${oneclick_env}"
  fi
  if [[ -z "${cidr}" ]]; then
    # 读不到就按上游默认判定：默认值本身就是风脸，宁可误拦也不放过
    cidr="192.168.0.0/18"
    src="上游默认（.one-click.env 不可读或未设该键）"
  fi
  if [[ ! "${cidr}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/([0-9]|[12][0-9]|3[0-2])$ ]]; then
    fail sandbox_cidr "沙箱网段格式非法: ${cidr}（来源: ${src}）"
    return
  fi
  local base="${cidr%%/*}" plen="${cidr##*/}"
  local bint bmask
  bint="$(ip2int "${base}")"
  bmask=$(( (0xFFFFFFFF << (32 - plen)) & 0xFFFFFFFF ))
  local hosts conflicts="" seen="" n=0 p pb pp pm net
  hosts="$(host_prefixes)"
  while read -r p; do
    if [[ -z "${p}" ]]; then continue; fi
    pb="$(ip2int "${p%%/*}")"
    pp="${p##*/}"
    if (( pp > 32 )); then continue; fi
    pm=$(( (0xFFFFFFFF << (32 - pp)) & 0xFFFFFFFF ))
    # 归一成网络号：接口地址行带的是 192.168.0.190/24 这类主机地址，直接用会跟路由行重复报两个“网段”
    net=$(( pb & pm ))
    if [[ " ${seen} " == *" ${net} "* ]]; then continue; fi
    seen="${seen} ${net}"
    n=$((n + 1))
    if (( ((bint ^ net) & bmask) == 0 || ((bint ^ net) & pm) == 0 )); then
      conflicts="${conflicts} $(int2ip "${net}")/${pp}"
    fi
  done <<<"${hosts}"
  if [[ -n "${conflicts}" ]]; then
    fail sandbox_cidr "沙箱网段 ${cidr}（来源: ${src}）与本机前缀重叠:${conflicts}——请设 CUBE_SANDBOX_NETWORK_CIDR=<独立私网段，如 10.100.0.0/18> 写入 ${oneclick_env} 后重启 cube-sandbox-cube-egress*（否则 egress 起来会吃掉这些网段的下一跳，整机断网）"
    return
  fi
  pass sandbox_cidr "沙箱网段 ${cidr}（来源: ${src}）与本机 ${n} 个 IPv4 前缀无重叠"
  check_route_attribution "${bint}" "${src}"
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
check_sandbox_cidr
check_resources

echo "== 结果: ${FAILURES} 项失败 =="
if [[ "${FAILURES}" -gt 0 ]]; then
  exit 1
fi
exit 0
