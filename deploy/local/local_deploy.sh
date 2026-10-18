#!/usr/bin/env bash
# CDS local node-track stack (SS-14 RT-07): all containers on --network host
# (host bridge networking is unavailable), ports shifted to avoid the
# CubeSandbox one-click stack (3306/6379/9000/9001/9443/9999).
# Cube dataplane ON, node gRPC plaintext (dev-only, loud warning at startup).
# Idempotent; secrets persist in /tmp/cds-local.env (never in the repo).
set -euo pipefail

# Strict argument parsing: unknown flags were silently ignored, and a stray
# `--` (the separator run() itself uses) swallowed --bootstrap — the deploy
# "succeeded" with no admin and no warning.
BOOTSTRAP_ADMIN=0
for _a in "$@"; do
  case "$_a" in
    --bootstrap) BOOTSTRAP_ADMIN=1 ;;
    *) echo "FATAL: unknown argument: $_a (usage: local_deploy.sh [--bootstrap])" >&2; exit 1 ;;
  esac
done

ENV_FILE=/tmp/cds-local.env
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [ ! -f "$ENV_FILE" ]; then
  {
    echo "POSTGRES_PASSWORD=$(openssl rand -hex 12)"
    echo "REDIS_PASSWORD=$(openssl rand -hex 12)"
    echo "CLICKHOUSE_PASSWORD=$(openssl rand -hex 12)"
    echo "MINIO_ROOT_PASSWORD=$(openssl rand -hex 12)"
    echo "VAULT_TOKEN=$(openssl rand -hex 16)"
    echo "CDS_JWT_SECRET_KEY=$(openssl rand -hex 32)"
    echo "CDS_KMS_SERVICE_TOKEN=$(openssl rand -hex 16)"
    echo "CDS_CUBE_AUDIT_SERVICE_TOKEN=$(openssl rand -hex 16)"
    echo "CDS_CUBE_TEMPLATE_SIGNING_KEY=$(openssl rand -hex 32)"
    echo "CDS_ADMIN_USER=cdsadmin"
    echo "CDS_ADMIN_PASS=$(openssl rand -base64 12 | tr -d '/+=' | head -c 16)!"
  } > "$ENV_FILE"
  echo "[local] generated $ENV_FILE (admin creds inside; /tmp only)"
fi
# /tmp is world-readable and this file holds every credential of the stack:
# permissions must not depend on the caller's umask (022 gave 644).
chmod 600 "$ENV_FILE"
set -a; source "$ENV_FILE"; set +a

run() {
  local name="$1" image="$2"; shift 2
  local args=() cmd=() seen=0 a
  for a in "$@"; do
    if [ "$seen" = "1" ]; then cmd+=("$a"); else if [ "$a" = "--" ]; then seen=1; else args+=("$a"); fi; fi
  done
  docker rm -f "$name" >/dev/null 2>&1 || true
  local _i _st=gone
  for _i in 1 2 3 4 5 6 7 8 9 10; do
    docker ps -q -f name="^${name}$" | grep -q . || { _st=gone; break; }
    sleep 1
    _st=present
  done
  [ "$_st" = gone ] || { echo "FATAL: $name still present after 10s teardown wait (crash-loop restart?); aborting before docker run"; exit 1; }
  docker run -d --name "$name" --network host --restart unless-stopped "${args[@]}" "$image" "${cmd[@]+"${cmd[@]}"}"
}

# Array-valued health probe: wait_healthy <name> <tries> <cmd...>. The old
# string form word-split AND glob-expanded the command in the host shell (a
# password containing * or ? was expanded against the host CWD), and it burned
# all tries on a crash-looping container instead of failing now.
wait_healthy() {
  local name="$1" tries="$2" i st; shift 2
  for ((i=1; i<=tries; i++)); do
    st=$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null || echo missing)
    case "$st" in
      exited|restarting|dead|missing)
        echo "FATAL: $name is $st while waiting for health:"; docker logs --tail 20 "$name" 2>&1 || true; exit 1;;
    esac
    docker exec "$name" "$@" >/dev/null 2>&1 && { echo "healthy: $name"; return 0; }
    sleep 2
  done
  echo "FATAL: $name not healthy after $((tries*2))s: $*"
  docker logs --tail 20 "$name" || true
  exit 1
}

wait_port() {
  local port="$1" tries="${2:-30}" i=0
  until curl -fsS "http://127.0.0.1:${port}/" >/dev/null 2>&1 || \
        timeout 2 bash -c "echo > /dev/tcp/127.0.0.1/${port}" 2>/dev/null; do
    i=$((i+1))
    if [ "$i" -ge "$tries" ]; then echo "FATAL: port $port not up after $i tries"; exit 1; fi
    sleep 2
  done
  echo "healthy: port $port"
}

echo "== middleware (host net, shifted ports) =="
run l-postgres postgres:16-alpine \
  -e POSTGRES_DB=cds -e POSTGRES_USER=cds -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  -v "l_cds_pgdata:/var/lib/postgresql/data"

run l-redis redis:7-alpine \
  -v "l_cds_redis:/data" \
  -- redis-server --port 6380 --requirepass "$REDIS_PASSWORD" --maxmemory 256mb --maxmemory-policy allkeys-lru

# ClickHouse: the official image does NOT recognize CLICKHOUSE_HTTP_PORT /
# CLICKHOUSE_PORT (they were silently ignored: the server kept listening on
# 8123/9000, the declared CDS_CLICKHOUSE_URL:8124 pointed at nothing, and
# nothing probed it until a business audit write failed). Ports move via the
# supported config.d mechanism instead, and the store is probed below.
CH_PORTS_FILE=$(mktemp /tmp/l_cds_ch_ports.XXXXXX.xml)
cat > "$CH_PORTS_FILE" <<'XMLEOF'
<clickhouse>
  <http_port>8124</http_port>
  <tcp_port>9005</tcp_port>
</clickhouse>
XMLEOF
run l-clickhouse clickhouse/clickhouse-server:24.3-alpine \
  -e CLICKHOUSE_DB=cds_audit -e CLICKHOUSE_USER=cds -e CLICKHOUSE_PASSWORD="$CLICKHOUSE_PASSWORD" \
  -v "l_cds_ch:/var/lib/clickhouse" \
  -v "${CH_PORTS_FILE}:/etc/clickhouse-server/config.d/ports.xml:ro"

run l-minio minio/minio:RELEASE.2025-01-20T14-49-07Z \
  -e MINIO_ROOT_USER=cds_minio_admin -e MINIO_ROOT_PASSWORD="$MINIO_ROOT_PASSWORD" \
  -v "l_cds_minio:/data" \
  -- server /data --address "127.0.0.1:9010" --console-address "127.0.0.1:9011"

run l-opa openpolicyagent/opa:latest \
  -- run --server --addr=0.0.0.0:8181 --log-level=info

run l-vault hashicorp/vault:1.17-flat \
  --cap-add IPC_LOCK \
  -e VAULT_DEV_ROOT_TOKEN_ID="$VAULT_TOKEN" -e VAULT_DEV_LISTEN_ADDRESS=0.0.0.0:8200

echo "== waiting for middleware =="
wait_healthy l-postgres 45 pg_isready -p 5432 -U cds
# redis-cli exits 0 even on WRONGPASS — grep the actual PONG.
wait_healthy l-redis 45 sh -c 'redis-cli -p 6380 -a "$1" --no-auth-warning ping | grep -q PONG' _ "$REDIS_PASSWORD"
wait_port 9010 45
wait_port 8181 30
wait_healthy l-vault 60 vault status -address=http://127.0.0.1:8200
# the audit store must answer BEFORE LOCAL_DEPLOY_DONE: cds-api never blocks
# on ClickHouse, so a dead audit path used to surface only at first write.
wait_healthy l-clickhouse 30 clickhouse-client --port 9005 --query "SELECT 1"
wait_port 8124 30

VROOT="$(docker exec l-vault printenv VAULT_DEV_ROOT_TOKEN_ID)"
if ! transit_out=$(docker exec -e VAULT_ADDR=http://127.0.0.1:8200 -e VAULT_TOKEN="$VROOT" \
  l-vault vault secrets enable -path=transit transit 2>&1); then
  printf '%s\n' "$transit_out" | grep -q 'already in use' \
    || { echo "FATAL: vault transit enable failed: $transit_out"; exit 1; }
fi
# verify the mount itself (a substring hit anywhere in `secrets list` output
# would have passed even with transit mounted elsewhere or not at all)
docker exec -e VAULT_ADDR=http://127.0.0.1:8200 -e VAULT_TOKEN="$VROOT" \
  l-vault vault secrets list -format=table | awk '{print $1}' | grep -qx 'transit/' \
  || { echo "FATAL: transit engine not mounted (verified via secrets list)"; exit 1; }

echo "== schema =="
# SS-14 RT-07: 会话结果卷 host_dir（cubelet bind mount 源, 必须宿主机存在）。
# 非 root 执行部署时 mkdir 会 Permission denied —— 如实报错, 不静默降级。
if ! mkdir -p /var/lib/cds/sandbox-results 2>/dev/null; then
  echo "FATAL: cannot create /var/lib/cds/sandbox-results (need root, or pre-create it); the node cubelet bind-mounts this dir for session result volumes"; exit 1; fi

docker run --rm --network host --env-file "$ENV_FILE" \
  -e ALEMBIC_DATABASE_URL="postgresql+asyncpg://cds:${POSTGRES_PASSWORD}@127.0.0.1:5432/cds" \
  cds-api:latest sh -c "python -m alembic upgrade head"

# SS-13/SS-14: 本机节点无 TEE 硬件（tee=none 如实披露, bare MicroVM 路径）——
# TEE quote 不可得, 证明门按部署策略关闭; TEE 部署保持默认 true。
echo "== api (cube dataplane on, node grpc plaintext dev) =="
run l-cds-api cds-api:latest \
  -e CDS_DEBUG=true \
  -e CDS_FEDERATION_ENABLED=false \
  -e CDS_COLUMN_ENCRYPTION_KEY="" \
  -e CDS_SM3_FALLBACK_ALLOWED=true \
  -e CDS_DATABASE_URL="postgresql+asyncpg://cds:${POSTGRES_PASSWORD}@127.0.0.1:5432/cds" \
  -e CDS_REDIS_URL="redis://:${REDIS_PASSWORD}@127.0.0.1:6380/0" \
  -e CDS_CLICKHOUSE_URL="http://127.0.0.1:8124/cds_audit" \
  -e CDS_OPA_URL="http://127.0.0.1:8181" \
  -e CDS_VAULT_ADDR="http://127.0.0.1:8200" \
  -e CDS_VAULT_TOKEN="$VAULT_TOKEN" \
  -e CDS_MINIO_ENDPOINT="127.0.0.1:9010" \
  -e CDS_MINIO_ACCESS_KEY="cds_minio_admin" \
  -e CDS_MINIO_SECRET_KEY="$MINIO_ROOT_PASSWORD" \
  -e CDS_JWT_SECRET_KEY="$CDS_JWT_SECRET_KEY" \
  -e CDS_KMS_SERVICE_TOKEN="$CDS_KMS_SERVICE_TOKEN" \
  -e CDS_DEV_SANDBOX_REQUIRE_CONTRACT="false" \
  -e CDS_AUTH_MFA_ENABLED="false" \
  -e CDS_AUTH_CERT_LOGIN="true" \
  -e CDS_CUBE_DATAPLANE_ENABLED="true" \
  -e CDS_LEGACY_L2_ENABLED="false" \
  -e CDS_SANDBOX_LEGACY_ENABLED="false" \
  -e CDS_KMS_REQUIRE_ATTESTATION=false \
  -e CDS_CUBE_NODE_GRPC_TLS="false" \
  -e CDS_CUBE_PINNED_RUNTIME_VERSION="b0c4cc33fbfccbacc1a72241918ef280e9a32772" \
  -e CDS_CUBE_NODE_TLS_PROXY_PORT="9443" \
  -e CDS_CUBE_RESULTS_DIR=/var/lib/cds/sandbox-results \
  -e CDS_CUBE_SANDBOX_IMAGE="127.0.0.1:5000/cds/cube-sandbox:latest" \
  -e CDS_CUBE_SANDBOX_STORAGE_MEDIA="" \
  -e CDS_CUBE_MASTER_ADDR="http://127.0.0.1:8089" \
  -e CDS_CUBE_VOLUME_PLUGIN_SOCKET="/run/cds-volume-plugin/cds.sock" \
  -v /run/cds-volume-plugin:/run/cds-volume-plugin \
  -v /var/lib/cds/sandbox-results:/var/lib/cds/sandbox-results \
  -e CDS_CUBE_DEK_LENGTH="16" \
  -e CDS_CUBE_AUDIT_SERVICE_TOKEN="$CDS_CUBE_AUDIT_SERVICE_TOKEN" \
  -e CDS_CUBE_TEMPLATE_SIGNING_KEY="$CDS_CUBE_TEMPLATE_SIGNING_KEY" \
  -e CDS_CUBE_TEMPLATE_URL_TTL_SECONDS="3600" \
  -e CDS_CUBE_TEMPLATE_DIST_RETRY="2" \
  -e CDS_RETENTION_JANITOR_ENABLED="true" \
  -e CDS_RETENTION_JANITOR_DRY_RUN="true" \
  -e CDS_MERKLE_PIPELINE_ENABLED="false" \
  -e CDS_EGRESS_AUDIT_ENABLED="true" \
  -e CDS_EGRESS_AUDIT_ENCRYPTION_ENABLED="true" \
  -e CDS_NODE_STALE_SECONDS="300" \
  -e CDS_RATE_LIMIT_ENABLED="true" \
  -v "l_cds_sandbox:/tmp/cds-sandbox" \
  -v "l_cds_sandbox_l0:/tmp/cds-sandbox-l0" \
  -v "l_cds_storage:/tmp/cds-storage"

i=0
until curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; do
  i=$((i+1))
  if [ "$i" -ge 60 ]; then echo "FATAL: cds-api /health not up after 60 tries"; docker logs --tail 30 l-cds-api || true; exit 1; fi
  sleep 2
done
echo "healthy: l-cds-api"

if [ "$BOOTSTRAP_ADMIN" = "1" ]; then
  BOOT_JSON=$(mktemp /tmp/l_bootstrap.XXXXXX.json)
  code=$(curl -s -o "$BOOT_JSON" -w '%{http_code}' -X POST http://127.0.0.1:8000/api/v1/auth/register \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"$CDS_ADMIN_USER\",\"email\":\"$CDS_ADMIN_USER@cds-local.cn\",\"password\":\"$CDS_ADMIN_PASS\",\"role\":\"admin\"}")
  if [ "$code" = "201" ]; then echo "bootstrap admin created ($CDS_ADMIN_USER)"; else
    if grep -q "already" "$BOOT_JSON" 2>/dev/null; then echo "bootstrap: admin exists, kept existing"; else
      # no `cat | head` pipe: under pipefail head's exit leaves cat with
      # SIGPIPE(141) and the command substitution aborts the whole script
      # before the real status code/body is ever printed.
      echo "FATAL: bootstrap register failed ($code): $(head -c 300 "$BOOT_JSON")"; rm -f "$BOOT_JSON"; exit 1; fi
  fi
  rm -f "$BOOT_JSON"
fi

echo "== summary =="
docker ps --format 'table {{.Names}}\t{{.Status}}'
# The cds-api:latest image may predate the working tree (in-flight diff is
# uncommitted); sync app/ into the container (image app root is /app/app) so
# the local node-track stack runs the tree under test. Re-run after any redeploy.
if [ -d "$ROOT/app" ]; then
  # the sync must carry the migrations too and re-run them from the synced
  # tree: previously alembic ran with the OLD image before the sync, so a tree
  # carrying new revisions booted the lane as "new code on old schema" and
  # only surfaced as runtime "column does not exist".
  SYNC_TAR=$(mktemp /tmp/l_cds_app_sync.XXXXXX.tar)
  tar cf "$SYNC_TAR" -C "$ROOT" app alembic alembic.ini
  docker cp "$SYNC_TAR" l-cds-api:/tmp/l_app_sync.tar
  rm -f "$SYNC_TAR"
  docker exec l-cds-api sh -c "tar xf /tmp/l_app_sync.tar -C /app"
  docker exec l-cds-api python -m alembic upgrade head
  docker restart l-cds-api >/dev/null
  i=0
  until curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; do
    i=$((i+1)); [ "$i" -ge 30 ] && { echo "FATAL: l-cds-api not healthy after app sync"; exit 1; }; sleep 2
  done
  echo "healthy: l-cds-api (app synced)"
fi
curl -fsS http://127.0.0.1:8000/health; echo
echo LOCAL_DEPLOY_DONE
