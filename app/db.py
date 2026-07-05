"""Database engine and session helpers.

SQLite is run in WAL mode so the APScheduler workers can write while the web
dashboard reads without hitting `database is locked`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

_settings = get_settings()

_is_sqlite = _settings.database_url.startswith("sqlite")

engine: Engine = create_engine(
    _settings.database_url,
    echo=False,
    # check_same_thread=False is required because APScheduler executes jobs on
    # worker threads distinct from the request threads holding sessions.
    connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {},
)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        # Wait rather than immediately erroring when a writer holds the lock.
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()


def init_db() -> None:
    """Create the data directories and tables. Idempotent."""
    _settings.ensure_dirs()
    # Import for side effects: registers every model on SQLModel.metadata.
    import app.models  # noqa: F401

    SQLModel.metadata.create_all(engine)


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
