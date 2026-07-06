"""Three-layer job deduplication.

The same opening routinely appears on a company's own ATS board, two or three
aggregators, and a niche board, each with a different id, URL and title
decoration. Layers run cheapest-first:

  1. fingerprint      exact match on normalised company + title + location
  2. canonical_url    same posting URL once tracking params are stripped
  3. fuzzy title      same company, title similarity >= threshold

A duplicate is never dropped. It becomes a JobAlias against the surviving Job so
the decision is auditable and the UI can show every board a role appeared on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rapidfuzz import fuzz
from sqlmodel import Session, select

from app.models import Job, JobAlias, JobStatus, utcnow
from app.pipeline.normalize import norm_company, norm_title

log = logging.getLogger(__name__)

# Tuned high: token_set_ratio already ignores word order and duplication, so a
# lower bar starts merging distinct roles ("Backend Engineer" vs "Backend
# Engineering Manager" scores in the 80s).
FUZZY_TITLE_THRESHOLD = 92


@dataclass(slots=True)
class DedupResult:
    job: Job
    is_new: bool
    matched_by: str = ""
    match_score: float | None = None


def titles_match(a: str, b: str, *, threshold: int = FUZZY_TITLE_THRESHOLD) -> tuple[bool, float]:
    """Compare two job titles. Returns (is_match, score).

    Guards against the classic false positive where one title is a strict
    superset of the other and adds a scope-changing word: token_set_ratio scores
    `engineer` vs `engineering manager` very high, so a bare ratio check is not
    enough. Any difference in role-scope keywords blocks the match outright.
    """
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return False, 0.0
    if na == nb:
        return True, 100.0

    scope_words = {"manager", "lead", "head", "director", "vp", "principal",
                   "staff", "intern", "internship", "graduate", "junior",
                   "senior", "sr", "jr", "architect", "consultant"}
    sa = {w for w in na.split() if w in scope_words}
    sb = {w for w in nb.split() if w in scope_words}
    if sa != sb:
        return False, 0.0

    # Level numerals must agree: "Engineer II" != "Engineer III".
    levels = {"i", "ii", "iii", "iv", "v", "1", "2", "3", "4", "5"}
    la = {w for w in na.split() if w in levels}
    lb = {w for w in nb.split() if w in levels}
    if la != lb:
        return False, 0.0

    score = float(fuzz.token_set_ratio(na, nb))
    return score >= threshold, score


def _find_by_fingerprint(session: Session, fp: str) -> Job | None:
    return session.exec(select(Job).where(Job.fingerprint == fp)).first()


def _find_by_canonical_url(session: Session, url: str) -> Job | None:
    if not url:
        return None
    return session.exec(select(Job).where(Job.canonical_url == url)).first()


def _find_by_fuzzy_title(session: Session, company: str, title: str) -> tuple[Job | None, float]:
    """Scan only same-company jobs, which keeps this cheap even at scale."""
    nc = norm_company(company)
    if not nc:
        return None, 0.0
    candidates = session.exec(
        select(Job).where(Job.status != JobStatus.EXPIRED)
    ).all()
    best: Job | None = None
    best_score = 0.0
    for cand in candidates:
        if norm_company(cand.company) != nc:
            continue
        ok, score = titles_match(title, cand.title)
        if ok and score > best_score:
            best, best_score = cand, score
    return best, best_score


def resolve(session: Session, incoming: Job) -> DedupResult:
    """Match `incoming` against stored jobs.

    On a hit, updates the surviving row's last_seen/seen_count, records an alias,
    and returns it. The incoming object is not added to the session.
    """
    layers = (
        ("fingerprint", lambda: _find_by_fingerprint(session, incoming.fingerprint)),
        ("canonical_url", lambda: _find_by_canonical_url(session, incoming.canonical_url)),
    )

    for name, finder in layers:
        existing = finder()
        if existing is not None and existing.id != incoming.id:
            return _record_duplicate(session, existing, incoming, name, None)

    existing, score = _find_by_fuzzy_title(session, incoming.company, incoming.title)
    if existing is not None:
        return _record_duplicate(session, existing, incoming, "fuzzy_title", score)

    session.add(incoming)
    return DedupResult(job=incoming, is_new=True)


def _record_duplicate(
    session: Session,
    existing: Job,
    incoming: Job,
    matched_by: str,
    match_score: float | None,
) -> DedupResult:
    existing.last_seen_at = utcnow()
    existing.seen_count += 1

    # Prefer richer data from the duplicate: aggregators often truncate, and the
    # company's own ATS board usually has the fuller description and real salary.
    if len(incoming.description or "") > len(existing.description or ""):
        existing.description = incoming.description
        existing.description_hash = incoming.description_hash
    if existing.salary_min is None and incoming.salary_min is not None:
        existing.salary_min = incoming.salary_min
        existing.salary_max = incoming.salary_max
        existing.salary_currency = incoming.salary_currency
        existing.salary_is_estimate = incoming.salary_is_estimate
    if not existing.posted_at and incoming.posted_at:
        existing.posted_at = incoming.posted_at
    # A sanctioned ATS platform enables auto-submit, so never lose it.
    if not existing.ats_platform and incoming.ats_platform:
        existing.ats_platform = incoming.ats_platform
        existing.ats_board_token = incoming.ats_board_token
        existing.apply_url = incoming.apply_url or existing.apply_url

    already = session.exec(
        select(JobAlias).where(
            JobAlias.source == incoming.source,
            JobAlias.source_id == incoming.source_id,
        )
    ).first()
    if already is None and existing.id is not None:
        session.add(
            JobAlias(
                job_id=existing.id,
                source=incoming.source,
                source_id=incoming.source_id,
                apply_url=incoming.apply_url,
                matched_by=matched_by,
                match_score=match_score,
            )
        )

    session.add(existing)
    log.debug(
        "dedup: %s/%s -> job %s via %s", incoming.source, incoming.source_id,
        existing.id, matched_by,
    )
    return DedupResult(job=existing, is_new=False, matched_by=matched_by, match_score=match_score)
