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

for tool in docker mkfs.ext4 e2fsck sha256sum truncate losetup mount umount; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "ERROR: required tool not found: $tool" >&2
        exit 3
    fi
done

if ! docker info >/dev/null 2>&1; then
    echo "ERROR: docker daemon not reachable" >&2
    exit 3
fi

mkdir -p "$OUT_DIR"
WORK="$(mktemp -d /tmp/cube-rootfs-convert.XXXXXX)"
CTR=""
LOOP=""
MNT=""

cleanup() {
    [ -n "$MNT" ] && mountpoint -q "$MNT" && umount "$MNT" || true
    [ -n "$LOOP" ] && losetup -d "$LOOP" 2>/dev/null || true
    [ -n "$CTR" ] && docker rm -f "$CTR" >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT

echo "[convert] $IMAGE_REF → ext4 (workdir $WORK)" >&2

# 1. Materialize the image rootfs.
CTR="$(docker create "$IMAGE_REF")"
docker export "$CTR" > "$WORK/rootfs.tar"
mkdir -p "$WORK/rootfs"
tar -xf "$WORK/rootfs.tar" -C "$WORK/rootfs"

# 2. Size the ext4 image: used size * 4/3 + 64MB headroom, min 256MB.
USED_KB="$(du -sk "$WORK/rootfs" | awk '{print $1}')"
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
if ! mount -o loop "$EXT4" "$MNT" 2>"$WORK/mount.err"; then
    cat "$WORK/mount.err" >&2
    echo "ERROR: loop mount failed (build host needs CAP_SYS_ADMIN / /dev/loop*)" >&2
    exit 4
fi
cp -a "$WORK/rootfs/." "$MNT/"
sync
umount "$MNT"
losetup -d "$LOOP"
LOOP=""
MNT=""

# 4. Filesystem check.
e2fsck -f -y "$EXT4" >/dev/null

# 5. Register: artifact + sha256 + size + probe config (C4: HTTP 2xx = READY).
mkdir -p "$OUT_DIR"
mv "$EXT4" "$OUT_DIR/artifact.ext4"
SHA256="$(sha256sum "$OUT_DIR/artifact.ext4" | awk '{print $1}')"
SIZE_BYTES="$(stat -c %s "$OUT_DIR/artifact.ext4")"

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
