"""Submission interface and safety gates.

Every path to sending an application goes through `SubmitGate.check`. The gates
are defence in depth, in order of severity:

  1. dry-run unless ENABLE_AUTO_SUBMIT is explicitly true
  2. never submit a resume that failed truthcheck
  3. per-day cap across all sources
  4. minimum score
  5. never submit twice to the same job

These are enforced here rather than in each submitter so a new backend cannot
accidentally bypass them.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta

from sqlmodel import Session, func, select

from app.config import get_settings
from app.models import (
    Application,
    ApplyMethod,
    Job,
    JobScore,
    Profile,
    ResumeVersion,
    utcnow,
)

log = logging.getLogger(__name__)


@dataclass(slots=True)
class SubmitResult:
    ok: bool
    method: ApplyMethod
    confirmation: str = ""
    error: str = ""
    dry_run: bool = False


@dataclass(slots=True)
class GateResult:
    allowed: bool
    reason: str = ""
    dry_run: bool = False


class SubmitGate:
    """Centralised pre-submission checks."""

    @staticmethod
    def check(
        session: Session,
        job: Job,
        resume_version: ResumeVersion | None,
        *,
        force: bool = False,
    ) -> GateResult:
        settings = get_settings()

        existing = session.exec(
            select(Application).where(Application.job_id == job.id)
        ).first()
        if existing is not None and existing.submitted_at is not None:
            return GateResult(False, f"already applied on {existing.submitted_at:%Y-%m-%d}")

        # A resume that failed verification must never be sent automatically.
        # `force` covers the user explicitly approving it in the UI after review;
        # it deliberately does not bypass this check.
        if resume_version is not None and not resume_version.truthcheck_passed:
            return GateResult(
                False,
                "tailored resume failed verification "
                f"({len(resume_version.unverifiable_claims)} unverified claims); "
                "review and edit it before submitting",
            )

        if not settings.enable_auto_submit and not force:
            return GateResult(True, "ENABLE_AUTO_SUBMIT is false", dry_run=True)

        since = utcnow() - timedelta(hours=24)
        sent_today = session.exec(
            select(func.count(Application.id)).where(
                Application.submitted_at.is_not(None),
                Application.submitted_at >= since,
                Application.method != ApplyMethod.DRY_RUN,
            )
        ).one()
        if sent_today >= settings.daily_apply_cap:
            return GateResult(
                False,
                f"daily cap reached ({sent_today}/{settings.daily_apply_cap} in 24h)",
            )

        if not force:
            score = session.exec(
                select(JobScore).where(JobScore.job_id == job.id)
            ).first()
            if score is None:
                return GateResult(False, "job has not been scored yet")
            if score.total < settings.auto_submit_min_score:
                return GateResult(
                    False,
                    f"score {score.total:.0f} is below the auto-submit minimum "
                    f"({settings.auto_submit_min_score})",
                )

        return GateResult(True)


class BaseSubmitter(ABC):
    """One implementation per ATS platform.

    `platform` must match `Job.ats_platform`. `can_auto_submit` is False for
    platforms with no sanctioned third-party endpoint; those route to the review
    queue instead.
    """

    platform: str = ""
    can_auto_submit: bool = False

    @abstractmethod
    async def submit(
        self,
        job: Job,
        profile: Profile,
        resume_version: ResumeVersion | None,
        *,
        cover_letter: str = "",
        dry_run: bool = True,
    ) -> SubmitResult:
        """Send the application, or simulate it when dry_run is True."""


_SUBMITTERS: dict[str, type[BaseSubmitter]] = {}


def register_submitter(cls: type[BaseSubmitter]) -> type[BaseSubmitter]:
    if not cls.platform:
        raise ValueError(f"{cls.__name__} must define a platform")
    _SUBMITTERS[cls.platform] = cls
    return cls


def get_submitter(platform: str) -> BaseSubmitter:
    """Return the submitter for a platform, or the manual fallback."""
    from app.apply.manual import ManualSubmitter

    cls = _SUBMITTERS.get(platform)
    return cls() if cls is not None else ManualSubmitter()


def auto_submittable_platforms() -> set[str]:
    return {name for name, cls in _SUBMITTERS.items() if cls.can_auto_submit}
