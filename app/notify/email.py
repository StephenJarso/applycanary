"""Email digests and high-score alerts.

Falls back to logging the digest when SMTP is unconfigured, so the pipeline is
testable without credentials and a missing password never loses information.
"""

from __future__ import annotations

import logging
from email.message import EmailMessage
from html import escape

from sqlmodel import Session, select

from app.config import get_settings
from app.models import Application, Job, JobScore, JobStatus, utcnow

log = logging.getLogger(__name__)


async def send(subject: str, html: str, text: str) -> bool:
    """Send one message. Returns False when SMTP is unconfigured or the send fails."""
    settings = get_settings()
    if not settings.email_enabled:
        log.info("EMAIL (not sent, SMTP unconfigured) — %s\n%s", subject, text)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user
    msg["To"] = settings.digest_to
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
        log.info("email sent: %s", subject)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("email send failed (%s): %s", subject, exc)
        return False


async def send_digest(session: Session, hours: int = 24) -> bool:
    """Summarise the last window: what was applied to, and what needs review."""
    from datetime import timedelta

    since = utcnow() - timedelta(hours=hours)

    applied = session.exec(
        select(Application, Job)
        .join(Job, Job.id == Application.job_id)
        .where(Application.submitted_at.is_not(None))
        .where(Application.submitted_at >= since)
        .order_by(Application.submitted_at.desc())
    ).all()

    queued = session.exec(
        select(Job, JobScore)
        .join(JobScore, JobScore.job_id == Job.id)
        .where(Job.status == JobStatus.QUEUED)
        .order_by(JobScore.total.desc())
        .limit(15)
    ).all()

    new_matches = session.exec(
        select(Job, JobScore)
        .join(JobScore, JobScore.job_id == Job.id)
        .where(Job.first_seen_at >= since)
        .where(JobScore.total >= 70)
        .order_by(JobScore.total.desc())
        .limit(15)
    ).all()

    if not (applied or queued or new_matches):
        log.info("digest: nothing to report for the last %dh", hours)
        return False

    subject = (
        f"Job digest — {len(applied)} applied, "
        f"{len(queued)} awaiting review, {len(new_matches)} new matches"
    )

    text_parts = [subject, ""]
    html_parts = [
        "<div style=\"font-family:system-ui,-apple-system,Segoe UI,sans-serif;"
        "max-width:640px;color:#111\">",
        f"<h2 style='margin:0 0 4px'>{escape(subject)}</h2>",
        f"<p style='color:#666;margin:0 0 20px;font-size:13px'>Last {hours} hours</p>",
    ]

    if applied:
        text_parts.append("APPLIED")
        html_parts.append("<h3 style='margin:20px 0 8px'>Applied</h3><ul>")
        for app, job in applied:
            line = f"  {job.title} at {job.company} ({app.method})"
            text_parts.append(line)
            html_parts.append(
                f"<li><strong>{escape(job.title)}</strong> at "
                f"{escape(job.company)} "
                f"<span style='color:#666'>({escape(str(app.method))})</span></li>"
            )
        html_parts.append("</ul>")
        text_parts.append("")

    if queued:
        text_parts.append("AWAITING YOUR REVIEW")
        html_parts.append(
            "<h3 style='margin:20px 0 8px'>Awaiting your review</h3>"
            "<p style='color:#666;font-size:13px;margin:0 0 8px'>"
            "Resume and cover letter are already prepared.</p><ul>"
        )
        for job, score in queued:
            text_parts.append(
                f"  [{score.total:.0f}] {job.title} at {job.company} — {job.apply_url}"
            )
            html_parts.append(
                f"<li><strong>{score.total:.0f}</strong> — "
                f"<a href='{escape(job.apply_url)}'>{escape(job.title)}</a> at "
                f"{escape(job.company)}</li>"
            )
        html_parts.append("</ul>")
        text_parts.append("")

    if new_matches:
        text_parts.append("NEW MATCHES")
        html_parts.append("<h3 style='margin:20px 0 8px'>New matches</h3><ul>")
        for job, score in new_matches:
            text_parts.append(
                f"  [{score.total:.0f}] {job.title} at {job.company} "
                f"({job.source}) — {job.apply_url}"
            )
            html_parts.append(
                f"<li><strong>{score.total:.0f}</strong> — "
                f"<a href='{escape(job.apply_url)}'>{escape(job.title)}</a> at "
                f"{escape(job.company)} "
                f"<span style='color:#888;font-size:12px'>{escape(job.source)}</span></li>"
            )
        html_parts.append("</ul>")

    html_parts.append("</div>")
    return await send(subject, "\n".join(html_parts), "\n".join(text_parts))


async def send_alert(job: Job, score: JobScore) -> bool:
    """Immediate notification for an exceptional match, sent as it is found."""
    subject = f"Strong match ({score.total:.0f}): {job.title} at {job.company}"
    reasoning = score.reasoning or "No reasoning recorded."
    text = (
        f"{subject}\n\n{reasoning}\n\n"
        f"Matched: {', '.join(score.matched_keywords[:12]) or 'none'}\n"
        f"Missing: {', '.join(score.missing_keywords[:12]) or 'none'}\n\n"
        f"Apply: {job.apply_url}\n"
    )
    html = (
        "<div style=\"font-family:system-ui,-apple-system,Segoe UI,sans-serif;"
        "max-width:640px;color:#111\">"
        f"<h2 style='margin:0 0 12px'>{escape(job.title)}</h2>"
        f"<p style='margin:0 0 4px'><strong>{escape(job.company)}</strong>"
        f"{' · ' + escape(job.location) if job.location else ''}</p>"
        f"<p style='font-size:22px;margin:12px 0'><strong>{score.total:.0f}</strong>"
        "<span style='color:#666;font-size:14px'> / 100</span></p>"
        f"<p>{escape(reasoning)}</p>"
        f"<p style='color:#666;font-size:13px'>Matched: "
        f"{escape(', '.join(score.matched_keywords[:12]) or 'none')}<br>Missing: "
        f"{escape(', '.join(score.missing_keywords[:12]) or 'none')}</p>"
        f"<p style='margin-top:20px'><a href='{escape(job.apply_url)}' "
        "style='background:#111;color:#fff;padding:10px 18px;border-radius:6px;"
        "text-decoration:none'>Open posting</a></p></div>"
    )
    return await send(subject, html, text)
