#!/usr/bin/env bash
# node_bringup.sh — SS-14 RT-07：本机 Cube 计算节点一键 bring-up（需 sudo）
#
# 用法: sudo bash deploy/cube-node/node_bringup.sh
# 产物: /data/cubelet (XFS, reflink=1, 30G loop) + cubelet 计算节点（上游 install-compute.sh）
#       + CDS 节点组件（TLS proxy / 审计 tailer / 卷插件，见 install.sh 头注）
# 幂等: 可重跑（loop/挂载存在则复用；安装器 auto 模式升级）
#
# 前置自检（脚本内执行）: /dev/kvm 在位、loop 设备可用、xfsprogs 在场。
set -euo pipefail

CUBE_UPSTREAM="${CUBE_UPSTREAM_DIR:-/home/kratos/Documents/workspace/CubeSandbox}"
DATA_DIR="/data/cubelet"
LOOP_IMG="/var/lib/cds-cube/cubelet.img"
SIZE_GB=30

if [[ $EUID -ne 0 ]]; then echo "FATAL: 需要 root（sudo bash $0）"; exit 1; fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NODE_DIR="$REPO_ROOT/deploy/cube-node"

# 1. 前置
[[ -e /dev/kvm ]] || { echo "FATAL: /dev/kvm 不存在（内核未启用 KVM）"; exit 1; }
command -v xfs_info >/dev/null || { echo "FATAL: xfsprogs 未安装（apt install xfsprogs）"; exit 1; }
command -v blkid >/dev/null || { echo "FATAL: blkid 不可用（util-linux）——无法判断镜像是否已建盘，拒跑"; exit 1; }
[[ -d "$CUBE_UPSTREAM" ]] || { echo "FATAL: 上游副本不在 $CUBE_UPSTREAM"; exit 1; }

# 2. 数据盘（XFS loop，幂等）
if ! mountpoint -q "$DATA_DIR"; then
  mkdir -p /var/lib/cds-cube "$DATA_DIR"
  if [[ ! -f "$LOOP_IMG" ]]; then
    echo "[bringup] 创建 ${SIZE_GB}G 稀疏 loop 镜像 $LOOP_IMG"
    truncate -s "${SIZE_GB}G" "$LOOP_IMG"
  fi
  # 已有文件系统 = 已含数据：只重挂，绝不再建盘（重跑销毁 /data/cubelet = 红线）
  EXISTING_FSTYPE="$(blkid -o value -s TYPE "$LOOP_IMG" 2>/dev/null || true)"
  if [[ -n "$EXISTING_FSTYPE" && "$EXISTING_FSTYPE" != "xfs" ]]; then
    echo "FATAL: $LOOP_IMG 已是 $EXISTING_FSTYPE 文件系统（非 XFS）——请人工确认后处理，本脚本不会覆写"; exit 1
  fi
  LOOP_DEV="$(losetup -f --show "$LOOP_IMG")"
  echo "$LOOP_DEV" > "/var/lib/cds-cube/loopdev"
  if [[ "$EXISTING_FSTYPE" == "xfs" ]]; then
    echo "[bringup] $LOOP_IMG 已有 XFS（数据保留）-> mount $DATA_DIR"
  else
    echo "[bringup] $LOOP_DEV -> mkfs.xfs -> mount $DATA_DIR"
    mkfs.xfs -f -K "$LOOP_DEV"
  fi
  mount "$LOOP_DEV" "$DATA_DIR"
  # 持久化（重启后自动挂载）：按镜像文件 + loop 选项记录；绝不写易失的 /dev/loopN 设备名
  if ! grep -q "$LOOP_IMG" /etc/fstab; then
    echo "$LOOP_IMG $DATA_DIR xfs loop,noatime 0 0" >> /etc/fstab
    echo "[bringup] fstab: $LOOP_IMG -> $DATA_DIR (xfs loop)"
  fi
  # 旧版本曾写 /dev/loopN 设备名（跨重启失配 → 重启后挂不上 → 落入重建盘路径）：清除
  if grep -qE "^/dev/loop[0-9]+ $DATA_DIR " /etc/fstab; then
    sed -i".bak-cds" -E "\#^/dev/loop[0-9]+ $DATA_DIR #d" /etc/fstab
    echo "[bringup] 已清除旧版遗留的 /dev/loopN fstab 行（备份 /etc/fstab.bak-cds）"
  fi
fi
xfs_info "$DATA_DIR" | grep -q 'reflink=1' || { echo "FATAL: reflink 不可用"; exit 1; }
echo "[bringup] 数据盘就绪: $(xfs_info "$DATA_DIR" | head -1)"

# 3. preflight + install（install.sh 要求 root；TLS 本地明文回退——本机低风险环境，大声告警形态）
echo "[bringup] preflight..."
bash "$NODE_DIR/preflight.sh" || { echo "FATAL: preflight 失败"; exit 1; }
echo "[bringup] install (CDS_CUBE_NODE_GRPC_TLS=false 本地明文，生产禁止)..."
CDS_CUBE_NODE_GRPC_TLS=false CUBE_UPSTREAM_DIR="$CUBE_UPSTREAM" bash "$NODE_DIR/install.sh"

# 4. verify
echo "[bringup] verify..."
bash "$NODE_DIR/verify.sh" --json
echo "BRINGUP_DONE"
