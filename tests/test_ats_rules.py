"""ATS rule engine tests.

These rules drive advice the user acts on by editing a real document, so the
important property is that a *clean* resume stays clean. A checker that flags
everything is as useless as one that flags nothing.
"""

from __future__ import annotations

from app.pipeline.ats_rules import check_against_job, check_resume, evaluate
from app.resume.parse import ParsedResume, _extract_content_signals

CLEAN = """\
Jane Mwangi
jane.mwangi@example.com | +254 700 000000 | Nairobi, Kenya
github.com/janemwangi | linkedin.com/in/janemwangi

Summary
Backend engineer with 4 years building Python services on AWS.

Experience
Senior Backend Engineer, Acme Ltd
Jan 2022 - Present
- Built a Django payments service handling 12k requests per minute.
- Reduced p95 latency by 40% by adding Redis caching.
- Migrated 30 endpoints from Flask to FastAPI with zero downtime.

Backend Engineer, Bitwise
Mar 2020 - Dec 2021
- Designed a PostgreSQL schema supporting 2M rows.
- Automated deployments with Docker and GitHub Actions.

Education
BSc Computer Science, University of Nairobi, 2019

Skills
Python, Django, FastAPI, PostgreSQL, Redis, Docker, AWS, Git
"""


def _parsed(text: str, **kwargs: object) -> ParsedResume:
    """Build a ParsedResume for rule tests.

    Defaults describe a clean text-bearing DOCX; any field can be overridden to
    exercise a specific rule (a scanned PDF, a multi-column layout, and so on).
    """
    fields = {"text": text, "file_type": "docx", "has_text_layer": True}
    fields.update(kwargs)
    out = ParsedResume(**fields)  # type: ignore[arg-type]
    _extract_content_signals(out)
    return out


class TestCleanResume:
    def test_clean_resume_scores_well(self) -> None:
        report = check_resume(_parsed(CLEAN))
        assert report.score >= 80, [f.message for f in report.findings]

    def test_clean_resume_has_no_critical_findings(self) -> None:
        report = check_resume(_parsed(CLEAN))
        assert report.critical == [], [f.message for f in report.critical]

    def test_clean_resume_passes(self) -> None:
        assert check_resume(_parsed(CLEAN)).passed

    def test_sections_detected(self) -> None:
        parsed = _parsed(CLEAN)
        for section in ("experience", "education", "skills", "summary"):
            assert section in parsed.sections_found

    def test_contact_details_detected(self) -> None:
        parsed = _parsed(CLEAN)
        assert parsed.emails == ["jane.mwangi@example.com"]
        assert parsed.phones
        assert any("github.com" in u for u in parsed.urls)


class TestLayoutRules:
    def test_scanned_pdf_is_critical(self) -> None:
        report = check_resume(
            _parsed("", file_type="pdf", has_text_layer=False)
        )
        assert any(f.rule == "no_text_layer" and f.severity == "critical"
                   for f in report.findings)

    def test_multi_column_is_critical(self) -> None:
        report = check_resume(_parsed(CLEAN, has_multi_column=True))
        assert any(f.rule == "multi_column" and f.severity == "critical"
                   for f in report.findings)

    def test_tables_flagged(self) -> None:
        report = check_resume(_parsed(CLEAN, table_count=3))
        assert any(f.rule == "tables" for f in report.findings)

    def test_header_footer_flagged(self) -> None:
        report = check_resume(
            _parsed(CLEAN, header_footer_text=["jane@example.com"])
        )
        assert any(f.rule == "header_footer" for f in report.findings)

    def test_exotic_font_flagged(self) -> None:
        report = check_resume(_parsed(CLEAN, fonts={"Zapfino"}))
        assert any(f.rule == "fonts" for f in report.findings)

    def test_safe_font_not_flagged(self) -> None:
        report = check_resume(_parsed(CLEAN, fonts={"Calibri", "Arial-Bold"}))
        assert not any(f.rule == "fonts" for f in report.findings)


class TestContentRules:
    def test_missing_contact_is_critical(self) -> None:
        report = check_resume(_parsed("Experience\nDid some work.\nEducation\nBSc"))
        assert any(f.rule == "no_email" and f.severity == "critical"
                   for f in report.findings)

    def test_missing_experience_section_flagged(self) -> None:
        text = "Jane\njane@example.com\n\nWhere I Have Made Impact\n- Built things"
        report = check_resume(_parsed(text))
        assert any(f.rule == "missing_section_experience" for f in report.findings)

    def test_creative_heading_reported_as_unknown(self) -> None:
        text = CLEAN.replace("Experience", "Where I've Made An Impact")
        parsed = _parsed(text)
        assert "where i've made an impact" in [h.lower() for h in parsed.unknown_headings]

    def test_short_resume_flagged(self) -> None:
        report = check_resume(_parsed("Jane\njane@example.com\nExperience\nSkills"))
        assert any(f.rule == "too_short" for f in report.findings)

    def test_no_dates_flagged(self) -> None:
        text = CLEAN.replace("Jan 2022 - Present", "").replace("Mar 2020 - Dec 2021", "")
        text = text.replace("2019", "").replace("12k", "").replace("40%", "")
        report = check_resume(_parsed(text))
        assert any(f.rule == "no_dates" for f in report.findings)


class TestJobMatching:
    def test_low_coverage_is_critical(self) -> None:
        findings = check_against_job(
            _parsed(CLEAN),
            matched_keywords=["python"],
            missing_keywords=["kubernetes", "terraform", "go", "kafka"],
        )
        assert any(
            f.rule == "low_keyword_coverage" and f.severity == "critical"
            for f in findings
        )

    def test_medium_coverage_is_a_warning_not_critical(self) -> None:
        findings = check_against_job(
            _parsed(CLEAN),
            matched_keywords=["python", "django", "aws"],
            missing_keywords=["kubernetes", "terraform"],
        )
        assert any(f.rule == "medium_keyword_coverage" for f in findings)
        assert not any(f.severity == "critical" for f in findings)

    def test_good_coverage_not_flagged(self) -> None:
        findings = check_against_job(
            _parsed(CLEAN),
            matched_keywords=["python", "django", "aws", "docker", "redis"],
            missing_keywords=[],
        )
        assert findings == []

    def test_missing_terms_listed_in_detail(self) -> None:
        findings = check_against_job(
            _parsed(CLEAN),
            matched_keywords=["python"],
            missing_keywords=["kubernetes", "terraform", "go", "kafka"],
        )
        assert "kubernetes" in findings[0].detail


class TestEvaluateTextEntryPoint:
    def test_evaluate_skips_layout_rules(self) -> None:
        report = evaluate(CLEAN)
        layout = {"multi_column", "tables", "header_footer", "fonts", "file_type"}
        assert not any(f.rule in layout for f in report.findings)

    def test_evaluate_scores_clean_text_well(self) -> None:
        assert evaluate(CLEAN).score >= 80

    def test_evaluate_applies_job_keywords(self) -> None:
        jd = "We need Kubernetes, Terraform and Kafka experience."
        assert evaluate(CLEAN, job_description=jd).score < evaluate(CLEAN).score

    def test_evaluate_is_deterministic(self) -> None:
        jd = "Python, Django, Kubernetes."
        first = evaluate(CLEAN, job_description=jd)
        second = evaluate(CLEAN, job_description=jd)
        assert first.score == second.score
