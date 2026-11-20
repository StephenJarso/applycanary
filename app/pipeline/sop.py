"""Statement of Purpose generator for fellowship / volunteership applications.

Mirrors the cover-letter path in app/apply/runner.py but is designed for
program applications, not job submissions. The SOP is never stored in
Application (which is job-centric); it is returned directly to the caller
for the user to copy and paste.

No truthcheck pass: the SOP prompt forbids fabrication and the content is
shorter and simpler than a full CV tailoring. The user reads and approves
it before submitting, which is the gate that matters here.
"""

from __future__ import annotations

import logging

from app.llm.client import get_llm
from app.llm.prompts import SOP_SYSTEM, build_sop_user
from app.models import Profile
from app.pipeline.github import GithubEvidence

log = logging.getLogger(__name__)


class SOPError(RuntimeError):
    pass


async def generate_sop(
    *,
    profile: Profile,
    title: str,
    org: str,
    description: str,
) -> str:
    """Generate a Statement of Purpose for an opportunity.

    Returns the plain-text letter body. Raises SOPError on any failure.
    """
    llm = get_llm()
    if not llm.available:
        raise SOPError(
            "no LLM API key configured; cannot generate statement of purpose"
        )

    resume_text = (profile.base_resume_text or "").strip()
    if not resume_text:
        raise SOPError("no base resume on file; upload one on the Profile page first")

    evidence = GithubEvidence.from_dict(profile.github_evidence or {})
    evidence_text = evidence.as_prompt_text()

    user_msg = build_sop_user(
        title=title,
        org=org,
        description=description,
        resume_text=resume_text,
        motivation_statement=getattr(profile, "motivation_statement", "") or "",
        github_evidence=evidence_text,
    )

    try:
        result = await llm.complete(
            system=SOP_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            model=llm.tailor_model,
            max_tokens=800,
        )
    except Exception as exc:  # noqa: BLE001
        raise SOPError(f"LLM call failed: {exc}") from exc

    text = (result.text or "").strip()
    if not text:
        raise SOPError("model returned an empty statement of purpose")

    log.info("sop: generated %d-char SOP for %r at %r", len(text), title, org)
    return text
