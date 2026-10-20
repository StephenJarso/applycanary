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

from sqlalchemy import event, text
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


_is_cockroach: bool | None = None


def is_cockroach() -> bool:
    """True when the server is actually CockroachDB, not vanilla Postgres.

    Both speak the postgres dialect, so the dialect name alone cannot tell
    them apart (this once sent a vanilla Postgres down the CockroachDB code path and died
    on `USING vec_cosine_ops`). CockroachDB reports a `crdb_version` server
    setting that Postgres lacks; probe it once and cache the answer.
    """
    global _is_cockroach  # noqa: PLW0603
    if _is_cockroach is None:
        if engine.dialect.name != "postgresql":
            _is_cockroach = False
        else:
            try:
                with engine.connect() as conn:
                    row = conn.execute(text(
                        "SELECT EXISTS (SELECT 1 FROM pg_settings "
                        "WHERE name = 'crdb_version')"
                    )).scalar()
                _is_cockroach = bool(row)
            except Exception:  # noqa: BLE001 - probe failure => assume vanilla
                _is_cockroach = False
    return _is_cockroach


# Which vector strategy the current engine supports, discovered lazily:
#   "cockroach" — native VECTOR + vec_cosine_ops (CockroachDB Cloud)
#   "pgvector"  — vanilla Postgres with the pgvector extension (e.g. Supabase)
#   "python"    — embeddings as JSON, distances computed in Python (SQLite)
_vector_backend: str | None = None


def vector_backend() -> str:
    """Resolve (once) which embedding storage/distance backend is in use.

    Plain Postgres without pgvector degrades to "python": the columns stay
    JSON and search runs in-process, exactly like SQLite — correct, just not
    index-accelerated. This keeps a random free Postgres (no extension perms)
    from breaking boot.
    """
    global _vector_backend  # noqa: PLW0603
    if _vector_backend is None:
        if engine.dialect.name == "sqlite":
            _vector_backend = "python"
        elif is_cockroach():
            _vector_backend = "cockroach"
        else:
            try:
                with engine.begin() as conn:
                    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                _vector_backend = "pgvector"
            except Exception:  # noqa: BLE001 - no permission / old Postgres
                log.warning(
                    "postgres detected but pgvector unavailable — "
                    "falling back to in-Python vector search"
                )
                _vector_backend = "python"
    return _vector_backend


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
    """A float-array column backed by the engine's native vector type.

    CockroachDB emits `VECTOR(dims)`; vanilla Postgres with pgvector emits
    `vector(dims)` (same literal syntax — both accept the `'[1,2,...]'` string
    form, and pgvector's cosine distance is the `<=>` operator). Engines with
    no vector type (SQLite, or Postgres without the extension) store JSON text
    and get in-Python distances from `app.memory.vectors`. The application
    never needs to know which strategy is in play.
    """

    cache_ok = True

    def __init__(self, dims: int) -> None:
        self.dims = dims

    def get_col_spec(self, **kw: object) -> str:  # noqa: ARG002
        if vector_backend() in ("cockroach", "pgvector"):
            return f"VECTOR({self.dims})" if is_cockroach() else f"vector({self.dims})"
        return "JSON"

    def bind_processor(self, dialect: object) -> object:  # noqa: ARG001
        def process(value: object) -> object:
            if value is None:
                return None
            if isinstance(value, str):
                return value
            if vector_backend() in ("cockroach", "pgvector"):
                # Both accept the array-literal string form.
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
    """Create the approximate-nearest-neighbour index for the active backend.

    ``vec_cosine_ops`` on CockroachDB, ``hnsw`` + ``vector_cosine_ops`` on
    pgvector (vanilla Postgres/Supabase), and nothing on the Python fallback —
    SQLite has no vector index, and in-process cosine is fast enough at small
    row counts. Without an ANN index, semantic queries would degrade to
    sequential scans. Both tables' embedding columns get one.
    """
    from sqlalchemy import text

    if is_cockroach():
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
    elif vector_backend() == "pgvector":
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_job_embedding_vec "
                "ON job_embedding USING hnsw (embedding vector_cosine_ops)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_agent_memory_vec "
                "ON agent_memory USING hnsw (embedding vector_cosine_ops)"
            ))
        log.info("pgvector: hnsw cosine indexes ensured")


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
        return f" DEFAULT {'TRUE' if value else 'FALSE'}"
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

        # Accounts that predate email verification have already proven
        # themselves by other means, and the operator never had a chance to ask
        # them to confirm. Leaving them at the column default would nag every
        # existing user, and would lock them all out the moment
        # REQUIRE_EMAIL_VERIFICATION is switched on. Grandfather them in — but
        # only on the migration that introduces the column, so this never
        # re-verifies someone who is legitimately pending.
        if "user.email_verified" in added:
            result = conn.execute(text("UPDATE \"user\" SET email_verified = TRUE"))
            log.info(
                "schema: grandfathered %s pre-existing account(s) as email-verified",
                result.rowcount,
            )

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
