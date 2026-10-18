#!/usr/bin/env bash
# 远程前端权威车道：node 镜像里跑 tsc / vitest / build。
# 退出码契约: 外层退出码 = 容器内聚合 RC（任一检查失败即 1）；哨兵 FE_LANE_DONE rc=N
#   自带结果。外层 harness 以退出码为准，哨兵行仅供人读（日志内容不可信源）。
set -uo pipefail
BASE=/root/cds-test
FE=$BASE/src/cds-frontend
IMG=${CDS_NODE_IMAGE:-swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/node:20-bookworm-slim}
LOGS=$BASE/srclogs
CACHE=$BASE/fe-cache

# 前置校验：路径/锁文件/docker 缺失必须立刻失败，禁止 docker -v 静默造出 root 属主空目录
[ -d "$FE" ] && [ -f "$FE/package.json" ] || { echo "FE_LANE_FAIL missing_package_json path=$FE"; exit 1; }
[ -f "$FE/package-lock.json" ] || { echo "FE_LANE_FAIL missing_lockfile path=$FE/package-lock.json"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "FE_LANE_FAIL docker_missing"; exit 1; }
mkdir -p "$LOGS" "$CACHE" || { echo "FE_LANE_FAIL mkdir_dirs"; exit 1; }

# 并发锁：共享 node_modules / srclogs，两条 lane 并行 = 互相毁缓存
exec 9>"$BASE/.fe_lane.lock" || { echo "FE_LANE_FAIL lock_open"; exit 1; }
if command -v flock >/dev/null 2>&1; then
  flock -n 9 || { echo "FE_LANE_FAIL busy"; exit 1; }
fi

# 缓存键 = lockfile 摘要：依赖变化自动切新目录（旧"目录非空即跳过"会永久吃陈旧/半写缓存）
SIG="$(sha256sum "$FE/package-lock.json" | cut -c1-16)"
NM="$CACHE/$SIG/node_modules"
NPMCACHE="$CACHE/_npm"
mkdir -p "$NM" "$NPMCACHE" || { echo "FE_LANE_FAIL mkdir_cache"; exit 1; }

echo "FE_IMAGE_REQUESTED=$IMG"
docker image inspect "$IMG" --format 'FE_IMAGE_DIGEST={{index .RepoDigests 0}}' 2>/dev/null \
  || echo "FE_IMAGE_DIGEST=unknown"

rc=0
timeout -k 30 "${FE_LANE_TIMEOUT:-2700}" docker run --rm -u root --network host \
  --security-opt no-new-privileges=true \
  -v "$FE":/work -v "$NM":/work/node_modules -v "$LOGS":/logs -v "$NPMCACHE":/tmp/.npm \
  -w /work -e HOME=/tmp -e CI=1 -e npm_config_cache=/tmp/.npm \
  --entrypoint bash "$IMG" -lc '
    set -uo pipefail
    NPM_CI_RC=0; TSC_RC=0; VITEST_RC=0; BUILD_RC=0
    if [ -z "$(ls -A node_modules 2>/dev/null)" ]; then
      npm ci --prefer-offline --no-audit --no-fund > /logs/fe_npm_ci.log 2>&1
      NPM_CI_RC=$?
    fi
    echo "NPM_CI_RC=$NPM_CI_RC"
    if [ -x node_modules/.bin/tsc ]; then
      node_modules/.bin/tsc -b > /logs/fe_tsc.log 2>&1; TSC_RC=$?
    else
      echo "node_modules/.bin/tsc missing (npm ci 未完成?)" > /logs/fe_tsc.log; TSC_RC=127
    fi
    echo "TSC_RC=$TSC_RC"
    if [ -x node_modules/.bin/vitest ]; then
      node_modules/.bin/vitest run > /logs/fe_vitest.log 2>&1; VITEST_RC=$?
    else
      echo "node_modules/.bin/vitest missing (npm ci 未完成?)" > /logs/fe_vitest.log; VITEST_RC=127
    fi
    echo "VITEST_RC=$VITEST_RC"
    npm run build > /logs/fe_build.log 2>&1; BUILD_RC=$?
    echo "BUILD_RC=$BUILD_RC"
    oneline() { tr -cd "[:print:]\n" < "$1" 2>/dev/null | tail -n 3 | tr "\n" "~" | cut -c1-600; }
    echo "FE_TSC_TAIL=$(oneline /logs/fe_tsc.log)"
    echo "FE_VITEST_TAIL=$(grep -aE "Test Files|Tests " /logs/fe_vitest.log 2>/dev/null | tr -cd "[:print:]\n" | tail -n 3 | tr "\n" "~" | cut -c1-600)"
    echo "FE_BUILD_TAIL=$(oneline /logs/fe_build.log)"
    FE_RC=0
    for v in $NPM_CI_RC $TSC_RC $VITEST_RC $BUILD_RC; do [ "$v" -eq 0 ] || FE_RC=1; done
    echo "FE_LANE_DONE rc=$FE_RC"
    exit "$FE_RC"
  ' || rc=$?
echo "FE_LANE_EXIT=$rc"
exit "$rc"
