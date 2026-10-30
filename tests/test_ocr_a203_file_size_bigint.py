"""A203 — `data_resources.file_size_bytes` must survive >2 GiB on PostgreSQL.

Boundary of this test (SQLite cannot reproduce the defect): SQLite's INTEGER is
already 64-bit, so the overflow is invisible on the local lane. What SQLite
*can* prove is which SQLAlchemy type the column carries, and what the
PostgreSQL DDL renders — `INTEGER` (4-byte signed, max 2,147,483,647) versus
`BIGINT`. Both are pinned here, plus the reference implementation the repo
already uses (`cube_template.size_bytes`), so a future re-narrowing of this
column reddens the test instead of surfacing in production on the first
2 GB+ upload.
"""
from sqlalchemy import BigInteger, Integer
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models.data_resource import DataResource

PG_INTEGER_MAX = 2_147_483_647


def _pg_ddl() -> str:
    return str(CreateTable(DataResource.__table__).compile(dialect=postgresql.dialect()))


def test_file_size_bytes_column_is_bigint():
    col = DataResource.__table__.c.file_size_bytes
    assert isinstance(col.type, BigInteger), (
        f"file_size_bytes is {type(col.type).__name__}; a value >{PG_INTEGER_MAX} "
        "bytes (~2 GiB) raises IntegerOutOfRangeError on PostgreSQL"
    )
    assert type(col.type) is BigInteger


def test_pg_ddl_declares_bigint():
    ddl = _pg_ddl()
    line = next(ln.strip() for ln in ddl.splitlines() if ln.strip().startswith("file_size_bytes"))
    assert "BIGINT" in line.upper(), f"PostgreSQL would create {line!r}"
    assert " INTEGER" not in f" {line.upper()}"


def test_orm_accepts_size_above_int4_max():
    """The value the production upload path writes must survive the column type."""
    value = PG_INTEGER_MAX + 1
    assert DataResource(file_size_bytes=value).file_size_bytes == value


def test_matches_the_known_good_reference_column():
    """`cube_template.size_bytes` is the same-repo, already-correct spelling (A203)."""
    from app.models.cube_template import CubeTemplate

    reference = CubeTemplate.__table__.c.size_bytes.type
    assert isinstance(reference, BigInteger)
    assert isinstance(DataResource.__table__.c.file_size_bytes.type, type(reference))
    assert not isinstance(DataResource.__table__.c.file_size_bytes.type, Integer) or isinstance(
        DataResource.__table__.c.file_size_bytes.type, BigInteger
    )
