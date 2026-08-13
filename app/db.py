"""Database engine and session helpers.

Two dialects are supported:

- **CockroachDB (production)** — the agent's memory layer. A `postgresql://`
  URL (CockroachDB is wire-compatible) is used with the `psycopg` driver, and
  vector columns get a real distributed vector index (`vec_cosine_ops`), so
  semantic search is executed in the database at scale.
- **SQLite (local dev / tests)** — zero-config fallback. Vector columns are
  stored as JSON text and distance is computed in Python. The feature set is
  identical; only the execution strategy differs. This keeps the test suite
  hermetic (no cloud dependency) while the production path uses the real
  CockroachDB features.

SQLite is run in WAL mode so the APScheduler workers can write while the web
dashboard reads without hitting `database is locked`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.types import UserDefinedType
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

log = logging.getLogger(__name__)

_settings = get_settings()

_url = _settings.database_url
# Normalise the bare postgresql:// scheme to the psycopg driver (CockroachDB
# serverless connection strings come from the Cloud Console as postgresql://).
if _url.startswith("postgresql://"):
    _url = _url.replace("postgresql://", "postgresql+psycopg://", 1)
elif _url.startswith("cockroachdb://"):
    _url = _url.replace("cockroachdb://", "postgresql+psycopg://", 1)

engine: Engine = create_engine(
    _url,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=3600,
    # check_same_thread=False is required because APScheduler executes jobs on
    # worker threads distinct from the request threads holding sessions.
    connect_args={"check_same_thread": False, "timeout": 30} if _url.startswith("sqlite") else {},
)


def is_cockroach() -> bool:
    """True when the engine speaks the Postgres wire protocol (CockroachDB)."""
    return engine.dialect.name == "postgresql"


if _url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        # Wait rather than immediately erroring when a writer holds the lock.
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()


class VectorType(UserDefinedType):
    """A float-array column backed by CockroachDB's native VECTOR type.

    On CockroachDB/Postgres this emits `VECTOR(dims)` and supports the built-in
    distance operators (`vec_cosine_distance`, ...). On SQLite — which has no
    vector type — values are stored as JSON text and distance is computed in
    Python by `app.memory.vectors`. The application never needs to know which
    strategy is in play.
    """

    cache_ok = True

    def __init__(self, dims: int) -> None:
        self.dims = dims

    def get_col_spec(self, **kw: object) -> str:  # noqa: ARG002
        if is_cockroach():
            return f"VECTOR({self.dims})"
        return "JSON"

    def bind_processor(self, dialect: object) -> object:  # noqa: ARG001
        def process(value: object) -> object:
            if value is None:
                return None
            if isinstance(value, str):
                return value
            if is_cockroach():
                # CockroachDB accepts the array-literal string form.
                return "[" + ",".join(repr(float(v)) for v in value) + "]"
            return json.dumps(value)

        return process

    def result_processor(self, dialect: object, coltype: object) -> object:  # noqa: ARG002
        def process(value: object) -> object:
            if value is None:
                return None
            if isinstance(value, list):
                return [float(v) for v in value]
            if isinstance(value, str):
                try:
                    return [float(v) for v in json.loads(value)]
                except (ValueError, json.JSONDecodeError):
                    stripped = value.strip().strip("[]")
                    return [float(v) for v in stripped.split(",") if v != ""]
            return value

        return process


def init_db() -> None:
    """Create the data directories and tables, then reconcile columns. Idempotent."""
    _settings.ensure_dirs()
    # Import for side effects: registers every model on SQLModel.metadata.
    import app.models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    sync_schema()
    _ensure_vector_indexes()


def _ensure_vector_indexes() -> None:
    """Create the distributed vector index on CockroachDB when missing.

    SQLite has no vector index (search falls back to Python distance). The
    index enables approximate nearest-neighbour search in the database; without
    it, semantic queries over millions of rows would degrade to sequential
    scans. Both tables' embedding columns get one.
    """
    if not is_cockroach():
        return
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_job_embedding_vec "
            "ON job_embedding USING vec_cosine_ops (embedding)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_agent_memory_vec "
            "ON agent_memory USING vec_cosine_ops (embedding)"
        ))
    log.info("cockroachdb: vector indexes ensured")


# SQLite types for the column kinds the models actually use. Anything unmapped
# falls back to the SQLAlchemy compiler, which is correct for the simple types.
def _column_type(column) -> str:  # noqa: ANN001
    try:
        return column.type.compile(dialect=engine.dialect)
    except Exception:  # noqa: BLE001 - unknown type, let the engine infer
        return "TEXT"


def _default_clause(column) -> str:  # noqa: ANN001
    """Literal DEFAULT for backfilling existing rows, or "" when none applies.

    Only Python-side scalar defaults are translated. Callables (``utcnow``) and
    server defaults are skipped: existing rows get NULL and the model's default
    applies on next write, which is preferable to stamping every historical row
    with the migration's run time.
    """
    default = getattr(column, "default", None)
    if default is None or getattr(default, "is_callable", False):
        return ""
    value = getattr(default, "arg", None)
    if value is None or callable(value):
        return ""
    if isinstance(value, bool):
        return f" DEFAULT {1 if value else 0}"
    if isinstance(value, (int, float)):
        return f" DEFAULT {value}"
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f" DEFAULT '{escaped}'"
    return ""


def sync_schema() -> None:
    """Add columns present on the models but missing from existing tables.

    ``create_all`` only ever creates whole tables: a column added to a model
    after a table exists is silently absent, and every query touching it fails
    at runtime. This walks the model metadata against the live schema and issues
    the missing ``ALTER TABLE ... ADD COLUMN`` statements.

    Deliberately additive only. Dropped columns, renames and type changes are
    left alone, since guessing at those risks destroying user data — this is a
    safety net for the common case (a new nullable/defaulted field), not a
    general migration engine.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    added: list[str] = []
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all just made it, so it is already current.
            live_columns = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in live_columns:
                    continue
                if not column.nullable and column.default is None:
                    # A NOT NULL column without a default cannot be added to a
                    # table with existing rows on either dialect.
                    log.warning(
                        "schema: cannot auto-add %s.%s (NOT NULL, no default)",
                        table.name, column.name,
                    )
                    continue
                ddl = (
                    f"ALTER TABLE {table.name} "
                    f"ADD COLUMN {column.name} {_column_type(column)}"
                    f"{_default_clause(column)}"
                )
                conn.execute(text(ddl))
                added.append(f"{table.name}.{column.name}")

    if added:
        log.info("schema: added %d missing column(s): %s", len(added), ", ".join(added))


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background jobs.

    Commits on clean exit, rolls back on exception so a failed poll cycle cannot
    leave half-ingested rows behind.
    """
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
