"""Retriever — finds relevant jobs.

The retriever's only goal is high recall: "Could this person reasonably apply?"
It does NOT estimate interview probability — that's the ranker's job.

Output: RankedOpportunity — a filtered, normalized, and fully scored job.
"""

import re
from datetime import datetime
from typing import Any

from ai.normalizer import normalize, seniority_level
from ai.skill_graph import expand, missing_skills
from ai.score_vector import ScoreVector, compute_score_vector
from ai.models import RankedOpportunity
from database.models import Job
from services.freshness import freshness_score
from services.eligibility import EligibilityEngine
from services.config import Config
from skills import skill_patterns


def retrieve(
    job: Job,
    user_skills: list[str],
    user_experience_years: int = 1,
    profile: dict | None = None,
) -> RankedOpportunity | None:
    """Run retrieval on a single job. Returns None if the job should be excluded."""
    if profile is None:
        browser_cfg = Config.browser_config()
        profile = browser_cfg.get("profile", {})

    # 1. Eligibility gate
    eligibility = EligibilityEngine()
    result = eligibility.check(job, profile)
    if not result.passed:
        return None

    # 2. Normalize title
    desc = f"{job.title}\n{job.company}\n{job.location}\n{job.description}"
    norm = normalize(job.title, job.description, user_skills)
    seniority = seniority_level(job.title)

    # 3. Skill extraction from job
    job_skills_raw = _extract_skills(job)
    job_skills = list(set(job_skills_raw))

    # 4. Skill graph expansion
    expanded_resume = expand(user_skills, max_depth=2, min_strength=0.3)
    expanded_job = expand(job_skills, max_depth=1, min_strength=0.3)

    # 5. Skill matching (graph-aware)
    matched = _match_skills(user_skills, job_skills, expanded_resume, expanded_job)
    gaps = missing_skills(user_skills, job_skills)

    # 6. Experience & location
    exp_match, exp_reason = _experience_match(job.description, job.experience_required, user_experience_years)
    loc_match, loc_reason = _location_match(job.location, job.remote)
    fresh = freshness_score(job.posted_at)

    # 7. Retrieval score (simple composite — not interview probability)
    skill_overlap_score = matched["overlap"]
    role_pass = norm.confidence >= 0.5 and norm.canonical not in ("NON_TECH", "MANAGER", "UNKNOWN")

    if skill_overlap_score == 0.0 and not role_pass:
        return None

    retrieval_score = (
        skill_overlap_score * 0.5
        + exp_match * 0.2
        + loc_match * 0.15
        + fresh * 0.15
    )

    # 8. Compute score vector
    expl: dict[str, str] = {}
    role_fit = norm.confidence if norm.canonical not in ("NON_TECH", "MANAGER", "UNKNOWN") else 0.0
    if norm.canonical == "BACKEND_ENGINEER":
        role_fit = max(role_fit, 0.9)
    expl["role_fit"] = f"{norm.canonical.replace('_', ' ').title()}"

    skill_fit = skill_overlap_score
    expl["skill_fit"] = f"{len(matched['matched'])} skills matched"
    expl["experience_fit"] = exp_reason
    expl["location_fit"] = "Remote" if job.remote else job.location

    company_fit = 0.7
    growth_value = 0.5
    if gaps:
        growth_value += min(len(gaps) * 0.05, 0.2)
    if seniority in ("junior", "mid"):
        growth_value += 0.1
    growth_value = min(growth_value, 1.0)

    sv = compute_score_vector(
        role_fit=role_fit,
        skill_fit=skill_fit,
        experience_fit=exp_match,
        company_fit=company_fit,
        location_fit=loc_match,
        growth_value=growth_value,
        explanations=expl,
    )

    return RankedOpportunity(
        job_id=str(job.id),
        company=job.company,
        title=job.title,
        url=job.url,
        location=job.location,
        remote=job.remote,
        posted_at=job.posted_at,
        source=job.source,
        canonical_role=norm.canonical,
        role_confidence=norm.confidence,
        seniority=seniority,
        matched_skills=matched["matched"],
        missing_skills=gaps,
        expanded_skills=expanded_resume,
        score_vector=sv,
        retrieval_score=round(retrieval_score, 3),
        skill_overlap=round(skill_overlap_score, 3),
        freshness=fresh,
        raw_description=desc,
        eligibility_reason="",
    )


# ── Internal helpers ─────────────────────────────────────────────────────


def _extract_skills(job: Job) -> list[str]:
    """Extract skill mentions from a job posting."""
    text = (job.title + " " + job.description + " " + job.experience_required).lower()
    found: set[str] = set()
    for pattern, name in skill_patterns():
        if pattern.search(text):
            found.add(name)
    return list(found)


def _match_skills(
    user_skills: list[str],
    job_skills: list[str],
    expanded_resume: dict[str, float],
    expanded_job: dict[str, float],
) -> dict:
    """Match skills between resume (expanded) and job requirements.

    Returns dict with:
      - matched: list of matched canonical skill names
      - overlap: 0-1 score (graph-aware, capped at job skill count)
    """
    user_lower = set(s.lower() for s in user_skills)
    job_lower = set(s.lower() for s in job_skills)

    exact_matches = [s for s in user_lower if s in job_lower]

    graph_match_count = 0.0
    for js_name, js_weight in expanded_job.items():
        js_lower = js_name.lower()
        if js_lower in user_lower:
            continue
        for us_name, us_weight in expanded_resume.items():
            if us_name.lower() == js_lower:
                graph_match_count += js_weight
                break

    denom = max(len(job_lower) + 2, 1)
    total = len(exact_matches) + graph_match_count
    overlap = min(total / denom, 1.0)

    return {"matched": list(set(exact_matches)), "overlap": overlap}


def _experience_match(description: str, exp_required: str, user_years: int) -> tuple[float, str]:
    """Soft experience fit score. Returns (0-1, reason)."""
    text = (description + " " + exp_required).lower()
    patterns = [
        (r"(\d+)\s*-\s*(\d+)\s*(?:years?|yrs?)", "range"),
        (r"(\d+)\+?\s*(?:years?|yrs?)", "single"),
    ]
    max_years = 0
    for pattern, kind in patterns:
        m = re.search(pattern, text)
        if m:
            if kind == "range":
                max_years = int(m.group(2))
            else:
                max_years = int(m.group(1))
            break

    if max_years <= 0:
        return 1.0, f"no minimum experience specified — your {user_years}yr is fine"
    ratio = user_years / max_years
    if ratio >= 0.5 and ratio <= 1.5:
        return 1.0, f"your {user_years}yr matches required {max_years}yr{'s' if max_years > 1 else ''}"
    elif ratio > 0 and ratio < 0.5:
        return ratio * 2, f"your {user_years}yr is below required {max_years}yr{'s' if max_years > 1 else ''}"
    else:
        return max(0.0, 1.0 - (ratio - 1.5)), f"your {user_years}yr exceeds required {max_years}yr{'s' if max_years > 1 else ''}"


def _location_match(location: str, remote: bool) -> tuple[float, str]:
    """Soft location fit score. Returns (0-1, reason)."""
    loc = location.lower()
    if remote or "remote" in loc or "anywhere" in loc:
        return 1.0, "remote friendly"
    if location and location != "remote":
        return 0.5, f"on-site ({location})"
    return 0.5, "location unknown"
