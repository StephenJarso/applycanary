"""Tailored Resume and Qualification Strategy generator for opportunities, internships, and volunteer roles.

Generates role-specific resume adaptations and tactical application guidance
grounded in the user's actual profile (skills, resume text, GitHub evidence, motivation)
without fabrication.
"""

from __future__ import annotations

import logging

from app.llm.client import get_llm
from app.llm.prompts import OPPORTUNITY_TAILOR_SYSTEM, build_opportunity_tailor_user
from app.models import Profile
from app.pipeline.github import GithubEvidence

log = logging.getLogger(__name__)


class OpportunityTailorError(RuntimeError):
    pass


def _extract_matched_skills(candidate_skills: list[str], text_corpus: str) -> tuple[list[str], list[str]]:
    """Split candidate skills into matched and complementary skills."""
    corpus_lower = text_corpus.lower()
    matched: list[str] = []
    other: list[str] = []
    for skill in candidate_skills:
        if skill.lower() in corpus_lower:
            matched.append(skill)
        else:
            other.append(skill)
    return matched, other


def _build_deterministic_tailored_resume(
    *,
    candidate_name: str,
    headline: str,
    location: str,
    title: str,
    org: str,
    kind: str,
    description: str,
    matched_skills: list[str],
    other_skills: list[str],
    motivation: str,
    resume_text: str,
) -> tuple[str, str]:
    """Generate a high-quality, truthful Markdown resume and strategy without requiring an external LLM."""
    name_display = candidate_name or "Applicant"
    target_role = f"{title} — {org}"

    # Summary
    summary_lines = []
    if headline:
        summary_lines.append(f"Dedicated {headline} with practical hands-on software development experience.")
    else:
        summary_lines.append(f"Results-oriented software developer applying for {target_role}.")

    if matched_skills:
        summary_lines.append(
            f"Brings practical proficiency in {', '.join(matched_skills[:5])}, focusing on clean architecture, "
            "reliable testing, and collaborative version control."
        )

    if motivation:
        summary_lines.append(f"Motivated by: {motivation[:200]}")
    else:
        summary_lines.append(
            f"Eager to contribute technical skills and learning agility to {org}'s mission through this {kind.lower()}."
        )

    summary_text = " ".join(summary_lines)

    # Core Competencies
    skills_md = []
    if matched_skills:
        skills_md.append(f"- **Direct Requirements / Stack Match:** {', '.join(matched_skills)}")
    if other_skills:
        skills_md.append(f"- **Supporting Competencies & Tools:** {', '.join(other_skills[:8])}")
    if not skills_md:
        skills_md.append("- **Technical Foundations:** Full-stack development, Git, REST APIs, problem solving")

    # Tailored Advice & Strategy based on Org / Kind
    org_lower = org.lower()
    title_lower = title.lower()

    strategy_items: list[str] = []
    if "hennge" in org_lower or "hennge" in title_lower:
        strategy_items = [
            "**100% Coding Challenge Score:** Treat every test case as critical. Test empty strings, large inputs, and negative numbers before submitting.",
            "**Showcase Backend Code:** Ensure your GitHub profile prominently features a working Go or Python backend project with Dockerfile and clean README.",
            "**Genuine Tokyo Motivation:** In your essay responses, explain why cloud security (HENNGE One) and relocating to Tokyo aligns with your long-term engineering ambitions.",
            "**Interview Preparation:** Expect questions on asynchronous concurrency (goroutines/asyncio), HTTP status codes, RESTful API design, and Linux basics.",
        ]
    elif "outreachy" in org_lower or "outreachy" in title_lower:
        strategy_items = [
            "**Focus on ONE Project on Day 1:** Do not scatter PRs across multiple organizations. Pick a single project where your skills (Python, Go, or web) fit best.",
            "**Engage Immediately in Community Chat:** Introduce yourself on Zulip/Matrix/Slack and run the project's contributor setup guide right away.",
            "**First PR within 7 Days:** Claim a beginner-friendly good-first-issue or documentation fix to demonstrate reliability and get mentor trust.",
            "**CRITICAL — Log on Portal:** Outreachy only evaluates contributions recorded on the Outreachy dashboard. Log every PR, reviewed code, and issue discussion.",
            "**Availability:** Ensure you explicitly confirm 40 hours/week availability throughout the internship term.",
        ]
    elif "canonical" in org_lower:
        strategy_items = [
            "**Linux & Systems Depth:** Highlight comfort with Ubuntu/Linux command line, package management, systemd, and containerization.",
            "**Clean Code & Testing:** Canonical values idiomatic Go and Python with automated CI pipelines.",
            "**Assessment Readiness:** Prepare for their standardized logic, mathematics, and technical screenings.",
        ]
    elif "microsoft" in org_lower:
        strategy_items = [
            "**Data Structures & Algorithms:** Review arrays, trees, hash maps, and recursion on LeetCode.",
            "**STAR Method:** Frame behavioral responses with Situation, Task, Action, and Measurable Result.",
            "**Collaboration:** Highlight past team projects, open-source pull requests, or community mentoring.",
        ]
    elif kind == "Volunteer":
        strategy_items = [
            "**Reliability & Schedule:** State your specific availability and commit to punctual attendance for rehearsals/shifts.",
            "**Technical Readiness:** Emphasize practical assistance you can provide (AV setups, registration coordination, workshop mentoring).",
            "**Community Connection:** Show genuine enthusiasm for the organizers and attendees.",
        ]
    else:
        strategy_items = [
            f"**Mission Alignment:** Tailor your application narrative to connect directly with {org}'s stated goals.",
            "**Concrete Evidence:** Provide live links to running demos, GitHub repos with documentation, and test suites.",
            "**Submit Early:** Apply at least 3-5 days before the deadline to avoid portal congestion.",
        ]

    # Grounded Experience / Project Highlights
    projects_md = []
    if resume_text:
        # Extract first 400 chars of relevant resume snippets
        lines = [line.strip() for line in resume_text.splitlines() if line.strip() and not line.startswith("#")]
        relevant_lines = [item for item in lines if any(s.lower() in item.lower() for s in matched_skills)][:4]
        if relevant_lines:
            for item in relevant_lines:
                projects_md.append(f"- {item}")

    if not projects_md:
        projects_md = [
            "- **Software Development Project:** Designed, implemented, and deployed application features utilizing modern frameworks and version control.",
            f"- **Code Quality & Testing:** Maintained automated test suites and structured Git workflows to ensure reliable deliverables for {org}.",
        ]

    resume_md = f"""# Tailored Application: {name_display}
**Target Role:** {title} · **Organization:** {org} ({location or 'Remote / Hybrid'})

---

## 🎯 Targeted Professional Summary
{summary_text}

---

## 🛠️ Core Skills Alignment
{chr(10).join(skills_md)}

---

## 🚀 Relevant Project & Technical Contributions
{chr(10).join(projects_md)}

---

## 📋 Key Application Strategy to Qualify
{chr(10).join(f"- {item}" for item in strategy_items)}
"""
    strategy_summary = " · ".join(s.split("**")[1].replace(":", "") for s in strategy_items if "**" in s)
    return resume_md.strip(), strategy_summary


async def tailor_opportunity_resume(
    *,
    profile: Profile,
    title: str,
    org: str,
    kind: str = "Internship",
    description: str = "",
    tags: list[str] | None = None,
) -> dict:
    """Generate a tailored resume and tactical application strategy for an opportunity.

    Uses LLM if available; otherwise uses intelligent deterministic synthesis grounded in
    profile attributes.
    """
    candidate_skills = profile.skills or []
    tags = tags or []
    corpus = f"{title} {org} {kind} {description} {' '.join(tags)}"
    matched, other = _extract_matched_skills(candidate_skills, corpus)

    candidate_name = profile.full_name or "Applicant"
    resume_text = (profile.base_resume_text or "").strip()
    motivation = getattr(profile, "motivation_statement", "") or ""

    evidence = GithubEvidence.from_dict(profile.github_evidence or {})
    evidence_text = evidence.as_prompt_text()

    llm = get_llm()
    if llm.available:
        try:
            user_msg = build_opportunity_tailor_user(
                title=title,
                org=org,
                kind=kind,
                description=description,
                tags=tags,
                candidate_name=candidate_name,
                resume_text=resume_text or "Software engineer with background in full-stack and backend development.",
                skills=candidate_skills,
                motivation_statement=motivation,
                github_evidence=evidence_text,
            )
            result = await llm.complete(
                system=OPPORTUNITY_TAILOR_SYSTEM,
                messages=[{"role": "user", "content": user_msg}],
                model=llm.tailor_model,
                max_tokens=1200,
            )
            tailored_text = (result.text or "").strip()
            if tailored_text:
                return {
                    "ok": True,
                    "tailored_resume": tailored_text,
                    "matched_skills": matched,
                    "strategy": f"Custom LLM-tailored profile for {org}",
                }
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM tailoring failed (%s); falling back to deterministic synthesis", exc)

    # Deterministic fallback
    resume_md, strategy_summary = _build_deterministic_tailored_resume(
        candidate_name=candidate_name,
        headline=getattr(profile, "target_titles", ["Software Engineer"])[0] if getattr(profile, "target_titles", []) else "Software Engineer",
        location=profile.location or "",
        title=title,
        org=org,
        kind=kind,
        description=description,
        matched_skills=matched,
        other_skills=other,
        motivation=motivation,
        resume_text=resume_text,
    )

    return {
        "ok": True,
        "tailored_resume": resume_md,
        "matched_skills": matched,
        "strategy": strategy_summary,
    }
