#!/usr/bin/env bash
# FISCO BCOS development environment initialiser for CDS
# Run after docker compose -f docker-compose.blockchain.yml up -d
set -euo pipefail

FISCO_HOME="${FISCO_HOME:-/tmp/fisco-bcos}"
CONSOLE_VERSION="v3.6.0"
CONSOLE_DIR="${FISCO_HOME}/console"
CONTRACT_DIR="$(cd "$(dirname "$0")/../contracts" && pwd)"
RPC_HOST="${FISCO_RPC_HOST:-127.0.0.1}"
RPC_PORT="${FISCO_RPC_PORT:-20201}"

echo "=== CDS FISCO BCOS initialisation ==="

# ── 1. Prepare directories ───────────────────────────────────────
mkdir -p "${FISCO_HOME}"

# ── 2. Download console tool ─────────────────────────────────────
if [ ! -d "${CONSOLE_DIR}" ]; then
    echo "[1/4] Downloading FISCO BCOS console ${CONSOLE_VERSION} ..."
    curl -fsSL "https://github.com/FISCO-BCOS/console/releases/download/${CONSOLE_VERSION}/console-${CONSOLE_VERSION}.tar.gz" \
        -o "${FISCO_HOME}/console.tar.gz"
    tar -xzf "${FISCO_HOME}/console.tar.gz" -C "${FISCO_HOME}"
    mv "${FISCO_HOME}/console-"* "${CONSOLE_DIR}"
    rm -f "${FISCO_HOME}/console.tar.gz"
    echo "      Console installed at ${CONSOLE_DIR}"
else
    echo "[1/4] Console already present at ${CONSOLE_DIR}, skipping download."
fi

# ── 3. Wait for node readiness ───────────────────────────────────
echo "[2/4] Waiting for FISCO BCOS node at ${RPC_HOST}:${RPC_PORT} ..."
for i in $(seq 1 60); do
    if curl -sf "http://${RPC_HOST}:${RPC_PORT}" \
         -H "Content-Type: application/json" \
         -d '{"jsonrpc":"2.0","method":"getClientVersion","params":[],"id":1}' \
         >/dev/null 2>&1; then
        echo "      Node is ready."
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "ERROR: Node did not become ready within 60 seconds." >&2
        exit 1
    fi
    sleep 1
done

# ── 4. Deploy AuditRegistry contract ─────────────────────────────
echo "[3/4] Deploying AuditRegistry contract ..."

# Copy the Solidity source into the console's contracts directory
mkdir -p "${CONSOLE_DIR}/contracts"
cp "${CONTRACT_DIR}/AuditRegistry.sol" "${CONSOLE_DIR}/contracts/"

# Build and deploy via console
cd "${CONSOLE_DIR}"
bash console.sh -p "${CONSOLE_DIR}" <<'CONSOLE_CMDS'
deploy AuditRegistry
exit
CONSOLE_CMDS

echo "[4/4] Done."
echo ""
echo "──────────────────────────────────────────────"
echo "  FISCO BCOS node"
echo "  P2P  : ${RPC_HOST}:20200"
echo "  RPC  : ${RPC_HOST}:${RPC_PORT}"
echo "  Console: ${CONSOLE_DIR}/console.sh"
echo "──────────────────────────────────────────────"
