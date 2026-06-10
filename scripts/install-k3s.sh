#!/usr/bin/env bash
# CDS K3s/K3d deployment helper with China-network defaults.
#
# Usage:
#   CDS_K3S_MODE=k3s bash scripts/install-k3s.sh   # native K3s, suitable for 243
#   CDS_K3S_MODE=k3d bash scripts/install-k3s.sh   # local k3d cluster
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
K8S_DIR="$PROJECT_DIR/k8s"

CDS_K3S_MODE="${CDS_K3S_MODE:-k3s}"
CLUSTER_NAME="${CLUSTER_NAME:-cds-cluster}"
K3S_INSTALL_URL="${K3S_INSTALL_URL:-https://get.k3s.io}"
INSTALL_K3S_MIRROR="${INSTALL_K3S_MIRROR:-cn}"
K3S_EXEC_ARGS="${K3S_EXEC_ARGS:---disable=traefik --write-kubeconfig-mode=644}"
K3D_VERSION="${K3D_VERSION:-v5.7.5}"
K3D_BIN="${K3D_BIN:-$HOME/.local/bin/k3d}"
KUBECTL="${KUBECTL:-kubectl}"
REGISTRY_CONFIG="${REGISTRY_CONFIG:-$K8S_DIR/registries.yaml}"
SANDBOX_IMAGE="${SANDBOX_IMAGE:-python:3.12-slim}"
CDS_IMPORT_LOCAL_IMAGES="${CDS_IMPORT_LOCAL_IMAGES:-true}"

log() {
    printf '[cds-k3s] %s\n' "$*"
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "missing required command: $1" >&2
        exit 1
    }
}

install_native_k3s() {
    need_cmd curl
    if command -v k3s >/dev/null 2>&1; then
        log "native k3s already installed: $(k3s --version | head -1)"
    else
        log "installing native k3s with INSTALL_K3S_MIRROR=$INSTALL_K3S_MIRROR"
        curl -sfL "$K3S_INSTALL_URL" | INSTALL_K3S_MIRROR="$INSTALL_K3S_MIRROR" sh -s - $K3S_EXEC_ARGS
    fi

    mkdir -p /etc/rancher/k3s
    if [ -f "$REGISTRY_CONFIG" ]; then
        log "installing registry mirror config to /etc/rancher/k3s/registries.yaml"
        cp "$REGISTRY_CONFIG" /etc/rancher/k3s/registries.yaml
        if command -v systemctl >/dev/null 2>&1; then
            systemctl restart k3s || true
        else
            service k3s restart || true
        fi
    fi

    KUBECTL="k3s kubectl"
}

install_k3d() {
    need_cmd curl
    need_cmd docker
    mkdir -p "$(dirname "$K3D_BIN")"
    if [ ! -x "$K3D_BIN" ]; then
        log "installing k3d $K3D_VERSION to $K3D_BIN"
        curl -fsSL "https://github.com/k3d-io/k3d/releases/download/$K3D_VERSION/k3d-linux-amd64" -o "$K3D_BIN"
        chmod +x "$K3D_BIN"
    fi
    log "k3d: $("$K3D_BIN" version 2>&1 | head -1)"

    if "$K3D_BIN" cluster list 2>/dev/null | grep -q "$CLUSTER_NAME"; then
        log "k3d cluster $CLUSTER_NAME exists; starting"
        "$K3D_BIN" cluster start "$CLUSTER_NAME" 2>/dev/null || true
    else
        log "creating k3d cluster $CLUSTER_NAME with registry mirror config"
        "$K3D_BIN" cluster create "$CLUSTER_NAME" \
            --servers 1 \
            --agents 0 \
            --port "30080:30080@server:0" \
            --port "30081:30081@server:0" \
            --port "30001:30001@server:0" \
            --port "30082:30082@server:0" \
            --k3s-arg "--disable=traefik@server:0" \
            --registry-config "$REGISTRY_CONFIG" \
            --wait
    fi

    "$K3D_BIN" kubeconfig merge "$CLUSTER_NAME" --kubeconfig-switch-context 2>/dev/null || true
    KUBECTL="kubectl"
}

deploy_cds() {
    log "using kubectl: $KUBECTL"
    log "applying CDS namespace/config/secrets"
    $KUBECTL apply -f "$K8S_DIR/namespace.yaml"
    $KUBECTL apply -f "$K8S_DIR/secrets.yaml"
    $KUBECTL apply -f "$K8S_DIR/configmaps.yaml"

    log "applying middleware manifests"
    for manifest in postgres.yaml redis.yaml minio.yaml vault.yaml clickhouse.yaml opa.yaml; do
        if [ -f "$K8S_DIR/$manifest" ]; then
            $KUBECTL apply -f "$K8S_DIR/$manifest"
        fi
    done

    log "pre-pulling sandbox image when possible: $SANDBOX_IMAGE"
    if command -v ctr >/dev/null 2>&1; then
        ctr images pull "$SANDBOX_IMAGE" || true
    elif command -v crictl >/dev/null 2>&1; then
        crictl pull "$SANDBOX_IMAGE" || true
    fi

    if [ "$CDS_IMPORT_LOCAL_IMAGES" = "true" ] && command -v docker >/dev/null 2>&1; then
        for image in cds-api:latest cds-frontend:latest; do
            if docker image inspect "$image" >/dev/null 2>&1; then
                log "importing local image into $CDS_K3S_MODE: $image"
                if [ "$CDS_K3S_MODE" = "k3s" ] && command -v k3s >/dev/null 2>&1; then
                    docker save "$image" | k3s ctr images import - || true
                elif [ "$CDS_K3S_MODE" = "k3d" ] && [ -x "$K3D_BIN" ]; then
                    "$K3D_BIN" image import "$image" -c "$CLUSTER_NAME" || true
                fi
            else
                log "local image not found, skipping import: $image"
            fi
        done
    fi

    if [ -f "$K8S_DIR/cds-app.yaml" ]; then
        log "applying CDS API and frontend manifests"
        $KUBECTL apply -f "$K8S_DIR/cds-app.yaml"
    fi

    log "current CDS pods"
    $KUBECTL get pods -n cds-system 2>/dev/null || true
}

case "$CDS_K3S_MODE" in
    k3s)
        install_native_k3s
        ;;
    k3d)
        install_k3d
        ;;
    *)
        echo "CDS_K3S_MODE must be k3s or k3d, got: $CDS_K3S_MODE" >&2
        exit 2
        ;;
esac

deploy_cds

log "done"
