#!/usr/bin/env bash
# 源码直跑版后端 lane（主机 venv + 真实 PostgreSQL，不用容器、不构建镜像）
# 用法: bash /root/cds-test/source_lane.sh [fast|full]
#   fast = runtime/compileall/import/alembic/parity + gate + ss15
#   full = 以上 + 全量
set -uo pipefail
MODE=${1:-fast}
SRC=/root/cds-test/src
LOGDIR=/root/cds-test/srclogs
PY=/root/cds-test/venv/bin/python
DBURL="${CDS_LANE_DB_URL:?export CDS_LANE_DB_URL=postgresql+asyncpg://<user>:<pw>@127.0.0.1:5432/cds 之后再跑}"
mkdir -p "$LOGDIR"
cd "$SRC" || { echo "STEP_FAIL no_tree"; exit 0; }
export HOME=/tmp
export PYTHONPATH="$SRC:$SRC/sdk"
export CDS_DATABASE_URL="$DBURL"
export PYTHONDONTWRITEBYTECODE=1
export PATH="/root/cds-test/venv/bin:/usr/local/bin:/usr/bin:/bin"

run() {
  local name="$1" log="$2" cmd="$3"
  echo "--- STEP ${name} ---"
  bash -c "$cmd" > "$LOGDIR/$log" 2>&1
  local rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "STEP_OK ${name}"
  else
    echo "STEP_FAIL ${name} rc=${rc}"
    tail -20 "$LOGDIR/$log"
  fi
}

v="$(/root/cds-test/venv/bin/python -V 2>&1)"
if [[ "$v" == Python\ 3.12.* ]]; then echo "STEP_OK runtime $v"; else echo "STEP_FAIL runtime $v"; exit 0; fi

( cd "$SRC" && find app tests alembic -name '*.py' -type f | sort | xargs md5sum | md5sum ) > "$LOGDIR/tree.md5"
if [ -f /root/cds-test/tree.manifest.local ]; then
  LOCAL_AGG=$(cut -d' ' -f1 /root/cds-test/tree.manifest.local)
  REMOTE_AGG=$(awk '{print $1}' "$LOGDIR/tree.md5")
  [ "$REMOTE_AGG" = "$LOCAL_AGG" ] && echo "STEP_OK tree_sync" || echo "STEP_WARN tree_drift local=$LOCAL_AGG remote=$REMOTE_AGG"
fi

run compileall compile.log "python -m compileall -q app tests alembic && echo COMPILE_OK"
run import_smoke import.log "python -c 'import app.main; print(\"IMPORT_OK\")'"
run alembic_upgrade alembic.log "python -m alembic upgrade head && python -m alembic current"
run alembic_check alembic_check.log "ALEMBIC_COMPARE_TYPES=0 python -m alembic check"
run schema_parity parity.log "python scripts/verify_schema_parity.py"

GATE=$(python - <<'PY'
import re
txt = open('/root/cds-test/src/.github/workflows/ci.yml', encoding='utf-8').read()
acc, started = [], False
for ln in txt.splitlines():
    if not started and "pytest" in ln and ln.rstrip().endswith("\\"):
        started = True
    if started:
        acc.append(ln)
        if not ln.rstrip().endswith("\\"):
            break
paths = re.findall(r"tests/[A-Za-z0-9_./-]+\.py", " ".join(acc))
seen, out = set(), []
for p in paths:
    if p not in seen:
        seen.add(p); out.append(p)
print(" ".join(out))
PY
)
echo "GATE_FILES=$(echo "$GATE" | wc -w)"
run gate gate.log "python -m pytest -q --tb=line -p no:cacheprovider $GATE"

SS15=$(ls tests/test_ss15_*.py 2>/dev/null | tr '\n' ' ')
[ -n "$SS15" ] && run ss15 ss15.log "python -m pytest -q --tb=line -p no:cacheprovider $SS15"

if [ "$MODE" = "full" ]; then
  run full full.log "python -m pytest -q --tb=line -p no:cacheprovider tests/ --ignore=tests/test_e2e_full_lifecycle.py"
fi

echo "LANE_DONE mode=$MODE"
for f in gate ss15 full; do
  [ -f "$LOGDIR/$f.log" ] && echo "SUMMARY $f: $(grep -aE '^[0-9]+ (passed|failed)|passed|failed' "$LOGDIR/$f.log" | tail -1)"
done
