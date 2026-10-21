from app.models import Job, Profile
from app.pipeline.relevance import (
    location_eligibility_reason,
    max_rank_for_experience,
    non_engineering_role_reason,
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


# ---------------------------------------------------------------------------
# Non-engineering titles: the fix for marketing/sales/finance roles showing up
# as 100% matches because the JD shared generic vocabulary with the resume.
# ---------------------------------------------------------------------------


def _kenyan_seeker(**overrides) -> Profile:
    values = {
        "full_name": "Stephen",
        "location": "Nairobi, Kenya",
        "target_titles": ["Backend Engineer", "Software Engineer"],
        "target_locations": ["Nairobi", "Kenya", "Remote"],
        "years_experience": 2,
        "base_resume_text": "golang postgres docker kubernetes",
        "skills": ["go", "postgresql", "docker"],
    }
    values.update(overrides)
    return Profile(**values)


def _posting(title: str, *, location: str = "", remote: bool = False, description: str = "") -> Job:
    return Job(
        title=title,
        company="Acme",
        location=location,
        is_remote=remote,
        description=description or "Build and operate backend services in Go.",
        fingerprint=f"fp-{title}-{location}",
    )


def test_non_engineering_titles_are_rejected() -> None:
    targets = ["Backend Engineer", "Software Engineer"]
    for title in (
        "Global Controller",
        "Product Marketing Manager, Market Intelligence",
        "Sales Engineer - Central",
        "Account Executive Product - Radar",
        "Technical Program Manager",
        "Director, Social Media",
    ):
        assert non_engineering_role_reason(title, targets), title


def test_engineering_titles_survive_the_profession_filter() -> None:
    targets = ["Backend Engineer"]
    for title in (
        "Backend Engineer",
        "Software Engineer GO",
        "Senior Software Engineer / Fullstack (m/w/d)",
        "C, Golang Software Engineer working on dqlite",
        "Product Engineer",
        "Member of Technical Staff",
        "DevOps Engineer",
    ):
        assert non_engineering_role_reason(title, targets) == "", title


def test_profession_filter_is_opt_in() -> None:
    assert non_engineering_role_reason("Global Controller", []) == ""


def test_hard_disqualifier_rejects_non_engineering_role() -> None:
    profile = _kenyan_seeker()
    reason = hard_disqualifier(
        _posting("Global Controller", location="Remote", remote=True), profile
    )
    assert "not a software engineering position" in reason


# ---------------------------------------------------------------------------
# Geography: remote abroad only when the employer hires there, onsite abroad
# only with visa/relocation support.
# ---------------------------------------------------------------------------


def test_remote_role_restricted_to_another_country_is_rejected() -> None:
    reason = location_eligibility_reason(
        _posting("Backend Engineer", location="Remote, US", remote=True),
        _kenyan_seeker(),
    )
    assert "limited to" in reason


def test_remote_role_open_worldwide_is_accepted() -> None:
    job = _posting(
        "Backend Engineer",
        location="Remote, US",
        remote=True,
        description="Fully remote, we hire worldwide including Africa.",
    )
    assert location_eligibility_reason(job, _kenyan_seeker()) == ""


def test_remote_role_with_unknown_location_is_accepted() -> None:
    job = _posting("Backend Engineer", location="", remote=True)
    assert location_eligibility_reason(job, _kenyan_seeker()) == ""


def test_onsite_abroad_without_sponsorship_is_rejected() -> None:
    reason = location_eligibility_reason(
        _posting("Backend Engineer", location="Berlin, Germany"),
        _kenyan_seeker(),
    )
    assert "no visa sponsorship or relocation" in reason


def test_onsite_abroad_with_visa_sponsorship_is_accepted() -> None:
    job = _posting(
        "Backend Engineer",
        location="Hengelo, Netherlands",
        description="Relocation package and visa sponsorship provided. Go backend role.",
    )
    assert location_eligibility_reason(job, _kenyan_seeker()) == ""


def test_onsite_at_home_is_accepted() -> None:
    job = _posting("Backend Engineer", location="Nairobi, Kenya")
    assert location_eligibility_reason(job, _kenyan_seeker()) == ""


def test_hard_disqualifier_rejects_us_remote_backend_role() -> None:
    job = _posting("Backend Engineer", location="Remote, United States", remote=True)
    reason = hard_disqualifier(job, _kenyan_seeker())
    assert "limited to" in reason
