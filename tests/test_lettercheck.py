"""Cover-letter gate tests.

The blocking cases matter most: a letter that breaks a stated word limit or
invents a figure is the failure this check exists to catch, because the letter is
sent under the candidate's own name.
"""

from __future__ import annotations

from app.pipeline.lettercheck import check_letter
from app.pipeline.requirements import extract_requirements

RESUME = """
Jane Doe
Backend Engineer

Experience
Acme Corp - Backend Engineer, Mar 2022 - Present
- Built REST APIs in Python using FastAPI and PostgreSQL
- Reduced p95 latency by 40% with Redis caching
- Deployed services with Docker on AWS

Skills
Python, FastAPI, PostgreSQL, Redis, Docker, AWS
"""

GITHUB = "Repository: task-queue (Python) using Redis and Celery."

JOB = """
Global Internship Program. You will build cloud services in Python and Go.

Please answer the following in your cover letter:
- Why do you want to build a career in Japan?
- Which open-source projects have you contributed to?

Keep your cover letter to a maximum of 200 words.
Provide a link to your GitHub profile.
"""

GOOD_LETTER = (
    "I want to build a career in Japan because I have followed HENNGE's approach to "
    "cloud security since university, and I want to work on infrastructure that "
    "other teams depend on. At Acme I built REST APIs in Python with FastAPI and "
    "PostgreSQL, and cut p95 latency by 40% by adding Redis caching. I contribute to "
    "open-source projects on GitHub, including a distributed task queue that uses "
    "Redis, where I maintain the worker pooling code. That work is why I want to "
    "spend the next decade building cloud services in Japan rather than only "
    "studying them from a distance."
)


def check(letter: str, *, job: str = JOB, company: str = "HENNGE"):
    return check_letter(
        letter,
        job_description=job,
        resume_text=RESUME,
        github_evidence=GITHUB,
        company=company,
    )


class TestExtraction:
    def test_reads_word_limit(self):
        assert extract_requirements(JOB).word_limit == 200

    def test_reads_questions(self):
        questions = extract_requirements(JOB).questions
        assert any("Japan" in q for q in questions)
        assert any("open-source" in q for q in questions)

    def test_reads_required_link_and_ignores_blurb(self):
        req = extract_requirements(JOB)
        assert "GitHub" in req.links

    def test_bare_mention_is_not_a_requirement(self):
        req = extract_requirements("We are a portfolio company building tools.")
        assert req.links == []

    def test_empty_description(self):
        assert extract_requirements("").empty


class TestBlocks:
    def test_word_limit_exceeded_blocks(self):
        r = check(" ".join(["word"] * 260))
        assert not r.passed
        assert any(f.rule == "word_limit_exceeded" for f in r.blocks)

    def test_invented_metric_blocks(self):
        r = check("I reduced latency by 95% at Acme and shipped with FastAPI.")
        assert not r.passed
        assert any(f.rule == "invented_metric" for f in r.blocks)

    def test_unsupported_skill_blocks(self):
        r = check("I have deep Kubernetes experience and built REST APIs in Python.")
        assert not r.passed
        assert any(f.rule == "unsupported_skill" for f in r.blocks)

    def test_empty_letter_blocks(self):
        assert not check("").passed

    def test_supported_figure_passes(self):
        r = check(GOOD_LETTER)
        assert not any(f.rule == "invented_metric" for f in r.findings)


class TestFlags:
    def test_unanswered_question_flagged(self):
        letter = "I built REST APIs in Python with FastAPI at Acme. I like HENNGE."
        r = check(letter)
        assert any(f.rule == "unanswered_question" for f in r.findings)

    def test_generic_filler_flagged(self):
        letter = (
            "I am writing to express my interest in this role at HENNGE. "
            "I built REST APIs in Python with FastAPI, and I want to build a career "
            "in Japan and contribute to open-source projects on GitHub."
        )
        r = check(letter)
        assert any(f.rule == "generic_filler" for f in r.findings)

    def test_unnamed_company_flagged(self):
        r = check(
            "I build REST APIs in Python with FastAPI and want to work in Japan.",
            company="HENNGE",
        )
        assert any(f.rule == "no_company_reference" for f in r.findings)

    def test_good_letter_passes(self):
        r = check(GOOD_LETTER)
        assert r.passed
        assert r.score >= 70
