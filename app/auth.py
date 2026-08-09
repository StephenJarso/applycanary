"""Authentication helpers, token signing, and request authentication checks.

Supports:
1. Session Cookie (`applycanary_session`)
2. HTTP Basic Auth (`Authorization: Basic <base64>`)
3. Optional disabling via configuration (AUTH_ENABLED=false and AUTH_PASSWORD="")
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time

from fastapi import Request

from app.config import get_settings

log = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "applycanary_session"
SESSION_MAX_AGE = 86400 * 30  # 30 days


def _get_secret() -> bytes:
    settings = get_settings()
    return settings.secret_key.encode("utf-8")


def create_session_token(username: str) -> str:
    """Generate a HMAC-SHA256 signed session token for a username."""
    timestamp = str(int(time.time()))
    payload = f"{username}:{timestamp}"
    signature = hmac.new(_get_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    token = f"{payload}:{signature}"
    return base64.urlsafe_b64encode(token.encode("utf-8")).decode("utf-8")


def validate_session_token(token: str) -> str | None:
    """Validate a signed session token. Returns username if valid, None otherwise."""
    if not token:
        return None
    try:
        decoded = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        parts = decoded.split(":")
        if len(parts) != 3:
            return None
        username, timestamp_str, signature = parts

        # Verify timestamp (expire after 30 days)
        timestamp = int(timestamp_str)
        if time.time() - timestamp > SESSION_MAX_AGE:
            return None

        # Verify signature
        payload = f"{username}:{timestamp_str}"
        expected_sig = hmac.new(_get_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if hmac.compare_digest(signature, expected_sig):
            return username
    except Exception:
        pass
    return None


def verify_credentials(username: str, password: str) -> bool:
    """Check username and password against settings."""
    settings = get_settings()
    if not settings.is_auth_required:
        return True

    expected_user = settings.auth_username or "admin"
    expected_pass = settings.auth_password

    user_match = hmac.compare_digest(username, expected_user)
    pass_match = hmac.compare_digest(password, expected_pass)
    return user_match and pass_match


def check_request_authenticated(request: Request) -> bool:
    """Return True if auth is disabled or if the request carries valid auth credentials."""
    settings = get_settings()
    if not settings.is_auth_required:
        return True

    # 1. Check Session Cookie
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_token and validate_session_token(cookie_token):
        return True

    # 2. Check HTTP Basic Auth Header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Basic "):
        try:
            encoded_creds = auth_header.split(" ", 1)[1]
            decoded_creds = base64.b64decode(encoded_creds).decode("utf-8")
            if ":" in decoded_creds:
                username, password = decoded_creds.split(":", 1)
                if verify_credentials(username, password):
                    return True
        except Exception:
            pass

    return False
