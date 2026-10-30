#!/usr/bin/env bash
# FISCO BCOS development environment initialiser for CDS
# Run after docker compose -f docker-compose.blockchain.yml up -d
set -euo pipefail

# 密钥落盘基目录权限：console 会在 FISCO_HOME 下生成账户/链密钥材料，默认
# /tmp 世界可写是劫持/窃取面 —— 写任何文件前先收紧 umask（基目录 0700，文件 0600）。
umask 077

FISCO_HOME="${FISCO_HOME:-/tmp/fisco-bcos}"
CONSOLE_VERSION="v3.6.0"
# 上游 tarball 校验和钩子：设置后下载必须 sha256 吻合；置空 = 显式未校验（WARN，不静默）
FISCO_CONSOLE_SHA256="${FISCO_CONSOLE_SHA256:-}"
CONSOLE_DIR="${FISCO_HOME}/console"
CONTRACT_DIR="$(cd "$(dirname "$0")/../contracts" && pwd)"
RPC_HOST="${FISCO_RPC_HOST:-127.0.0.1}"
RPC_PORT="${FISCO_RPC_PORT:-20201}"
DEPLOY_LOG="${FISCO_HOME}/deploy.log"
ADDR_FILE="${FISCO_HOME}/audit-registry.address"

echo "=== CDS FISCO BCOS initialisation ==="

# ── 1. Prepare directories ───────────────────────────────────────
mkdir -p "${FISCO_HOME}"

# ── 2. Download console tool ─────────────────────────────────────
# 幂等判据看关键文件可执行，不是目录存在（半次解包的目录不算装好）
if [ ! -x "${CONSOLE_DIR}/console.sh" ]; then
    echo "[1/4] Downloading FISCO BCOS console ${CONSOLE_VERSION} ..."
    if [ -d "${CONSOLE_DIR}" ]; then
        echo "      ${CONSOLE_DIR} 存在但 console.sh 不可执行，清理后重装"
        rm -rf "${CONSOLE_DIR}"
    fi
    curl -fsSL --connect-timeout 10 --max-time 600 \
        "https://github.com/FISCO-BCOS/console/releases/download/${CONSOLE_VERSION}/console-${CONSOLE_VERSION}.tar.gz" \
        -o "${FISCO_HOME}/console.tar.gz"
    if [ -n "$FISCO_CONSOLE_SHA256" ]; then
        printf '%s  %s\n' "$FISCO_CONSOLE_SHA256" "${FISCO_HOME}/console.tar.gz" \
            | sha256sum -c - || {
                echo "ERROR: console tarball sha256 mismatch (expected ${FISCO_CONSOLE_SHA256})" >&2
                rm -f "${FISCO_HOME}/console.tar.gz"
                exit 1
            }
    else
        echo "WARN: FISCO_CONSOLE_SHA256 未设置 —— console tarball 未经校验（仅限开发环境）" >&2
    fi
    # --no-same-owner：解包产物归当前用户，不被上游归档里的 uid/gid 重写
    tar --no-same-owner -xzf "${FISCO_HOME}/console.tar.gz" -C "${FISCO_HOME}"
    mv "${FISCO_HOME}/console-"* "${CONSOLE_DIR}"
    rm -f "${FISCO_HOME}/console.tar.gz"
    echo "      Console installed at ${CONSOLE_DIR}"
else
    echo "[1/4] Console already present at ${CONSOLE_DIR}, skipping download."
fi

# ── 3. Wait for node readiness ───────────────────────────────────
echo "[2/4] Waiting for FISCO BCOS node at ${RPC_HOST}:${RPC_PORT} ..."
# curl -f 只看 HTTP 状态码：200 + 空体/非 JSON 也会被当成就绪。必须拿到 JSON-RPC 的
# result 字段；同时带 connect/read 超时，网络黑洞不会把 60 次重试拖成无限等。
node_ready=0
for i in $(seq 1 60); do
    resp=""
    resp="$(curl -sf --connect-timeout 2 --max-time 5 "http://${RPC_HOST}:${RPC_PORT}" \
        -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"getClientVersion","params":[],"id":1}' 2>/dev/null)" || resp=""
    case "$resp" in
        *'"result"'*) node_ready=1; break ;;
    esac
    if [ "$i" -eq 60 ]; then
        echo "ERROR: Node did not return a JSON-RPC result within 60 seconds." >&2
        exit 1
    fi
    sleep 1
done
[ "$node_ready" -eq 1 ] || { echo "ERROR: node never became ready" >&2; exit 1; }
echo "      Node is ready."

# ── 4. Deploy AuditRegistry contract ─────────────────────────────
echo "[3/4] Deploying AuditRegistry contract ..."

if [ ! -f "$CONTRACT_DIR/AuditRegistry.sol" ]; then
    echo "ERROR: contract source missing: $CONTRACT_DIR/AuditRegistry.sol" >&2
    exit 1
fi

# 幂等：上一轮已把部署地址落盘 → 直接复用，绝不重复部署双份合约
if [ -s "$ADDR_FILE" ]; then
    echo "      Reusing deployed address from ${ADDR_FILE}: $(cat "$ADDR_FILE")"
else
    # Copy the Solidity source into the console's contracts directory
    mkdir -p "${CONSOLE_DIR}/contracts"
    cp "${CONTRACT_DIR}/AuditRegistry.sol" "${CONSOLE_DIR}/contracts/"

    # Build and deploy via console —— console.sh 部署失败也返回 0：全量输出落
    # deploy.log，只有日志里真出现 contract address 才算成功
    cd "${CONSOLE_DIR}"
    bash console.sh -p "${CONSOLE_DIR}" >"$DEPLOY_LOG" 2>&1 <<'CONSOLE_CMDS'
deploy AuditRegistry
exit
CONSOLE_CMDS
    if ! grep -q "contract address" "$DEPLOY_LOG"; then
        echo "ERROR: console deploy produced no contract address; see ${DEPLOY_LOG}" >&2
        tail -n 40 "$DEPLOY_LOG" >&2 || true
        exit 1
    fi
    ADDR="$(grep "contract address" "$DEPLOY_LOG" | grep -oE '0x[0-9a-fA-F]+' | tail -1)"
    if [ -z "$ADDR" ]; then
        echo "ERROR: 'contract address' logged but no 0x address parsed; see ${DEPLOY_LOG}" >&2
        exit 1
    fi
    # umask 077 已收紧 → 地址文件 0600；落盘后重复运行幂等复用
    printf '%s\n' "$ADDR" > "$ADDR_FILE"
    echo "      AuditRegistry deployed at ${ADDR} (recorded in ${ADDR_FILE})"
fi

echo "[4/4] Done."
echo ""
echo "──────────────────────────────────────────────"
echo "  FISCO BCOS node"
echo "  P2P  : ${RPC_HOST}:20200"
echo "  RPC  : ${RPC_HOST}:${RPC_PORT}"
echo "  Console: ${CONSOLE_DIR}/console.sh"
echo "──────────────────────────────────────────────"
