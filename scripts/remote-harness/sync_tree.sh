#!/usr/bin/env bash
# 全量同步本地工作树到远程 harness，并按 lane 的口径核对内容一致性（单流 tar，一个目标一条流）
set -uo pipefail
L="${CDS_LOCAL_ROOT:-$(git rev-parse --show-toplevel)}"
R='root@100.112.5.253'
SSH="ssh -p 22022 -o StrictHostKeyChecking=no $R"
SRC=/root/cds-test/src

agg() {  # 与 source_lane.sh 的 tree.md5 完全同口径
  find app tests alembic -name '*.py' -type f | sort | xargs md5sum | md5sum | cut -d' ' -f1
}

LOCAL_AGG=$(cd "$L" && agg)

tar czf - -C "$L" app tests alembic sdk scripts deploy .github pyproject.toml \
  alembic.ini requirements.txt 2>/dev/null \
  | $SSH "tar xzf - -C $SRC" || { echo SYNC_ALL_FAIL; exit 1; }

# 前端源码单独一条流（lane 的 FE 步骤用 /root/cds-test/fe-root，这里只同步 src）
tar czf - -C "$L/cds-frontend" src index.html vite.config.ts tsconfig.json package.json 2>/dev/null \
  | $SSH "mkdir -p $SRC/cds-frontend && tar xzf - -C $SRC/cds-frontend" || { echo SYNC_ALL_FAIL; exit 1; }

REMOTE_SNIP='find app tests alembic -name "*.py" -type f | sort | xargs md5sum | md5sum | cut -d" " -f1'
REMOTE_AGG=$($SSH "cd $SRC && $REMOTE_SNIP")
$SSH "echo $REMOTE_AGG > /root/cds-test/tree.manifest.local"

if [ "$LOCAL_AGG" = "$REMOTE_AGG" ]; then
  echo "TREE_MD5_MATCH $LOCAL_AGG"
else
  echo "TREE_MD5_DIFF local=$LOCAL_AGG remote=$REMOTE_AGG"
  exit 1
fi

# 内容级抽查：本轮改动的关键符号必须在远端可见
$SSH "cd $SRC && grep -c 'purpose_scope' tests/test_sm2_standard_interop.py >/dev/null \
  && grep -q '_MERKLE_ORDER_BY' app/api/audit.py \
  && grep -q 'quota-usage' app/api/quota.py \
  && ls alembic/versions/0022_*.py >/dev/null" \
  && echo SYNC_ALL_OK || { echo SYNC_ALL_VERIFY_FAIL; exit 1; }
