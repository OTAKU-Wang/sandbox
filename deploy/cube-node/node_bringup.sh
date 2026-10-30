#!/usr/bin/env bash
# node_bringup.sh — SS-14 RT-07：本机 Cube 计算节点一键 bring-up（需 sudo）
#
# 用法: sudo CDS_ALLOW_INSECURE_GRPC=yes bash deploy/cube-node/node_bringup.sh
#       （明文 gRPC 回退必须显式确认，见第 3 段；生产请配 sidecar 证书后直接跑 install.sh）
# 环境变量: CUBE_UPSTREAM_DIR 必填——上游 CubeSandbox 干净副本（无个人目录默认值）
# 产物: /data/cubelet (XFS, reflink=1, 30G loop) + cubelet 计算节点（上游 install-compute.sh）
#       + CDS 节点组件（TLS proxy / 审计 tailer / 卷插件，见 install.sh 头注）
# 幂等: 可重跑（loop/挂载/附着存在则复用；安装器 auto 模式升级）
#
# 前置自检（脚本内执行，且全部早于任何磁盘写操作）: /dev/kvm 在位、loop 控制节点与
# 空闲 loop 可用、xfsprogs/blkid/findmnt/losetup 在场、上游目录属主与权限可信。
set -euo pipefail

DATA_DIR="${CDS_CUBE_DATA_DIR:-/data/cubelet}"
LOOP_IMG="/var/lib/cds-cube/cubelet.img"
STATE_DIR="/var/lib/cds-cube"
LOCK_FILE="${STATE_DIR}/bringup.lock"
SIZE_GB=30

die()  { printf 'FATAL: %s\n' "$*" >&2; exit 1; }
note() { printf '[bringup] %s\n' "$*"; }

if [[ $EUID -ne 0 ]]; then die "需要 root（sudo bash $0）"; fi

# 0. 独占锁：cloud-init / 人工 / CI 并发跑同一脚本会互相踩盘（同镜像双附着 = 数据损坏）
mkdir -p "${STATE_DIR}"
if command -v flock >/dev/null 2>&1; then
  exec 9>"${LOCK_FILE}"
  flock -n 9 || die "另一个 bring-up 正在运行（锁: ${LOCK_FILE}）——拒绝并发执行"
else
  note "⚠ 本环境无 flock：无法阻止并发 bring-up 互踩（如实披露）"
fi

# 上游目录必须由调用方显式给出：root 执行的安装器不得猜路径，更不得把某台开发机的
# 个人工作目录当生产默认值（换一台机器它就是一个必然不存在的路径）
CUBE_UPSTREAM="${CUBE_UPSTREAM_DIR:-}"
[[ -n "${CUBE_UPSTREAM}" ]] || die "必须显式设置 CUBE_UPSTREAM_DIR=/path/to/CubeSandbox——不提供个人目录默认值"
[[ -d "${CUBE_UPSTREAM}" ]] || die "上游副本不在 ${CUBE_UPSTREAM}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NODE_DIR="${REPO_ROOT}/deploy/cube-node"

# 1. 前置自检（必须早于 losetup/mkfs：半途才发现工具缺失会留下半态磁盘）
[[ -e /dev/kvm ]] || die "/dev/kvm 不存在（内核未启用 KVM）"
command -v xfs_info >/dev/null || die "xfsprogs 未安装（apt install xfsprogs）"
command -v mkfs.xfs  >/dev/null || die "mkfs.xfs 未安装（xfsprogs）"
command -v blkid    >/dev/null || die "blkid 不可用（util-linux）——无法判断镜像是否已建盘，拒跑"
command -v findmnt  >/dev/null || die "findmnt 不可用（util-linux）——无法校验挂载来源，拒跑"
command -v losetup  >/dev/null || die "losetup 不可用（util-linux）"
# loop 控制节点必须在位：没有它就只能猜空闲 loop 号，撞上别的进程就是同镜像双附着
[[ -c /dev/loop-control ]] || die "/dev/loop-control 不存在（loop 模块未加载：modprobe loop）——无法安全分配 loop 设备，拒跑"
losetup -f >/dev/null 2>&1 || die "无空闲 loop 设备（losetup 分配失败）——先回收既有附着再重跑"

# root 即将执行上游目录里的安装脚本：目录不得被非属主改写（否则普通用户可提权）
if ! UP_OWNER="$(stat -c '%U' "${CUBE_UPSTREAM}" 2>/dev/null)"; then
  die "无法 stat 上游目录 ${CUBE_UPSTREAM}——权限无法确认，拒跑"
fi
UP_MODE="$(stat -c '%a' "${CUBE_UPSTREAM}" 2>/dev/null)" || die "无法 stat 上游目录权限 ${CUBE_UPSTREAM}"
note "上游目录 ${CUBE_UPSTREAM} 属主=${UP_OWNER} 模式=${UP_MODE}"
case "${UP_MODE: -2}" in
  *[2367]*) die "上游目录 ${CUBE_UPSTREAM} 模式 ${UP_MODE}：group/other 含写位——root 要执行的安装脚本不得放在非属主可改写的目录（先 chmod go-w，或换目录）" ;;
esac

# 2. 数据盘（XFS loop，幂等）
# 路径要进 sed/正则：元字符必须转义，否则 DATA_DIR 里的 . 会匹配任意字符、
# * 会扩量，最坏情况删掉别人的 fstab 行
esc_sed() { printf '%s' "$1" | sed -e 's/[].[^$*\/&]/\\&/g'; }

# legacy 清理：旧版本曾写 /dev/loopN 设备名（跨重启失配 → 重启后挂不上 → 落入重建盘
# 路径）。这段必须无条件执行（与当前挂载状态无关）：放进 `if ! mountpoint` 里，已挂载
# 的节点就永远清不掉坏行，重启后照样挂不上。
esc="$(esc_sed "${DATA_DIR}")"
if grep -qE "^/dev/loop[0-9]+ ${esc} " /etc/fstab; then
  sed -i".bak-cds" -E "\#^/dev/loop[0-9]+ ${esc} #d" /etc/fstab
  note "已清除旧版遗留的 /dev/loopN fstab 行（备份 /etc/fstab.bak-cds）"
fi

# fstab 条目必须存在（与挂载状态无关：已挂载但缺行 = 重启后不挂载）
ensure_fstab_entry() {
  if grep -qF "${LOOP_IMG}" /etc/fstab; then
    return 0
  fi
  # 末行没有换行时直接 >> 会把新条目粘在上一行尾部（fstab 解出半行 → 重启挂错盘）
  if [[ -s /etc/fstab ]] && [[ -n "$(tail -c 1 /etc/fstab)" ]]; then
    echo >> /etc/fstab
  fi
  printf '%s %s xfs loop,noatime 0 0\n' "${LOOP_IMG}" "${DATA_DIR}" >> /etc/fstab
  note "fstab: ${LOOP_IMG} -> ${DATA_DIR} (xfs loop)"
}

if mountpoint -q "${DATA_DIR}"; then
  # 已挂载也必须校验来源与 fs 类型：别的设备挂在同一目录时继续写 = 毁别人的数据
  MNT_SRC="$(findmnt -n -o SOURCE --mountpoint "${DATA_DIR}" 2>/dev/null || true)"
  MNT_FSTYPE="$(findmnt -n -o FSTYPE --mountpoint "${DATA_DIR}" 2>/dev/null || true)"
  ATTACHED="$(losetup -j "${LOOP_IMG}" 2>/dev/null | head -n1 | cut -d: -f1 || true)"
  case "${MNT_SRC}" in
    "${LOOP_IMG}") : ;;
    /dev/loop*)
      [[ -n "${ATTACHED}" && "${MNT_SRC}" == "${ATTACHED}" ]] ||
        die "数据目录 ${DATA_DIR} 的挂载来源是 ${MNT_SRC}，与镜像 ${LOOP_IMG} 的附着（${ATTACHED:-无}）不一致——错配即中止，不覆盖别人的挂载"
      ;;
    *) die "数据目录 ${DATA_DIR} 已挂载但来源不是本节点镜像（SOURCE=${MNT_SRC:-未取到}，期望 ${LOOP_IMG} 或其 loop 设备）——错配即中止" ;;
  esac
  [[ "${MNT_FSTYPE}" == "xfs" ]] || die "数据目录 ${DATA_DIR} 的 fs 类型为 ${MNT_FSTYPE:-未取到}，要求 xfs——中止（不覆盖别人的文件系统）"
  note "数据盘已挂载且来源正确: ${MNT_SRC} (${MNT_FSTYPE})"
else
  mkdir -p "${STATE_DIR}" "${DATA_DIR}"
  if [[ ! -f "${LOOP_IMG}" ]]; then
    note "创建 ${SIZE_GB}G 稀疏 loop 镜像 ${LOOP_IMG}"
    truncate -s "${SIZE_GB}G" "${LOOP_IMG}"
  fi
  # blkid 三态：0 = 探测到文件系统；2 = 确认无文件系统；其它（IO 错/权限/不支持）=
  # 无法确认。把「探测失败」当「空盘」直接 mkfs -f 就是抹掉既有数据。
  set +e
  BLKID_OUT="$(blkid -o value -s TYPE "${LOOP_IMG}" 2>&1)"
  blkid_rc=$?
  set -e
  if [[ ${blkid_rc} -eq 0 ]]; then
    EXISTING_FSTYPE="${BLKID_OUT}"
  elif [[ ${blkid_rc} -eq 2 ]]; then
    EXISTING_FSTYPE=""
  else
    die "blkid 探测 ${LOOP_IMG} 返回 ${blkid_rc}（输出: ${BLKID_OUT}）：无法确认镜像内是否已有文件系统——继续就会 mkfs 覆写数据，中止（不猜、不伪造「空盘」）"
  fi
  if [[ -n "${EXISTING_FSTYPE}" && "${EXISTING_FSTYPE}" != "xfs" ]]; then
    die "${LOOP_IMG} 已是 ${EXISTING_FSTYPE} 文件系统（非 XFS）——请人工确认后处理，本脚本不会覆写"
  fi
  # 既有附着必须复用：同一镜像两个 loop 附着 = 两份互不知情的内核缓存 → 静默损坏
  LOOP_DEV="$(losetup -j "${LOOP_IMG}" 2>/dev/null | head -n1 | cut -d: -f1 || true)"
  if [[ -n "${LOOP_DEV}" ]]; then
    note "复用已有 loop 附着: ${LOOP_DEV} -> ${LOOP_IMG}"
  else
    LOOP_DEV="$(losetup -f --show "${LOOP_IMG}")"
  fi
  [[ -n "${LOOP_DEV}" ]] || die "losetup 未给出设备名——中止（不猜 loop 号）"
  printf '%s\n' "${LOOP_DEV}" > "${STATE_DIR}/loopdev"

  # mkfs/mount 任一步失败：回滚本次新附着，绝不留「已格式化但没挂上」的半态；
  # 镜像文件与其中数据一律不删（重跑销毁 /data/cubelet = 红线）
  disk_rollback() {
    trap - ERR
    if [[ -n "${LOOP_DEV:-}" ]] && ! mountpoint -q "${DATA_DIR}"; then
      losetup -d "${LOOP_DEV}" 2>/dev/null || true
      printf '[bringup] 磁盘段失败：已回滚 loop 附着 %s（镜像文件与数据未删除）\n' "${LOOP_DEV}" >&2
    fi
  }
  trap 'disk_rollback' ERR
  if [[ "${EXISTING_FSTYPE}" == "xfs" ]]; then
    note "${LOOP_IMG} 已有 XFS（数据保留）-> mount ${DATA_DIR}"
  else
    note "${LOOP_DEV} -> mkfs.xfs -> mount ${DATA_DIR}"
    mkfs.xfs -f -K "${LOOP_DEV}"
  fi
  mount "${LOOP_DEV}" "${DATA_DIR}"
  trap - ERR
fi
ensure_fstab_entry
xfs_info "${DATA_DIR}" | grep -q 'reflink=1' || die "reflink 不可用（XFS reflink=0）"
note "数据盘就绪: $(xfs_info "${DATA_DIR}" | head -1)"

# 3. preflight + install（install.sh 要求 root；TLS 本地明文回退——本机低风险环境）
# 明文 gRPC 回退必须显式确认：prod 误跑不得静默把 TLS 关掉（M-08 告警纪律）
if [[ "${CDS_ALLOW_INSECURE_GRPC:-}" != "yes" ]]; then
  die "本机 bring-up 会以 CDS_CUBE_NODE_GRPC_TLS=false 走明文 gRPC 回退——必须显式 CDS_ALLOW_INSECURE_GRPC=yes 确认；生产环境请配 sidecar 证书后直接跑 install.sh"
fi
note "preflight..."
bash "${NODE_DIR}/preflight.sh" || die "preflight 失败"
note "install (CDS_CUBE_NODE_GRPC_TLS=false 本地明文，生产禁止)..."
CDS_CUBE_NODE_GRPC_TLS=false CUBE_UPSTREAM_DIR="${CUBE_UPSTREAM}" bash "${NODE_DIR}/install.sh" \
  || { printf 'FATAL: install.sh 失败——不继续 verify，也不打 BRINGUP_DONE\n' >&2; exit 1; }

# 4. verify（验收不过 = 非零退出：BRINGUP_DONE 不得在验收未过时说谎）
note "verify..."
bash "${NODE_DIR}/verify.sh" --json || die "verify 失败——节点未达验收口径"
echo "BRINGUP_DONE"
