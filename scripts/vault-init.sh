#!/bin/sh
# Vault initialization and unsealing script for k3d deployment.
# Handles: init → unseal → enable Transit → create keys
# 必须保持 POSIX /bin/sh（vault 容器内是 busybox ash，无 bash 子串扩展）。
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

die() {
  echo "ERROR: $*" >&2
  exit 1
}

# Helper: update base64-encoded k8s secret (kubectl replace + 回读校验)
# 返回 0 = secret 里确实已是新值；非 0 = 没写进去（调用方必须当失败处理）
update_k8s_secret() {
  _key="$1"
  _value="$2"
  command -v kubectl >/dev/null 2>&1 || return 1
  _b64=$(printf '%s' "$_value" | base64 | tr -d '\n')
  # kubectl -o json 是缩进美化格式："key": "..." —— sed 模式必须容忍冒号两侧空白
  if ! _json=$(kubectl get secret cds-secrets -n cds-system -o json); then
    return 1
  fi
  _new=$(printf '%s' "$_json" | sed "s|\"$_key\"[[:space:]]*:[[:space:]]*\"[^\"]*\"|\"$_key\": \"$_b64\"|")
  if ! printf '%s' "$_new" | grep -q "\"$_key\"[[:space:]]*:[[:space:]]*\"$_b64\""; then
    # data 里根本没有这个键：sed 原地不动会"静默成功"，凭据实际从未落盘。
    # 显式注入 data 块首。
    _new=$(printf '%s' "$_json" | sed "s|\"data\"[[:space:]]*:[[:space:]]*{|\"data\": {\n    \"$_key\": \"$_b64\",|")
  fi
  if ! printf '%s' "$_new" | kubectl replace -f - >/dev/null; then
    return 1
  fi
  # 回读校验：replace 被 webhook 拒绝/部分写入都逃不过这一眼
  _got=$(kubectl get secret cds-secrets -n cds-system -o "jsonpath={.data.$_key}" || echo "")
  [ "$_got" = "$_b64" ]
}

echo "=== Waiting for Vault to be reachable ==="
# vault status 退出码: 0=可达且 unsealed, 2=可达但 sealed —— 两者都是"可达"。
# 只认 rc=0 会在 sealed 状态（恰恰最需要本脚本的状态）误判为不可达。
reachable() {
  _rc=0
  vault status -address="$VAULT_ADDR" >/dev/null 2>&1 || _rc=$?
  case "$_rc" in
    0|2) return 0 ;;
  esac
  if command -v curl >/dev/null 2>&1; then
    _body=$(curl -fsS -m 3 "$VAULT_ADDR/v1/sys/health" 2>/dev/null || true)
    case "$_body" in
      *'"sealed":true'*|*sealedok*) return 0 ;;
    esac
  fi
  return 1
}

i=1
while [ "$i" -le 30 ]; do
  if reachable; then
    echo "Vault is reachable"
    break
  fi
  if [ "$i" = "30" ]; then
    die "Vault not reachable at $VAULT_ADDR after 30 attempts"
  fi
  echo "Waiting... ($i/30)"
  sleep 2
  i=$((i+1))
done

# Check initialization status —— 解析失败必须报错：把"解析不出来"默认为
# 未初始化，会对已初始化实例再跑 operator init（Vault 会拒绝，但更糟的是
# 半解析状态会跳过 unseal/转密钥等必要步骤）。
INIT_STATUS=$(vault status -address="$VAULT_ADDR" -format=json 2>/dev/null || echo '{}')
IS_INITIALIZED=$(echo "$INIT_STATUS" | grep -o '"initialized"[[:space:]]*:[[:space:]]*[^,}]*' | cut -d: -f2 | tr -d ' "')
IS_SEALED=$(echo "$INIT_STATUS" | grep -o '"sealed"[[:space:]]*:[[:space:]]*[^,}]*' | cut -d: -f2 | tr -d ' "')
case "$IS_INITIALIZED" in
  true|false) ;;
  *) die "cannot parse 'initialized' from vault status output (拒绝按默认值继续): $(echo "$INIT_STATUS" | head -c 200)" ;;
esac
case "$IS_SEALED" in
  true|false) ;;
  *) die "cannot parse 'sealed' from vault status output: $(echo "$INIT_STATUS" | head -c 200)" ;;
esac

post_unseal_check() {
  _rc=0
  vault status -address="$VAULT_ADDR" >/dev/null 2>&1 || _rc=$?
  if [ "$_rc" -eq 2 ]; then
    die "unseal 命令返回成功但实例 still sealed（threshold 未满足或 key 不对）"
  fi
  if [ "$_rc" -ne 0 ]; then
    die "unseal 后状态复核失败 (rc=$_rc)"
  fi
  echo "Vault unsealed (verified)"
}

# Step 1: Initialize if needed
if [ "$IS_INITIALIZED" != "true" ]; then
  echo "=== Initializing Vault (1 key share, 1 threshold) ==="
  # init 的 stderr 是唯一的失败原因来源，不得丢进 /dev/null
  INIT_ERR=$(mktemp /tmp/vault-init-err.XXXXXX)
  if ! INIT_OUTPUT=$(vault operator init -key-shares=1 -key-threshold=1 -format=json 2>"$INIT_ERR"); then
    echo "ERROR: vault operator init failed: $(cat "$INIT_ERR")" >&2
    rm -f "$INIT_ERR"
    exit 1
  fi
  rm -f "$INIT_ERR"
  if [ -z "$INIT_OUTPUT" ]; then
    die "Vault init returned empty output"
  fi

  # Extract unseal key and root token（单份额：解析不出来 = 稍后必然丢钥匙）
  UNSEAL_KEY=$(echo "$INIT_OUTPUT" | grep -o '"unseal_keys_b64"[[:space:]]*:[[:space:]]*\[[^]]*"[^"]*"' | grep -o '"[^"]*"$' | tr -d '"')
  ROOT_TOKEN=$(echo "$INIT_OUTPUT" | grep -o '"root_token"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*"root_token"[[:space:]]*:[[:space:]]*"//; s/"$//')
  if [ -z "$UNSEAL_KEY" ] || [ -z "$ROOT_TOKEN" ]; then
    die "cannot parse unseal key / root token out of init output —— 立即从 vault operator generate-root 或原始输出人工恢复，勿再重跑 init"
  fi

  # POSIX sh 没有 ${VAR:0:8}（dash/ash 下 Bad substitution）
  echo "Vault initialized. Root token: $(printf '%s' "$ROOT_TOKEN" | cut -c1-8)..."

  # Save to Kubernetes secret —— 单份额 unseal key，写不进去又没打印 = 数据永久不可恢复
  if ! update_k8s_secret "VAULT_TOKEN" "$ROOT_TOKEN" || \
     ! update_k8s_secret "VAULT_UNSEAL_KEY" "$UNSEAL_KEY"; then
    echo "ERROR: 无法写入 k8s secret cds-secrets。这是唯一一次拿到这些凭据，立即妥善保存：" >&2
    echo "VAULT_UNSEAL_KEY=$UNSEAL_KEY" >&2
    echo "VAULT_ROOT_TOKEN=$ROOT_TOKEN" >&2
    exit 1
  fi
  echo "Secrets updated in k8s (read-back verified)"

  # Unseal immediately after init（key 走 stdin，不进 argv/ps）
  echo "=== Unsealing Vault ==="
  printf '%s' "$UNSEAL_KEY" | vault operator unseal -address="$VAULT_ADDR" >/dev/null
  post_unseal_check

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
      die "Vault is sealed and no unseal key found in k8s secret. Manual unseal required: vault operator unseal <key>"
    fi

    printf '%s' "$UNSEAL_KEY" | vault operator unseal -address="$VAULT_ADDR" >/dev/null
    post_unseal_check
  else
    echo "Vault is already unsealed"
  fi

  # Get root token from k8s secret
  ROOT_TOKEN=""
  if command -v kubectl >/dev/null 2>&1; then
    ROOT_TOKEN=$(kubectl get secret cds-secrets -n cds-system -o jsonpath='{.data.VAULT_TOKEN}' 2>/dev/null | base64 -d 2>/dev/null || echo "")
  fi

  if [ -z "$ROOT_TOKEN" ]; then
    echo "WARNING: No root token found in k8s secret, falling back to VAULT_TOKEN env"
    ROOT_TOKEN="${VAULT_TOKEN:-}"
  fi

  # 空 token 继续跑 = 后面 enable/write 全部 403 还未必立刻可见，宁可现在停
  if [ -z "$ROOT_TOKEN" ]; then
    die "no usable root token (secret VAULT_TOKEN missing, VAULT_TOKEN env unset) —— 拒绝以空凭据继续"
  fi

  export VAULT_TOKEN="$ROOT_TOKEN"
fi

# Verify Vault is ready (sealed 时 rc=2 也算失败)
echo "=== Verifying Vault status ==="
vault status -address="$VAULT_ADDR" >/dev/null || die "Vault not ready (status rc=$?)"

# Step 3: Enable Transit engine —— 幂等分支只认 already-exists 类错误，其余硬失败
echo "=== Enabling Transit engine ==="
if ! _out=$(vault secrets enable -address="$VAULT_ADDR" -path=cds-transit transit 2>&1); then
  case "$_out" in
    *"path is already in use"*|*already*enabled*) echo "Transit engine already enabled" ;;
    *) die "enable transit failed: $_out" ;;
  esac
fi

# Step 4: Create keys
echo "=== Creating KEK key ==="
if ! _out=$(vault write -address="$VAULT_ADDR" cds-transit/keys/cds-kek type=aes256-gcm96 2>&1); then
  case "$_out" in
    *"path is already in use"*|*"already exists"*) echo "KEK key already exists" ;;
    *) die "create KEK key failed: $_out" ;;
  esac
fi

echo "=== Creating session key ==="
if ! _out=$(vault write -address="$VAULT_ADDR" cds-transit/keys/cds-session type=aes256-gcm96 2>&1); then
  case "$_out" in
    *"path is already in use"*|*"already exists"*) echo "Session key already exists" ;;
    *) die "create session key failed: $_out" ;;
  esac
fi

echo "=== Listing Transit keys ==="
vault list -address="$VAULT_ADDR" cds-transit/keys || echo "WARN: 无法列出 Transit keys（权限/网络），请人工核验"

echo "=== Vault setup complete ==="
