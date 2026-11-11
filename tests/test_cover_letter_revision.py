"""The letter prompt carries the posting's asks, and a blocked draft is revised once.

The gate in `lettercheck` decides what may be sent; these tests cover the other
half — that the model is told what the posting wants before it writes, and that a
blocked draft comes back with the findings instead of being silently rewritten.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.apply import runner
from app.llm.prompts import build_cover_letter_user
from app.pipeline.requirements import extract_requirements
from tests.test_lettercheck import GITHUB, GOOD_LETTER, JOB, RESUME

BLOCKED_LETTER = " ".join(["word"] * 260)


def _prompt(**kwargs: object) -> str:
    base: dict[str, object] = {
        "title": "Global Internship Program",
        "company": "HENNGE",
        "description": JOB,
        "resume_text": RESUME,
    }
    base.update(kwargs)
    return build_cover_letter_user(**base)  # type: ignore[arg-type]


class FakeLlm:
    """Returns canned letters, recording the prompt each one answered."""

    available = True
    tailor_model = "fake"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def complete(self, **kwargs: object) -> object:
        self.prompts.append(str(kwargs["messages"][0]["content"]))  # type: ignore[index]
        return SimpleNamespace(text=self.replies.pop(0))


async def _letter(monkeypatch, replies: list[str]):
    llm = FakeLlm(replies)
    monkeypatch.setattr(runner, "get_llm", lambda: llm)
    job = SimpleNamespace(
        id=1, title="Global Internship Program", company="HENNGE", description=JOB
    )
    profile = SimpleNamespace(
        base_resume_text=RESUME, github_evidence={"repos": GITHUB}
    )
    cover, check = await runner._cover_letter(job, profile, None)
    return llm, cover, check


class TestRequirementsInPrompt:
    def test_word_limit_and_questions_are_stated(self):
        text = _prompt(requirements=extract_requirements(JOB))
        assert "<application_requirements>" in text
        assert "200 words" in text
        assert "career in Japan" in text
        assert "GitHub" in text

    def test_no_requirements_block_when_posting_asks_nothing(self):
        text = _prompt(description="Backend engineer. Python and Postgres.")
        assert "<application_requirements>" not in text

    def test_revision_carries_the_findings(self):
        text = _prompt(
            revise=["The letter is 260 words; the posting allows 200."],
            previous=BLOCKED_LETTER,
        )
        assert "<previous_draft>" in text
        assert "allows 200" in text
        assert "Rewrite the letter" in text


class TestRevision:
    async def test_blocked_draft_is_revised_with_findings(self, monkeypatch):
        llm, cover, check = await _letter(monkeypatch, [BLOCKED_LETTER, GOOD_LETTER])

        assert cover == GOOD_LETTER
        assert check["passed"] is True
        assert len(llm.prompts) == 2
        assert "automatic checker rejected" in llm.prompts[1]
        assert "200 words" in llm.prompts[1]

    async def test_passing_draft_is_not_revised(self, monkeypatch):
        llm, cover, check = await _letter(monkeypatch, [GOOD_LETTER])

        assert cover == GOOD_LETTER
        assert check["passed"] is True
        assert len(llm.prompts) == 1

    async def test_failed_revision_keeps_a_blocked_verdict(self, monkeypatch):
        llm, cover, check = await _letter(
            monkeypatch, [BLOCKED_LETTER, BLOCKED_LETTER]
        )

        assert len(llm.prompts) == 2
        assert check["passed"] is False
        assert any(f["severity"] == "block" for f in check["findings"])
