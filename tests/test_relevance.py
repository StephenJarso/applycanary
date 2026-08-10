from app.models import Job, Profile
from app.pipeline.relevance import (
    max_rank_for_experience,
    seniority_matches,
    title_matches_target,
    title_mismatch_penalty,
    title_rank,
)
from app.pipeline.score import hard_disqualifier


def _job(title: str) -> Job:
    return Job(
        title=title, company="Acme", location="",
        description="A real engineering opening worth evaluating.",
        fingerprint="x",
    )


def test_title_matches_target_when_unconfigured() -> None:
    assert title_matches_target("Senior Frontend Engineer", []) is True


def test_title_matches_target_exact() -> None:
    assert title_matches_target("Senior Frontend Engineer", ["Frontend Engineer"])


def test_title_matches_target_strips_seniority() -> None:
    # seniority/scope words are stripped before comparing core role tokens
    assert title_matches_target("Staff Frontend Engineer", ["Frontend Engineer"])
    assert title_matches_target("Lead Backend Engineer", ["Backend Engineer"])


def test_title_does_not_match_different_role() -> None:
    assert not title_matches_target("Frontend Engineer", ["Backend Engineer"])
    assert not title_matches_target("Product Designer", ["Frontend Engineer"])


def test_title_matches_target_handles_noise() -> None:
    assert title_matches_target(
        "Senior Frontend Engineer (m/f/d) - Remote", ["frontend engineer"]
    )


def test_title_rank() -> None:
    assert title_rank("Junior Engineer") == 0
    assert title_rank("Software Engineer") == 2
    assert title_rank("Senior Software Engineer") == 3
    assert title_rank("Lead Engineer") == 3
    assert title_rank("Staff Engineer") == 4
    assert title_rank("Engineering Manager") == 5


def test_max_rank_for_experience() -> None:
    assert max_rank_for_experience(None) == 5  # no data -> no filter
    assert max_rank_for_experience(0) == 0
    assert max_rank_for_experience(1) == 0
    assert max_rank_for_experience(2) == 3
    assert max_rank_for_experience(3) == 3
    assert max_rank_for_experience(4) == 4
    assert max_rank_for_experience(8) == 5


def test_seniority_matches() -> None:
    assert seniority_matches("Senior Engineer", None) is True
    # 1 year -> junior only; senior/lead/staff/manager all too senior
    assert seniority_matches("Junior Engineer", 1) is True
    assert seniority_matches("Senior Engineer", 1) is False
    assert seniority_matches("Staff Engineer", 1) is False
    # 2-3 years -> mid/senior IC ok, staff+ not
    assert seniority_matches("Senior Engineer", 3) is True
    assert seniority_matches("Staff Engineer", 3) is False
    # 4-6 years -> senior staff/principal OK, management not
    assert seniority_matches("Staff Engineer", 4) is True
    assert seniority_matches("Engineering Manager", 4) is False
    # 7+ years -> management ok
    assert seniority_matches("Director of Engineering", 9) is True


def test_unrelated_title_penalises_but_does_not_disqualify() -> None:
    """A title outside the targets must stay scoreable.

    Treating this as fatal meant tier 2 never ran for any posting whose title
    did not fuzzy-match, which silently hid real openings — target_titles are
    routinely written as search keywords rather than exact titles.
    """
    profile = Profile(full_name="x", target_titles=["Frontend Engineer"])
    job = _job("Backend Engineer")

    assert hard_disqualifier(job, profile) == ""

    penalty, reason = title_mismatch_penalty(job, profile)
    assert penalty > 0
    assert "target roles" in reason


def test_matching_title_carries_no_penalty() -> None:
    profile = Profile(full_name="x", target_titles=["Frontend Engineer"])
    penalty, reason = title_mismatch_penalty(_job("Senior Frontend Engineer"), profile)
    assert penalty == 0.0
    assert reason == ""


def test_no_targets_configured_carries_no_penalty() -> None:
    profile = Profile(full_name="x", target_titles=[])
    penalty, _ = title_mismatch_penalty(_job("Anything At All"), profile)
    assert penalty == 0.0


def test_hard_disqualifier_rejects_over_level() -> None:
    profile = Profile(full_name="x", years_experience=2)
    reason = hard_disqualifier(_job("Principal Engineer"), profile)
    assert reason and "exceeds your experience band" in reason


def test_hard_disqualifier_accepts_matching_role_and_level() -> None:
    profile = Profile(
        full_name="x",
        target_titles=["Frontend Engineer"],
        years_experience=4,
        base_resume_text="react aws node",
        skills=["react", "node"],
    )
    assert hard_disqualifier(_job("Senior Frontend Engineer"), profile) == ""
