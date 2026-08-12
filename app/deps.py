"""Shared FastAPI dependencies.

`current_user` is the seam the whole multi-user model hangs on: every handler
that touches per-user data takes it, and scopes its queries by `user.id`. It
reads the id the auth middleware already resolved onto `request.state`, so a
request costs one identity lookup regardless of how many dependencies ask.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session, select

from app.db import get_session
from app.models import JobStatus, Profile, User, UserJob


def current_user(
    request: Request, session: Session = Depends(get_session)
) -> User:
    """The authenticated caller. 401s when there is none.

    The middleware has already rejected unauthenticated requests to guarded
    paths, so reaching here without a user means a route was added outside that
    guard — fail closed rather than silently serving another user's data.
    """
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, "Authentication required")
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "Authentication required")
    return user


def current_profile(
    user: User = Depends(current_user), session: Session = Depends(get_session)
) -> Profile:
    """The caller's profile, 404 if they have not created one yet."""
    profile = session.exec(
        select(Profile).where(Profile.user_id == user.id)
    ).first()
    if profile is None:
        raise HTTPException(404, "no profile configured")
    return profile


def require_admin(user: User = Depends(current_user)) -> User:
    """Gate for operator-only actions such as minting invite codes."""
    if not user.is_admin:
        raise HTTPException(403, "administrator access required")
    return user


def user_job(
    session: Session, user_id: int, job_id: int
) -> UserJob:
    """The user's workflow row for a job, creating a NEW one if absent.

    Everywhere the code used to read `job.status` now asks "what is *this*
    user's status for this job?" — and the answer for a posting they have never
    interacted with is NEW, so `user_job().status` is the direct replacement.
    """
    row = session.exec(
        select(UserJob).where(
            UserJob.user_id == user_id, UserJob.job_id == job_id
        )
    ).first()
    if row is not None:
        return row
    row = UserJob(user_id=user_id, job_id=job_id, status=JobStatus.NEW)
    session.add(row)
    return row
