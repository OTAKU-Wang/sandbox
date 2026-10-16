#!/usr/bin/env bash
# Generates /root/cds.env with strong random secrets (openssl). Usage:
#   make_env.sh <OUT> [CUBE_DP] [CUBE_TLS]   (defaults: false false)
set -euo pipefail
OUT="${1:-/root/cds.env}"
CUBE_DP="${2:-false}"
CUBE_TLS="${3:-false}"
rand() { openssl rand -hex "$1"; }
umask 077
cat > "$OUT" <<EOF
POSTGRES_PASSWORD=$(rand 16)
REDIS_PASSWORD=$(rand 16)
CLICKHOUSE_PASSWORD=$(rand 16)
MINIO_ROOT_USER=cds_minio_admin
MINIO_ROOT_PASSWORD=$(rand 16)
VAULT_TOKEN=$(rand 16)
CDS_JWT_SECRET_KEY=$(rand 32)
CDS_KMS_SERVICE_TOKEN=$(rand 16)
CDS_CUBE_AUDIT_SERVICE_TOKEN=$(rand 16)
CDS_CUBE_TEMPLATE_SIGNING_KEY=$(rand 32)
CDS_CUBE_DATAPLANE_ENABLED=${CUBE_DP}
CDS_CUBE_NODE_GRPC_TLS=${CUBE_TLS}
CDS_SANDBOX_LEGACY_ENABLED=false
CDS_AUTH_MFA_ENABLED=true
CDS_MFA_SECRET_ENCRYPTION_KEY=
EOF
chmod 600 "$OUT"
echo "env written: $OUT"
