#!/usr/bin/env python3
"""Create the first ApplyCanary admin account on a fresh deployment.

Usage:
    BOOTSTRAP_EMAIL=you@example.com \
    BOOTSTRAP_PASSWORD="a-long-password" \
    python scripts/bootstrap_admin.py

The command is intentionally one-shot and refuses to overwrite an existing
user. Run it from a private Railway shell or another trusted environment.
"""

from __future__ import annotations

import getpass
import os
import sys

from sqlmodel import select

from app.auth import generate_invite_code, hash_password
from app.db import init_db, session_scope
from app.models import InviteCode, Profile, User, utcnow


def main() -> int:
    email = (os.environ.get("BOOTSTRAP_EMAIL") or input("Admin email: ")).strip().lower()
    password = os.environ.get("BOOTSTRAP_PASSWORD") or getpass.getpass("Admin password: ")
    if not email or len(password) < 10:
        print("email is required and password must be at least 10 characters", file=sys.stderr)
        return 2

    init_db()
    with session_scope() as session:
        if session.exec(select(User)).first() is not None:
            print("refusing to bootstrap: a user already exists", file=sys.stderr)
            return 1
        user = User(
            email=email,
            password_hash=hash_password(password),
            is_admin=True,
        )
        session.add(user)
        session.flush()
        session.add(Profile(user_id=user.id, email=email))
        invite = InviteCode(
            code=generate_invite_code(),
            created_by_id=user.id,
            created_at=utcnow(),
        )
        session.add(invite)
        print(f"created admin {email}")
        print(f"initial invite code: {invite.code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
