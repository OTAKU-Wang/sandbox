#!/bin/bash
# k3d kubectl wrapper — runs kubectl inside the k3d server container
# Usage: bash scripts/k3d-kubectl.sh get pods -n cds-system
#   K3D_CONTAINER / CLUSTER_NAME 可覆盖容器名（多集群开发机不再写死 cds-cluster）
set -euo pipefail

command -v docker >/dev/null 2>&1 || { echo "ERROR: docker not found" >&2; exit 1; }

CLUSTER_NAME="${CLUSTER_NAME:-cds-cluster}"
K3D_CONTAINER="${K3D_CONTAINER:-k3d-${CLUSTER_NAME}-server-0}"

# 容器不存在/没起来时 docker exec 会抛底层错误码；先给出可读的显式失败
if [ "$(docker inspect -f '{{.State.Running}}' "$K3D_CONTAINER" 2>/dev/null || echo missing)" != "true" ]; then
    echo "ERROR: k3d container '$K3D_CONTAINER' not found or not running (CLUSTER_NAME=$CLUSTER_NAME)" >&2
    echo "       先跑: bash scripts/install-k3s.sh (CDS_K3S_MODE=k3d) 或修正 K3D_CONTAINER" >&2
    exit 1
fi

# -i 必须带：kubectl apply -f - 之类从 stdin 喂资源的用法很常见
exec docker exec -i "$K3D_CONTAINER" kubectl "$@"
