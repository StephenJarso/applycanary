"""What an application asks for, beyond a CV and a cover letter.

Postings for motivation-heavy programmes do not only compare documents. They cap
the letter length, ask explicit questions ("why Japan?", "which open-source
projects have you contributed to?"), and expect links or attachments. Nothing in
the pipeline read those asks, so a letter could blow a stated 200-word limit or
ignore the one question the employer actually cares about and still be sent.

This module extracts the asks deterministically — no LLM — so the same posting
always produces the same checklist, it works with no API key configured, and the
result can be shown to the person submitting as an explanation rather than a
verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# "maximum 250 words", "up to 200 words", "no more than 300 words", "250-word
# limit", "500 words max". Several forms because postings use all of them.
_WORD_LIMIT_PATTERNS = (
    re.compile(
        r"(?:max(?:imum)?(?:\s+of)?|up to|no more than|at most|within|under|"
        r"limit of|not exceed(?:ing)?)\s*(\d{2,4})\s*words?\b",
        re.I,
    ),
    re.compile(r"(\d{2,4})[\s-]*word\s+(?:limit|maximum|max(?:imum)?)", re.I),
    re.compile(r"(\d{2,4})\s*words?\s*(?:max(?:imum)?|limit)", re.I),
)

# Questions that are marketing copy rather than asks for the applicant. Without
# this filter every "Ready to join us?" becomes a checklist item.
_RHETORICAL = (
    "sound like you", "does that sound", "ready to", "why wait", "interested in joining",
    "want to join", "are you the one", "looking for a challenge", "what are you waiting",
)

# Only sentences that direct the applicant to provide something count as asks;
# a bare mention of "portfolio" in a company blurb does not.
_DIRECTIVE = re.compile(
    r"\b(link|url|attach|upload|submit|provide|include|share|send|paste)\b", re.I
)

_LINK_HINTS: dict[str, tuple[str, ...]] = {
    "GitHub": ("github", "gitlab"),
    "Portfolio": ("portfolio", "personal website", "personal site"),
    "LinkedIn": ("linkedin",),
}

_ATTACHMENT = re.compile(
    r"\b(transcript|certificate|recommendation letter|portfolio|degree certificate)\b",
    re.I,
)

_EXTRA_STEPS: dict[str, tuple[str, ...]] = {
    "Coding challenge": ("coding challenge", "coding test", "programming challenge",
                         "coding assignment"),
    "Take-home exercise": ("take-home", "take home exercise", "take-home assignment"),
    "Assessment": ("online assessment", "aptitude test", "technical assessment",
                   "online test"),
    "Video interview": ("video interview", "recorded interview"),
    "Written essay": ("essay", "short essay", "written response"),
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n{2,}")


@dataclass(slots=True)
class Requirements:
    """The application's explicit asks, as found in the posting text."""

    word_limit: int | None = None
    questions: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    extra_steps: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (
            self.word_limit or self.questions or self.links
            or self.attachments or self.extra_steps
        )

    def as_dict(self) -> dict:
        return {
            "word_limit": self.word_limit,
            "questions": list(self.questions),
            "links": list(self.links),
            "attachments": list(self.attachments),
            "extra_steps": list(self.extra_steps),
        }


def extract_requirements(description: str) -> Requirements:
    """Read a posting's application asks. Never raises, even on junk input."""
    text = description or ""
    out = Requirements()
    if not text.strip():
        return out

    limits = [
        int(m.group(1))
        for pattern in _WORD_LIMIT_PATTERNS
        for m in pattern.finditer(text)
    ]
    # The strictest stated limit governs; postings sometimes repeat an upper
    # bound in a summary and again in the detail.
    out.word_limit = min(limits) if limits else None

    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]

    for sentence in sentences:
        if not sentence.endswith("?"):
            continue
        low = sentence.lower()
        if len(sentence) < 15 or any(bad in low for bad in _RHETORICAL):
            continue
        if sentence not in out.questions:
            out.questions.append(sentence)
    out.questions = out.questions[:6]

    for label, hints in _LINK_HINTS.items():
        for sentence in sentences:
            low = sentence.lower()
            if any(hint in low for hint in hints) and _DIRECTIVE.search(sentence):
                out.links.append(label)
                break

    for match in _ATTACHMENT.finditer(text):
        sentence = _sentence_around(text, match.start())
        if not _DIRECTIVE.search(sentence):
            continue
        label = match.group(1).title()
        if label not in out.attachments:
            out.attachments.append(label)

    low_text = text.lower()
    for label, hints in _EXTRA_STEPS.items():
        if any(hint in low_text for hint in hints):
            out.extra_steps.append(label)

    return out


def _sentence_around(text: str, index: int) -> str:
    """The sentence containing `index`, so a directive can be matched locally."""
    start = max(text.rfind(".", 0, index), text.rfind("\n", 0, index))
    end_candidates = [i for i in (text.find(".", index), text.find("\n", index)) if i != -1]
    end = min(end_candidates) if end_candidates else len(text)
    return text[start + 1:end + 1]
