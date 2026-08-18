"""Email digests and high-score alerts.

Prefers Resend (RESEND_API_KEY) over legacy SMTP (SMTP_HOST).  When neither
is configured the message is logged only, so the pipeline is testable without
credentials and a missing password never loses information.

Templates use inline CSS — every major client strips <style> blocks.
"""

from __future__ import annotations

import asyncio
import logging
from email.message import EmailMessage
from html import escape

from sqlmodel import Session, select

from app.config import get_settings
from app.models import Application, Job, JobScore, JobStatus, UserJob, utcnow

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _recipient(profile=None, user=None) -> str:  # noqa: ANN001
    """Best address for one user: profile email, account email, their override,
    then the operator's global digest address as a last resort."""
    settings = get_settings()
    for candidate in (
        (profile.email if profile else "") or "",
        (user.email if user else "") or "",
        (profile.digest_to if profile else "") or "",
        settings.digest_to,
    ):
        if candidate:
            return candidate
    return ""


def _score_color(score: float) -> str:
    """Hex colour for a 0-100 score badge."""
    if score >= 90:
        return "#16a34a"  # green
    if score >= 75:
        return "#2563eb"  # blue
    if score >= 60:
        return "#d97706"  # amber
    return "#6b7280"      # grey


# ---------------------------------------------------------------------------
#  Modern HTML layout helpers
# ---------------------------------------------------------------------------

_BRAND_DARK = "#111827"
_BRAND_ACCENT = "#2563eb"
_BRAND_BG = "#f9fafb"
_BRAND_TEXT = "#111827"
_BRAND_DIM = "#6b7280"

_CONTAINER = (
    "font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;"
    "max-width:600px;margin:0 auto;padding:0;color:#{_BRAND_TEXT}"
)


def _wrap(body: str, *, preheader: str = "") -> str:
    """Wrap a body fragment in a full email HTML document with header/footer."""
    preheader_tag = (
        f'<div style="display:none;max-height:0;overflow:hidden">{escape(preheader)}</div>'
        if preheader
        else ""
    )
    return (
        "<!DOCTYPE html>"
        "<html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "</head><body style='margin:0;padding:0;background:#f3f4f6'>"
        f"{preheader_tag}"
        # Outer wrapper — centred on desktop, full-width on mobile
        "<table role='presentation' width='100%' cellspacing='0' cellpadding='0'"
        " style='background:#f3f4f6'>"
        "<tr><td align='center' style='padding:24px 12px'>"
        # Main card
        "<table role='presentation' width='100%' cellspacing='0' cellpadding='0'"
        " style='max-width:600px;background:#ffffff;border-radius:12px;"
        "overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)'>"
        # Header
        f"<tr><td style='background:{_BRAND_DARK};padding:20px 28px'>"
        "<span style='font-size:18px;font-weight:700;color:#ffffff;"
        "letter-spacing:-.3px'>ApplyCanary</span>"
        "</td></tr>"
        # Body
        f"<tr><td style='padding:28px'>{body}</td></tr>"
        # Footer
        f"<tr><td style='padding:16px 28px 24px;border-top:1px solid #e5e7eb'>"
        f"<p style='margin:0;font-size:12px;color:{_BRAND_DIM};line-height:1.5'>"
        "This email was sent by ApplyCanary — your agentic job-search assistant. "
        "Adjust alert thresholds in your profile settings.</p>"
        "</td></tr>"
        "</table>"
        "</td></tr></table>"
        "</body></html>"
    )


def _score_badge(score: float) -> str:
    """Inline-styled score badge."""
    color = _score_color(score)
    return (
        f'<span style="display:inline-block;background:{color};color:#fff;'
        f'font-weight:700;font-size:14px;padding:3px 10px;border-radius:6px">'
        f"{escape(f'{score:.0f}')}"
        "</span>"
    )


def _job_row(
    title: str,
    company: str,
    score: float | None = None,
    location: str = "",
    source: str = "",
    url: str = "",
    meta: str = "",
) -> str:
    """One job entry for a digest row."""
    score_html = f" {_score_badge(score)}" if score is not None else ""
    loc = f" · {escape(location)}" if location else ""
    src = "<span style='color:#9ca3af;font-size:12px;margin-left:6px'>"
    f"{escape(source)}</span>" if source else ""
    meta_html = (
        f"<div style='font-size:12px;color:{_BRAND_DIM};margin-top:2px'>"
        f"{escape(meta)}</div>"
        if meta
        else ""
    )
    link = url or "#"
    return (
        f"<tr><td style='padding:10px 0;border-bottom:1px solid #f3f4f6'>"
        f"<table role='presentation' width='100%' cellspacing='0' cellpadding='0'>"
        f"<tr><td>"
        f"<a href='{escape(link)}' style='color:{_BRAND_TEXT};text-decoration:none;"
        f"font-weight:600;font-size:15px'>{escape(title)}</a>"
        f"{score_html}{src}"
        f"<div style='font-size:13px;color:{_BRAND_DIM};margin-top:2px'>"
        f"{escape(company)}{loc}</div>"
        f"{meta_html}"
        f"</td></tr></table></td></tr>"
    )


def _section_heading(title: str, count: int) -> str:
    return (
        f"<tr><td style='padding:20px 0 8px'>"
        f"<span style='font-size:13px;font-weight:700;text-transform:uppercase;"
        f"letter-spacing:.5px;color:{_BRAND_DIM}'>{escape(title)}</span>"
        f" <span style='font-size:12px;color:#d1d5db'>({count})</span>"
        "</td></tr>"
    )


def _cta_button(url: str, label: str) -> str:
    return (
        f"<tr><td style='padding:16px 0'>"
        f"<a href='{escape(url)}' style='display:inline-block;background:{_BRAND_ACCENT};"
        f"color:#fff;font-weight:600;font-size:14px;padding:10px 22px;"
        f"border-radius:8px;text-decoration:none'>{escape(label)}</a>"
        "</td></tr>"
    )


# ---------------------------------------------------------------------------
#  Low-level send
# ---------------------------------------------------------------------------

async def send(subject: str, html: str, text: str, *, to: str = "") -> bool:
    """Send one message.  Resend preferred; SMTP fallback; log-only when unconfigured."""
    settings = get_settings()
    to = to or settings.digest_to
    if not settings.email_enabled:
        log.info("EMAIL (not sent, no backend configured) — %s\n%s", subject, text)
        return False
    if not to:
        log.warning("email: no recipient for %r (set profile email or DIGEST_TO)", subject)
        return False

    # --- Resend (preferred) -----------------------------------------------
    if settings.resend_api_key:
        try:
            import resend as _resend

            _resend.api_key = settings.resend_api_key

            def _do() -> dict:  # noqa: ANN202
                return _resend.Emails.send({  # type: ignore[return-value]
                    "from": settings.email_from,
                    "to": [to],
                    "subject": subject,
                    "html": html,
                    "text": text,
                })

            result = await asyncio.to_thread(_do)
            log.info("email sent via Resend (%s): %s", result.get("id", "?"), subject)
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("Resend send failed (%s): %s — falling back to SMTP", subject, exc)
            # Fall through to SMTP if Resend blows up mid-send.

    # --- SMTP (legacy fallback) -------------------------------------------
    if settings.smtp_host and settings.smtp_user:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.smtp_user
        msg["To"] = to
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        try:
            import aiosmtplib

            await aiosmtplib.send(
                msg,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user,
                password=settings.smtp_password,
                start_tls=settings.smtp_port == 587,
                use_tls=settings.smtp_port == 465,
            )
            log.info("email sent via SMTP: %s", subject)
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("email send failed (%s): %s", subject, exc)
            return False

    return False


# ---------------------------------------------------------------------------
#  Digest — daily summary of activity for one user
# ---------------------------------------------------------------------------

async def send_digest(
    session: Session,
    hours: int = 24,
    *,  # keyword-only
    profile=None,  # noqa: ANN001
    user=None,  # noqa: ANN001
) -> bool:
    """Summarise the last window *for one user*: applications, queue, new matches.

    Everything is filtered by the user's id: with per-user workflow state, a
    global digest would report the whole instance's activity to whoever happens
    to be first in the fan-out loop.
    """
    from datetime import timedelta

    uid = (profile.user_id if profile else None) or (user.id if user else None)
    since = utcnow() - timedelta(hours=hours)

    applied_stmt = (
        select(Application, Job)
        .join(Job, Job.id == Application.job_id)
        .where(Application.submitted_at.is_not(None))
        .where(Application.submitted_at >= since)
    )
    if uid:
        applied_stmt = applied_stmt.where(Application.user_id == uid)
    applied = session.exec(
        applied_stmt.order_by(Application.submitted_at.desc())
    ).all()

    # Per-user queue.  The legacy global `job.status` column is always NEW and
    # must not drive this; the user's workflow state lives in UserJob.
    queued_stmt = (
        select(Job, JobScore, UserJob)
        .join(JobScore, JobScore.job_id == Job.id)
        .join(
            UserJob,
            (UserJob.job_id == Job.id) & (UserJob.user_id == uid),
            isouter=True,
        )
        .where(UserJob.status == JobStatus.QUEUED)
    )
    queued = session.exec(
        queued_stmt.order_by(JobScore.total.desc()).limit(15)
    ).all()

    new_stmt = (
        select(Job, JobScore)
        .join(JobScore, JobScore.job_id == Job.id)
        .where(Job.first_seen_at >= since)
        .where(JobScore.total >= 70)
    )
    if uid:
        new_stmt = new_stmt.where(JobScore.user_id == uid)
    new_matches = session.exec(
        new_stmt.order_by(JobScore.total.desc()).limit(15)
    ).all()

    if not (applied or queued or new_matches):
        log.info("digest: nothing to report for the last %dh", hours)
        return False

    subject = (
        f"Job digest — {len(applied)} applied, "
        f"{len(queued)} awaiting review, {len(new_matches)} new matches"
    )

    # --- Plain-text fallback -----------------------------------------------
    text_parts = [subject, ""]
    if applied:
        text_parts.append("APPLIED")
        for app, job in applied:
            text_parts.append(f"  {job.title} at {job.company} ({app.method})")
        text_parts.append("")
    if queued:
        text_parts.append("AWAITING YOUR REVIEW")
        for job, score, _uj in queued:
            text_parts.append(f"  [{score.total:.0f}] {job.title} at {job.company} — {job.apply_url}")
        text_parts.append("")
    if new_matches:
        text_parts.append("NEW MATCHES")
        for job, score in new_matches:
            text_parts.append(f"  [{score.total:.0f}] {job.title} at {job.company} ({job.source})")

    # --- Modern HTML -------------------------------------------------------
    rows: list[str] = []

    if applied:
        rows.append(_section_heading("Applied", len(applied)))
        rows.append("<tr><td><table role='presentation' width='100%' cellspacing='0' cellpadding='0'>")
        for app, job in applied:
            rows.append(
                _job_row(
                    job.title,
                    job.company,
                    url=job.apply_url,
                    meta=f"Submitted via {escape(str(app.method))}",
                )
            )
        rows.append("</table></td></tr>")

    if queued:
        rows.append(_section_heading("Awaiting your review", len(queued)))
        rows.append(
            "<tr><td style='padding:0 0 4px;font-size:13px;color:"
            f"{_BRAND_DIM}'>Resume and cover letter are ready.</td></tr>"
        )
        rows.append("<tr><td><table role='presentation' width='100%' cellspacing='0' cellpadding='0'>")
        for job, score, _uj in queued:
            rows.append(
                _job_row(
                    job.title,
                    job.company,
                    score=score.total,
                    url=job.apply_url,
                )
            )
        rows.append("</table></td></tr>")

    if new_matches:
        rows.append(_section_heading("New matches", len(new_matches)))
        rows.append("<tr><td><table role='presentation' width='100%' cellspacing='0' cellpadding='0'>")
        for job, score in new_matches:
            rows.append(
                _job_row(
                    job.title,
                    job.company,
                    score=score.total,
                    location=job.location,
                    source=job.source,
                    url=job.apply_url,
                )
            )
        rows.append("</table></td></tr>")

    body = "\n".join(rows)
    html = _wrap(body, preheader=subject)
    return await send(subject, html, "\n".join(text_parts), to=_recipient(profile, user))


# ---------------------------------------------------------------------------
#  Immediate alert — fired the moment a high-scoring job is found
# ---------------------------------------------------------------------------

async def send_alert(
    job: Job,
    score: JobScore,
    *,  # keyword-only
    profile=None,  # noqa: ANN001
    user=None,  # noqa: ANN001
) -> bool:
    """Immediate notification for an exceptional match, sent as it is found.

    Goes to the *user's own* address (profile email -> account email -> their
    digest override -> operator DIGEST_TO), not a single global inbox.
    """
    reasoning = score.reasoning or "No reasoning recorded."
    matched = ", ".join(score.matched_keywords[:12]) or "none"
    missing = ", ".join(score.missing_keywords[:12]) or "none"

    subject = (
        f"Strong match ({score.total:.0f}): "
        f"{job.title} at {job.company}"
    )

    # --- Plain text ---------------------------------------------------------
    text = (
        f"{subject}\n\n"
        f"{reasoning}\n\n"
        f"Matched: {matched}\n"
        f"Missing: {missing}\n\n"
        f"Apply: {job.apply_url}\n"
    )

    # --- Modern HTML --------------------------------------------------------
    loc = f" · {escape(job.location)}" if job.location else ""
    body = (
        "<table role='presentation' width='100%' cellspacing='0' cellpadding='0'>"
        # Title
        f"<tr><td style='padding-bottom:4px'>"
        f"<h2 style='margin:0;font-size:20px;color:{_BRAND_TEXT}'>"
        f"{escape(job.title)}</h2>"
        f"<p style='margin:4px 0 0;font-size:15px;color:{_BRAND_DIM}'>"
        f"{escape(job.company)}{loc}</p></td></tr>"
        # Score badge
        f"<tr><td style='padding:12px 0'>"
        f"{_score_badge(score.total)}"
        " <span style='font-size:13px;color:#9ca3af;margin-left:4px'>/ 100</span>"
        "</td></tr>"
        # Reasoning
        f"<tr><td style='padding:0 0 12px;font-size:14px;line-height:1.6;"
        f"color:#374151'>{escape(reasoning)}</td></tr>"
        # Keywords
        f"<tr><td style='padding:0 0 12px'>"
        f"<div style='font-size:12px;color:{_BRAND_DIM}'>"
        f"<strong>Matched:</strong> {escape(matched)}</div>"
        f"<div style='font-size:12px;color:{_BRAND_DIM};margin-top:2px'>"
        f"<strong>Missing:</strong> {escape(missing)}</div>"
        "</td></tr>"
        # CTA
    )
    html = _wrap(body, preheader=f"Strong match ({score.total:.0f}): {job.title} at {job.company}")
    # Insert the button into the wrap — easiest is to put it in body
    html = html.replace("</table>\n</td></tr></table>", "", 1)  # no, simpler:
    # Just rebuild with CTA included
    body_with_cta = body + _cta_button(job.apply_url or "#", "Open posting")
    html = _wrap(body_with_cta, preheader=subject)
    return await send(subject, html, text, to=_recipient(profile, user))
