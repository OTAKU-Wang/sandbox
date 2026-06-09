#!/bin/bash
# CDS k3d (k3s-in-Docker) Deployment Script — 中国网络优化
# 使用渡鸦镜像加速 Docker Hub 拉取
# Usage: bash scripts/install-k3s.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
K8S_DIR="$PROJECT_DIR/k8s"
K3D="${K3D:-/home/kratos/.local/bin/k3d}"
KUBECTL="kubectl"

# 渡鸦镜像 (中国 Docker Hub 加速)
RAVEN_MIRROR="docker.1ms.run"

echo "=== CDS k3d Deployment (渡鸦镜像加速) ==="
echo "Project: $PROJECT_DIR"
echo "Mirror: $RAVEN_MIRROR"
echo ""

# ── Step 1: Check k3d ──────────────────────────────────────────
if [ ! -f "$K3D" ] || [ ! -x "$K3D" ]; then
    echo "[1/7] Installing k3d..."
    curl -sL "https://github.com/k3d-io/k3d/releases/download/v5.7.5/k3d-linux-amd64" -o "$K3D"
    chmod +x "$K3D"
fi
echo "[1/7] k3d: $($K3D version 2>&1 | head -1)"

# ── Step 2: Create k3d cluster ────────────────────────────────
echo "[2/7] Creating k3d cluster..."
CLUSTER_NAME="cds-cluster"

if $K3D cluster list 2>/dev/null | grep -q "$CLUSTER_NAME"; then
    echo "  Cluster $CLUSTER_NAME already exists, starting..."
    $K3D cluster start "$CLUSTER_NAME" 2>/dev/null || true
else
    echo "  Creating cluster with Raven mirror..."
    $K3D cluster create "$CLUSTER_NAME" \
        --servers 1 \
        --agents 0 \
        --port "30080:30080@server:0" \
        --port "30001:30001@server:0" \
        --port "30082:30082@server:0" \
        --k3s-arg "--disable=traefik@server:0" \
        --registry-config "$K8S_DIR/registries.yaml" \
        --wait
    echo "  Cluster created"
fi

# ── Step 3: Get kubeconfig ────────────────────────────────────
echo "[3/7] Configuring kubectl..."
$K3D kubeconfig merge "$CLUSTER_NAME" --kubeconfig-switch-context 2>/dev/null || true
export KUBECONFIG="$HOME/.kube/config"

# Wait for node
echo "  Waiting for node ready..."
for i in $(seq 1 30); do
    if kubectl get nodes 2>/dev/null | grep -q " Ready"; then
        echo "  Node ready"
        break
    fi
    sleep 2
done

# ── Step 4: Create namespace & secrets ─────────────────────────
echo "[4/7] Deploying namespace, secrets, configmaps..."
kubectl apply -f "$K8S_DIR/namespace.yaml"
kubectl apply -f "$K8S_DIR/secrets.yaml"
kubectl apply -f "$K8S_DIR/configmaps.yaml"

# ── Step 5: Deploy middleware ──────────────────────────────────
echo "[5/7] Deploying middleware components..."
for manifest in postgres.yaml redis.yaml minio.yaml vault.yaml clickhouse.yaml opa.yaml; do
    if [ -f "$K8S_DIR/$manifest" ]; then
        echo "  Applying $manifest..."
        kubectl apply -f "$K8S_DIR/$manifest"
    fi
done

echo "  Waiting for middleware pods (timeout: 180s)..."
kubectl wait --for=condition=ready pod -l app -n cds-system --timeout=180s 2>/dev/null || echo "  Some pods still starting..."

# ── Step 6: Build & import CDS image ──────────────────────────
echo "[6/7] Building CDS API image..."
cd "$PROJECT_DIR"

if [ -f "Dockerfile.api" ]; then
    docker build -t cds-api:latest -f Dockerfile.api .
else
    echo "  Dockerfile.api not found, skipping app build"
fi

# Import into k3d
if docker image inspect cds-api:latest &>/dev/null; then
    echo "  Importing image into k3d..."
    $K3D image import cds-api:latest -c "$CLUSTER_NAME" 2>/dev/null || echo "  Import skipped"
fi

# ── Step 7: Deploy CDS app ────────────────────────────────────
echo "[7/7] Deploying CDS API..."
if [ -f "$K8S_DIR/cds-app.yaml" ]; then
    kubectl apply -f "$K8S_DIR/cds-app.yaml"
    echo "  Waiting for CDS API pod..."
    kubectl wait --for=condition=ready pod -l app=cds-api -n cds-system --timeout=120s 2>/dev/null || echo "  CDS API still starting..."
fi

# ── Status ─────────────────────────────────────────────────────
echo ""
echo "=== Deployment Status ==="
kubectl get pods -n cds-system 2>/dev/null
echo ""
echo "=== Services ==="
kubectl get svc -n cds-system 2>/dev/null
echo ""
echo "=== Access Points ==="
echo "CDS API:       http://localhost:30080"
echo "MinIO Console: http://localhost:30001"
echo "Vault:         http://localhost:30082"
echo ""
echo "=== k3d deployment complete ==="
