"""Password hashing, session tokens, and per-request user resolution.

Three things changed when the app went multi-user, and each was a correctness
problem rather than a preference:

1. Passwords are hashed (scrypt) instead of compared as plaintext config values.
2. A session token names a *user id* and a *token version*, so a handler can ask
   "who is this?" rather than only "is this anybody?", and a password change
   invalidates tokens issued before it.
3. `resolve_current_user` returns the User (or None) instead of a bool.

scrypt comes from hashlib rather than bcrypt/argon2 because it is memory-hard,
ships in the stdlib on the Python 3.12 this project already requires, and avoids
a compiled dependency in the image and in CI.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time

from fastapi import Request
from sqlmodel import Session, select

from app.config import get_settings
from app.models import User

log = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "applycanary_session"
SESSION_MAX_AGE = 86400 * 30  # 30 days

# scrypt parameters. n=2**14 with r=8, p=1 costs ~16MB and a few tens of ms per
# hash: enough to make offline cracking expensive without making login feel slow.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32

# Stored as: scrypt$n$r$p$<salt-b64>$<hash-b64>. The parameters travel with the
# hash so they can be raised later without invalidating existing passwords.
_HASH_PREFIX = "scrypt"


def hash_password(password: str) -> str:
    """Hash a password for storage. Never store or log the plaintext."""
    if not password:
        raise ValueError("password must not be empty")
    salt = os.urandom(_SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_KEY_BYTES,
    )
    return "$".join((
        _HASH_PREFIX, str(_SCRYPT_N), str(_SCRYPT_R), str(_SCRYPT_P),
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(derived).decode("ascii"),
    ))


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash, in constant time."""
    if not password or not stored:
        return False
    try:
        prefix, n_s, r_s, p_s, salt_b64, hash_b64 = stored.split("$")
        if prefix != _HASH_PREFIX:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        derived = hashlib.scrypt(
            password.encode("utf-8"), salt=salt,
            n=int(n_s), r=int(r_s), p=int(p_s), dklen=len(expected),
        )
    except (ValueError, TypeError):
        # Malformed hash: treat as a failed login, not a crash.
        return False
    return hmac.compare_digest(derived, expected)


def generate_invite_code() -> str:
    """A URL-safe, unguessable single-use registration code."""
    return secrets.token_urlsafe(12)


# ---------------------------------------------------------------- sessions


def _get_secret() -> bytes:
    return get_settings().secret_key.encode("utf-8")


def create_session_token(user: User) -> str:
    """Sign a session token naming the user and their current token version."""
    payload = f"{user.id}:{user.token_version}:{int(time.time())}"
    signature = hmac.new(
        _get_secret(), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    token = f"{payload}:{signature}"
    return base64.urlsafe_b64encode(token.encode("utf-8")).decode("utf-8")


def parse_session_token(token: str) -> tuple[int, int] | None:
    """Return (user_id, token_version) from a valid token, else None.

    Verifies the signature and the age only. Whether the user still exists, is
    active, and still carries this token_version is decided against the database
    in `resolve_current_user` — a signature alone must never be enough.
    """
    if not token:
        return None
    try:
        decoded = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        user_id_s, version_s, timestamp_s, signature = decoded.split(":")

        if time.time() - int(timestamp_s) > SESSION_MAX_AGE:
            return None

        payload = f"{user_id_s}:{version_s}:{timestamp_s}"
        expected = hmac.new(
            _get_secret(), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        return int(user_id_s), int(version_s)
    except (ValueError, TypeError, AttributeError):
        return None


# ---------------------------------------------------------------- resolution


def authenticate(session: Session, email: str, password: str) -> User | None:
    """Look up a user by email and verify their password."""
    if not email or not password:
        return None
    user = session.exec(
        select(User).where(User.email == email.strip().lower())
    ).first()
    if user is None or not user.is_active:
        # Hash anyway so a missing account and a wrong password take the same
        # time, which keeps the endpoint from confirming which emails exist.
        verify_password(password, hash_password("dummy"))
        return None
    if not verify_password(password, user.password_hash):
        # Transitional upgrade for installations that stored a legacy plaintext
        # password before multi-user hashing was introduced. It is re-hashed
        # immediately after a successful comparison.
        if not user.password_hash.startswith(f"{_HASH_PREFIX}$") or not hmac.compare_digest(password, user.password_hash):
            return None
        user.password_hash = hash_password(password)
        session.add(user)
        session.commit()
    return user


def resolve_current_user(request: Request, session: Session) -> User | None:
    """Identify the caller from their session cookie or Basic Auth header."""
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    parsed = parse_session_token(cookie) if cookie else None
    if parsed is not None:
        user_id, token_version = parsed
        user = session.get(User, user_id)
        # token_version mismatch means the password changed after this token was
        # issued, so the token is stale even though its signature is still good.
        if user is not None and user.is_active and user.token_version == token_version:
            return user

    header = request.headers.get("Authorization") or ""
    if header.startswith("Basic "):
        try:
            decoded = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
        except (ValueError, TypeError):
            return None
        if ":" in decoded:
            email, password = decoded.split(":", 1)
            return authenticate(session, email, password)

    return None
