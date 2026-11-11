"""Verify a generated cover letter the way `truthcheck` verifies the CV.

The tailored resume is gated twice: ATS rules score its structure and
`truthcheck` blocks unsupported claims. The cover letter — the other document
sent under the candidate's own name — used to have no gate at all, so a letter
that invented a figure, blew a stated word limit, or silently ignored the
questions the posting asked could reach an employer.

Like `truthcheck`, this is deterministic and fails closed: the same letter always
yields the same verdict, and a crash is a block rather than a pass. Only blocks
stop a submission; everything else is surfaced for the person to fix.

Word limits and invented figures are blocking because they are objectively
checkable and are screened on. A question that reads as unanswered and a generic
opening are flags: the checks are heuristics, and a false block would train the
user to override the gate.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.pipeline.keywords import extract_skills
from app.pipeline.requirements import Requirements, extract_requirements
from app.pipeline.truthcheck import numeric_claims

log = logging.getLogger(__name__)

PENALTY = {"block": 25, "flag": 8}

# Openers and phrases the generation prompt already forbids. Checking them here
# is the difference between "the prompt asked" and "the letter cannot contain".
_GENERIC = (
    "i am writing to express my interest",
    "i am writing to apply",
    "passionate about leveraging",
    "leverage my skills",
    "synergy",
    "team player",
    "hard-working individual",
    "hit the ground running",
    "i would be a great fit",
    "please find my resume attached",
)

_STOPWORDS = {
    "about", "after", "also", "and", "any", "are", "because", "been", "before",
    "being", "both", "can", "could", "describe", "does", "each", "from", "give",
    "have", "how", "into", "its", "more", "most", "much", "must", "need", "not",
    "our", "please", "share", "should", "some", "tell", "than", "that", "their",
    "them", "then", "there", "these", "they", "this", "those", "through", "under",
    "what", "when", "where", "which", "while", "who", "why", "will", "with",
    "would", "your", "yours", "you",
}


@dataclass(slots=True)
class LetterFinding:
    rule: str
    severity: str  # "block" | "flag"
    message: str
    fix: str


@dataclass(slots=True)
class LetterReport:
    word_count: int
    requirements: Requirements
    findings: list[LetterFinding] = field(default_factory=list)
    checker_failed: bool = False

    @property
    def blocks(self) -> list[LetterFinding]:
        return [f for f in self.findings if f.severity == "block"]

    @property
    def passed(self) -> bool:
        """True only when nothing blocks and the checker itself ran cleanly."""
        return not self.blocks and not self.checker_failed

    @property
    def score(self) -> float:
        deducted = sum(PENALTY.get(f.severity, 0) for f in self.findings)
        return round(max(0.0, min(100.0, 100.0 - deducted)), 1)

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "score": self.score,
            "word_count": self.word_count,
            "requirements": self.requirements.as_dict(),
            "findings": [
                {
                    "rule": f.rule, "severity": f.severity,
                    "message": f.message, "fix": f.fix,
                }
                for f in self.findings
            ],
        }


def check_letter(
    letter: str,
    *,
    job_description: str,
    resume_text: str,
    github_evidence: str = "",
    company: str = "",
) -> LetterReport:
    """Check a letter against the posting's asks and the candidate's evidence."""
    requirements = extract_requirements(job_description)
    report = LetterReport(
        word_count=len((letter or "").split()), requirements=requirements
    )
    try:
        _run_checks(
            letter or "", job_description, resume_text, github_evidence, company, report
        )
    except Exception as exc:  # noqa: BLE001 - a crash must not read as approval
        log.exception("cover letter check failed")
        report.checker_failed = True
        report.findings.append(LetterFinding(
            "checker_error", "block",
            f"Letter verification did not complete ({type(exc).__name__}).",
            "Read the letter once before sending.",
        ))
    return report


def _run_checks(
    letter: str,
    job_description: str,
    resume_text: str,
    github_evidence: str,
    company: str,
    report: LetterReport,
) -> None:
    text = letter.strip()
    if not text:
        report.findings.append(LetterFinding(
            "no_letter", "block",
            "No cover letter was generated.",
            "Run Tailor again, or write a short letter naming the role and one "
            "concrete reason you want it.",
        ))
        return

    words = report.word_count
    limit = report.requirements.word_limit
    if limit and words > limit:
        report.findings.append(LetterFinding(
            "word_limit_exceeded", "block",
            f"The letter is {words} words; this posting states a {limit}-word limit.",
            f"Cut it to under {limit} words. Editors apply that limit literally, "
            "and an over-length letter is often not read at all.",
        ))
    elif words < 90:
        report.findings.append(LetterFinding(
            "too_short", "flag",
            f"The letter is only {words} words.",
            "Add one paragraph of concrete evidence — a project and what it did — "
            "or the letter reads as a formality.",
        ))

    source = f"{resume_text}\n{github_evidence}"
    source_numbers = set(numeric_claims(source))
    for number in numeric_claims(text):
        if number not in source_numbers:
            report.findings.append(LetterFinding(
                "invented_metric", "block",
                f"The letter cites {number!r}, which is not in your resume or GitHub.",
                "Remove the figure or replace it with one your source material "
                "actually supports.",
            ))

    supported = extract_skills(source)
    claimed = extract_skills(text)
    # A term used to describe the employer's own domain ("HENNGE's cloud
    # security work") is not a claim about the candidate. Only words outside
    # sentences that name the company count as claims.
    if company:
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if company.lower() in sentence.lower():
                claimed -= extract_skills(sentence)
    for skill in sorted(claimed - supported):
        report.findings.append(LetterFinding(
            "unsupported_skill", "block",
            f"The letter claims {skill!r}, which has no support in your resume or GitHub.",
            "Name a technology you have actually used, or drop the claim.",
        ))

    for question in report.requirements.questions[:4]:
        if _coverage(question, text) < 0.25:
            report.findings.append(LetterFinding(
                "unanswered_question", "flag",
                f"The posting asks: \"{question}\" — the letter does not clearly answer it.",
                "Answer it in one sentence. These questions are usually the "
                "actual shortlisting criterion.",
            ))

    for label in report.requirements.links:
        if label.lower() not in text.lower() and label.lower() not in source.lower():
            report.findings.append(LetterFinding(
                "missing_required_link", "flag",
                f"This posting asks for a {label} link and neither the letter nor "
                "the CV carries one.",
                f"Add your {label} URL to the CV header and the form.",
            ))

    for label in report.requirements.extra_steps:
        report.findings.append(LetterFinding(
            "extra_step", "flag",
            f"This posting includes a further step: {label}.",
            "Submit the letter first if the step is time-limited; the challenge "
            "is usually the actual filter.",
        ))

    low = text.lower()
    for phrase in _GENERIC:
        if phrase in low:
            report.findings.append(LetterFinding(
                "generic_filler", "flag",
                f"The letter contains the stock phrase \"{phrase}\".",
                "Replace it with a sentence only you could write about this role.",
            ))
            break

    if company and company.lower() not in low:
        report.findings.append(LetterFinding(
            "no_company_reference", "flag",
            "The letter never names the company.",
            "Name them, and one specific thing about them. A letter that could be "
            "sent anywhere is read as one that was.",
        ))


def _coverage(question: str, letter: str) -> float:
    """Fraction of a question's content words that appear in the letter.

    Deliberately lexical: it only needs to catch a question that was ignored
    outright, not judge the quality of the answer.
    """
    words = {
        w for w in re.findall(r"[a-z][a-z0-9+#.-]{3,}", question.lower())
        if w not in _STOPWORDS
    }
    if not words:
        return 1.0
    letter_low = letter.lower()
    hit = sum(1 for w in words if w in letter_low)
    return hit / len(words)
