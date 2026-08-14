"""The AI interview coach — the agentic heart of ApplyCanary.

A spoken (or typed) mock interview against a real posting:

1. **Start** — the coach pulls the pre-generated speech/behavioural questions,
   recalls what it already knows about this candidate (semantic memory search),
   and opens a session row in CockroachDB. Reload the page and the interview
   resumes where it stopped: the session state is the memory layer.
2. **Answer** — the candidate's spoken answer is transcribed (Amazon Transcribe,
   or the browser's speech API), then evaluated by the LLM against the question's
   rubric *and* the coach's recalled memory of past sessions ("you rushed the
   last behavioural answer — slow down and use STAR").
3. **Finish** — a summary is computed, persisted as a new agent memory entry,
   and the next session starts smarter.

Every evaluation is grounded in the user's actual resume and the actual posting
— the same honesty rules as the rest of the app: the coach scores what the
candidate said, not what they wish they had said.

Without any LLM key the coach degrades to a keyword-based scoring heuristic so
the feature still demos; with an LLM provider (Gemini / OpenRouter / Groq /
Ollama / Bedrock) it gives real, rubric-aware coaching.
"""

from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.llm.client import get_llm
from app.llm.prompts import INTERVIEW_COACH_SYSTEM, build_interview_answer_user
from app.memory.vectors import recall_memory, save_memory
from app.models import (
    InterviewPrep,
    InterviewSession,
    InterviewTurn,
    Job,
    Profile,
    utcnow,
)

log = logging.getLogger(__name__)

MAX_QUESTIONS = 6
MIN_ANSWER_CHARS = 8


class InterviewError(RuntimeError):
    pass


def _question_sim(raw: dict) -> dict:
    """Normalise one prep question entry to {question, key_points, time, rubric}."""
    question = str(raw.get("question") or "").strip()
    if not question:
        return {}
    return {
        "question": question,
        "expected_key_points": [str(p) for p in (raw.get("expected_key_points") or []) if str(p).strip()],
        "time_minutes": raw.get("time_minutes"),
        "evaluation_rubric": raw.get("evaluation_rubric") or {},
    }


def _build_question_list(prep: InterviewPrep | None, mode: str) -> list[dict]:
    """Ordered questions for this session, snapshot from prep."""
    if prep is None:
        return []
    if mode == "speech":
        raw = (prep.speech_interview or []) or (prep.behavioural_questions or [])
    else:
        raw = list(prep.technical_questions or []) + list(prep.behavioural_questions or [])
    out = [_question_sim(q) for q in raw if _question_sim(q)]
    return out[:MAX_QUESTIONS]


def _fallback_questions(job: Job, skills: list[str]) -> list[dict]:
    """Deterministic question set when no LLM prep exists (demo mode)."""
    skills_block = ", ".join(skills[:4]) if skills else "your background"
    questions = [
        {"question": f"Tell me about yourself and why you are a fit for {job.title} at {job.company}.",
         "expected_key_points": ["relevant experience", "fit with the role", "genuine interest"],
         "time_minutes": 3, "evaluation_rubric": {}},
        {"question": f"What draws you to this {job.title} role specifically?",
         "expected_key_points": ["specifics from the posting", "career direction"],
         "time_minutes": 3, "evaluation_rubric": {}},
        {"question": f"Walk me through a project that best demonstrates your {skills_block}.",
         "expected_key_points": ["clear situation/task", "your specific actions", "measured result"],
         "time_minutes": 5, "evaluation_rubric": {}},
        {"question": "What is a weakness you have, and how are you working on it?",
         "expected_key_points": ["honest, non-fatal weakness", "concrete improvement steps"],
         "time_minutes": 3, "evaluation_rubric": {}},
        {"question": "Do you have any questions for us?",
         "expected_key_points": ["questions that show engagement with the role/company"],
         "time_minutes": 2, "evaluation_rubric": {}},
    ]
    return questions[:MAX_QUESTIONS]


# ---------------------------------------------------------------- lifecycle


async def start_session(
    session: Session,
    job: Job,
    profile: Profile,
    *,
    user_id: int,
    mode: str = "speech",
) -> InterviewSession:
    """Open a new interview session for a job.

    Prefers the pre-generated speech questions; falls back to a deterministic
    question set so the feature works before any LLM prep has run.
    """
    prep = session.exec(
        select(InterviewPrep).where(
            InterviewPrep.job_id == job.id, InterviewPrep.user_id == user_id
        )
    ).first()
    questions = _build_question_list(prep, mode) or _fallback_questions(
        job, list(profile.skills or [])
    )
    if not questions:
        raise InterviewError("no questions available for this job")

    session_row = InterviewSession(
        user_id=user_id,
        job_id=job.id,
        status="asking",
        mode=mode,
        questions=questions,
        question_index=0,
        total_questions=len(questions),
        started_at=utcnow(),
    )
    session.add(session_row)
    session.commit()
    session.refresh(session_row)
    log.info(
        "interview session %s started for job %s (mode=%s, %d questions)",
        session_row.id, job.id, mode, len(questions),
    )
    return session_row


async def submit_answer(
    session: Session,
    session_row: InterviewSession,
    *,
    answer_text: str,
    audio_key: str = "",
    duration_seconds: int = 0,
) -> InterviewTurn:
    """Evaluate the current question's answer and advance the session."""
    if session_row.status != "asking":
        raise InterviewError("this interview is not accepting answers")
    if session_row.question_index >= session_row.total_questions:
        raise InterviewError("interview already complete")

    q = session_row.questions[session_row.question_index]
    question = str(q.get("question") or "")
    key_points = [str(p) for p in (q.get("expected_key_points") or [])]
    rubric = q.get("evaluation_rubric") or {}
    time_minutes = q.get("time_minutes")

    job = session.get(Job, session_row.job_id)
    profile = session.exec(
        select(Profile).where(Profile.user_id == session_row.user_id)
    ).first()

    # The coach's memory: semantically recalled past feedback, folded into the
    # evaluation. This is the agentic-memory payoff — the coach gets better
    # with every session.
    recalled = await recall_memory(
        session, session_row.user_id,
        f"interview feedback for {job.title if job else ''} at {job.company if job else ''}",
        limit=3,
    )
    memory_context = "\n".join(
        f"- ({mem.kind}) {mem.content[:400]}" for mem, _sim in recalled
    )

    evaluation = await _evaluate(
        question=question,
        key_points=key_points,
        rubric=rubric,
        answer=answer_text,
        resume=(profile.base_resume_text if profile else "") or "",
        memory_context=memory_context,
    )

    turn = InterviewTurn(
        session_id=session_row.id,
        user_id=session_row.user_id,
        job_id=session_row.job_id,
        question_index=session_row.question_index,
        question=question,
        expected_key_points=key_points,
        time_minutes=time_minutes,
        rubric=rubric,
        answer_text=answer_text[:4000],
        answer_audio=audio_key,
        duration_seconds=duration_seconds,
        score=evaluation.get("score"),
        feedback=evaluation.get("feedback", ""),
        strengths=evaluation.get("strengths", []),
        improvements=evaluation.get("improvements", []),
        model_used=evaluation.get("model_used", ""),
        created_at=utcnow(),
    )
    session.add(turn)
    session.commit()
    session.refresh(turn)

    session_row.question_index += 1
    if session_row.question_index >= session_row.total_questions:
        await _finalize(session, session_row, job)
    else:
        session.add(session_row)
        session.commit()
    return turn


async def _finalize(session: Session, session_row: InterviewSession, job: Job | None) -> None:
    """Compute the aggregate report and persist it as agent memory."""
    turns = session.exec(
        select(InterviewTurn)
        .where(InterviewTurn.session_id == session_row.id)
        .order_by(InterviewTurn.question_index)
    ).all()
    scores = [t.score for t in turns if t.score is not None]
    strengths: list[str] = []
    improvements: list[str] = []
    for t in turns:
        strengths.extend(t.strengths or [])
        improvements.extend(t.improvements or [])

    def _top(items: list[str], n: int = 3) -> list[str]:
        seen: list[str] = []
        for item in items:
            item = item.strip()
            if item and item not in seen:
                seen.append(item)
        return seen[:n]

    avg = round(sum(scores) / len(scores), 1) if scores else None
    summary = {
        "avg_score": avg,
        "strengths": _top(strengths),
        "improvements": _top(improvements),
        "answered": len(turns),
        "total": session_row.total_questions,
        "model_used": (turns[-1].model_used if turns else ""),
    }
    session_row.summary = summary
    session_row.avg_score = avg
    session_row.status = "finished"
    session_row.finished_at = utcnow()
    session.add(session_row)
    session.commit()

    # Persist the session as long-term agent memory, embedded for future recall.
    content = (
        f"Interview for {job.title if job else 'role'} at {job.company if job else 'company'}: "
        f"scored {avg}/100 across {len(turns)} answers. "
        f"Strengths: {', '.join(summary['strengths']) or 'none recorded'}. "
        f"Improvements to focus on: {', '.join(summary['improvements']) or 'none recorded'}."
    )
    try:
        # Best-effort: memory persistence must never break the interview flow.
        await save_memory(
            session,
            user_id=session_row.user_id,
            kind="interview_summary",
            content=content,
            metadata={
                "job_id": session_row.job_id,
                "company": job.company if job else "",
                "avg_score": avg,
            },
        )
    except Exception:  # noqa: BLE001
        log.exception("failed to persist interview memory for session %s", session_row.id)


# ---------------------------------------------------------------- evaluation


async def _evaluate(
    *,
    question: str,
    key_points: list[str],
    rubric: dict,
    answer: str,
    resume: str,
    memory_context: str,
) -> dict:
    """LLM rubric evaluation; keyword heuristic fallback without a model."""
    llm = get_llm()
    if not llm.available:
        return _heuristic_evaluation(key_points, answer)

    try:
        parsed, result = await llm.complete_json(
            model=llm.tailor_model,
            system=INTERVIEW_COACH_SYSTEM,
            messages=[{
                "role": "user",
                "content": build_interview_answer_user(
                    question=question,
                    key_points=key_points,
                    rubric=rubric,
                    answer=answer,
                    resume=resume,
                    memory_context=memory_context,
                ),
            }],
            max_tokens=1600,
            temperature=0.2,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("interview evaluation failed (%s); using heuristic", exc)
        return _heuristic_evaluation(key_points, answer)

    score = parsed.get("score")
    try:
        score = max(0, min(100, int(float(score)))) if score is not None else None
    except (TypeError, ValueError):
        score = None
    if score is None:
        return _heuristic_evaluation(key_points, answer)

    return {
        "score": score,
        "feedback": str(parsed.get("feedback") or ""),
        "strengths": [str(s) for s in (parsed.get("strengths") or []) if str(s).strip()],
        "improvements": [str(s) for s in (parsed.get("improvements") or []) if str(s).strip()],
        "model_used": result.model,
    }


def _heuristic_evaluation(key_points: list[str], answer: str) -> dict:
    """Keyword-cover heuristic for the no-LLM demo path."""
    text = answer.lower()
    covered = [p for p in key_points if any(
        word in text for word in str(p).lower().split() if len(word) > 3
    )]
    ratio = len(covered) / max(1, len(key_points))
    base = 40 + round(ratio * 45)
    length = len(answer.split())
    if length < 15:
        base -= 15
    elif length > 60:
        base += 5
    score = max(10, min(92, base))
    feedback = (
        "Solid structure — you hit most of the key points." if ratio >= 0.6
        else "Good start, but several key points were missed — aim to cover "
             "the specifics an interviewer looks for."
    )
    return {
        "score": score,
        "feedback": feedback,
        "strengths": [covered[0]] if covered else ["Answered the question"],
        "improvements": [p for p in key_points if p not in covered][:3],
        "model_used": "heuristic",
    }
