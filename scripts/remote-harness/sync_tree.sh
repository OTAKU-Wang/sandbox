#!/usr/bin/env bash
# 全量同步本地工作树到远程 harness，并按 lane 的口径核对内容一致性（单流 tar，一个目标一条流）
# 哨兵契约: SYNC_ALL_FAIL / TREE_MD5_MATCH / TREE_MD5_DIFF / SYNC_ALL_OK / SYNC_ALL_VERIFY_FAIL
#   —— TREE_MD5_MATCH 只在聚合一致且内容抽查全过后才打印。
set -uo pipefail
L="${CDS_LOCAL_ROOT:-}"
if [ -z "$L" ]; then
  L="$(git rev-parse --show-toplevel 2>/dev/null || true)"
fi
if [ -z "$L" ] || [ ! -d "$L" ]; then
  echo "SYNC_ALL_FAIL bad_local_root (export CDS_LOCAL_ROOT=<repo> 或在 git 仓库内运行)"
  exit 1
fi
R='root@100.112.5.253'
# 主机密钥: accept-new（首见记录、变更即断），长期请改用 ~/.ssh/config 别名 + 固定 known_hosts
SSH="ssh -p 22022 -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new $R"
SRC=/root/cds-test/src

# 并发防护：两个实例同时向同一 $SRC 解包会互相覆盖出假 DIFF
LOCKF="${TMPDIR:-/tmp}/cds-sync-tree.lock"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCKF"
  flock -n 9 || { echo "SYNC_ALL_FAIL busy (另一条 sync_tree 正在运行)"; exit 1; }
fi

agg() {  # 与 source_lane.sh 的 tree.md5 完全同口径（-print0/xargs -0 -r + 空集拦截）
  local n
  n="$(find app tests alembic -name '*.py' -type f | wc -l)"
  if [ "${n:-0}" -eq 0 ]; then
    echo "agg: app/tests/alembic 下没有任何 .py，拒绝产出空集哈希" >&2
    return 1
  fi
  find app tests alembic -name '*.py' -type f -print0 \
    | sort -z | xargs -0 -r md5sum | md5sum | cut -d' ' -f1
}

LOCAL_AGG="$(cd "$L" && agg)" || { echo "SYNC_ALL_FAIL local_agg"; exit 1; }
case "$LOCAL_AGG" in
  ''|*[!0-9a-f]*) echo "SYNC_ALL_FAIL local_agg_not_md5"; exit 1 ;;
  *) [ "${#LOCAL_AGG}" -eq 32 ] || { echo "SYNC_ALL_FAIL local_agg_len"; exit 1; } ;;
esac

# 先本地打包成临时 tar 再走管道：分离 tar 侧与 ssh 侧错误（"file changed as we read it"
# 这类 tar rc=1 不再污染管道退出码），并可校验包完整性。
TGZ="$(mktemp "${TMPDIR:-/tmp}/cds-sync.XXXXXX.tgz")" || { echo SYNC_ALL_FAIL; exit 1; }
FE_TGZ="$(mktemp "${TMPDIR:-/tmp}/cds-fe-sync.XXXXXX.tgz")" || { echo SYNC_ALL_FAIL; exit 1; }
trap 'rm -f "$TGZ" "$FE_TGZ"' EXIT

tar -czf "$TGZ" -C "$L" app tests alembic sdk scripts deploy .github pyproject.toml \
  alembic.ini requirements.txt || { echo SYNC_ALL_FAIL backend_tar; exit 1; }
gzip -t "$TGZ" || { echo SYNC_ALL_FAIL backend_tgz_corrupt; exit 1; }
[ -d "$L/cds-frontend" ] || { echo "SYNC_ALL_FAIL missing_frontend_dir $L/cds-frontend"; exit 1; }
tar -czf "$FE_TGZ" -C "$L/cds-frontend" src index.html vite.config.ts tsconfig.json package.json \
  || { echo SYNC_ALL_FAIL frontend_tar; exit 1; }
gzip -t "$FE_TGZ" || { echo SYNC_ALL_FAIL frontend_tgz_corrupt; exit 1; }

# 远端全量替换：先清同名目录再解包（tar 不删除，避免"远端多出旧文件"的脏状态）
cat "$TGZ" | $SSH "mkdir -p $SRC && rm -rf $SRC/app $SRC/tests $SRC/alembic $SRC/sdk $SRC/scripts $SRC/deploy $SRC/.github && tar xzf - -C $SRC" \
  || { echo SYNC_ALL_FAIL backend_stream; exit 1; }
cat "$FE_TGZ" | $SSH "mkdir -p $SRC/cds-frontend && rm -rf $SRC/cds-frontend/src && tar xzf - -C $SRC/cds-frontend" \
  || { echo SYNC_ALL_FAIL frontend_stream; exit 1; }

REMOTE_SNIP='n=$(find app tests alembic -name "*.py" -type f | wc -l); [ "$n" -gt 0 ] || exit 1; find app tests alembic -name "*.py" -type f -print0 | sort -z | xargs -0 -r md5sum | md5sum | cut -d" " -f1'
REMOTE_AGG="$($SSH "cd $SRC && $REMOTE_SNIP")" || { echo SYNC_ALL_FAIL remote_agg; exit 1; }
case "$REMOTE_AGG" in
  ''|*[!0-9a-f]*) echo "SYNC_ALL_FAIL remote_agg_not_md5 value=<$REMOTE_AGG>"; exit 1 ;;
  *) [ "${#REMOTE_AGG}" -eq 32 ] || { echo "SYNC_ALL_FAIL remote_agg_len"; exit 1; } ;;
esac
# manifest 作为远端返回值的安全落盘：值只经参数传递（$1 单引号引用，不进远端二次解析），
# 先写 .tmp 再 mv，原子替换。
$SSH 'printf "%s\n" "$1" > "$2.tmp" && mv "$2.tmp" "$2"' _ "$LOCAL_AGG" /root/cds-test/tree.manifest.local \
  || { echo SYNC_ALL_FAIL manifest_write; exit 1; }

MATCH=1
[ "$LOCAL_AGG" = "$REMOTE_AGG" ] || MATCH=0

# 内容级抽查：本轮改动的关键符号必须在远端可见。符号清单集中在数组里维护，
# 失效时逐条报出具体项，不再用 && 链把失败糊成一个笼统哨兵。
VERIFY_SPECS=(
  "tests/test_sm2_standard_interop.py::purpose_scope"
  "app/api/audit.py::_MERKLE_ORDER_BY"
  "app/api/quota.py::quota-usage"
  "alembic/versions/0022_*.py::"
)
VERIFY_FAILS=0
for spec in "${VERIFY_SPECS[@]}"; do
  fpath="${spec%%::*}"; sym="${spec##*::}"
  if [ -n "$sym" ]; then
    if ! $SSH "cd $SRC && grep -rq -- '$sym' $fpath"; then
      echo "SYNC_ALL_VERIFY_MISS $fpath :: $sym"
      VERIFY_FAILS=$((VERIFY_FAILS+1))
    fi
  else
    if ! $SSH "cd $SRC && ls $fpath"; then
      echo "SYNC_ALL_VERIFY_MISS $fpath (文件不存在)"
      VERIFY_FAILS=$((VERIFY_FAILS+1))
    fi
  fi
done

if [ "$MATCH" -eq 1 ] && [ "$VERIFY_FAILS" -eq 0 ]; then
  echo "TREE_MD5_MATCH $LOCAL_AGG"
  echo SYNC_ALL_OK
  exit 0
fi
if [ "$MATCH" -ne 1 ]; then
  echo "TREE_MD5_DIFF local=$LOCAL_AGG remote=$REMOTE_AGG"
fi
if [ "$VERIFY_FAILS" -ne 0 ]; then
  echo "SYNC_ALL_VERIFY_FAIL n=$VERIFY_FAILS"
fi
exit 1
