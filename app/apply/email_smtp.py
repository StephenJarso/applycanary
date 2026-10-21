"""Email applications, sent from the candidate's own mailbox.

Plenty of postings — especially smaller companies, agencies and Kenyan
employers — ask for a CV by email instead of hosting an application form. This
submitter covers exactly those: it only fires when the posting itself states an
address to apply to, and it sends from the user's own Gmail (SMTP + app
password) so replies land in the inbox they already monitor.

Nothing here bypasses `SubmitGate`: the resume still has to pass truthcheck, the
job is still scored, and the daily cap still applies. A dry run is the default
unless the user explicitly approves the send.
"""

from __future__ import annotations

import logging
import mimetypes
import re
from email.message import EmailMessage
from pathlib import Path

from app.apply.base import BaseSubmitter, SubmitResult, register_submitter
from app.models import ApplyMethod, Job, Profile, ResumeVersion
from app.secrets import decrypt_secret

log = logging.getLogger(__name__)

DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 587

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Words that make an address in a JD look like the application inbox rather
# than a privacy/legal/no-reply footer.
_APPLICATION_CUES = (
    "apply", "application", "send your", "send us", "send a", "send an",
    "email your", "email us", "email a", "cv", "resume", "curriculum",
    "career", "hiring", "job",
)
_NON_APPLICATION_LOCALS = (
    "noreply", "no-reply", "donotreply", "do-not-reply", "unsubscribe",
    "privacy", "legal", "abuse",
)


def application_email(job: Job) -> str:
    """Address this posting says to apply to, or "" when it names none.

    A `mailto:` apply URL wins. Otherwise the description is scanned and an
    address only counts when apply/send/CV language sits next to it, so a
    contact address in boilerplate does not become an accidental recipient.
    """
    url = (job.apply_url or "").strip()
    if url.lower().startswith("mailto:"):
        address = url[7:].split("?")[0].strip()
        if _EMAIL_RE.fullmatch(address):
            return address

    text = job.description or ""
    for match in _EMAIL_RE.finditer(text):
        address = match.group(0).rstrip(".,;:)")
        local = address.split("@")[0].lower()
        if any(marker in local for marker in _NON_APPLICATION_LOCALS):
            continue
        around = text[max(0, match.start() - 160): match.end() + 160].lower()
        if any(cue in around for cue in _APPLICATION_CUES):
            return address
    return ""


@register_submitter
class EmailSubmitter(BaseSubmitter):
    platform = "email"
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
        to = application_email(job)
        if not to:
            return SubmitResult(
                ok=False,
                method=ApplyMethod.MANUAL,
                error=(
                    "This posting does not state an email address to apply to; "
                    "use its application form."
                ),
            )

        password = decrypt_secret(profile.smtp_app_password_encrypted)
        if not (profile.smtp_user and password):
            return SubmitResult(
                ok=False,
                method=ApplyMethod.MANUAL,
                error=(
                    "Connect your email in Profile → Send applications from my "
                    "inbox before using email applications."
                ),
            )

        subject, body = _compose(job, profile, cover_letter)
        attachments = _attachments(resume_version)

        if dry_run:
            log.info(
                "DRY RUN: would email application for %s at %s to %s (%d attachments)",
                job.title, job.company, to, len(attachments),
            )
            return SubmitResult(
                ok=True,
                method=ApplyMethod.DRY_RUN,
                confirmation=(
                    f"Dry run: would email {to} with your CV"
                    f"{' and cover letter' if cover_letter else ''}. Nothing sent."
                ),
                dry_run=True,
            )

        message = EmailMessage()
        message["From"] = profile.smtp_user
        message["To"] = to
        message["Reply-To"] = profile.smtp_user
        message["Subject"] = subject
        message.set_content(body)
        for path, mime, _ in attachments:
            maintype, _, subtype = mime.partition("/")
            message.add_attachment(
                path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name,
            )

        port = profile.smtp_port or DEFAULT_SMTP_PORT
        try:
            import aiosmtplib

            await aiosmtplib.send(
                message,
                hostname=profile.smtp_host or DEFAULT_SMTP_HOST,
                port=port,
                username=profile.smtp_user,
                password=password,
                # 587 upgrades with STARTTLS; 465 is TLS from the first byte.
                start_tls=port == 587,
                use_tls=port == 465,
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001 — surface it on the application row
            log.warning("email application to %s failed: %s", to, exc)
            return SubmitResult(
                ok=False,
                method=ApplyMethod.EMAIL,
                error=f"SMTP send failed ({type(exc).__name__}): {exc}",
            )

        log.info("emailed application for job %s to %s", job.id, to)
        return SubmitResult(
            ok=True,
            method=ApplyMethod.EMAIL,
            confirmation=f"Emailed your application to {to}.",
        )


def _compose(job: Job, profile: Profile, cover_letter: str) -> tuple[str, str]:
    name = profile.full_name.strip()
    subject = f"Application: {job.title}"
    if name:
        subject = f"{subject} — {name}"
    body = cover_letter.strip() or (
        f"Hello,\n\n"
        f"I would like to apply for the {job.title} role at {job.company}. "
        f"My CV is attached.\n\n"
        f"Best regards,\n{name or profile.email}\n"
    )
    return subject, body


def _attachments(resume_version: ResumeVersion | None) -> list[tuple[Path, str, str]]:
    """Resume files that exist on disk, PDF first (it is what recruiters open)."""
    if resume_version is None:
        return []
    found: list[tuple[Path, str, str]] = []
    for raw in (resume_version.pdf_path, resume_version.docx_path):
        if not raw:
            continue
        path = Path(raw)
        if not path.is_file():
            continue
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        found.append((path, mime, path.name))
    return found
