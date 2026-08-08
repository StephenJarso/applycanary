"""SQLModel schema.

Timestamps are naive UTC throughout (`utcnow()`); SQLite has no tz-aware type
and mixing aware/naive datetimes is a common source of comparison bugs.

Note: this module deliberately does NOT use `from __future__ import annotations`.
That turns every annotation into a string, and SQLAlchemy cannot resolve a PEP
604 union like `JobScore | None` as a relationship target — mappers fail to
configure on the first query. Relationship targets use quoted forward
references instead, which is the form SQLModel documents.
"""

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Optional

from sqlalchemy import Column, Index, Text, UniqueConstraint
from sqlmodel import JSON, Field, Relationship, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class JobStatus(StrEnum):
    NEW = "new"                # ingested, not yet scored
    SCORED = "scored"          # scored, awaiting a decision
    QUEUED = "queued"          # artifacts built, awaiting approval or auto-submit
    APPLIED = "applied"
    REJECTED = "rejected"      # filtered out by scoring
    SKIPPED = "skipped"        # user declined
    EXPIRED = "expired"        # vanished from source
    FAILED = "failed"          # submission errored


class ApplyMethod(StrEnum):
    API = "api"                # sanctioned public endpoint
    MANUAL = "manual"          # review queue, human clicks submit
    DRY_RUN = "dry_run"        # built but deliberately not sent


class Severity(StrEnum):
    CRITICAL = "critical"      # ATS will likely drop or garble the resume
    WARNING = "warning"
    INFO = "info"


# ---------------------------------------------------------------- profile


class Profile(SQLModel, table=True):
    """Single-row table holding the job seeker's details."""

    __tablename__ = "profile"

    id: int | None = Field(default=None, primary_key=True)
    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin_url: str = ""
    github_username: str = ""
    portfolio_url: str = ""

    base_resume_path: str = ""
    base_resume_text: str = Field(default="", sa_column=Column(Text))
    skills: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # Hard filters applied before any paid API call.
    min_salary: int | None = None
    salary_currency: str = "USD"
    target_titles: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    target_locations: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    remote_only: bool = False
    work_authorization: str = ""
    years_experience: int | None = None
    excluded_companies: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    github_evidence: dict = Field(default_factory=dict, sa_column=Column(JSON))
    github_synced_at: datetime | None = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------- jobs


class Job(SQLModel, table=True):
    __tablename__ = "job"
    __table_args__ = (
        Index("ix_job_status_score", "status", "posted_at"),
        Index("ix_job_source_sourceid", "source", "source_id"),
    )

    id: int | None = Field(default=None, primary_key=True)

    # Deterministic dedup key: sha256(company + title + location bucket).
    fingerprint: str = Field(index=True, unique=True)

    source: str = Field(index=True)
    source_id: str = ""

    company: str = Field(index=True)
    title: str
    location: str = ""
    is_remote: bool = False

    description: str = Field(default="", sa_column=Column(Text))
    description_hash: str = ""

    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str = ""
    salary_is_estimate: bool = False

    apply_url: str = ""
    canonical_url: str = Field(default="", index=True)
    # Platform + board token, e.g. ("smartrecruiters", "acme"). Determines which
    # submitter can handle the job.
    ats_platform: str = ""
    ats_board_token: str = ""

    posted_at: datetime | None = None
    first_seen_at: datetime = Field(default_factory=utcnow, index=True)
    last_seen_at: datetime = Field(default_factory=utcnow)

    status: JobStatus = Field(default=JobStatus.NEW, index=True)
    seen_count: int = 1

    score: Optional["JobScore"] = Relationship(
        back_populates="job",
        sa_relationship_kwargs={"uselist": False, "cascade": "all, delete-orphan"},
    )
    aliases: list["JobAlias"] = Relationship(
        back_populates="job",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    application: Optional["Application"] = Relationship(
        back_populates="job",
        sa_relationship_kwargs={"uselist": False, "cascade": "all, delete-orphan"},
    )

    @property
    def age(self) -> timedelta:
        """How long since posting. Drives the fast-apply prioritisation."""
        return utcnow() - (self.posted_at or self.first_seen_at)


class JobAlias(SQLModel, table=True):
    """A duplicate sighting of a job already stored under another source.

    Kept rather than discarded so dedup decisions stay auditable and the UI can
    show 'seen on 4 boards'.
    """

    __tablename__ = "job_alias"
    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_alias_source"),)

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    source: str
    source_id: str = ""
    apply_url: str = ""
    # Which dedup layer matched: "fingerprint" | "fuzzy_title" | "canonical_url"
    matched_by: str = ""
    match_score: float | None = None
    seen_at: datetime = Field(default_factory=utcnow)

    job: Optional["Job"] = Relationship(back_populates="aliases")


class JobScore(SQLModel, table=True):
    __tablename__ = "job_score"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True, unique=True)

    keyword_score: float = 0.0      # tier 1, TF-IDF overlap
    semantic_score: float = 0.0     # tier 2, LLM fit
    ats_score: float = 0.0          # resume structure vs this JD
    total: float = Field(default=0.0, index=True)

    matched_keywords: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    missing_keywords: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    verdict: str = ""               # strong_match | possible | weak | disqualified
    reasoning: str = Field(default="", sa_column=Column(Text))
    # Which stage decided this: "tier1_filter" | "tier1_keyword" | "tier2_llm"
    decided_by: str = ""
    disqualifier: str = ""          # populated when a hard filter rejected it
    model_used: str = ""
    scored_at: datetime = Field(default_factory=utcnow)

    job: Optional["Job"] = Relationship(back_populates="score")


# ---------------------------------------------------------------- artifacts


class ResumeVersion(SQLModel, table=True):
    __tablename__ = "resume_version"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int | None = Field(default=None, foreign_key="job.id", index=True)

    docx_path: str = ""
    pdf_path: str = ""
    text: str = Field(default="", sa_column=Column(Text))
    diff_summary: str = Field(default="", sa_column=Column(Text))

    ats_score_before: float = 0.0
    ats_score_after: float = 0.0
    keywords_added: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # Anti-fabrication gate result. A version is never eligible for submission
    # unless truthcheck_passed is True.
    truthcheck_passed: bool = False
    truthcheck_notes: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    unverifiable_claims: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    model_used: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Application(SQLModel, table=True):
    __tablename__ = "application"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True, unique=True)
    resume_version_id: int | None = Field(default=None, foreign_key="resume_version.id")

    method: ApplyMethod = Field(default=ApplyMethod.MANUAL)
    cover_letter: str = Field(default="", sa_column=Column(Text))
    # Pre-filled answers to the form's questions, for one-click manual submit.
    form_answers: dict = Field(default_factory=dict, sa_column=Column(JSON))

    queued_at: datetime = Field(default_factory=utcnow)
    submitted_at: datetime | None = Field(default=None, index=True)
    confirmation: str = ""
    error: str = Field(default="", sa_column=Column(Text))
    attempts: int = 0

    follow_up_due: datetime | None = None
    response_received: bool = False
    outcome: str = ""               # interview | rejected | ghosted | offer

    job: Optional["Job"] = Relationship(back_populates="application")


class InterviewPrep(SQLModel, table=True):
    __tablename__ = "interview_prep"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True, unique=True)

    technical_questions: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    behavioural_questions: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    questions_to_ask: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    company_notes: str = Field(default="", sa_column=Column(Text))
    # Requirements with no supporting evidence: what to study before interviewing.
    skill_gaps: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    model_used: str = ""
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------- telemetry


class SourceRun(SQLModel, table=True):
    """One connector execution. Makes a silently-broken source visible."""

    __tablename__ = "source_run"

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    started_at: datetime = Field(default_factory=utcnow, index=True)
    duration_ms: int = 0
    found: int = 0
    new_jobs: int = 0
    duplicates: int = 0
    ok: bool = True
    error: str = Field(default="", sa_column=Column(Text))
