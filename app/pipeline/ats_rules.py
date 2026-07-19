"""Deterministic ATS compatibility rules.

No LLM involved: these are mechanical, explainable checks that produce the same
verdict every run, which matters because the user acts on them to edit a real
document. Each rule returns an id, severity, what is wrong, and how to fix it.

Scoring starts at 100 and deducts per finding. `critical` findings mean an ATS is
likely to garble or discard content outright.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.resume.parse import ParsedResume

PENALTY = {"critical": 22, "warning": 9, "info": 3}

# Fonts that commonly fail to map to a standard encoding, producing mojibake.
_SAFE_FONT_HINTS = (
    "arial", "helvetica", "calibri", "times", "georgia", "garamond", "verdana",
    "tahoma", "cambria", "book antiqua", "trebuchet", "lato", "roboto",
    "opensans", "open sans", "liberation", "nimbus", "dejavu", "carlito",
)

_DATE_RANGE = re.compile(
    r"""(?:
        (?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*'?\d{2,4}
        | \d{1,2}\s*[/-]\s*\d{4}
        | \b(19|20)\d{2}\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

_ACTION_VERBS = {
    "built", "led", "shipped", "designed", "implemented", "migrated", "reduced",
    "increased", "automated", "optimised", "optimized", "launched", "owned",
    "scaled", "delivered", "refactored", "developed", "created", "improved",
    "architected", "deployed", "integrated", "mentored", "drove", "cut",
}


@dataclass(slots=True)
class Finding:
    rule: str
    severity: str
    message: str
    fix: str
    detail: str = ""


@dataclass(slots=True)
class AtsReport:
    score: float
    findings: list[Finding]

    @property
    def critical(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "critical"]

    @property
    def passed(self) -> bool:
        return not self.critical and self.score >= 70

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "passed": self.passed,
            "findings": [
                {
                    "rule": f.rule, "severity": f.severity, "message": f.message,
                    "fix": f.fix, "detail": f.detail,
                }
                for f in self.findings
            ],
        }


def check_resume(parsed: ParsedResume) -> AtsReport:
    """Structural and content checks that do not depend on a job description."""
    f: list[Finding] = []

    if not parsed.has_text_layer:
        f.append(Finding(
            "no_text_layer", "critical",
            "The file has no extractable text layer.",
            "Export from the original document instead of scanning or "
            "screenshotting. An ATS reads zero words from an image-only PDF.",
        ))

    if parsed.file_type not in ("pdf", "docx"):
        f.append(Finding(
            "file_type", "warning",
            f"Format is .{parsed.file_type}.",
            "Submit .docx or a text-based .pdf. Those are the two formats every "
            "major ATS parses reliably.",
        ))

    if parsed.has_multi_column:
        f.append(Finding(
            "multi_column", "critical",
            "A multi-column layout was detected.",
            "Switch to a single-column layout. Parsers read straight across the "
            "page, so columns interleave into unreadable text.",
        ))

    if parsed.table_count:
        f.append(Finding(
            "tables", "critical",
            f"{parsed.table_count} table(s) detected.",
            "Replace tables with plain paragraphs and bullet lists. Cell content "
            "is frequently flattened out of order or dropped.",
            detail=f"table_count={parsed.table_count}",
        ))

    if parsed.header_footer_text:
        sample = ", ".join(dict.fromkeys(parsed.header_footer_text))[:120]
        contacts_at_risk = any(
            _looks_like_contact(t) for t in parsed.header_footer_text
        )
        f.append(Finding(
            "header_footer", "critical" if contacts_at_risk else "warning",
            "Text sits in the page header/footer region.",
            "Move it into the document body. Many parsers ignore these regions "
            + ("and your contact details are there, so you may be unreachable."
               if contacts_at_risk else "entirely."),
            detail=sample,
        ))

    if parsed.image_count:
        f.append(Finding(
            "images", "warning",
            f"{parsed.image_count} image(s) detected.",
            "Remove photos, logos and icon graphics. They carry no parseable "
            "text and some parsers abort on them.",
        ))

    if unsafe := _unsafe_fonts(parsed.fonts):
        f.append(Finding(
            "fonts", "info",
            "Non-standard fonts detected.",
            "Use a common font such as Arial, Calibri or Georgia so characters "
            "map to standard encoding rather than becoming mojibake.",
            detail=", ".join(sorted(unsafe)[:6]),
        ))

    # --- contact block ---
    if not parsed.emails:
        f.append(Finding(
            "no_email", "critical",
            "No email address found in the body text.",
            "Add a plain-text email near the top. Without it the application may "
            "be unroutable.",
        ))
    if not parsed.phones:
        f.append(Finding(
            "no_phone", "warning",
            "No phone number found.",
            "Add a phone number with country code in plain text.",
        ))

    # --- sections ---
    for required in ("experience", "education", "skills"):
        if required not in parsed.sections_found:
            f.append(Finding(
                f"missing_section_{required}", "warning",
                f"No standard '{required}' heading found.",
                f"Add a literal '{required.title()}' heading. Parsers key off "
                "conventional headings to bucket content.",
            ))

    if parsed.unknown_headings:
        f.append(Finding(
            "nonstandard_headings", "warning",
            "Headings that an ATS may not recognise were found.",
            "Rename creative headings to conventional ones (Experience, "
            "Education, Skills, Projects).",
            detail=", ".join(dict.fromkeys(parsed.unknown_headings))[:140],
        ))

    if not _DATE_RANGE.search(parsed.text):
        f.append(Finding(
            "no_dates", "warning",
            "No recognisable employment dates found.",
            "Use an unambiguous format such as 'Mar 2023 - Present' so tenure "
            "can be computed.",
        ))

    # --- length and prose ---
    wc = parsed.word_count
    if wc and wc < 200:
        f.append(Finding(
            "too_short", "warning", f"Only {wc} words of text.",
            "Expand with concrete, quantified accomplishments. Very short "
            "resumes score poorly on keyword coverage.",
        ))
    elif wc > 1200:
        f.append(Finding(
            "too_long", "info", f"{wc} words is long.",
            "Trim toward 1-2 pages, keeping the most role-relevant material.",
        ))

    if len(parsed.bullet_glyphs - {"-", "*"}) > 2:
        f.append(Finding(
            "mixed_bullets", "info",
            "Several different bullet glyphs are in use.",
            "Standardise on one simple bullet character.",
            detail=" ".join(sorted(parsed.bullet_glyphs)),
        ))

    if wc > 150 and not _has_action_verbs(parsed.words):
        f.append(Finding(
            "weak_verbs", "info",
            "Few recognisable accomplishment verbs found.",
            "Start bullets with concrete verbs (built, led, reduced, migrated) "
            "and attach a measurable result.",
        ))

    return AtsReport(score=_score(f), findings=f)


def check_against_job(
    parsed: ParsedResume,
    *,
    matched_keywords: Iterable[str],
    missing_keywords: Iterable[str],
) -> list[Finding]:
    """JD-relative findings, appended to the structural ones."""
    missing = [k for k in missing_keywords if k]
    matched = [k for k in matched_keywords if k]
    findings: list[Finding] = []

    total = len(missing) + len(matched)
    if total:
        coverage = len(matched) / total
        if coverage < 0.4:
            findings.append(Finding(
                "low_keyword_coverage", "critical",
                f"Only {coverage:.0%} of the job's key terms appear in the resume.",
                "Work the genuinely-held missing terms into your existing "
                "bullets using the job's own wording. Do not add skills you do "
                "not have.",
                detail=", ".join(missing[:12]),
            ))
        elif coverage < 0.65:
            findings.append(Finding(
                "medium_keyword_coverage", "warning",
                f"{coverage:.0%} keyword coverage against this job.",
                "Mirror the posting's terminology for skills you already have.",
                detail=", ".join(missing[:12]),
            ))
    return findings


def combine(report: AtsReport, extra: list[Finding]) -> AtsReport:
    merged = [*report.findings, *extra]
    return AtsReport(score=_score(merged), findings=merged)


def evaluate(resume_text: str, *, job_description: str = "") -> AtsReport:
    """Evaluate raw resume *text*, with no file to inspect.

    Used when comparing a generated variant against the original: there is no
    document yet, so layout rules (columns, tables, headers) cannot apply and are
    skipped rather than reported as passing. Only content rules run, which keeps
    before/after comparisons honest — both sides are measured the same way.
    """
    from app.resume.parse import ParsedResume, _extract_content_signals

    parsed = ParsedResume(text=resume_text or "", file_type="txt", has_text_layer=True)
    _extract_content_signals(parsed)

    report = check_resume(parsed)
    # Drop findings that are meaningless without a real file.
    layout_only = {"file_type", "multi_column", "tables", "header_footer",
                   "images", "fonts", "no_text_layer"}
    content = [f for f in report.findings if f.rule not in layout_only]

    if job_description:
        _, matched, missing = _overlap(resume_text, job_description)
        content += check_against_job(
            parsed, matched_keywords=matched, missing_keywords=missing
        )

    return AtsReport(score=_score(content), findings=content)


def _overlap(resume_text: str, jd_text: str) -> tuple[float, list[str], list[str]]:
    from app.pipeline.keywords import keyword_overlap

    return keyword_overlap(resume_text, jd_text)


# ---------------------------------------------------------------- helpers


def _score(findings: list[Finding]) -> float:
    score = 100.0 - sum(PENALTY.get(f.severity, 0) for f in findings)
    return round(max(0.0, min(100.0, score)), 1)


def _unsafe_fonts(fonts: set[str]) -> set[str]:
    unsafe = set()
    for font in fonts:
        low = font.lower()
        if not any(hint in low for hint in _SAFE_FONT_HINTS):
            unsafe.add(font)
    return unsafe


def _looks_like_contact(text: str) -> bool:
    low = text.lower()
    if "@" in low:
        return True
    if any(tok in low for tok in ("linkedin", "github", "tel:", "phone")):
        return True
    return sum(c.isdigit() for c in low) >= 9


def _has_action_verbs(words: list[str]) -> bool:
    return len(_ACTION_VERBS.intersection(words)) >= 3
