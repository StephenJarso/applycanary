"""Vector search over the CockroachDB memory layer.

Two execution strategies, one interface:

- **CockroachDB** — nearest-neighbour is a SQL query using the distributed
  vector index (`vec_cosine_ops`) and the built-in `vec_cosine_distance`
  function. Scales to millions of rows; no separate vector store.
- **SQLite fallback** — embeddings are fetched and distance computed in Python.
  Functionally identical, correct for dev/tests/offline.

Callers never branch on the dialect; `semantic_search` hides it.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlmodel import Session, select

from app.db import is_cockroach
from app.memory.embeddings import cosine, embed_text
from app.models import AgentMemory, Job, JobEmbedding, utcnow

log = logging.getLogger(__name__)


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


async def embed_job(session: Session, job: Job) -> list[float]:
    """Ensure a JobEmbedding row exists for `job`; returns the vector.

    Idempotent: an existing embedding is returned untouched. The source text is
    title + company + location + description, which captures the semantic
    content a candidate cares about.
    """
    row = session.exec(
        select(JobEmbedding).where(JobEmbedding.job_id == job.id)
    ).first()
    if row is not None:
        return row.embedding or []

    source = "\n".join(
        filter(None, [job.title, job.company, job.location, job.description or ""])
    )
    vector = await embed_text(source, dims=1024)
    session.add(JobEmbedding(job_id=job.id, embedding=vector, dims=1024, model="titan-v2-or-local"))
    session.commit()
    return vector


async def backfill_embeddings(session: Session, limit: int = 200) -> int:
    """Embed jobs that have none yet; returns the count embedded.

    Called from the scheduler so the vector index fills in naturally as jobs
    arrive, and from an admin endpoint to catch up a large backlog.
    """
    missing = session.exec(
        select(Job)
        .outerjoin(JobEmbedding, JobEmbedding.job_id == Job.id)
        .where(JobEmbedding.id.is_(None))
        .where(Job.expired_at.is_(None))
        .limit(limit)
    ).all()
    for job in missing:
        await embed_job(session, job)
    if missing:
        log.info("embeddings: embedded %d job(s)", len(missing))
    return len(missing)


async def similar_jobs(
    session: Session,
    job_id: int,
    limit: int = 6,
) -> list[tuple[Job, float]]:
    """Jobs semantically closest to `job_id`, excluding itself and expired ones."""
    job = session.get(Job, job_id)
    if job is None:
        return []
    vector = await embed_job(session, job)
    if not vector:
        return []
    return await _search(session, table="job_embedding", vector=vector, limit=limit,
                         exclude_id=job_id, extra="AND j.expired_at IS NULL")


async def search_jobs(
    session: Session,
    query: str,
    limit: int = 12,
) -> list[tuple[Job, float]]:
    """Semantic job search: embed the query, return the closest postings."""
    vector = await embed_text(query, dims=1024)
    return await _search(session, table="job_embedding", vector=vector, limit=limit,
                         extra="AND j.expired_at IS NULL")


async def recall_memory(
    session: Session,
    user_id: int,
    query: str,
    limit: int = 5,
) -> list[tuple[AgentMemory, float]]:
    """Semantically recall the agent's stored memories relevant to `query`."""
    vector = await embed_text(query, dims=1024)
    return await _search(session, table="agent_memory", vector=vector, limit=limit,
                         extra=f"AND m.user_id = {int(user_id)}")


async def _search(
    session: Session,
    *,
    table: str,
    vector: list[float],
    limit: int,
    exclude_id: int | None = None,
    extra: str = "",
) -> list[tuple[object, float]]:
    """Nearest neighbours of `vector` in `table` (job_embedding | agent_memory).

    Returns [(row, similarity)] ordered best-first. `extra` is raw SQL appended
    to the WHERE clause — the callers above only pass constants, never user
    input, so string interpolation is safe here.
    """
    if is_cockroach():
        return await _search_sql(session, table, vector, limit, exclude_id, extra)
    return _search_python(session, table, vector, limit, exclude_id, extra)


def _python_scope(table: str, extra: str) -> str | None:
    """Extract a user scope from the SQL `extra` clause for the Python path."""
    if table != "agent_memory" or "m.user_id = " not in extra:
        return None
    value = extra.split("m.user_id = ", 1)[1].split(" ", 1)[0]
    return value.strip("'\"")


# ---------------------------------------------------------------- cockroachdb


async def _search_sql(
    session: Session,
    table: str,
    vector: list[float],
    limit: int,
    exclude_id: int | None,
    extra: str,
) -> list[tuple[object, float]]:
    lit = _vector_literal(vector)
    if table == "job_embedding":
        sql = text(
            "SELECT e.job_id AS ref_id, "
            "1 - vec_cosine_distance(e.embedding, :q::vector) AS sim "
            "FROM job_embedding e JOIN job j ON j.id = e.job_id "
            f"WHERE 1=1 {extra}"
            + (" AND e.job_id != :exclude" if exclude_id is not None else "")
            + " ORDER BY sim DESC LIMIT :limit"
        )
    else:
        sql = text(
            "SELECT m.id AS ref_id, "
            "1 - vec_cosine_distance(m.embedding, :q::vector) AS sim "
            f"FROM agent_memory m WHERE 1=1 {extra}"
            + (" AND m.id != :exclude" if exclude_id is not None else "")
            + " ORDER BY sim DESC LIMIT :limit"
        )

    params: dict = {"q": lit, "limit": limit}
    if exclude_id is not None:
        params["exclude"] = exclude_id
    rows = session.exec(sql, params).all()

    out: list[tuple[object, float]] = []
    for row in rows:
        ref_id = row.ref_id
        sim = float(row.sim)
        if table == "job_embedding":
            job = session.get(Job, ref_id)
            if job is not None:
                out.append((job, sim))
        else:
            mem = session.get(AgentMemory, ref_id)
            if mem is not None:
                out.append((mem, sim))
    return out


# ---------------------------------------------------------------- sqlite fallback


def _search_python(
    session: Session,
    table: str,
    vector: list[float],
    limit: int,
    exclude_id: int | None,
    extra: str = "",
) -> list[tuple[object, float]]:
    scope = _python_scope(table, extra)
    if table == "job_embedding":
        rows = session.exec(select(JobEmbedding)).all()
        scored: list[tuple[float, JobEmbedding]] = []
        for row in rows:
            if row.job_id == exclude_id or not row.embedding:
                continue
            scored.append((cosine(vector, row.embedding), row))
    else:
        rows = session.exec(select(AgentMemory)).all()
        scored = []
        for row in rows:
            if row.id == exclude_id or not row.embedding:
                continue
            if scope is not None and str(row.user_id) != scope:
                continue
            scored.append((cosine(vector, row.embedding), row))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    out: list[tuple[object, float]] = []
    for sim, row in scored[:limit]:
        if table == "job_embedding":
            job = session.get(Job, row.job_id)
            if job is not None:
                out.append((job, sim))
        else:
            out.append((row, sim))
    return out


# ---------------------------------------------------------------- memory writes


async def save_memory(
    session: Session,
    *,
    user_id: int,
    kind: str,
    content: str,
    metadata: dict | None = None,
) -> AgentMemory:
    """Persist a piece of agent memory with its embedding.

    The embedding is what makes later recall semantic: the agent queries this
    table by meaning, not by keyword, so "system design" surfaces a past note
    about distributed systems even when the words differ.
    """
    vector = await embed_text(content[:4000], dims=1024)
    entry = AgentMemory(
        user_id=user_id,
        kind=kind,
        content=content,
        embedding=vector,
        meta=metadata or {},
        created_at=utcnow(),
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry
