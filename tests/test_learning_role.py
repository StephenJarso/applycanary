"""Learning-role detection used by CV tailoring.

The distinction matters: an apprenticeship posting should be answered with
shipped projects and learning velocity, while a senior role with the same
keywords in its boilerplate should not.
"""

from __future__ import annotations

from app.llm.prompts import build_tailoring_user
from app.pipeline.tailor import is_learning_role


class TestIsLearningRole:
    def test_apprenticeship_and_internship_titles_match(self):
        assert is_learning_role("Software Engineering Apprentice")
        assert is_learning_role("Backend Intern")
        assert is_learning_role("Graduate Trainee Programme")
        assert is_learning_role("Entry-Level Developer")

    def test_paid_to_learn_description_matches(self):
        assert is_learning_role(
            "Software Engineer", "A paid-to-learn programme with a monthly stipend."
        )

    def test_senior_role_does_not_match(self):
        assert not is_learning_role(
            "Senior Backend Engineer", "5+ years of experience building APIs."
        )


class TestTailoringPrompt:
    def test_learning_hint_only_appears_for_learning_roles(self):
        common = {
            "title": "Backend Engineer",
            "company": "Acme",
            "description": "Build APIs.",
            "resume_text": "Built a Django app.",
            "missing_keywords": [],
            "github_evidence": "",
        }
        assert "learning velocity" not in build_tailoring_user(**common)
        assert "learning velocity" in build_tailoring_user(
            **common, learning_role=True
        )
