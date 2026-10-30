"""A203 — the three remaining "size" columns must survive >2 GiB on PostgreSQL.

Boundary of this test (SQLite cannot reproduce the defect itself): SQLite's
INTEGER is already 64-bit, so the `IntegerOutOfRangeError` only ever fires on
PostgreSQL. What the local lane *can* prove is which SQLAlchemy type each
column carries and what the PostgreSQL DDL renders (`INTEGER` = 4-byte signed,
max 2,147,483,647, versus `BIGINT`).

`tests/test_ocr_a203_file_size_bigint.py` pins `data_resources.file_size_bytes`
(the first of the four columns the finding lists). This file pins the other
three — `trained_models.size_bytes` (model checkpoints),
`delivery_bundles.size_bytes` (delivered bundles) and
`rag_generative_models.size_bytes` (trained RAG weights) — plus the same
already-correct reference column (`cube_templates.size_bytes`), so a future
re-narrowing of any of them reddens here instead of surfacing in production on
the first 2 GB+ checkpoint/bundle write. The DDL half of the same change is
revision `0029_ocr_size_bigint_op_lease`.
"""
from sqlalchemy import BigInteger, Integer
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models.cube_template import CubeTemplate
from app.models.delivery_bundle import DeliveryBundle
from app.models.rag_generative_model import RAGGenerativeModel
from app.models.trained_model import TrainedModel

PG_INTEGER_MAX = 2_147_483_647

COLUMNS = [
    ("trained_models.size_bytes", TrainedModel, "size_bytes"),
    ("delivery_bundles.size_bytes", DeliveryBundle, "size_bytes"),
    ("rag_generative_models.size_bytes", RAGGenerativeModel, "size_bytes"),
]


def _pg_column_line(model, column: str) -> str:
    ddl = str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
    return next(ln.strip() for ln in ddl.splitlines() if ln.strip().startswith(column))


def test_a203_still_lists_exactly_these_three_columns():
    """Guard against silently narrowing the scope of this test."""
    assert len(COLUMNS) == 3
    assert [(m.__tablename__, c) for _, m, c in COLUMNS] == [
        ("trained_models", "size_bytes"),
        ("delivery_bundles", "size_bytes"),
        ("rag_generative_models", "size_bytes"),
    ]


def test_every_size_column_is_bigint():
    wrong = []
    for label, model, column in COLUMNS:
        col = model.__table__.c[column]
        if not isinstance(col.type, BigInteger):
            wrong.append(f"{label} is {type(col.type).__name__}")
    assert not wrong, (
        "; ".join(wrong)
        + f" — a value >{PG_INTEGER_MAX} bytes (~2 GiB) raises "
        "IntegerOutOfRangeError on PostgreSQL at INSERT time"
    )


def test_pg_ddl_declares_bigint_for_every_size_column():
    for label, model, column in COLUMNS:
        line = _pg_column_line(model, column)
        assert "BIGINT" in line.upper(), f"PostgreSQL would create {line!r} for {label}"
        assert " INTEGER" not in f" {line.upper()}", f"PostgreSQL would create {line!r} for {label}"


def test_orm_accepts_size_above_int4_max_for_every_column():
    value = PG_INTEGER_MAX + 1
    for label, model, column in COLUMNS:
        assert getattr(model(**{column: value}), column) == value, label


def test_matches_the_known_good_reference_column():
    """`cube_templates.size_bytes` is the same-repo, already-correct spelling (A203)."""
    reference = CubeTemplate.__table__.c.size_bytes.type
    assert isinstance(reference, BigInteger)
    for label, model, column in COLUMNS:
        coltype = model.__table__.c[column].type
        assert isinstance(coltype, type(reference)), label
        assert not (isinstance(coltype, Integer) and not isinstance(coltype, BigInteger)), label
