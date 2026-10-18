#!/usr/bin/env bash
# Generates /root/cds.env with strong random secrets (openssl). Usage:
#   make_env.sh <OUT> [CUBE_DP] [CUBE_TLS]   (defaults: false false)
# Guards (batch-87 claims): rand failures must abort (a failed command
# substitution inside a heredoc is invisible to set -e — cat is the command
# whose exit code counts), boolean args are whitelisted before interpolation,
# the env file is written atomically with 0600 (never follows symlinks in
# place), and every secret is verified non-empty before "written" is printed.
set -euo pipefail
OUT="${1:-/root/cds.env}"
CUBE_DP="${2:-false}"
CUBE_TLS="${3:-false}"
case "$CUBE_DP" in true|false) ;; *) echo "FATAL: CUBE_DP must be true/false" >&2; exit 1;; esac
case "$CUBE_TLS" in true|false) ;; *) echo "FATAL: CUBE_TLS must be true/false" >&2; exit 1;; esac

rand() { # <bytes> — hard-fails on empty output / openssl failure
  local v
  v="$(openssl rand -hex "$1")" || { echo "FATAL: openssl rand failed" >&2; exit 1; }
  [ -n "$v" ] || { echo "FATAL: openssl rand returned empty" >&2; exit 1; }
  printf '%s' "$v"
}

umask 077
TMP="$(mktemp "${OUT}.XXXXXX")"
trap 'rm -f "$TMP"' EXIT
{
  printf 'POSTGRES_PASSWORD=%s\n'        "$(rand 16)"
  printf 'REDIS_PASSWORD=%s\n'          "$(rand 16)"
  printf 'CLICKHOUSE_PASSWORD=%s\n'     "$(rand 16)"
  printf 'MINIO_ROOT_USER=%s\n'         'cds_minio_admin'
  printf 'MINIO_ROOT_PASSWORD=%s\n'     "$(rand 16)"
  printf 'VAULT_TOKEN=%s\n'             "$(rand 16)"
  printf 'CDS_JWT_SECRET_KEY=%s\n'      "$(rand 32)"
  printf 'CDS_KMS_SERVICE_TOKEN=%s\n'   "$(rand 16)"
  printf 'CDS_CUBE_AUDIT_SERVICE_TOKEN=%s\n' "$(rand 16)"
  printf 'CDS_CUBE_TEMPLATE_SIGNING_KEY=%s\n' "$(rand 32)"
  # remote_deploy.sh fail-fast requires both: MFA is enabled below, so its
  # at-rest key must exist, and prod posture refuses an empty column DEK.
  printf 'CDS_COLUMN_ENCRYPTION_KEY=%s\n' "$(rand 16)"
  printf 'CDS_MFA_SECRET_ENCRYPTION_KEY=%s\n' "$(rand 16)"
  printf 'CDS_CUBE_DATAPLANE_ENABLED=%s\n' "$CUBE_DP"
  printf 'CDS_CUBE_NODE_GRPC_TLS=%s\n'    "$CUBE_TLS"
  printf 'CDS_SANDBOX_LEGACY_ENABLED=false\n'
  printf 'CDS_AUTH_MFA_ENABLED=true\n'
} > "$TMP"

# verify-before-publish: every secret line must carry a value
while IFS='=' read -r k v; do
  case "$k" in
    *_PASSWORD|*_TOKEN|*_KEY)
      [ -n "$v" ] || { echo "FATAL: $k is empty in $TMP" >&2; exit 1; };;
  esac
done < "$TMP"
chmod 600 "$TMP"
mv -f "$TMP" "$OUT"
trap - EXIT
echo "env written: $OUT"
