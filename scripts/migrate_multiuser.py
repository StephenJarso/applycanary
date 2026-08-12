"""One-way migration from the single-user database to the multi-user schema.

Phase 2 of plan.md. Rehearse on a copy before the real run — the script takes
the database file as an argument precisely so a mistake costs a scratch file,
not the production database:

    cp data/applycanary.db /tmp/rehearsal.db
    MIGRATE_PASSWORD=... python scripts/migrate_multiuser.py \\
        --db /tmp/rehearsal.db --email admin
    # ... inspect the output and the rehearsal copy ...
    MIGRATE_PASSWORD=... python scripts/migrate_multiuser.py \\
        --db data/applycanary.db --email admin

What it does, in one SQLite transaction so a failure anywhere rolls back:

1. Back up the database to a timestamped copy and verify the copy opens.
2. Delete the fixture profiles (rows with no name or email) so they cannot
   become orphaned accounts.
3. Create the multi-user tables: user, invite_code, user_job.
4. Create the owner account. The email defaults to AUTH_USERNAME (override
   with --email); the password is taken from MIGRATE_PASSWORD or prompted —
   never from AUTH_PASSWORD, which is plaintext in .env and must not survive.
5. Add user_id columns and backfill every existing row to the owner.
6. Populate user_job from the old job.status values (a missing row now means
   NEW); stamp job.expired_at for jobs whose status was EXPIRED.
7. Rebuild job_score, application and interview_prep with a composite
   UNIQUE(user_id, job_id). SQLite cannot add a unique constraint in place,
   so each table is recreated and its rows copied inside the transaction.
8. Assert every table's row count survived the rebuild, and run
   PRAGMA foreign_key_check.

After migrating, boot the new app once (sync_schema adds the per-profile
preference columns), then log in as the owner with the email and password
chosen here. The old single-user app cannot run against the migrated schema:
its job_score inserts carry no user_id, which the rebuilt tables reject.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import hash_password
from app.config import get_settings

log = logging.getLogger("migrate_multiuser")

# Tables whose per-job rows must be recreated because SQLite cannot alter a
# unique constraint in place. user_id is NOT NULL: no row may exist that is not
# owned, and the ORM always writes one.
REBUILD_DDL = {
    "job_score": """
        CREATE TABLE job_score_new (
            id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            keyword_score FLOAT NOT NULL,
            semantic_score FLOAT NOT NULL,
            ats_score FLOAT NOT NULL,
            total FLOAT NOT NULL,
            matched_keywords JSON,
            missing_keywords JSON,
            verdict VARCHAR NOT NULL,
            reasoning TEXT,
            decided_by VARCHAR NOT NULL,
            disqualifier VARCHAR NOT NULL,
            model_used VARCHAR NOT NULL,
            scored_at DATETIME NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_score_user_job UNIQUE (user_id, job_id),
            FOREIGN KEY(user_id) REFERENCES "user" (id),
            FOREIGN KEY(job_id) REFERENCES job (id)
        )
    """,
    "application": """
        CREATE TABLE application_new (
            id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            resume_version_id INTEGER,
            method VARCHAR(7) NOT NULL,
            cover_letter TEXT,
            form_answers JSON,
            queued_at DATETIME NOT NULL,
            submitted_at DATETIME,
            confirmation VARCHAR NOT NULL,
            error TEXT,
            attempts INTEGER NOT NULL,
            follow_up_due DATETIME,
            response_received BOOLEAN NOT NULL,
            outcome VARCHAR NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_application_user_job UNIQUE (user_id, job_id),
            FOREIGN KEY(user_id) REFERENCES "user" (id),
            FOREIGN KEY(job_id) REFERENCES job (id),
            FOREIGN KEY(resume_version_id) REFERENCES resume_version (id)
        )
    """,
    "interview_prep": """
        CREATE TABLE interview_prep_new (
            id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            technical_questions JSON,
            behavioural_questions JSON,
            questions_to_ask JSON,
            company_notes TEXT,
            skill_gaps JSON,
            speech_interview JSON,
            technical_interview JSON,
            model_used VARCHAR NOT NULL,
            created_at DATETIME NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_prep_user_job UNIQUE (user_id, job_id),
            FOREIGN KEY(user_id) REFERENCES "user" (id),
            FOREIGN KEY(job_id) REFERENCES job (id)
        )
    """,
}

# Non-unique indexes that belong on the rebuilt tables (SQLModel emits them with
# these names; the legacy UNIQUE(job_id) indexes die with the old tables).
REBUILD_INDEXES = {
    "job_score": [
        "CREATE INDEX ix_job_score_user_id ON job_score (user_id)",
        "CREATE INDEX ix_job_score_job_id ON job_score (job_id)",
        "CREATE INDEX ix_job_score_total ON job_score (total)",
    ],
    "application": [
        "CREATE INDEX ix_application_user_id ON application (user_id)",
        "CREATE INDEX ix_application_job_id ON application (job_id)",
        "CREATE INDEX ix_application_submitted_at ON application (submitted_at)",
    ],
    "interview_prep": [
        "CREATE INDEX ix_interview_prep_user_id ON interview_prep (user_id)",
        "CREATE INDEX ix_interview_prep_job_id ON interview_prep (job_id)",
    ],
}

# Columns on the rebuilt tables that exist in the old ones (user_id is filled
# by backfill, not copied). Used for INSERT ... SELECT.
COPY_COLUMNS = {
    "job_score": [
        "job_id", "keyword_score", "semantic_score", "ats_score", "total",
        "matched_keywords", "missing_keywords", "verdict", "reasoning",
        "decided_by", "disqualifier", "model_used", "scored_at",
    ],
    "application": [
        "job_id", "resume_version_id", "method", "cover_letter", "form_answers",
        "queued_at", "submitted_at", "confirmation", "error", "attempts",
        "follow_up_due", "response_received", "outcome",
    ],
    "interview_prep": [
        "job_id", "technical_questions", "behavioural_questions",
        "questions_to_ask", "company_notes", "skill_gaps",
        "speech_interview", "technical_interview", "model_used", "created_at",
    ],
}

TRACKED_TABLES = [
    "job", "job_alias", "job_score", "application", "interview_prep",
    "resume_version", "profile", "source_run",
]


def utcnow_str() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")


def row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TRACKED_TABLES}


def backup(db_path: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.parent / f"{db_path.stem}.multiuser-{stamp}{db_path.suffix}"
    shutil.copy2(db_path, backup_path)
    check = sqlite3.connect(backup_path)
    try:
        check.execute("SELECT COUNT(*) FROM job").fetchone()
    except sqlite3.Error as exc:
        raise SystemExit(
            f"backup {backup_path} does not open — refusing to touch the original"
        ) from exc
    finally:
        check.close()
    print(f"backup:  {backup_path}")
    return backup_path


def prompt_password() -> str:
    first = getpass.getpass("New password for the owner account (>= 10 chars): ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        raise SystemExit("passwords do not match")
    return first


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", required=True,
        help="path to the SQLite database file to migrate",
    )
    parser.add_argument(
        "--email", default="",
        help="owner login email (default: AUTH_USERNAME from .env)",
    )
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()

    settings = get_settings()
    owner_email = (args.email or settings.auth_username or "").strip().lower()
    if not owner_email:
        raise SystemExit("no owner email: pass --email or set AUTH_USERNAME in .env")

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        raise SystemExit(f"database not found: {db_path}")
    if not args.yes:
        answer = input(
            f"Migrate {db_path} to the multi-user schema? The old app will no "
            "longer work against it. [y/N] "
        ).strip().lower()
        if answer not in ("y", "yes"):
            print("aborted")
            return 1

    password = os.environ.get("MIGRATE_PASSWORD") or prompt_password()
    if len(password) < 10:
        raise SystemExit("password must be at least 10 characters")
    password_hash = hash_password(password)

    backup(db_path)

    conn = sqlite3.connect(db_path, timeout=30)
    conn.isolation_level = None  # manual transaction control
    try:
        conn.execute("BEGIN IMMEDIATE")

        # --- preconditions -------------------------------------------------
        existing = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for t in ("user", "invite_code", "user_job"):
            if t in existing:
                raise SystemExit(f"already migrated: {t} exists. Refusing to re-run.")
        if "job" not in existing or "status" not in {
            c[1] for c in conn.execute("PRAGMA table_info(job)")
        }:
            raise SystemExit("not a legacy database: job table has no status column")

        before = row_counts(conn)

        # --- 2. fixture profiles -------------------------------------------
        fixtures = conn.execute(
            "SELECT id, full_name FROM profile "
            "WHERE (full_name IS NULL OR full_name = '') "
            "AND (email IS NULL OR email = '')"
        ).fetchall()
        for prof_id, name in fixtures:
            print(f"drop fixture profile: id={prof_id} name={name!r}")
            conn.execute("DELETE FROM profile WHERE id = ?", (prof_id,))

        # --- 3. new tables --------------------------------------------------
        conn.execute("""CREATE TABLE "user" (
            id INTEGER NOT NULL PRIMARY KEY,
            email VARCHAR NOT NULL,
            password_hash VARCHAR NOT NULL,
            is_active BOOLEAN NOT NULL,
            is_admin BOOLEAN NOT NULL,
            token_version INTEGER NOT NULL,
            created_at DATETIME NOT NULL,
            last_login_at DATETIME
        )""")
        conn.execute("CREATE UNIQUE INDEX ix_user_email ON \"user\" (email)")

        conn.execute("""CREATE TABLE invite_code (
            id INTEGER NOT NULL PRIMARY KEY,
            code VARCHAR NOT NULL,
            created_by_id INTEGER,
            used_by_id INTEGER,
            used_at DATETIME,
            expires_at DATETIME,
            created_at DATETIME NOT NULL,
            FOREIGN KEY(created_by_id) REFERENCES "user" (id),
            FOREIGN KEY(used_by_id) REFERENCES "user" (id)
        )""")
        conn.execute("CREATE UNIQUE INDEX ix_invite_code_code ON invite_code (code)")

        conn.execute("""CREATE TABLE user_job (
            id INTEGER NOT NULL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            status VARCHAR(8) NOT NULL,
            created_at DATETIME NOT NULL,
            CONSTRAINT uq_userjob_user_job UNIQUE (user_id, job_id),
            FOREIGN KEY(user_id) REFERENCES "user" (id),
            FOREIGN KEY(job_id) REFERENCES job (id)
        )""")
        conn.execute("CREATE INDEX ix_user_job_user_id ON user_job (user_id)")
        conn.execute("CREATE INDEX ix_user_job_job_id ON user_job (job_id)")
        conn.execute(
            "CREATE INDEX ix_userjob_user_status ON user_job (user_id, status)"
        )

        # --- 4. owner account -----------------------------------------------
        conn.execute(
            "INSERT INTO \"user\" "
            "(email, password_hash, is_active, is_admin, token_version, created_at) "
            "VALUES (?, ?, 1, 1, 1, ?)",
            (owner_email, password_hash, utcnow_str()),
        )
        owner_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        print(f"owner account: id={owner_id} email={owner_email} is_admin=True")

        # --- 5. user_id columns + backfill -----------------------------------
        conn.execute("ALTER TABLE profile ADD COLUMN user_id INTEGER")
        conn.execute(
            "UPDATE profile SET user_id = ? WHERE user_id IS NULL", (owner_id,)
        )
        for table in ("job_score", "application", "interview_prep", "resume_version"):
            cols = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
            if "user_id" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER")
            conn.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (owner_id,))

        # --- 6. user_job from job.status; expired_at --------------------------
        conn.execute(
            "INSERT INTO user_job (user_id, job_id, status, created_at) "
            "SELECT ?, id, status, ? FROM job WHERE status <> 'NEW'",
            (owner_id, utcnow_str()),
        )
        job_cols = {c[1] for c in conn.execute("PRAGMA table_info(job)")}
        if "expired_at" not in job_cols:
            conn.execute("ALTER TABLE job ADD COLUMN expired_at DATETIME")
        expired = conn.execute(
            "SELECT COUNT(*) FROM job WHERE status = 'EXPIRED'"
        ).fetchone()[0]
        if expired:
            conn.execute(
                "UPDATE job SET expired_at = ? WHERE status = 'EXPIRED'",
                (utcnow_str(),),
            )
            print(f"job.expired_at stamped on {expired} EXPIRED postings")
        conn.execute("DROP INDEX IF EXISTS ix_job_status")
        conn.execute("DROP INDEX IF EXISTS ix_job_status_score")
        conn.execute("CREATE INDEX ix_job_status_score ON job (posted_at)")

        # --- 7. rebuild the three composite-unique tables ---------------------
        for table, ddl in REBUILD_DDL.items():
            conn.execute(ddl)
            cols = ", ".join(f'"{c}"' for c in COPY_COLUMNS[table])
            conn.execute(
                f'INSERT INTO {table}_new (id, user_id, {cols}) '
                f"SELECT id, ?, {cols} FROM {table}",
                (owner_id,),
            )
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
            for idx in REBUILD_INDEXES[table]:
                conn.execute(idx)
            print(f"rebuilt {table}: composite UNIQUE(user_id, job_id)")

        # --- 8. verify --------------------------------------------------------
        after = row_counts(conn)
        # profile shrinks on purpose: the fixture rows were deleted. Everything
        # else must survive the rebuild byte for byte.
        strict = set(TRACKED_TABLES) - {"profile"}
        mismatches = {
            t for t in strict if before[t] != after[t]
        }
        conn.commit()
    except Exception:
        conn.rollback()
        log.exception("migration failed — rolled back")
        raise

    print("\nrow counts (before -> after):")
    for t in TRACKED_TABLES:
        if t == "profile":
            expected = before[t] - len(fixtures)
            flag = "" if after[t] == expected else "  <-- MISMATCH"
            print(f"  {t:<16} {before[t]:>6} -> {after[t]:>6}{flag}  (-{len(fixtures)} fixtures)")
        else:
            flag = "" if before[t] == after[t] else "  <-- MISMATCH"
            print(f"  {t:<16} {before[t]:>6} -> {after[t]:>6}{flag}")
    if mismatches:
        print(f"FAIL: row counts changed for: {sorted(mismatches)}")
        return 1

    user_jobs = conn.execute("SELECT COUNT(*) FROM user_job").fetchone()[0]
    non_new = conn.execute(
        "SELECT COUNT(*) FROM job WHERE status <> 'NEW'"
    ).fetchone()[0]
    print(f"\nuser_job rows: {user_jobs} (non-NEW jobs: {non_new})")
    if user_jobs != non_new:
        print("FAIL: user_job count does not match the non-NEW job count")
        return 1

    owner_rows = {
        t: conn.execute(
            f"SELECT COUNT(*) FROM {t} WHERE user_id = ?", (owner_id,)
        ).fetchone()[0]
        for t in ("job_score", "application", "interview_prep", "resume_version")
    }
    print("rows owned by the migrated account:")
    for t, n in owner_rows.items():
        status = "ok" if n == after[t] else "FAIL"
        print(f"  {t:<16} {n:>6}/{after[t]:>6}  {status}")
        if n != after[t]:
            return 1

    fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_violations:
        print(f"FAIL: foreign_key_check found {len(fk_violations)} violations")
        for v in fk_violations[:10]:
            print("  ", v)
        return 1
    print("foreign_key_check: clean")

    # The composite unique must actually reject a duplicate (user, job) row.
    # Probe with a pair that already exists in that table, or the insert is not
    # a duplicate and correctly succeeds.
    probes = {
        "user_job": (
            "INSERT INTO user_job (user_id, job_id, status, created_at) "
            "VALUES (?, ?, 'NEW', ?)",
            "SELECT job_id FROM user_job WHERE user_id = ? LIMIT 1",
        ),
        "job_score": (
            "INSERT INTO job_score (user_id, job_id, keyword_score, semantic_score, "
            "ats_score, total, verdict, decided_by, disqualifier, model_used, scored_at) "
            "VALUES (?, ?, 0, 0, 0, 0, '', '', '', '', ?)",
            "SELECT job_id FROM job_score WHERE user_id = ? LIMIT 1",
        ),
        "application": (
            "INSERT INTO application (user_id, job_id, method, queued_at, confirmation, "
            "attempts, response_received, outcome) "
            "VALUES (?, ?, 'MANUAL', ?, '', 0, 0, '')",
            "SELECT job_id FROM application WHERE user_id = ? LIMIT 1",
        ),
        "interview_prep": (
            "INSERT INTO interview_prep (user_id, job_id, model_used, created_at) "
            "VALUES (?, ?, '', ?)",
            "SELECT job_id FROM interview_prep WHERE user_id = ? LIMIT 1",
        ),
    }
    for table, (insert_sql, lookup_sql) in probes.items():
        probe_job = conn.execute(lookup_sql, (owner_id,)).fetchone()
        if probe_job is None:
            print(f"{table}: no rows to probe the composite unique against; skipped")
            continue
        try:
            conn.execute(insert_sql, (owner_id, probe_job[0], utcnow_str()))
        except sqlite3.IntegrityError:
            print(f"composite unique enforced on {table}")
        else:
            print(f"FAIL: {table} accepted a duplicate (user_id, job_id)")
            return 1

    conn.close()
    print("\nmigration complete. Next: boot the new app (sync_schema adds the")
    print("profile preference columns), then log in as the owner.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
