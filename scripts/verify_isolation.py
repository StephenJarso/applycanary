"""Prove tenant isolation holds: two users, one shared job posting.

Run against a scratch database:
    DATABASE_URL=sqlite:////tmp/iso.db python scripts/verify_isolation.py
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.auth import hash_password
from app.db import engine, init_db
from app.deps import user_job
from app.models import Job, JobScore, JobStatus, User


def main() -> int:
    init_db()
    failures: list[str] = []

    with Session(engine) as session:
        a = User(email="a@example.com", password_hash=hash_password("pw"))
        b = User(email="b@example.com", password_hash=hash_password("pw"))
        session.add(a)
        session.add(b)
        session.commit()
        session.refresh(a)
        session.refresh(b)

        job = Job(
            fingerprint="fp-iso-1",
            source="manual",
            company="Acme",
            title="Backend Engineer",
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        # 1. Both users score the same job. The old unique(job_id) made this
        #    impossible; the composite unique(job_id, user_id) allows it.
        session.add(JobScore(job_id=job.id, user_id=a.id, total=91.0))
        session.add(JobScore(job_id=job.id, user_id=b.id, total=12.0))
        session.commit()
        totals = sorted(
            r.total for r in session.exec(
                select(JobScore).where(JobScore.job_id == job.id)
            ).all()
        )
        print(f"1. both users scored the same job: {totals}")
        if totals != [12.0, 91.0]:
            failures.append(f"expected [12.0, 91.0], got {totals}")

        # 2. A skips the job; B's view of it must be untouched.
        ua = user_job(session, a.id, job.id)
        ua.status = JobStatus.SKIPPED
        session.add(ua)
        session.commit()
        ub = user_job(session, b.id, job.id)
        print(f"2. A={ua.status.value}  B={ub.status.value}  (B must stay 'new')")
        if ub.status is not JobStatus.NEW:
            failures.append(f"B's status leaked: {ub.status}")

        # 3. A B-scoped score query must never surface A's row.
        mine = session.exec(
            select(JobScore).where(
                JobScore.job_id == job.id, JobScore.user_id == b.id
            )
        ).one()
        print(f"3. B sees only its own score: {mine.total} (not 91.0)")
        if mine.total != 12.0:
            failures.append(f"cross-tenant score leak: {mine.total}")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("\nall isolation checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
