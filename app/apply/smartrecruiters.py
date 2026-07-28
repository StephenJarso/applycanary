"""SmartRecruiters submitter.

SmartRecruiters is the one major ATS in this project's source set that documents a
public candidate-facing application endpoint, which is why it is the only
auto-submit backend here. Everything else routes to the review queue.

The endpoint shape must be confirmed against current docs before enabling
auto-submit; `scripts/verify_sources.py` checks the job-board side, and the first
live send should be done with a single job and ENABLE_AUTO_SUBMIT toggled
deliberately. Treat a 4xx here as "the contract changed", not "retry".
"""

from __future__ import annotations

import logging

import httpx

from app.apply.base import BaseSubmitter, SubmitResult, register_submitter
from app.models import ApplyMethod, Job, Profile, ResumeVersion
from app.sources.base import DEFAULT_TIMEOUT, USER_AGENT

log = logging.getLogger(__name__)

POSTINGS_API = "https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting}/candidates"


@register_submitter
class SmartRecruitersSubmitter(BaseSubmitter):
    platform = "smartrecruiters"
    can_auto_submit = True

    async def submit(
        self,
        job: Job,
        profile: Profile,
        resume_version: ResumeVersion | None,
        *,
        cover_letter: str = "",
        dry_run: bool = True,
    ) -> SubmitResult:
        if not job.ats_board_token or not job.source_id:
            return SubmitResult(
                ok=False,
                method=ApplyMethod.MANUAL,
                error="missing company token or posting id; cannot auto-submit",
            )

        payload = self._build_payload(job, profile, cover_letter)

        if dry_run:
            log.info(
                "DRY RUN: would POST application for %s at %s (%d payload keys)",
                job.title, job.company, len(payload),
            )
            return SubmitResult(
                ok=True,
                method=ApplyMethod.DRY_RUN,
                confirmation="Dry run: payload built and validated, nothing sent.",
                dry_run=True,
            )

        url = POSTINGS_API.format(company=job.ats_board_token, posting=job.source_id)

        # Attachment upload uses a multipart contract that could not be verified
        # offline, so the resume is not attached here. Sending a guessed multipart
        # shape would fail server-side in a way that looks like a submitted
        # application. The queue entry keeps the file for manual attachment.
        attachment_note = ""
        if resume_version is not None and resume_version.docx_path:
            attachment_note = (
                " Resume was NOT attached (multipart contract unverified) — "
                "attach it manually if the posting requires it."
            )

        try:
            async with httpx.AsyncClient(
                timeout=DEFAULT_TIMEOUT,
                headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
                follow_redirects=True,
            ) as client:
                resp = await client.post(url, json=payload)

            if resp.status_code in (200, 201, 202):
                ref = ""
                try:
                    body = resp.json()
                    ref = str(body.get("id") or body.get("candidateId") or "")
                except Exception:  # noqa: BLE001
                    pass
                return SubmitResult(
                    ok=True,
                    method=ApplyMethod.API,
                    confirmation=(
                        f"Submitted to SmartRecruiters"
                        f"{f' (ref {ref})' if ref else ''}.{attachment_note}"
                    ),
                )

            body = (resp.text or "")[:400]
            if resp.status_code in (400, 403, 404, 409, 422):
                return SubmitResult(
                    ok=False,
                    method=ApplyMethod.API,
                    error=(
                        f"HTTP {resp.status_code} — not retryable. Either the posting "
                        f"closed, the endpoint contract changed, or a required field is "
                        f"missing. Apply manually at {job.apply_url}. Response: {body}"
                    ),
                )
            return SubmitResult(
                ok=False,
                method=ApplyMethod.API,
                error=f"HTTP {resp.status_code}: {body}",
            )

        except Exception as exc:  # noqa: BLE001
            return SubmitResult(
                ok=False,
                method=ApplyMethod.API,
                error=f"{type(exc).__name__}: {exc}",
            )

    # ------------------------------------------------------------------
    @staticmethod
    def _build_payload(job: Job, profile: Profile, cover_letter: str) -> dict:
        names = (profile.full_name or "").split(" ")
        payload: dict = {
            "firstName": names[0] if names else "",
            "lastName": " ".join(names[1:]) if len(names) > 1 else "",
            "email": profile.email,
            "location": {"city": profile.location} if profile.location else {},
        }
        if profile.phone:
            payload["phoneNumber"] = profile.phone

        web: list[dict] = []
        if profile.linkedin_url:
            web.append({"type": "LINKEDIN", "value": profile.linkedin_url})
        if profile.github_username:
            web.append({
                "type": "OTHER",
                "value": f"https://github.com/{profile.github_username}",
            })
        if profile.portfolio_url:
            web.append({"type": "PORTFOLIO", "value": profile.portfolio_url})
        if web:
            payload["web"] = {"links": web}

        if cover_letter:
            payload["answers"] = [{"questionId": "coverLetter", "value": cover_letter}]

        return payload
