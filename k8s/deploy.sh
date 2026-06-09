#!/bin/bash
# CDS k3s Deployment Script
# Usage: bash k8s/deploy.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KUBECTL="kubectl"

# Check if k3s kubectl is available
if command -v k3s &>/dev/null; then
    KUBECTL="k3s kubectl"
fi

echo "=== CDS k3s Deployment ==="
echo "Using kubectl: $KUBECTL"
echo ""

# Step 1: Create namespace
echo "[1/7] Creating namespace..."
$KUBECTL apply -f "$SCRIPT_DIR/namespace.yaml"

# Step 2: Create secrets
echo "[2/7] Creating secrets..."
$KUBECTL apply -f "$SCRIPT_DIR/secrets.yaml"

# Step 3: Create configmaps
echo "[3/7] Creating configmaps..."
$KUBECTL apply -f "$SCRIPT_DIR/configmaps.yaml"

# Step 4: Deploy PostgreSQL
echo "[4/7] Deploying PostgreSQL..."
$KUBECTL apply -f "$SCRIPT_DIR/postgres.yaml"

# Step 5: Deploy Redis
echo "[5/7] Deploying Redis..."
$KUBECTL apply -f "$SCRIPT_DIR/redis.yaml"

# Step 6: Deploy MinIO + Vault + ClickHouse + OPA
echo "[6/7] Deploying infrastructure services..."
$KUBECTL apply -f "$SCRIPT_DIR/minio.yaml"
$KUBECTL apply -f "$SCRIPT_DIR/vault.yaml"
$KUBECTL apply -f "$SCRIPT_DIR/clickhouse.yaml"
$KUBECTL apply -f "$SCRIPT_DIR/opa.yaml"

# Step 7: Wait for all pods
echo "[7/7] Waiting for pods to be ready..."
$KUBECTL wait --for=condition=ready pod -l app=postgres -n cds-system --timeout=120s || true
$KUBECTL wait --for=condition=ready pod -l app=redis -n cds-system --timeout=60s || true
$KUBECTL wait --for=condition=ready pod -l app=minio -n cds-system --timeout=60s || true
$KUBECTL wait --for=condition=ready pod -l app=vault -n cds-system --timeout=60s || true
$KUBECTL wait --for=condition=ready pod -l app=clickhouse -n cds-system --timeout=60s || true
$KUBECTL wait --for=condition=ready pod -l app=opa -n cds-system --timeout=60s || true

# Step 8: Initialize and unseal Vault
echo "[8/8] Initializing Vault..."
VAULT_POD=$($KUBECTL get pods -n cds-system -l app=vault -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [ -n "$VAULT_POD" ]; then
  $KUBECTL cp "$SCRIPT_DIR/../scripts/vault-init.sh" "cds-system/$VAULT_POD:/tmp/vault-init.sh" 2>/dev/null || true
  $KUBECTL exec -n cds-system "$VAULT_POD" -- sh /tmp/vault-init.sh 2>&1 || echo "Vault init script failed (may need manual init)"
fi

echo ""
echo "=== Deployment Status ==="
$KUBECTL get pods -n cds-system
echo ""
echo "=== Services ==="
$KUBECTL get svc -n cds-system
echo ""
echo "=== CDS Infrastructure Deployed ==="
