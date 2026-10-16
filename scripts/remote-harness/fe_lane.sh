#!/usr/bin/env bash
# 远程前端权威车道：node 镜像里跑 tsc / vitest / build，哨兵取容器内真实退出码
set -uo pipefail
FE=/root/cds-test/src/cds-frontend
IMG=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/node:20-bookworm-slim
NM=/root/cds-test/fe-cache/node_modules
mkdir -p /root/cds-test/fe-cache /root/cds-test/srclogs

docker run --rm -u root --network host \
  -v "$FE":/work -v "$NM":/work/node_modules -v /root/cds-test/srclogs:/logs \
  -w /work -e HOME=/tmp -e CI=1 --entrypoint bash "$IMG" -lc '
    set -uo pipefail
    if [ ! -d node_modules ] || [ -z "$(ls -A node_modules 2>/dev/null)" ]; then
      npm ci --prefer-offline --no-audit --no-fund > /logs/fe_npm_ci.log 2>&1
      echo "NPM_CI_RC=$?"
    fi
    npx tsc -b > /logs/fe_tsc.log 2>&1
    echo "TSC_RC=$?"
    npx vitest run > /logs/fe_vitest.log 2>&1
    echo "VITEST_RC=$?"
    npm run build > /logs/fe_build.log 2>&1
    echo "BUILD_RC=$?"
    echo "FE_TSC_TAIL=$(tail -3 /logs/fe_tsc.log | tr "\n" "|")"
    echo "FE_VITEST_TAIL=$(grep -aE "Test Files|Tests " /logs/fe_vitest.log | tail -3 | tr "\n" "|")"
    echo "FE_BUILD_TAIL=$(tail -3 /logs/fe_build.log | tr "\n" "|")"
    echo FE_LANE_DONE
  '
echo "FE_LANE_EXIT=$?"
