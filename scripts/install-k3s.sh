#!/usr/bin/env bash
# CDS K3s/K3d deployment helper with China-network defaults.
#
# Usage:
#   CDS_K3S_MODE=k3s bash scripts/install-k3s.sh   # native K3s, suitable for 243
#   CDS_K3S_MODE=k3d bash scripts/install-k3s.sh   # local k3d cluster
# Optional env:
#   INSTALL_K3S_VERSION   pin the k3s channel for the installer (strongly recommended)
#   CDS_K3D_SHA256        expected sha256 of the k3d binary (recommended pin)
#   CDS_ALLOW_UNLISTED_INSTALLER=1  accept a non-allowlisted K3S_INSTALL_URL (https still required)
#   KUBECTL               explicit kubectl command override (word-split as command+args)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
K8S_DIR="$PROJECT_DIR/k8s"

CDS_K3S_MODE="${CDS_K3S_MODE:-k3s}"
CLUSTER_NAME="${CLUSTER_NAME:-cds-cluster}"
K3S_INSTALL_URL="${K3S_INSTALL_URL:-https://get.k3s.io}"
INSTALL_K3S_MIRROR="${INSTALL_K3S_MIRROR:-cn}"
# 供应链：--write-kubeconfig-mode=644 会把含 admin 凭据的 /etc/rancher/k3s/k3s.yaml
# 变成全局可读——本机多用户环境请改成 600 或换专用 kubeconfig。此处保留 644 仅为
# dev 单机便利，风险已知并由本注释显式披露。
K3S_EXEC_ARGS="${K3S_EXEC_ARGS:---disable=traefik --write-kubeconfig-mode=644}"
K3D_VERSION="${K3D_VERSION:-v5.7.5}"
K3D_BIN="${K3D_BIN:-$HOME/.local/bin/k3d}"
KUBECTL="${KUBECTL:-}"                       # 显式覆盖时按 命令+参数 词法拆分
KUBECTL_CMD=()
REGISTRY_CONFIG="${REGISTRY_CONFIG:-$K8S_DIR/registries.yaml}"
SANDBOX_IMAGE="${SANDBOX_IMAGE:-python:3.12-slim}"
CDS_IMPORT_LOCAL_IMAGES="${CDS_IMPORT_LOCAL_IMAGES:-true}"

log() {
    printf '[cds-k3s] %s\n' "$*"
}

die() {
    printf '[cds-k3s] ERROR: %s\n' "$*" >&2
    exit 1
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

install_native_k3s() {
    # k3s 模式必需要 root（写 /etc/rancher、systemctl），晚失败不如早拒绝
    [ "${EUID:-$(id -u)}" -eq 0 ] || die "k3s 模式需要 root/sudo 运行"
    need_cmd curl

    if command -v k3s >/dev/null 2>&1; then
        kver="$(k3s --version 2>&1 || true)"
        log "native k3s already installed: ${kver%%$'\n'*}"
    else
        # 供应链门：安装脚本以 root 执行，URL 必须 https 且在白名单内；版本建议固定
        INSTALL_URL="$K3S_INSTALL_URL"
        case "$INSTALL_URL" in
            https://get.k3s.io|https://get.k3s.io/*|\
            https://rancher-mirror.rancher.cn/*|https://get.rancher.cn/*|https://channels.rancher.cn/*) ;;
            https://*)
                log "WARNING: 非白名单 k3s 安装源: $INSTALL_URL"
                [ "${CDS_ALLOW_UNLISTED_INSTALLER:-0}" = "1" ] \
                    || die "拒绝执行未白名单安装源（确认可信后 export CDS_ALLOW_UNLISTED_INSTALLER=1）" ;;
            *) die "k3s 安装 URL 必须走 https://: $INSTALL_URL" ;;
        esac
        log "installing native k3s with INSTALL_K3S_MIRROR=$INSTALL_K3S_MIRROR INSTALL_K3S_VERSION=${INSTALL_K3S_VERSION:-<unset: 建议固定版本>}"
        curl -sfL "$INSTALL_URL" \
            | INSTALL_K3S_MIRROR="$INSTALL_K3S_MIRROR" INSTALL_K3S_VERSION="${INSTALL_K3S_VERSION:-}" sh -s - $K3S_EXEC_ARGS
    fi

    mkdir -p /etc/rancher/k3s
    RESTART_NEEDED=0
    if [ -f "$REGISTRY_CONFIG" ]; then
        if [ -f /etc/rancher/k3s/registries.yaml ] && cmp -s "$REGISTRY_CONFIG" /etc/rancher/k3s/registries.yaml; then
            log "registries.yaml 与现网一致，跳过覆盖与重启"
        else
            if [ -f /etc/rancher/k3s/registries.yaml ]; then
                cp -p /etc/rancher/k3s/registries.yaml "/etc/rancher/k3s/registries.yaml.bak.$(date +%s)"
                log "已备份原 registries.yaml（保留主机既有仓库/凭据配置）"
            fi
            log "installing registry mirror config to /etc/rancher/k3s/registries.yaml"
            cp "$REGISTRY_CONFIG" /etc/rancher/k3s/registries.yaml
            RESTART_NEEDED=1
        fi
    else
        log "WARNING: $REGISTRY_CONFIG 不存在 —— 未配置镜像加速，国内网络下后续镜像拉取大概率失败"
    fi
    if [ "$RESTART_NEEDED" -eq 1 ]; then
        # registries.yaml 生效依赖重启；吞错继续 = 后面整场 ImagePullBackOff 无从排查
        if command -v systemctl >/dev/null 2>&1; then
            systemctl restart k3s 2>/dev/null || service k3s restart \
                || die "k3s 重启失败，registries.yaml 未生效"
        else
            service k3s restart || die "k3s 重启失败，registries.yaml 未生效"
        fi
    fi

    if [ ${#KUBECTL_CMD[@]} -eq 0 ]; then
        KUBECTL_CMD=(k3s kubectl)
    fi
}

install_k3d() {
    need_cmd curl
    need_cmd docker
    mkdir -p "$(dirname "$K3D_BIN")"
    if [ ! -x "$K3D_BIN" ]; then
        K3D_ARCH=""
        case "$(uname -m)" in
            x86_64) K3D_ARCH=amd64 ;;
            aarch64|arm64) K3D_ARCH=arm64 ;;
            *) die "不支持的架构: $(uname -m)（k3d 仅发布 amd64/arm64）" ;;
        esac
        K3D_URL="${K3D_INSTALL_URL:-https://github.com/k3d-io/k3d/releases/download/$K3D_VERSION/k3d-linux-${K3D_ARCH}}"
        case "$K3D_URL" in
            https://*) ;;
            *) die "k3d 下载 URL 必须走 https://: $K3D_URL" ;;
        esac
        log "installing k3d $K3D_VERSION ($K3D_ARCH) to $K3D_BIN"
        # 先落临时文件、校验、再原子 install，避免校验失败残留半截可执行文件
        tmpbin="$(mktemp)"
        trap 'rm -f "$tmpbin"' RETURN
        curl -fsSL "$K3D_URL" -o "$tmpbin" || die "k3d 下载失败: $K3D_URL"
        [ -s "$tmpbin" ] || die "k3d 下载内容为空"
        if [ -n "${CDS_K3D_SHA256:-}" ]; then
            printf '%s  %s\n' "$CDS_K3D_SHA256" "$tmpbin" | sha256sum -c - >/dev/null \
                || die "k3d 二进制 sha256 校验失败"
        else
            log "WARNING: 未设置 CDS_K3D_SHA256，跳过 k3d 完整性校验"
        fi
        install -m 0755 "$tmpbin" "$K3D_BIN"
    fi
    kv="$("$K3D_BIN" version 2>&1 || true)"
    log "k3d: ${kv%%$'\n'*}"

    [ -f "$REGISTRY_CONFIG" ] || die "registry config not found: $REGISTRY_CONFIG（k3d 创建集群必须带 --registry-config）"

    if "$K3D_BIN" cluster get "$CLUSTER_NAME" >/dev/null 2>&1; then
        log "k3d cluster $CLUSTER_NAME exists; starting"
        "$K3D_BIN" cluster start "$CLUSTER_NAME" || die "k3d cluster start 失败（端口占用/docker 异常？检查 30080/30081/30001/30082）"
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
            --wait || die "k3d cluster create 失败"
    fi

    # kubeconfig merge 失败后继续 = 可能把带 secrets 的 manifests apply 到旧 context/别的集群
    if ! "$K3D_BIN" kubeconfig merge "$CLUSTER_NAME" --kubeconfig-switch-context; then
        die "k3d kubeconfig merge 失败 —— 拒绝在不明 context 上继续部署"
    fi
    if [ ${#KUBECTL_CMD[@]} -eq 0 ]; then
        KUBECTL_CMD=(kubectl)
        need_cmd kubectl
    else
        need_cmd "${KUBECTL_CMD[0]}"
    fi
}

if [ -n "$KUBECTL" ]; then
    # shellcheck disable=SC2206  # 显式覆盖语义就是"命令+参数"词法拆分
    KUBECTL_CMD=(${KUBECTL})
fi

deploy_cds() {
    log "using kubectl: ${KUBECTL_CMD[*]}"
    log "applying CDS namespace/config/secrets"
    "${KUBECTL_CMD[@]}" apply -f "$K8S_DIR/namespace.yaml"
    "${KUBECTL_CMD[@]}" apply -f "$K8S_DIR/secrets.yaml"
    "${KUBECTL_CMD[@]}" apply -f "$K8S_DIR/configmaps.yaml"

    log "applying middleware manifests"
    for manifest in postgres.yaml redis.yaml minio.yaml vault.yaml clickhouse.yaml opa.yaml; do
        if [ -f "$K8S_DIR/$manifest" ]; then
            "${KUBECTL_CMD[@]}" apply -f "$K8S_DIR/$manifest"
        fi
    done

    # 预热必须打到集群实际使用的 containerd（k3s 自带 ctr / k3d server 节点内 k3s ctr），
    # 主机 ctr/crictl 连的是别的 socket+namespace，热了个寂寞。
    log "pre-pulling sandbox image when possible: $SANDBOX_IMAGE"
    case "$CDS_K3S_MODE" in
        k3s)
            if command -v k3s >/dev/null 2>&1; then
                k3s ctr images pull "$SANDBOX_IMAGE" || log "WARNING: 预热拉取失败 ($SANDBOX_IMAGE)"
            fi ;;
        k3d)
            if command -v docker >/dev/null 2>&1 && "$K3D_BIN" cluster get "$CLUSTER_NAME" >/dev/null 2>&1; then
                docker exec "k3d-${CLUSTER_NAME}-server-0" k3s ctr images pull "$SANDBOX_IMAGE" \
                    || log "WARNING: k3d 节点内预热拉取失败 ($SANDBOX_IMAGE)"
            fi ;;
    esac

    # 本地镜像是关键交付（cds-api/cds-frontend 可能只存在于本机 docker）：
    # 导入失败或导入后 containerd 看不见 → 直接失败，不留 ImagePullBackOff 猜谜。
    if [ "$CDS_IMPORT_LOCAL_IMAGES" = "true" ] && command -v docker >/dev/null 2>&1; then
        for image in cds-api:latest cds-frontend:latest; do
            if docker image inspect "$image" >/dev/null 2>&1; then
                log "importing local image into $CDS_K3S_MODE: $image"
                if [ "$CDS_K3S_MODE" = "k3s" ] && command -v k3s >/dev/null 2>&1; then
                    docker save "$image" | k3s ctr images import - || die "镜像导入失败: $image"
                    k3s ctr images ls -q | grep -qx -- "$image" \
                        || die "导入后 containerd 仍看不到镜像: $image"
                elif [ "$CDS_K3S_MODE" = "k3d" ] && [ -x "$K3D_BIN" ]; then
                    "$K3D_BIN" image import "$image" -c "$CLUSTER_NAME" || die "k3d 镜像导入失败: $image"
                fi
            else
                log "local image not found, skipping import: $image"
            fi
        done
    fi

    if [ -f "$K8S_DIR/cds-app.yaml" ]; then
        log "applying CDS API and frontend manifests"
        "${KUBECTL_CMD[@]}" apply -f "$K8S_DIR/cds-app.yaml"
    fi

    # 命名空间以 namespace.yaml 的 metadata.name 为准，不重复硬编码
    NS="$(awk '/^metadata:/{m=1;next} m && /^  name:/{print $2; exit}' "$K8S_DIR/namespace.yaml" || true)"
    NS="${NS:-cds-system}"
    log "current CDS pods (ns=$NS)"
    "${KUBECTL_CMD[@]}" get pods -n "$NS" || log "WARNING: 无法读取命名空间 $NS 的 pods，请手动核验部署状态"
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
