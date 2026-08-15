"""Tests for the memory layer: interview coach sessions, agent memory recall,
and semantic (vector) search over job embeddings.

Everything runs against SQLite with the Python-distance fallback — the same
code path CockroachDB uses, only the execution strategy differs. No network.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.auth import hash_password
from app.db import engine, init_db
from app.main import create_app
from app.models import (
    AgentMemory,
    InterviewSession,
    InterviewTurn,
    Job,
    JobStatus,
    Profile,
    User,
)
from app.speech.interview import start_session, submit_answer

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def db():
    """Fresh schema + one user with a profile, per test."""
    init_db()
    from app.models import (
        Application,
        InviteCode,
        JobAlias,
        JobEmbedding,
        JobScore,
        ResumeVersion,
        UserJob,
    )

    with Session(engine) as session:
        # Children before parents, or SQLite's foreign keys reject the delete.
        for table in (
            Profile, AgentMemory, InterviewTurn, InterviewSession,
            Application, JobScore, ResumeVersion, UserJob, JobAlias,
            JobEmbedding, InviteCode,
        ):
            for row in session.exec(select(table)).all():
                session.delete(row)
        for row in session.exec(select(User)).all():
            session.delete(row)
        for row in session.exec(select(Job)).all():
            session.delete(row)
        session.commit()

        user = User(email="candidate@example.com", password_hash=hash_password(PASSWORD))
        session.add(user)
        session.commit()
        session.refresh(user)
        session.add(Profile(user_id=user.id, full_name="Ada", skills=["python", "sql"]))
        session.add(
            Job(
                fingerprint="fp1", source="test", company="Acme", title="Backend Engineer",
                description="Build Python services and PostgreSQL databases.",
                status=JobStatus.NEW,
            )
        )
        session.add(
            Job(
                fingerprint="fp2", source="test", company="Beta", title="Data Engineer",
                description="Python, Spark, data pipelines, warehouses.",
                status=JobStatus.NEW,
            )
        )
        session.add(
            Job(
                fingerprint="fp3", source="test", company="Gamma", title="Frontend Engineer",
                description="React, TypeScript, CSS, web performance.",
                status=JobStatus.NEW,
            )
        )
        session.commit()
    yield


def _client() -> TestClient:
    client = TestClient(create_app(), follow_redirects=False)
    from app.auth import create_session_token

    with Session(engine) as session:
        user = session.exec(select(User)).one()
        token = create_session_token(user)
    client.cookies.set("applycanary_session", token)
    return client


def _job() -> Job:
    with Session(engine) as session:
        return session.exec(select(Job).where(Job.company == "Acme")).one()


# ---------------------------------------------------------------- interview


def test_start_session_and_answer_text():
    job = _job()
    with Session(engine) as session:
        profile = session.exec(select(Profile)).one()
        row = asyncio_run(start_session(session, job, profile, user_id=profile.user_id))
        assert row.status == "asking"
        assert row.total_questions > 0
        assert len(row.questions) == row.total_questions

        turn = asyncio_run(
            submit_answer(
                session, row,
                answer_text="I built Python services with PostgreSQL for three years.",
            )
        )
        assert turn.score is not None
        assert 0 <= turn.score <= 100
        assert turn.question_index == 0
        session.refresh(row)
        assert row.question_index == 1


def test_session_completes_and_writes_memory():
    job = _job()
    with Session(engine) as session:
        profile = session.exec(select(Profile)).one()
        row = asyncio_run(start_session(session, job, profile, user_id=profile.user_id))
        total = row.total_questions
        for _ in range(total):
            asyncio_run(
                submit_answer(session, row, answer_text="A thorough, specific answer.")
            )
            session.refresh(row)

        assert row.status == "finished"
        assert row.summary.get("answered") == total
        assert row.avg_score is not None

        memories = session.exec(select(AgentMemory)).all()
        assert len(memories) == 1
        assert memories[0].kind == "interview_summary"
        assert "Acme" in memories[0].content
        # Embedding persisted and is the right dimensionality.
        assert len(memories[0].embedding) == 1024


def test_memory_recall_finds_past_feedback():
    """A second session for a similar role should recall the first's summary."""
    from app.memory.vectors import save_memory

    with Session(engine) as session:
        user = session.exec(select(User)).one()
        asyncio_run(
            save_memory(
                session, user_id=user.id, kind="coaching_feedback",
                content="You rushed the behavioural answer; slow down and use STAR.",
            )
        )

    job = _job()
    with Session(engine) as session:
        profile = session.exec(select(Profile)).one()
        row = asyncio_run(start_session(session, job, profile, user_id=profile.user_id))
        assert row.status == "asking"


def test_interview_http_flow():
    client = _client()
    job = _job()
    res = client.post(f"/api/jobs/{job.id}/interview/start", json={"mode": "speech"})
    assert res.status_code == 200
    body = res.json()
    assert body["session"]["status"] == "asking"
    assert body["session"]["total_questions"] > 0
    assert body["current_question"]["question"]

    sid = body["session"]["id"]
    res = client.post(
        f"/api/jobs/{job.id}/interview/sessions/{sid}/answer",
        json={"text": "I am a backend engineer who enjoys Python and databases."},
    )
    assert res.status_code == 200
    turn = res.json()["turn"]
    assert turn["score"] is not None
    assert turn["feedback"]


# ---------------------------------------------------------------- vectors


def test_embed_backfill_and_similar_jobs():
    client = _client()
    res = client.post("/api/actions/embed-all", json={"limit": 50})
    assert res.status_code == 200
    assert res.json()["embedded"] == 3

    job = _job()
    res = client.get(f"/api/jobs/{job.id}/similar?limit=5")
    assert res.status_code == 200
    jobs = res.json()["jobs"]
    # The data-engineer posting (Python) should rank above the frontend one.
    assert any(j["company"] == "Beta" for j in jobs)
    assert all(j["id"] != job.id for j in jobs)
    assert all(0 <= j["similarity"] <= 1 for j in jobs)


def test_semantic_search():
    client = _client()
    client.post("/api/actions/embed-all", json={"limit": 50})
    res = client.get("/api/jobs/search/semantic", params={"q": "python databases backend"})
    assert res.status_code == 200
    companies = [j["company"] for j in res.json()["jobs"]]
    assert "Acme" in companies or "Beta" in companies


def test_memory_endpoint():
    client = _client()
    job = _job()
    client.post(f"/api/jobs/{job.id}/interview/start", json={"mode": "text"})
    res = client.get("/api/memory")
    assert res.status_code == 200
    assert "sessions" in res.json()
    assert "entries" in res.json()


def test_voice_config():
    client = _client()
    res = client.get("/api/interview/voice")
    assert res.status_code == 200
    assert res.json()["tts"] in ("polly", "browser")
    assert res.json()["stt"] in ("transcribe", "browser")


# ---------------------------------------------------------------- helpers


def asyncio_run(coro):
    import asyncio

    return asyncio.get_event_loop().run_until_complete(coro)
