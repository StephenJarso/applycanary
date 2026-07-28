"""Manual submitter: the default path for platforms with no sanctioned API.

Does everything except the final click. It assembles the tailored resume, cover
letter and pre-filled answers, then hands the user a deep link. That keeps the
speed advantage — artifacts are ready the moment a job appears — without
automating a submission the platform has not sanctioned.
"""

from __future__ import annotations

import logging

from app.apply.base import BaseSubmitter, SubmitResult, register_submitter
from app.models import ApplyMethod, Job, Profile, ResumeVersion

log = logging.getLogger(__name__)


@register_submitter
class ManualSubmitter(BaseSubmitter):
    platform = "manual"
    can_auto_submit = False

    async def submit(
        self,
        job: Job,
        profile: Profile,
        resume_version: ResumeVersion | None,
        *,
        cover_letter: str = "",
        dry_run: bool = True,
    ) -> SubmitResult:
        """Never sends anything. Queues the application for one-click review."""
        return SubmitResult(
            ok=True,
            method=ApplyMethod.MANUAL,
            confirmation=(
                f"Queued for review. Open {job.apply_url} to submit — the tailored "
                "resume and cover letter are attached to this entry."
            ),
            dry_run=True,
        )


def build_form_answers(job: Job, profile: Profile) -> dict:
    """Pre-fill the questions nearly every application form asks.

    Values come from the profile only. Anything requiring a judgement the user has
    not recorded is left blank rather than guessed, since these answers are
    submitted under their name.
    """
    answers = {
        "first_name": (profile.full_name or "").split(" ")[0] if profile.full_name else "",
        "last_name": " ".join((profile.full_name or "").split(" ")[1:]),
        "full_name": profile.full_name,
        "email": profile.email,
        "phone": profile.phone,
        "location": profile.location,
        "linkedin": profile.linkedin_url,
        "github": (
            f"https://github.com/{profile.github_username}"
            if profile.github_username else ""
        ),
        "portfolio": profile.portfolio_url,
        "work_authorization": profile.work_authorization,
        "years_experience": (
            str(profile.years_experience) if profile.years_experience is not None else ""
        ),
        "requires_sponsorship": "",   # legally significant; user must answer
        "salary_expectation": (
            f"{profile.min_salary:,} {profile.salary_currency}"
            if profile.min_salary else ""
        ),
        "start_date": "",
        "why_this_company": "",       # deliberately blank; generic answers read as spam
    }
    # Blank keys are kept, not dropped: the UI renders them as fields still
    # needing an answer, which is more useful than hiding them.
    return answers
