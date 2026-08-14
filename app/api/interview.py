"""API for the AI interview coach, vector search, and agent memory.

Mounted at /api alongside the rest of the dashboard API. All endpoints are
per-user: sessions, turns, and memories are scoped by user_id so one user can
never read another's interview history or coaching feedback.
"""

from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.config import get_settings
from app.db import get_session
from app.deps import current_user
from app.models import (
    AgentMemory,
    InterviewSession,
    InterviewTurn,
    Job,
    Profile,
    User,
)
from app.speech import interview as coach
from app.speech.polly import synthesize
from app.speech.transcribe import transcribe_audio
from app.storage import save_audio

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["interview"])


# ---------------------------------------------------------------- schemas


class StartInterviewIn(BaseModel):
    mode: str = Field(default="speech", pattern="^(speech|text)$")


class AnswerIn(BaseModel):
    text: str = ""
    audio_b64: str = ""
    duration_seconds: int = 0


class TtsIn(BaseModel):
    text: str


class TurnOut(BaseModel):
    id: int
    question_index: int
    question: str
    expected_key_points: list[str]
    time_minutes: int | None = None
    rubric: dict
    answer_text: str
    score: float | None = None
    feedback: str
    strengths: list[str]
    improvements: list[str]
    model_used: str
    created_at: object | None = None


class SessionOut(BaseModel):
    id: int
    job_id: int
    status: str
    mode: str
    question_index: int
    total_questions: int
    avg_score: float | None = None
    summary: dict = Field(default_factory=dict)
    finished: bool = False
    started_at: object | None = None
    finished_at: object | None = None


class InterviewState(BaseModel):
    session: SessionOut
    current_question: dict | None = None
    turn: TurnOut | None = None
    memory: list[dict] = Field(default_factory=list)


def _session_out(row: InterviewSession) -> SessionOut:
    return SessionOut(
        id=row.id or 0,
        job_id=row.job_id,
        status=row.status,
        mode=row.mode,
        question_index=row.question_index,
        total_questions=row.total_questions,
        avg_score=row.avg_score,
        summary=row.summary or {},
        finished=row.status == "finished",
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _turn_out(turn: InterviewTurn) -> TurnOut:
    return TurnOut(
        id=turn.id or 0,
        question_index=turn.question_index,
        question=turn.question,
        expected_key_points=list(turn.expected_key_points or []),
        time_minutes=turn.time_minutes,
        rubric=turn.rubric or {},
        answer_text=turn.answer_text,
        score=turn.score,
        feedback=turn.feedback,
        strengths=list(turn.strengths or []),
        improvements=list(turn.improvements or []),
        model_used=turn.model_used,
        created_at=turn.created_at,
    )


def _get_session_or_404(session: Session, session_id: int, user: User) -> InterviewSession:
    row = session.get(InterviewSession, session_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "interview session not found")
    return row


# ---------------------------------------------------------------- voice config


@router.get("/interview/voice")
def voice_config() -> dict:
    """Tell the frontend which speech engines are active.

    The interview works either way: AWS Polly/Transcribe when configured,
    browser speechSynthesis/SpeechRecognition otherwise. The client picks once
    at session start so the UX is stable mid-interview.
    """
    settings = get_settings()
    return {
        "tts": "polly" if settings.polly_enabled else "browser",
        "stt": "transcribe" if settings.transcribe_enabled else "browser",
        "voice_id": settings.polly_voice_id if settings.polly_enabled else "",
        "aws_enabled": settings.aws_enabled,
    }


@router.post("/interview/tts")
async def tts(payload: TtsIn) -> dict:
    """Synthesize a line of interviewer speech with Amazon Polly."""
    audio = await synthesize(payload.text)
    if audio is None:
        raise HTTPException(503, "Polly is not configured; use the browser fallback")
    return {"audio_b64": base64.b64encode(audio).decode(), "content_type": "audio/mpeg"}


# ---------------------------------------------------------------- interview


@router.post("/jobs/{job_id}/interview/start", response_model=InterviewState)
async def start_interview(
    job_id: int,
    payload: StartInterviewIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> InterviewState:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    profile = session.exec(
        select(Profile).where(Profile.user_id == user.id)
    ).first()
    if profile is None:
        raise HTTPException(400, "create a profile and upload a resume first")

    try:
        row = await coach.start_session(
            session, job, profile, user_id=user.id, mode=payload.mode
        )
    except coach.InterviewError as exc:
        raise HTTPException(400, str(exc)) from exc

    # Surface the coach's recalled memory so the UI can show continuity.
    from app.memory.vectors import recall_memory

    recalled = await recall_memory(
        session, user.id, f"past interview coaching for {job.title} at {job.company}", limit=3
    )
    memory = [
        {"kind": mem.kind, "content": mem.content, "created_at": mem.created_at}
        for mem, _sim in recalled
    ]
    current = row.questions[row.question_index] if row.questions else None
    return InterviewState(
        session=_session_out(row), current_question=current, memory=memory
    )


@router.post("/jobs/{job_id}/interview/sessions/{session_id}/answer", response_model=InterviewState)
async def answer_question(
    job_id: int,
    session_id: int,
    payload: AnswerIn,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> InterviewState:
    row = _get_session_or_404(session, session_id, user)
    if row.job_id != job_id:
        raise HTTPException(404, "interview session not found")

    answer_text = (payload.text or "").strip()
    audio_key = ""
    if payload.audio_b64:
        audio_bytes = base64.b64decode(payload.audio_b64)
        if len(audio_bytes) > 90 * 16000 * 2:
            raise HTTPException(413, "audio too long (90s max)")
        if not answer_text:
            transcribed = await transcribe_audio(audio_bytes)
            if transcribed is None:
                raise HTTPException(
                    503,
                    "Transcribe is not configured; record with browser speech "
                    "recognition instead (stt=browser)",
                )
            answer_text = transcribed
        if answer_text:
            audio_key = await save_audio(
                user_id=user.id, session_id=session_id,
                question_index=row.question_index, audio_bytes=audio_bytes,
            )

    if len(answer_text) < 4:
        raise HTTPException(400, "answer is empty — speak up or type a response")

    try:
        turn = await coach.submit_answer(
            session, row,
            answer_text=answer_text, audio_key=audio_key,
            duration_seconds=payload.duration_seconds,
        )
    except coach.InterviewError as exc:
        raise HTTPException(409, str(exc)) from exc

    session.refresh(row)
    current = (
        row.questions[row.question_index]
        if row.status == "asking" and row.questions and row.question_index < row.total_questions
        else None
    )
    return InterviewState(
        session=_session_out(row), current_question=current, turn=_turn_out(turn)
    )


@router.get("/interview/sessions/{session_id}", response_model=InterviewState)
def interview_state(
    session_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> InterviewState:
    row = _get_session_or_404(session, session_id, user)
    turns = session.exec(
        select(InterviewTurn)
        .where(InterviewTurn.session_id == session_id)
        .order_by(InterviewTurn.question_index)
    ).all()
    current = (
        row.questions[row.question_index]
        if row.status == "asking" and row.questions and row.question_index < row.total_questions
        else None
    )
    return InterviewState(
        session=_session_out(row),
        current_question=current,
        turn=_turn_out(turns[-1]) if turns else None,
    )


# ---------------------------------------------------------------- memory


@router.get("/memory")
def agent_memory(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
    limit: int = 20,
) -> dict:
    """The agent's memory of this user: interview history and stored insights.

    `entries` are the raw agent-memory rows (interview summaries and coaching
    feedback), `sessions` are the interview sessions with their scores, and
    `trend` is the average score over the last few sessions — the story the
    memory layer can tell about improvement.
    """
    entries = session.exec(
        select(AgentMemory)
        .where(AgentMemory.user_id == user.id)
        .order_by(AgentMemory.created_at.desc())
        .limit(limit)
    ).all()
    sessions = session.exec(
        select(InterviewSession)
        .where(InterviewSession.user_id == user.id)
        .order_by(InterviewSession.started_at.desc())
        .limit(limit)
    ).all()

    scored = [s for s in reversed(sessions) if s.avg_score is not None]
    trend = (
        [{"score": s.avg_score, "date": s.finished_at or s.started_at} for s in scored]
        if len(scored) >= 1
        else []
    )

    return {
        "entries": [
            {
                "id": e.id, "kind": e.kind, "content": e.content,
                "metadata": e.meta or {}, "created_at": e.created_at,
            }
            for e in entries
        ],
        "sessions": [
            {
                "id": s.id, "job_id": s.job_id, "status": s.status, "mode": s.mode,
                "avg_score": s.avg_score, "summary": s.summary or {},
                "started_at": s.started_at, "finished_at": s.finished_at,
            }
            for s in sessions
        ],
        "trend": trend,
        "counts": {
            "sessions": len(sessions),
            "memories": len(entries),
        },
    }


# ---------------------------------------------------------------- vector search


@router.get("/jobs/{job_id}/similar")
async def similar_jobs(
    job_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),  # noqa: ARG001 - auth gate; data is shared
    limit: int = 6,
) -> dict:
    """Semantically similar postings, via the distributed vector index."""
    from app.memory.vectors import similar_jobs

    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    try:
        rows = await similar_jobs(session, job_id, limit=min(limit, 12))
    except Exception as exc:  # noqa: BLE001
        log.exception("similar jobs failed")
        raise HTTPException(503, f"vector search failed: {exc}") from exc
    return {
        "jobs": [
            {
                "id": j.id, "title": j.title, "company": j.company,
                "location": j.location, "is_remote": j.is_remote,
                "source": j.source, "apply_url": j.apply_url,
                "salary_min": j.salary_min, "salary_max": j.salary_max,
                "salary_currency": j.salary_currency,
                "salary_is_estimate": j.salary_is_estimate,
                "posted_at": j.posted_at, "first_seen_at": j.first_seen_at,
                "similarity": round(sim, 3),
            }
            for j, sim in rows
        ],
        "embedded": True,
    }


@router.get("/jobs/search/semantic")
async def semantic_search(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),  # noqa: ARG001
    q: str = "",
    limit: int = 12,
) -> dict:
    """Semantic job search: embed the query, return the closest postings."""
    if not q.strip():
        raise HTTPException(400, "query is empty")
    from app.memory.vectors import search_jobs

    rows = await search_jobs(session, q.strip(), limit=min(limit, 24))
    return {
        "query": q,
        "jobs": [
            {
                "id": j.id, "title": j.title, "company": j.company,
                "location": j.location, "is_remote": j.is_remote,
                "source": j.source, "apply_url": j.apply_url,
                "salary_min": j.salary_min, "salary_max": j.salary_max,
                "salary_currency": j.salary_currency,
                "salary_is_estimate": j.salary_is_estimate,
                "posted_at": j.posted_at, "first_seen_at": j.first_seen_at,
                "similarity": round(sim, 3),
            }
            for j, sim in rows
        ],
    }


@router.post("/actions/embed-all")
async def embed_all(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),  # noqa: ARG001
    limit: int = 100,
) -> dict:
    """Backfill embeddings for jobs missing them (idempotent, rate-friendly)."""
    from app.memory.vectors import backfill_embeddings

    count = await backfill_embeddings(session, limit=min(limit, 500))
    return {"ok": True, "embedded": count}
