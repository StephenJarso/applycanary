"""Verify tailored resume content against its source material.

The tailoring prompt forbids fabrication, but a prompt is a request, not a
guarantee. This module re-checks the model's output in code, because the failure
mode is serious: a fabricated claim goes onto a real resume that a real person
submits under their own name.

The checks are deliberately conservative about what they *block* versus what they
*flag*. A false block is an annoyance; a false pass puts an invented claim in
front of an employer. So numbers and proper nouns — the things models most often
invent and that are most damaging — are hard-blocked when unsupported, while
softer wording changes are surfaced for review.

`verify` never raises: a checker that crashes must not become a path that skips
checking.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.pipeline.keywords import extract_skills

log = logging.getLogger(__name__)

# Employment-shaped claims. If a model emits one of these and it is not in the
# source, it has invented a job or a credential.
_CREDENTIAL_PATTERNS = (
    re.compile(r"\b(?:bachelor|master|mba|ph\.?d|doctorate|b\.?sc|m\.?sc|b\.?eng)\b", re.I),
    re.compile(r"\b(?:certified|certification|certificate)\b", re.I),
)

# Words that inflate scope; flagged, not blocked, since they can be legitimate.
_INFLATION_WORDS = (
    "led", "spearheaded", "founded", "managed", "directed", "owned",
    "architected", "mentored", "supervised", "head of", "principal",
)

_NUMBER = re.compile(r"\b\d[\d,]*\.?\d*\s*(?:%|percent|k\b|m\b|x\b)?", re.I)
_PROPER_NOUN = re.compile(r"\b[A-Z][a-zA-Z0-9.+#-]{2,}\b")

# Tokens that look like proper nouns but are ordinary sentence starts or common
# resume words; excluding them keeps the noise down.
_PROPER_NOUN_ALLOW = {
    "The", "This", "That", "These", "Those", "Built", "Designed", "Created",
    "Developed", "Implemented", "Improved", "Reduced", "Increased", "Wrote",
    "Added", "Migrated", "Refactored", "Shipped", "Launched", "Maintained",
    "Worked", "Used", "Using", "Collaborated", "Partnered", "Delivered",
    "And", "But", "For", "With", "From", "Into", "Also", "While", "When",
    "Team", "Teams", "Company", "Project", "Projects", "Feature", "Features",
    "API", "APIs", "CI", "CD", "SQL", "REST", "HTTP", "JSON", "AWS", "GCP",
}


@dataclass(slots=True)
class Violation:
    kind: str
    severity: str  # "block" | "flag"
    detail: str
    bullet: str = ""

    def __str__(self) -> str:
        return f"[{self.severity}/{self.kind}] {self.detail}"


@dataclass(slots=True)
class TruthReport:
    violations: list[Violation] = field(default_factory=list)
    checked_bullets: int = 0
    checker_failed: bool = False

    @property
    def blocks(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "block"]

    @property
    def flags(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "flag"]

    @property
    def passed(self) -> bool:
        """True only if nothing is blocking and the checker itself ran cleanly."""
        return not self.blocks and not self.checker_failed

    def summary(self) -> str:
        if self.checker_failed:
            return "Verification could not complete; manual review required."
        if not self.violations:
            return f"All {self.checked_bullets} bullets trace to source material."
        return (
            f"{len(self.blocks)} blocking, {len(self.flags)} to review "
            f"across {self.checked_bullets} bullets."
        )


def verify(tailored: dict, *, resume_text: str, github_evidence: str = "") -> TruthReport:
    """Check every generated bullet against the resume and GitHub evidence."""
    report = TruthReport()
    try:
        _run_checks(tailored, resume_text, github_evidence, report)
    except Exception as exc:  # noqa: BLE001 - a crash must not mean "approved"
        log.exception("truthcheck failed")
        report.checker_failed = True
        report.violations.append(Violation(
            kind="checker_error", severity="block",
            detail=f"Verification did not complete ({type(exc).__name__}); review manually.",
        ))
    return report


def _run_checks(
    tailored: dict, resume_text: str, github_evidence: str, report: TruthReport
) -> None:
    source = f"{resume_text}\n{github_evidence}"
    source_low = source.lower()
    source_numbers = set(_normalise_numbers(source))
    source_skills = extract_skills(source)
    source_proper = {m.lower() for m in _PROPER_NOUN.findall(source)}

    bullets = tailored.get("bullets")
    if not isinstance(bullets, list):
        bullets = []

    for entry in bullets:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("rewritten") or "").strip()
        if not text:
            continue
        report.checked_bullets += 1

        # Invented figures are the most common and most damaging fabrication.
        for number in _normalise_numbers(text):
            if number not in source_numbers:
                report.violations.append(Violation(
                    kind="invented_metric", severity="block",
                    detail=f"Figure {number!r} does not appear in your resume or GitHub.",
                    bullet=text,
                ))

        for pattern in _CREDENTIAL_PATTERNS:
            match = pattern.search(text)
            if match and not pattern.search(source):
                report.violations.append(Violation(
                    kind="invented_credential", severity="block",
                    detail=f"Mentions {match.group(0)!r}, which is not in your source material.",
                    bullet=text,
                ))

        for skill in extract_skills(text) - source_skills:
            report.violations.append(Violation(
                kind="unsupported_skill", severity="block",
                detail=f"Claims {skill!r}, which has no support in your resume or GitHub.",
                bullet=text,
            ))

        for token in set(_PROPER_NOUN.findall(text)):
            if token in _PROPER_NOUN_ALLOW or token.lower() in source_proper:
                continue
            if len(token) < 4:
                continue
            report.violations.append(Violation(
                kind="unknown_proper_noun", severity="flag",
                detail=f"{token!r} does not appear in your source material — verify it.",
                bullet=text,
            ))

        original = str(entry.get("original") or "")
        if original:
            for word in _INFLATION_WORDS:
                if _has_word(text, word) and not _has_word(original, word) \
                        and word not in source_low:
                    report.violations.append(Violation(
                        kind="scope_inflation", severity="flag",
                        detail=f"Adds {word!r}, which the original bullet did not claim.",
                        bullet=text,
                    ))

        if not str(entry.get("evidence") or "").strip():
            report.violations.append(Violation(
                kind="missing_evidence", severity="flag",
                detail="No evidence source cited for this bullet.",
                bullet=text,
            ))

    for skill in _as_list(tailored.get("skills_to_surface")):
        if skill.lower() not in source_low and skill.lower() not in source_skills:
            report.violations.append(Violation(
                kind="unsupported_skill", severity="block",
                detail=f"Lists skill {skill!r}, which is absent from your source material.",
            ))

    summary = str(tailored.get("summary") or "")
    if summary:
        for number in _normalise_numbers(summary):
            if number not in source_numbers:
                report.violations.append(Violation(
                    kind="invented_metric", severity="block",
                    detail=f"Summary cites {number!r}, which is not in your source material.",
                    bullet=summary,
                ))


def _normalise_numbers(text: str) -> list[str]:
    """Extract comparable numeric tokens, ignoring thousands separators.

    Years are skipped: a date range in a rewritten bullet is normal and is checked
    by the credential/employer rules instead.
    """
    out: list[str] = []
    for raw in _NUMBER.findall(text or ""):
        token = raw.strip().lower().replace(",", "").replace(" ", "")
        if not token:
            continue
        digits = token.rstrip("%kmx").rstrip("percent")
        if digits.isdigit() and 1900 <= int(digits) <= 2100:
            continue
        out.append(token)
    return out


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text or "", re.I) is not None


def _as_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]
