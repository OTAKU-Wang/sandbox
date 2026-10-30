#!/usr/bin/env bash
# scripts/cube-rootfs-convert.sh — SS-10 CB-05 fallback build tool (M1).
#
# 红线 2: this tool runs on an INDEPENDENT build machine (the CDS build
# host), never on a compute node. It converts a CDS scenario image into a
# rootfs ext4 artifact for cube template distribution:
#
#   docker export → populate → mkfs.ext4 → e2fsck → artifact + sha256 + size
#
# Replaces the upstream TC build chain (CubeTemplateCenter/pkg/{build,image}),
# which is a Go service coupled to the CubeTemplateCenter daemon (M1 spike
# verdict: not reusable as a script).
#
# Usage:
#   cube-rootfs-convert.sh <image_ref> <out_dir>
#
# Output: <out_dir>/artifact.ext4 + one JSON object on stdout:
#   {"artifact_path": ..., "sha256": ..., "size_bytes": ..., "probe_config": ...}
#
# The probe config travels with the template registry row (C4 readiness
# semantics: HTTP probe 2xx = READY).
#
# Fails hard (non-zero exit) on any missing prerequisite — no simulated
# artifact, no silent fallback (诚实降级).

set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "usage: $0 <image_ref> <out_dir>" >&2
    exit 2
fi

IMAGE_REF="$1"
OUT_DIR="$2"

# IMAGE_REF 会进 docker 参数位：先拒绝选项形状（- 开头会被当 flag），调用位再以 -- 隔离
case "$IMAGE_REF" in
    -*) echo "ERROR: image ref must not start with '-': $IMAGE_REF" >&2; exit 2 ;;
esac

for c in docker tar mkfs.ext4 e2fsck sha256sum stat truncate losetup mount umount df flock python3; do
    if ! command -v "$c" >/dev/null 2>&1; then
        echo "ERROR: required tool not found: $c" >&2
        exit 3
    fi
done

if ! docker info >/dev/null 2>&1; then
    echo "ERROR: docker daemon not reachable" >&2
    exit 3
fi

mkdir -p "$OUT_DIR"
# 同一 OUT_DIR 的并发构建必须加锁：发布靠同 FS rename 原子替换，两条流水线互踩会产孤儿临时件
exec 9>"$OUT_DIR/.cube-rootfs-convert.lock"
if ! flock -n 9; then
    echo "ERROR: another cube-rootfs-convert build holds $OUT_DIR" >&2
    exit 5
fi
WORK="$(mktemp -d /tmp/cube-rootfs-convert.XXXXXX)"
# 工作盘空间预检：/tmp 常是 tmpfs（size≈RAM/2），近满必然中途炸——提前用 df -Pk 拒绝
for d in "$WORK" "$OUT_DIR"; do
    avail_kb="$(df -Pk "$d" | awk 'NR==2 {print $4}')"
    if [ "${avail_kb:-0}" -lt 262144 ]; then
        echo "ERROR: less than 256MiB free on $d (avail_kb=${avail_kb:-?})" >&2
        exit 4
    fi
done
CTR=""
LOOP=""
MNT=""

cleanup() {
    # 不得 rm -rf 活挂载点：以 /proc/mounts 为准，先正常 umount，瞬时 busy 用 umount -l 兜底
    if [ -n "$MNT" ] && grep -q " $MNT " /proc/mounts 2>/dev/null; then
        umount "$MNT" 2>/dev/null || umount -l "$MNT" 2>/dev/null || true
    fi
    [ -n "$LOOP" ] && losetup -d "$LOOP" 2>/dev/null || true
    [ -n "$CTR" ] && docker rm -f "$CTR" >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT

echo "[convert] $IMAGE_REF → ext4 (workdir $WORK)" >&2

# 1. Materialize the image rootfs.
CTR="$(docker create -- "$IMAGE_REF")"
docker export "$CTR" > "$WORK/rootfs.tar"
# export 成功后立即回收容器，不拖到 trap
docker rm -f "$CTR" >/dev/null 2>&1 || true
CTR=""
mkdir -p "$WORK/rootfs"
tar -xf "$WORK/rootfs.tar" -C "$WORK/rootfs"

# 2. Size the ext4 image: used size * 4/3 + 64MB headroom, min 256MB.
USED_KB="$(du -sk "$WORK/rootfs" | awk '{print $1}')"
# 近空 rootfs（export 失败/拿错镜像）必须拒绝：绝不发布一个看起来成功的空 artifact
if [ "$USED_KB" -lt 10240 ]; then
    echo "ERROR: exported rootfs is empty or near-empty (used=${USED_KB}KB) — refusing to publish" >&2
    exit 4
fi
USED_MB=$(( (USED_KB + 1023) / 1024 ))
SIZE_MB=$(( (USED_MB * 4) / 3 + 64 ))
[ "$SIZE_MB" -lt 256 ] && SIZE_MB=256
EXT4="$WORK/artifact.ext4"
truncate -s "${SIZE_MB}M" "$EXT4"
mkfs.ext4 -q -F "$EXT4"

# 3. Populate via loop mount (requires CAP_SYS_ADMIN on the build host).
if ! LOOP="$(losetup -f --show "$EXT4" 2>"$WORK/losetup.err")"; then
    cat "$WORK/losetup.err" >&2
    echo "ERROR: loop device setup failed (build host needs CAP_SYS_ADMIN / /dev/loop*)" >&2
    exit 4
fi
MNT="$WORK/mnt"
mkdir -p "$MNT"
# EXT4 已经由 losetup 关联到 $LOOP；这里必须挂 $LOOP 本身，再 -o loop 会二次关联（双挂载点）
if ! mount "$LOOP" "$MNT" 2>"$WORK/mount.err"; then
    cat "$WORK/mount.err" >&2
    echo "ERROR: loop mount failed (build host needs CAP_SYS_ADMIN / /dev/loop*)" >&2
    exit 4
fi
cp -a "$WORK/rootfs/." "$MNT/"
sync
if ! umount "$MNT" 2>/dev/null; then
    # 瞬时 busy：lazy 兜底后以 /proc/mounts 复核真正解除（lazy 是异步的）
    umount -l "$MNT"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        grep -q " $MNT " /proc/mounts 2>/dev/null || break
        sleep 1
    done
    if grep -q " $MNT " /proc/mounts 2>/dev/null; then
        echo "ERROR: $MNT still mounted after lazy umount" >&2
        exit 4
    fi
fi
MNT=""
losetup -d "$LOOP"
LOOP=""

# 4. Filesystem check — 输出落盘可诊断（rc 0=clean, 1=errors corrected 均可接受）。
e2fsck_rc=0
e2fsck -f -y "$EXT4" >"$WORK/e2fsck.log" 2>&1 || e2fsck_rc=$?
if [ "$e2fsck_rc" -gt 1 ]; then
    cat "$WORK/e2fsck.log" >&2
    echo "ERROR: e2fsck failed (rc=$e2fsck_rc), see log above" >&2
    exit 4
fi

# 5. Register: artifact + sha256 + size + probe config (C4: HTTP 2xx = READY).
# 发布原子性：先以 OUT_DIR 内临时名 .artifact.ext4.* 落地（同 FS），算完 sha/size
# 再同 FS rename 原子替换 artifact.ext4 —— 读取方永远不会看到半成品。
TMP_ART="$(mktemp "$OUT_DIR/.artifact.ext4.XXXXXX")"
mv "$EXT4" "$TMP_ART"
SHA256="$(sha256sum "$TMP_ART" | awk '{print $1}')"
SIZE_BYTES="$(stat -c %s "$TMP_ART")"
mv -f "$TMP_ART" "$OUT_DIR/artifact.ext4"

python3 - "$OUT_DIR/artifact.ext4" "$SHA256" "$SIZE_BYTES" <<'PY'
import json, sys
path, sha, size = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({
    "artifact_path": path,
    "sha256": sha,
    "size_bytes": int(size),
    "probe_config": {"type": "http", "port": 80, "path": "/", "expected_status": "2xx"},
}))
PY
