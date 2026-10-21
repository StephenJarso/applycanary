"""Email applications: recipient detection, one-click send, mailbox storage.

The sender only fires for postings that state an address to apply to, sends
from the user's own mailbox, and never exposes the app password it uses.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.router import EmailSendingIn
from app.apply.email_smtp import EmailSubmitter, application_email
from app.auth import SESSION_COOKIE_NAME, create_session_token
from app.db import engine, init_db
from app.main import create_app
from app.models import ApplyMethod, Job, Profile, ResumeVersion, User
from app.secrets import decrypt_secret


def asyncio_run(coro):  # noqa: ANN001, ANN201
    # A private loop, like the other test modules: asyncio.run() calls
    # set_event_loop(None), which breaks tests that still use get_event_loop().
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _job(*, description: str = "", apply_url: str = "") -> Job:
    return Job(
        title="Backend Engineer",
        company="Acme",
        source="test",
        location="Nairobi, Kenya",
        description=description or "Build Go services.",
        apply_url=apply_url,
        fingerprint="email-apply",
    )


def _profile(**overrides) -> Profile:
    values = {
        "user_id": 1,
        "full_name": "Stephen Jacob",
        "email": "stephen@example.com",
        "smtp_user": "stephen@example.com",
    }
    values.update(overrides)
    return Profile(**values)


# ---------------------------------------------------------------- recipients


def test_apply_address_is_read_from_the_description():
    job = _job(description="Send your CV and cover letter to jobs@acme.co.ke by Friday.")
    assert application_email(job) == "jobs@acme.co.ke"


def test_mailto_apply_url_wins():
    job = _job(description="Use the form.", apply_url="mailto:careers@acme.io?subject=Job")
    assert application_email(job) == "careers@acme.io"


def test_no_apply_address_means_no_email_application():
    assert application_email(_job(description="Apply through Greenhouse.")) == ""


def test_boilerplate_addresses_are_not_mistaken_for_the_application_inbox():
    job = _job(
        description=(
            "Questions about this role? Apply via our careers portal. "
            "Privacy requests: privacy@acme.com. Do not reply to noreply@acme.com."
        )
    )
    assert application_email(job) == ""


# ---------------------------------------------------------------- submitter


def test_dry_run_needs_a_connected_mailbox():
    result = asyncio_run(
        EmailSubmitter().submit(
            _job(description="Send your CV to jobs@acme.com"),
            _profile(),
            None,
            dry_run=True,
        )
    )
    assert result.ok is False
    assert "Connect your email" in result.error


def test_dry_run_reports_the_recipient_without_sending(monkeypatch):
    from app.secrets import encrypt_secret

    profile = _profile(smtp_app_password_encrypted=encrypt_secret("abcd efgh ijkl mnop"))

    def fail_send(*_args, **_kwargs):  # pragma: no cover - must not be called
        raise AssertionError("dry run must not open SMTP")

    monkeypatch.setattr("aiosmtplib.send", fail_send)

    result = asyncio_run(
        EmailSubmitter().submit(
            _job(description="Send your CV to jobs@acme.com"),
            profile,
            None,
            dry_run=True,
        )
    )

    assert result.ok is True
    assert result.dry_run is True
    assert result.method == ApplyMethod.DRY_RUN
    assert "jobs@acme.com" in result.confirmation


def test_live_send_emails_the_cv_from_the_user_mailbox(monkeypatch, tmp_path):
    from app.secrets import encrypt_secret

    sent: dict = {}

    async def fake_send(message, **kwargs):  # noqa: ANN001, ANN003
        sent["message"] = message
        sent["kwargs"] = kwargs

    monkeypatch.setattr("aiosmtplib.send", fake_send)

    cv = tmp_path / "stephen-cv.pdf"
    cv.write_bytes(b"%PDF-1.4 fake")
    version = ResumeVersion(
        user_id=1, job_id=1, text="x", pdf_path=str(cv), truthcheck_passed=True,
    )
    profile = _profile(smtp_app_password_encrypted=encrypt_secret("app-password"))

    result = asyncio_run(
        EmailSubmitter().submit(
            _job(description="Send your CV to jobs@acme.com"),
            profile,
            version,
            cover_letter="Hello, please find my CV attached.",
            dry_run=False,
        )
    )

    assert result.ok is True
    assert result.method == ApplyMethod.EMAIL
    message = sent["message"]
    assert message["To"] == "jobs@acme.com"
    assert message["From"] == "stephen@example.com"
    assert "Backend Engineer" in message["Subject"]
    attachments = [part.get_filename() for part in message.iter_attachments()]
    assert attachments == ["stephen-cv.pdf"]
    assert sent["kwargs"]["username"] == "stephen@example.com"
    assert sent["kwargs"]["password"] == "app-password"


# ---------------------------------------------------------------- mailbox API


@pytest.fixture
def mailbox():
    """A fresh account and signed-in client; no shared rows are deleted."""
    init_db()
    with Session(engine) as session:
        user = User(email=f"mailbox-{uuid4().hex}@applycanary.test", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)
        user_id = user.id
        token = create_session_token(user)

    client = TestClient(create_app(), follow_redirects=False)
    client.cookies.set(SESSION_COOKIE_NAME, token)
    return client, user_id


def test_mailbox_is_stored_encrypted_and_never_returned(mailbox):
    client, user_id = mailbox
    res = client.put(
        "/api/profile/email-sending",
        json={"smtp_user": "stephen@gmail.com", "smtp_app_password": "abcd efgh ijkl mnop"},
    )

    assert res.status_code == 200
    assert res.json()["detail"]["configured"] is True
    assert "password" not in res.text.lower()

    with Session(engine) as session:
        profile = session.exec(
            select(Profile).where(Profile.user_id == user_id)
        ).one()
        assert profile.smtp_user == "stephen@gmail.com"
        assert profile.smtp_app_password_encrypted != "abcd efgh ijkl mnop"
        assert decrypt_secret(profile.smtp_app_password_encrypted) == "abcd efgh ijkl mnop"

    profile_res = client.get("/api/profile")
    body = profile_res.json()
    assert body["email_sending_configured"] is True
    assert body["smtp_user"] == "stephen@gmail.com"
    assert "abcd efgh" not in profile_res.text


def test_disconnect_clears_the_mailbox(mailbox):
    client, _user_id = mailbox
    client.put(
        "/api/profile/email-sending",
        json={"smtp_user": "stephen@gmail.com", "smtp_app_password": "abcd efgh ijkl mnop"},
    )

    res = client.put(
        "/api/profile/email-sending",
        json={"disconnect": True},
    )

    assert res.status_code == 200
    assert res.json()["detail"]["configured"] is False
    assert client.get("/api/profile").json()["email_sending_configured"] is False


def test_password_can_be_kept_when_only_the_address_changes(mailbox):
    client, user_id = mailbox
    client.put(
        "/api/profile/email-sending",
        json={"smtp_user": "old@gmail.com", "smtp_app_password": "abcd efgh ijkl mnop"},
    )

    res = client.put(
        "/api/profile/email-sending",
        json={"smtp_user": "new@gmail.com"},
    )

    assert res.status_code == 200
    with Session(engine) as session:
        profile = session.exec(select(Profile).where(Profile.user_id == user_id)).one()
    assert profile.smtp_user == "new@gmail.com"
    assert decrypt_secret(profile.smtp_app_password_encrypted) == "abcd efgh ijkl mnop"


def test_the_password_field_is_never_accepted_from_profile_save(mailbox):
    # ProfileIn has no smtp fields: a stray client cannot write the secret.
    client, user_id = mailbox
    res = client.put(
        "/api/profile",
        json={"full_name": "Stephen", "smtp_app_password": "leak"},
    )
    assert res.status_code == 200
    with Session(engine) as session:
        profile = session.exec(select(Profile).where(Profile.user_id == user_id)).one()
    assert profile.smtp_app_password_encrypted == ""


def test_email_sending_input_defaults_are_gmail():
    payload = EmailSendingIn()
    assert payload.smtp_host == "smtp.gmail.com"
    assert payload.smtp_port == 587
