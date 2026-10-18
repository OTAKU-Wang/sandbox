#!/bin/bash
# Strict PG pre-deploy validation: alembic chain + create_all on real PostgreSQL.
# Every step checks the REAL exit code (no masking — see D-e2e-08 lesson).
set -u
cd /root || { echo "FATAL: cannot cd /root"; exit 1; }
[ -f /root/cds.env ] || { echo "FATAL: /root/cds.env missing"; exit 1; }
[ -f /root/cds-api3.tar.gz ] || { echo "FATAL: /root/cds-api3.tar.gz missing"; exit 1; }

PW=$(grep -m1 '^POSTGRES_PASSWORD=' /root/cds.env | cut -d= -f2- | tr -d '\r')
[ -n "$PW" ] || { echo "FATAL: POSTGRES_PASSWORD empty in /root/cds.env"; exit 1; }
case "$PW" in
  *[' @:/#?%']*) echo "FATAL: POSTGRES_PASSWORD contains URL-reserved chars; DB URLs would misparse"; exit 1;;
esac

# unique per-run DB names + trap cleanup: leftovers previously poisoned the
# next run (createdb fails on existing DB) and silent dropdb failures let a
# dirty pgv1 pass as ALL GREEN.
SUF="$$"
PGV1="pgv1_$SUF"; PGV2="pgv2_$SUF"
cleanup() {
  docker exec cds-postgres dropdb -U cds --if-exists "$PGV1" || echo "WARN: dropdb $PGV1 failed"
  docker exec cds-postgres dropdb -U cds --if-exists "$PGV2" || echo "WARN: dropdb $PGV2 failed"
}
trap cleanup EXIT

echo "=== load new api image ==="
LOADED=$(docker load -i /root/cds-api3.tar.gz) || { echo "FATAL: docker load failed"; exit 1; }
echo "$LOADED"
IMAGE=$(printf '%s' "$LOADED" | sed -n 's/^Loaded image: //p' | head -1)
[ -n "$IMAGE" ] || { echo "FATAL: could not parse image ref from docker load"; exit 1; }
rm -f /root/cds-api3.tar.gz

echo "=== fresh test DBs ($PGV1, $PGV2) ==="
docker exec cds-postgres createdb -U cds "$PGV1" || { echo "FATAL: createdb $PGV1 failed"; exit 1; }
docker exec cds-postgres createdb -U cds "$PGV2" || { echo "FATAL: createdb $PGV2 failed"; exit 1; }

echo "=== A: alembic full chain on PG ($PGV1) ==="
docker run --rm --network cds-net -e POSTGRES_PASSWORD="$PW" --entrypoint bash "$IMAGE" -c "
  export CDS_DATABASE_URL='postgresql+asyncpg://cds:'"$POSTGRES_PASSWORD"'@cds-postgres:5432/$PGV1'
  python -m alembic upgrade head
"
rc=$?
echo "ALEMBIC_PG_RC=$rc"
[ $rc -eq 0 ] || exit 1

echo "=== B: create_all on PG ($PGV2) ==="
docker run --rm --network cds-net -e POSTGRES_PASSWORD="$PW" --entrypoint bash "$IMAGE" -c "
  export CDS_DATABASE_URL='postgresql+asyncpg://cds:'"$POSTGRES_PASSWORD"'@cds-postgres:5432/$PGV2'
  python - <<'PYEOF'
import asyncio, os
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.database import Base
import app.models  # noqa: F401

async def go():
    e = create_async_engine(os.environ['CDS_DATABASE_URL'])
    async with e.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    await e.dispose()
    print('CREATE_ALL_PG_OK')

asyncio.run(go())
PYEOF
"
rc=$?
echo "CREATEALL_PG_RC=$rc"
[ $rc -eq 0 ] || exit 1

echo PG_VALIDATION_ALL_GREEN
