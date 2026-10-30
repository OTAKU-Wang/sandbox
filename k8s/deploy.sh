#!/bin/bash
# CDS k3s Deployment Script
# Usage: bash k8s/deploy.sh
#
# 有意手动（不是遗漏）：本脚本 apply 10 个清单，kafka.yaml / debezium.yaml
# （CDC 链路：zookeeper + kafka + debezium-connect）**不**属于本脚本的 apply 集。
# 理由：CDC 是 opt-in 特性——只有把 cds-secrets 里 CDS_CDC_KAFKA_BROKERS 指到
# kafka:9092 时才有意义；默认拉起会白付 3 组 JVM（zoo/kafka/connect）的内存，
# 且 debezium connector 配置属于部署方决策，不该被一条通用 deploy 替你拍板。
# 需要 CDC 时按序手动 apply：
#   $KUBECTL apply -f k8s/kafka.yaml      # zookeeper + kafka（先等 Ready）
#   $KUBECTL apply -f k8s/debezium.yaml   # connect + connector 注册
# 然后把 cds-secrets 的 CDS_CDC_KAFKA_BROKERS 设为 "kafka:9092" 并
#   $KUBECTL rollout restart deploy/cds-api -n cds-system
# 详见 k8s/README.md「CDC：有意手动」。

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
echo "[1/9] Creating namespace..."
$KUBECTL apply -f "$SCRIPT_DIR/namespace.yaml"

# Step 2: Create secrets
echo "[2/9] Creating secrets..."
$KUBECTL apply -f "$SCRIPT_DIR/secrets.yaml"

# Step 3: Create configmaps
echo "[3/9] Creating configmaps..."
$KUBECTL apply -f "$SCRIPT_DIR/configmaps.yaml"

# Step 4: Deploy PostgreSQL
echo "[4/9] Deploying PostgreSQL..."
$KUBECTL apply -f "$SCRIPT_DIR/postgres.yaml"

# Step 5: Deploy Redis
echo "[5/9] Deploying Redis..."
$KUBECTL apply -f "$SCRIPT_DIR/redis.yaml"

# Step 6: Deploy MinIO + Vault + ClickHouse + OPA
echo "[6/9] Deploying infrastructure services..."
$KUBECTL apply -f "$SCRIPT_DIR/minio.yaml"
$KUBECTL apply -f "$SCRIPT_DIR/vault.yaml"
$KUBECTL apply -f "$SCRIPT_DIR/clickhouse.yaml"
$KUBECTL apply -f "$SCRIPT_DIR/opa.yaml"

# Step 7: Deploy API service
echo "[7/9] Deploying CDS API..."
$KUBECTL apply -f "$SCRIPT_DIR/cds-app.yaml"

# Step 8: Wait for all pods
echo "[8/9] Waiting for pods to be ready..."
$KUBECTL wait --for=condition=ready pod -l app=postgres -n cds-system --timeout=120s || true
$KUBECTL wait --for=condition=ready pod -l app=redis -n cds-system --timeout=60s || true
$KUBECTL wait --for=condition=ready pod -l app=minio -n cds-system --timeout=60s || true
$KUBECTL wait --for=condition=ready pod -l app=vault -n cds-system --timeout=60s || true
$KUBECTL wait --for=condition=ready pod -l app=clickhouse -n cds-system --timeout=60s || true
$KUBECTL wait --for=condition=ready pod -l app=opa -n cds-system --timeout=60s || true
$KUBECTL wait --for=condition=ready pod -l app=cds-api -n cds-system --timeout=120s || true
$KUBECTL wait --for=condition=ready pod -l app=cds-frontend -n cds-system --timeout=120s || true

# Step 9: Initialize and unseal Vault
echo "[9/9] Initializing Vault..."
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
