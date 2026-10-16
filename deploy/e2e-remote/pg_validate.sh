#!/bin/bash
# Strict PG pre-deploy validation: alembic chain + create_all on real PostgreSQL.
# Every step checks the REAL exit code (no `| tail` masking — see D-e2e-08 lesson).
set -u
cd /root
PW=$(grep '^POSTGRES_PASSWORD=' /root/cds.env | cut -d= -f2)

echo "=== load new api image ==="
docker load -i /root/cds-api3.tar.gz
rc=$?
echo "LOAD_RC=$rc"
[ $rc -eq 0 ] || exit 1
rm -f /root/cds-api3.tar.gz

echo "=== fresh test DBs ==="
docker exec cds-postgres dropdb -U cds --if-exists pgv1 2>/dev/null
docker exec cds-postgres dropdb -U cds --if-exists pgv2 2>/dev/null
docker exec cds-postgres createdb -U cds pgv1
docker exec cds-postgres createdb -U cds pgv2
rc=$?
echo "DBS_RC=$rc"
[ $rc -eq 0 ] || exit 1

echo "=== A: alembic full chain on PG (pgv1) ==="
docker run --rm --network cds-net -e POSTGRES_PASSWORD="$PW" --entrypoint bash cds-api:latest -c "
  export CDS_DATABASE_URL='postgresql+asyncpg://cds:'\"\$POSTGRES_PASSWORD\"'@cds-postgres:5432/pgv1'
  python -m alembic upgrade head
"
rc=$?
echo "ALEMBIC_PG_RC=$rc"
[ $rc -eq 0 ] || exit 1

echo "=== B: create_all on PG (pgv2) ==="
docker run --rm --network cds-net -e POSTGRES_PASSWORD="$PW" --entrypoint bash cds-api:latest -c "
  export CDS_DATABASE_URL='postgresql+asyncpg://cds:'\"\$POSTGRES_PASSWORD\"'@cds-postgres:5432/pgv2'
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

echo "=== cleanup test DBs ==="
docker exec cds-postgres dropdb -U cds pgv1
docker exec cds-postgres dropdb -U cds pgv2
echo PG_VALIDATION_ALL_GREEN
