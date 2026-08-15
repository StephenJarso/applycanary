#!/usr/bin/env python3
"""Recovery: restore profile rows deleted from the live database.

Context: the pytest suite redirects its database to a temp dir via
`tests/conftest.py`, but when DATABASE_URL/DATA_DIR are exported in the shell
(the owner's development environment) the redirect is a no-op and the auth
fixture's "clean user table" step runs against the real database — deleting
every profile and invite code before the foreign-key check stops the user
deletion.

This restores what can be recovered:
- the owner's profile (user_id=2, stephenjacob815@gmail.com) from the
  pre-migration backup (`data/applycanary.multiuser-*.db`), re-pointed at the
  per-user resume file that still exists on disk;
- empty placeholder profiles for the other two users so no handler 404s.

Run:  .venv/bin/python scripts/restore_profiles.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE_DB = ROOT / "data" / "applycanary.db"
BACKUPS = sorted((ROOT / "data").glob("applycanary.multiuser-*.db"))

# user_id -> (email, resume file relative to project root, or None)
USERS = {
    1: ("admin", None),
    2: ("stephenjacob815@gmail.com", "data/resumes/user_2/base.docx"),
    3: ("stephenjarso6@gmail.com", None),
}


def main() -> int:
    if not BACKUPS:
        print("no pre-migration backup found; aborting")
        return 1
    backup = BACKUPS[-1]
    print(f"backup: {backup}")

    live = sqlite3.connect(LIVE_DB)
    live.execute("PRAGMA foreign_keys=ON")

    backup_conn = sqlite3.connect(backup)
    backup_conn.row_factory = sqlite3.Row
    owner = backup_conn.execute(
        "SELECT * FROM profile WHERE id = 1"
    ).fetchone()

    existing = {r[0] for r in live.execute("SELECT user_id FROM profile")}
    restored = 0

    for user_id, (email, resume_rel) in USERS.items():
        if user_id in existing:
            print(f"profile for user {user_id} already exists; skipping")
            continue

        resume_text = ""
        resume_path = resume_rel or ""
        if resume_rel and (ROOT / resume_rel).exists():
            try:
                from app.resume.parse import parse_resume  # noqa: PLC0415

                parsed = parse_resume(ROOT / resume_rel)
                resume_text = parsed.text
                print(f"  parsed resume: {parsed.word_count} words")
            except Exception as exc:  # noqa: BLE001
                print(f"  could not parse {resume_rel}: {exc}")

        cols = {
            "user_id": user_id,
            "full_name": "",
            "email": "",
            "phone": "",
            "location": "",
            "linkedin_url": "",
            "github_username": "",
            "portfolio_url": "",
            "base_resume_path": resume_path,
            "base_resume_text": resume_text,
            "skills": "[]",
            "target_titles": "[]",
            "target_locations": "[]",
            "excluded_companies": "[]",
            "remote_only": 0,
            "work_authorization": "",
            "years_experience": None,
            "github_evidence": "{}",
            "github_synced_at": None,
            "min_salary": None,
            "salary_currency": "USD",
            "created_at": "2026-08-13 00:00:00",
            "updated_at": "2026-08-13 00:00:00",
            "alert_min_score": 90,
            "auto_submit_min_score": 70,
            "daily_apply_cap": 5,
            "enable_auto_submit": 0,
            "digest_to": "",
            "referral_link": "",
        }

        if owner is not None and email == owner["email"]:
            for key in (
                "full_name", "email", "phone", "location", "linkedin_url",
                "github_username", "portfolio_url", "skills", "min_salary",
                "salary_currency", "target_titles", "target_locations",
                "remote_only", "work_authorization", "years_experience",
                "excluded_companies", "github_evidence", "github_synced_at",
                "created_at", "updated_at",
            ):
                if key in owner and owner[key] is not None:
                    cols[key] = owner[key]

        placeholders = ", ".join(f":{k}" for k in cols)
        live.execute(
            f"INSERT INTO profile ({', '.join(cols)}) VALUES ({placeholders})",
            cols,
        )
        restored += 1
        print(f"restored profile for user {user_id} ({email})")

    live.commit()
    live.close()
    print(f"done: restored {restored} profile(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
