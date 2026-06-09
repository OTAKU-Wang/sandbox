#!/bin/sh
# Vault initialization and unsealing script for k3d deployment.
# Handles: init → unseal → enable Transit → create keys
#
# Usage (from host):
#   kubectl exec -n cds-system deploy/vault -- sh /vault/config/vault-init.sh
#
# Or copy into pod and run:
#   kubectl cp scripts/vault-init.sh cds-system/vault-xxx:/tmp/vault-init.sh
#   kubectl exec -n cds-system deploy/vault -- sh /tmp/vault-init.sh

set -e

VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
export VAULT_ADDR

# Helper: update base64-encoded k8s secret
update_k8s_secret() {
  local key="$1"
  local value="$2"
  if command -v kubectl >/dev/null 2>&1; then
    local b64
    b64=$(echo -n "$value" | base64)
    # Create JSON patch to add/replace a data field
    kubectl get secret cds-secrets -n cds-system -o json 2>/dev/null | \
      sed "s|\"$key\":\"[^\"]*\"|\"$key\":\"$b64\"|" | \
      kubectl apply -f - 2>/dev/null || true
  fi
}

echo "=== Waiting for Vault to be reachable ==="
for i in $(seq 1 30); do
  if vault status -address="$VAULT_ADDR" >/dev/null 2>&1; then
    echo "Vault is reachable"
    break
  fi
  if [ "$i" = "30" ]; then
    echo "ERROR: Vault not reachable after 30 attempts"
    exit 1
  fi
  echo "Waiting... ($i/30)"
  sleep 2
done

# Check initialization status
INIT_STATUS=$(vault status -address="$VAULT_ADDR" -format=json 2>/dev/null || echo '{}')
IS_INITIALIZED=$(echo "$INIT_STATUS" | grep -o '"initialized":[^,}]*' | cut -d: -f2 | tr -d ' ')
IS_SEALED=$(echo "$INIT_STATUS" | grep -o '"sealed":[^,}]*' | cut -d: -f2 | tr -d ' ')

# Step 1: Initialize if needed
if [ "$IS_INITIALIZED" != "true" ]; then
  echo "=== Initializing Vault (1 key share, 1 threshold) ==="
  INIT_OUTPUT=$(vault operator init -key-shares=1 -key-threshold=1 -format=json 2>/dev/null)
  if [ -z "$INIT_OUTPUT" ]; then
    echo "ERROR: Vault init failed"
    exit 1
  fi

  # Extract unseal key and root token
  UNSEAL_KEY=$(echo "$INIT_OUTPUT" | grep -o '"unseal_keys_b64":\["[^"]*"' | cut -d'"' -f4)
  ROOT_TOKEN=$(echo "$INIT_OUTPUT" | grep -o '"root_token":"[^"]*"' | cut -d'"' -f4)

  echo "Vault initialized. Root token: ${ROOT_TOKEN:0:8}..."

  # Save to Kubernetes secret
  update_k8s_secret "VAULT_TOKEN" "$ROOT_TOKEN"
  update_k8s_secret "VAULT_UNSEAL_KEY" "$UNSEAL_KEY"
  echo "Secrets updated in k8s"

  # Unseal immediately after init
  echo "=== Unsealing Vault ==="
  vault operator unseal -address="$VAULT_ADDR" "$UNSEAL_KEY" >/dev/null 2>&1
  echo "Vault unsealed"

  export VAULT_TOKEN="$ROOT_TOKEN"
else
  echo "Vault already initialized"

  # Unseal if sealed
  if [ "$IS_SEALED" = "true" ]; then
    echo "=== Vault is sealed, attempting unseal ==="

    UNSEAL_KEY=""
    if command -v kubectl >/dev/null 2>&1; then
      UNSEAL_KEY=$(kubectl get secret cds-secrets -n cds-system -o jsonpath='{.data.VAULT_UNSEAL_KEY}' 2>/dev/null | base64 -d 2>/dev/null || echo "")
    fi

    if [ -z "$UNSEAL_KEY" ]; then
      echo "ERROR: Vault is sealed and no unseal key found in k8s secret"
      echo "Manual unseal required: vault operator unseal <key>"
      exit 1
    fi

    vault operator unseal -address="$VAULT_ADDR" "$UNSEAL_KEY" >/dev/null 2>&1
    echo "Vault unsealed"
  else
    echo "Vault is already unsealed"
  fi

  # Get root token from k8s secret
  ROOT_TOKEN=""
  if command -v kubectl >/dev/null 2>&1; then
    ROOT_TOKEN=$(kubectl get secret cds-secrets -n cds-system -o jsonpath='{.data.VAULT_TOKEN}' 2>/dev/null | base64 -d 2>/dev/null || echo "")
  fi

  if [ -z "$ROOT_TOKEN" ]; then
    echo "WARNING: No root token found, using VAULT_TOKEN env"
    ROOT_TOKEN="${VAULT_TOKEN:-}"
  fi

  export VAULT_TOKEN="$ROOT_TOKEN"
fi

# Verify Vault is ready
echo "=== Verifying Vault status ==="
vault status -address="$VAULT_ADDR" || { echo "ERROR: Vault not ready"; exit 1; }

# Step 3: Enable Transit engine if not already enabled
echo "=== Enabling Transit engine ==="
vault secrets enable -address="$VAULT_ADDR" -path=cds-transit transit 2>/dev/null || echo "Transit engine already enabled"

# Step 4: Create keys
echo "=== Creating KEK key ==="
vault write -address="$VAULT_ADDR" cds-transit/keys/cds-kek type=aes256-gcm96 2>/dev/null || echo "KEK key already exists"

echo "=== Creating session key ==="
vault write -address="$VAULT_ADDR" cds-transit/keys/cds-session type=aes256-gcm96 2>/dev/null || echo "Session key already exists"

echo "=== Listing Transit keys ==="
vault list -address="$VAULT_ADDR" cds-transit/keys 2>/dev/null || true

echo "=== Vault setup complete ==="
