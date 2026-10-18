#!/usr/bin/env bash
# 源码直跑版后端 lane（主机 venv + 真实 PostgreSQL，不用容器、不构建镜像）
# 用法: bash /root/cds-test/source_lane.sh [fast|full]
#   fast = runtime/compileall/import/alembic/parity + gate + ss15
#   full = 以上 + 全量
# 退出码契约: 0=全绿(LANE_DONE)；1=至少一个 step 失败(LANE_FAIL fails=N)；2=环境门
#   （无源码树 / python 版本不符 / 拿不到锁 / MODE 非法）。
# 口径说明: lane 对齐生产 py3.12 镜像，CI gate 跑 py3.11 —— 两者定位不同（见
#   docs/cr-2026-10-15-review.md §5），是刻意分工而非漂移。
set -uo pipefail
MODE=${1:-fast}
case "$MODE" in
  fast|full) ;;
  *) echo "usage: source_lane.sh [fast|full] (got: $MODE)" >&2; exit 2 ;;
esac
SRC=/root/cds-test/src
LOGDIR=/root/cds-test/srclogs
PY=/root/cds-test/venv/bin/python
DBURL="${CDS_LANE_DB_URL:?export CDS_LANE_DB_URL=postgresql+asyncpg://<user>:<pw>@127.0.0.1:5432/cds 之后再跑}"
mkdir -p "$LOGDIR"
# 并发锁：本 lane 直连真实 PG 且共用 LOGDIR，两次并发会互相覆盖日志并在同库并发 alembic
exec 9>"$LOGDIR/.lane.lock" || { echo "LANE_FAIL lock_open"; exit 2; }
flock -n 9 || { echo "LANE_FAIL busy (另一条 lane 正在运行)"; exit 2; }
# 本轮日志先清理：失败 tail 与 SUMMARY 都不得读到上一轮残留
rm -f "$LOGDIR/gate.log" "$LOGDIR/runtime.log" "$LOGDIR/compile.log" "$LOGDIR/import.log" \
      "$LOGDIR/alembic.log" "$LOGDIR/alembic_check.log" "$LOGDIR/parity.log" \
      "$LOGDIR/ss15.log" "$LOGDIR/full.log" \
      "$LOGDIR/tree.md5" "$LOGDIR/tree.md5.err"
cd "$SRC" || { echo "STEP_FAIL no_tree"; exit 2; }
export HOME=/tmp
export PYTHONPATH="$SRC:$SRC/sdk"
export CDS_DATABASE_URL="$DBURL"
export PYTHONDONTWRITEBYTECODE=1
export PATH="/root/cds-test/venv/bin:/usr/local/bin:/usr/bin:/bin"
export SRC
FAILS=0
RAN=""

# DSN 脱敏：alembic/SQLAlchemy 栈可能把连接串写进日志，tail 上送 harness 前先遮掉口令
mask_dsn() { sed -E 's#(://[^/:@[:space:]]+:)[^@[:space:]]+@#\1***@#g'; }

run() {
  local name="$1" log="$2" cmd="$3"
  echo "--- STEP ${name} ---"
  timeout -k 30 "${LANE_STEP_TIMEOUT:-3600}" bash -c "$cmd" > "$LOGDIR/$log" 2>&1
  local rc=$?
  RAN="$RAN $name"
  if [ "$rc" -eq 0 ]; then
    echo "STEP_OK ${name}"
  else
    FAILS=$((FAILS+1))
    echo "STEP_FAIL ${name} rc=${rc}"
    tail -20 "$LOGDIR/$log" | mask_dsn
  fi
}

v="$("$PY" -V 2>&1)"
if [[ "$v" == Python\ 3.12.* ]]; then
  echo "STEP_OK runtime $v"
else
  echo "STEP_FAIL runtime $v (lane 必须用 $PY 的 Python 3.12)"
  exit 2
fi

# 源码树聚合哈希：-print0/sort -z/xargs -0 -r 防空格文件名与空输入挂起；
# 空集直接判失败，禁止"双错即匹配"的 d41d8... 假哈希。口径与 sync_tree.sh 完全一致。
tree_ok=1
( cd "$SRC" \
  && N="$(find app tests alembic -name '*.py' -type f | wc -l)" \
  && [ "${N:-0}" -gt 0 ] \
  && find app tests alembic -name '*.py' -type f -print0 | sort -z | xargs -0 -r md5sum | md5sum ) \
  > "$LOGDIR/tree.md5" 2>"$LOGDIR/tree.md5.err" || tree_ok=0
if [ "$tree_ok" -ne 0 ] || ! grep -Eq '[0-9a-f]{32}' "$LOGDIR/tree.md5"; then
  echo "STEP_FAIL tree_hash"; FAILS=$((FAILS+1))
else
  REMOTE_AGG="$(awk '{print $1}' "$LOGDIR/tree.md5")"
  MANIFEST=/root/cds-test/tree.manifest.local
  if [ ! -f "$MANIFEST" ]; then
    echo "STEP_FAIL tree_manifest_missing (先跑 scripts/remote-harness/sync_tree.sh)"; FAILS=$((FAILS+1))
  else
    LOCAL_AGG="$(cut -d' ' -f1 "$MANIFEST" | tr -d '[:space:]')"
    case "$LOCAL_AGG" in
      ''|*[!0-9a-f]*) echo "STEP_FAIL tree_manifest_invalid"; FAILS=$((FAILS+1)) ;;
      *) if [ "$REMOTE_AGG" = "$LOCAL_AGG" ]; then
           echo "STEP_OK tree_sync"
         else
           echo "STEP_FAIL tree_drift local=$LOCAL_AGG remote=$REMOTE_AGG"; FAILS=$((FAILS+1))
         fi ;;
    esac
  fi
fi

run compileall compile.log "python -m compileall -q app tests alembic && echo COMPILE_OK"
run import_smoke import.log "python -c 'import app.main; print(\"IMPORT_OK\")'"
run alembic_upgrade alembic.log "python -m alembic upgrade head && python -m alembic current"
run alembic_check alembic_check.log "ALEMBIC_COMPARE_TYPES=0 python -m alembic check"
run schema_parity parity.log "python scripts/verify_schema_parity.py"

# gate 清单解析：锚定 "name: Gate tests" 步骤区，剔除 --xxx=... 选项 token
# （ci.yml 由 tests/test_ci_workflow_policy.py 锁定命名；解析失败/空集必须硬失败，
#   空集直接跑 pytest 会退化为"跑全量"或"跑错集合"的假绿）。
GATE="$(SRC="$SRC" "$PY" - <<'PY'
import os, re, sys
src = os.environ.get("SRC") or "/root/cds-test/src"
try:
    txt = open(src + "/.github/workflows/ci.yml", encoding="utf-8").read()
except OSError as exc:
    sys.stderr.write("ci.yml unreadable: %s\n" % exc)
    raise SystemExit(1)
region, in_gate = [], False
for ln in txt.splitlines():
    if not in_gate:
        if re.search(r"name:\s*Gate tests", ln):
            in_gate = True
        continue
    if re.match(r"^\s*-?\s*name:\s", ln):
        break
    region.append(ln)
body = re.sub(r"--\w[\w-]*=\S+", " ", " ".join(region))
paths = []
for m in re.findall(r"tests/[A-Za-z0-9_./-]+\.py", body):
    if m not in paths:
        paths.append(m)
if not paths:
    sys.stderr.write("no gate test files parsed from ci.yml 'Gate tests' step\n")
    raise SystemExit(1)
print(" ".join(paths))
PY
)"
gate_rc=$?
if [ "$gate_rc" -ne 0 ] || [ -z "$(printf '%s' "$GATE" | tr -d '[:space:]')" ]; then
  echo "STEP_FAIL gate_parse rc=$gate_rc"
  FAILS=$((FAILS+1))
else
  echo "GATE_FILES=$(printf '%s\n' $GATE | wc -w | tr -d ' ')"
  run gate gate.log "python -m pytest -q --tb=line -p no:cacheprovider $GATE"
fi

# SS15 是关键门禁：glob 用 nullglob 数组 + printf %q（禁止 ls 输出拼进 bash -c），
# 一个都没有时是失败而不是静默跳过。
shopt -s nullglob
SS15=(tests/test_ss15_*.py)
shopt -u nullglob
if [ "${#SS15[@]}" -eq 0 ]; then
  echo "STEP_FAIL ss15_missing (tree 里没有 tests/test_ss15_*.py)"; FAILS=$((FAILS+1))
else
  SS15_ARGS="$(printf '%q ' "${SS15[@]}")"
  run ss15 ss15.log "python -m pytest -q --tb=line -p no:cacheprovider $SS15_ARGS"
fi

if [ "$MODE" = "full" ]; then
  run full full.log "python -m pytest -q --tb=line -p no:cacheprovider tests/ --ignore=tests/test_e2e_full_lifecycle.py"
fi

# SUMMARY 只报本轮跑过的 step，且经过 DSN 脱敏
for f in $RAN; do
  logf="$LOGDIR/${f}.log"
  [ -s "$logf" ] && echo "SUMMARY $f: $(grep -aE '^[0-9]+ (passed|failed)|passed|failed' "$logf" | tail -1 | mask_dsn)"
done

if [ "$FAILS" -ne 0 ]; then
  echo "LANE_FAIL mode=$MODE fails=$FAILS"
  exit 1
fi
echo "LANE_DONE mode=$MODE rc=0"
