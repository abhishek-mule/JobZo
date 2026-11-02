from __future__ import annotations
from typing import Any

import re
from ai.normalizer import seniority_level, SENIORITY_MULTIPLIER
from services.company_registry import get_by_alias

TOP_TIER = {
    "nvidia", "google", "apple", "meta", "microsoft", "amazon",
    "openai", "anthropic", "stripe", "gitlab", "vercel", "linear",
    "notion", "databricks", "snowflake", "palantir",
    "figma", "canva", "airbnb", "uber", "netflix", "spotify",
    "coinbase", "robinhood", "samsung", "intel", "amd",
    "red hat", "redhat", "hashicorp", "mongodb", "elastic",
    "cloudflare", "atlassian", "automattic",
}

# Role family patterns for career gate matching
FAMILY_PATTERNS = [
    ("software", r"(software|sde|engineer|developer|programmer)"),
    ("frontend", r"(frontend|front-end|react|vue|angular|ui)"),
    ("backend", r"(backend|back-end|api|microservices)"),
    ("fullstack", r"(fullstack|full-stack|full stack)"),
    ("mobile", r"(android|ios|mobile|flutter|react-native)"),
    ("devops", r"(devops|sre|platform|infrastructure|cloud|kubernetes|aws|gcp|azure)"),
    ("data", r"(data|ml|machine learning|ai engineer|data engineer|analytics)"),
    ("security", r"(security|appsec|infosec|devsecops)"),
    ("qa", r"(qa|test|quality|automation)"),
]

def estimate(job, profile: dict[str, Any]) -> dict[str, Any]:
    """Estimate interview probability (0-100) with reasons."""
    reasons = []
    score = 50.0
    signals = 0

    exp_years = profile.get("experience_years", 0)
    career_stage = profile.get("career_stage", "graduate")
    declared_skills = {s.lower().strip() for s in profile.get("declared_skills", [])}
    is_student = career_stage == "student"
    is_fresher = exp_years <= 2

    title_lower = (job.title or "").lower()
    desc_lower = (job.description or "").lower()
    company_name = job.company or ""
    company = get_by_alias(company_name)
    company_lower = company_name.lower().strip()
    is_top_tier = company_lower in TOP_TIER or any(t in company_lower for t in TOP_TIER)

    # ── Factor 1: Seniority match ────────────────────────────────────
    seniority = seniority_level(title_lower)
    seniority_mult = SENIORITY_MULTIPLIER.get(seniority, 0.5)
    score *= seniority_mult

    if seniority == "intern" or seniority == "junior":
        reasons.append("✓ Entry-level title")
        signals += 1
    elif seniority == "senior" or seniority == "manager":
        reasons.append("✗ Senior-level title")
        if is_fresher:
            score *= 0.3

    # ── Factor 2: Experience requirement ──────────────────────────────
    try:
        from services.experience import parse_experience
        text = " ".join(filter(None, [job.title, job.description, job.experience_required]))
        req = parse_experience(text)
        if req.confidence >= 0.5 and req.min_years is not None:
            if req.min_years > exp_years + 1:
                reasons.append(f"✗ Requires {req.min_years}+ years")
                score *= 0.4
            elif req.min_years <= exp_years:
                reasons.append(f"✓ Experience matches ({exp_years}yrs)")
                signals += 1
    except Exception:
        pass

    # ── Factor 3: Skill match ────────────────────────────────────────
    job_skills = {s.lower().strip() for s in (job.skills or []) if isinstance(s, str)}
    if declared_skills and job_skills:
        matched = declared_skills & job_skills
        missing = job_skills - declared_skills
        match_pct = len(matched) / max(len(job_skills), 1) * 100

        if match_pct >= 60:
            reasons.append(f"✓ {len(matched)} skills match ({int(match_pct)}%)")
            score *= 1.2
            signals += 1
        elif match_pct >= 30:
            reasons.append(f"~ {len(matched)} skills match ({int(match_pct)}%)")
        else:
            top_missing = list(missing)[:3]
            if top_missing:
                reasons.append(f"✗ Missing: {', '.join(top_missing)}")
                score *= 0.6

    # ── Factor 4: Role-family gate (career filter) ───────────────────
    desired_families = {f.lower() for f in profile.get("desired_role_families", [])}
    if desired_families:
        job_family = None
        t = title_lower
        for fam, pat in FAMILY_PATTERNS:
            if re.search(pat, t):
                job_family = fam
                break
        if job_family and job_family not in desired_families:
            reasons.append(f"✗ Role family mismatch: {job_family} (you want {', '.join(sorted(desired_families))})")
            score *= 0.15
            signals -= 2
        elif job_family:
            reasons.append(f"✓ Role family match: {job_family}")
            score *= 1.2
            signals += 1
        else:
            reasons.append("? Unrecognized role family")
            score *= 0.6

    # ── Factor 5: Graduate/fresher signals ───────────────────────────
    is_graduate_title = any(kw in title_lower for kw in [
        "graduate", "fresher", "campus", "entry level", "new grad",
        "trainee", "apprentice", "intern",
    ])
    company_graduate = company and company.graduate_roles
    company_fresher = company and company.fresher_friendly

    if is_graduate_title:
        reasons.append("✓ Graduate program")
        score *= 1.3
        signals += 1
    if company_graduate and is_fresher:
        reasons.append("✓ Company hires graduates")
        score *= 1.2
        signals += 1
    if company_fresher and is_fresher:
        reasons.append("✓ Fresher-friendly company")
        score *= 1.1
        signals += 1

    # ── Factor 6: Company tier penalty for non-graduate roles ────────
    if is_top_tier and is_fresher and not is_graduate_title and not company_graduate:
        reasons.append("✗ Competitive company — experienced-hire role")
        score *= 0.4

    # ── Factor 7: Location / visa ────────────────────────────────────
    loc = (job.location or "").lower()
    us_eu = ["san francisco", "new york", "london", "berlin", "united states", "usa", "uk"]
    if any(city in loc for city in us_eu) and not job.remote:
        reasons.append(f"✗ On-site in {loc} (outside India)")
        score *= 0.5

    # ── Factor 8: Remote flexibility ─────────────────────────────────
    if job.remote or "remote" in loc:
        reasons.append("✓ Remote role")
        score *= 1.1

    # ── Confidence ───────────────────────────────────────────────────
    if signals >= 3:
        confidence = "High"
    elif signals >= 1:
        confidence = "Medium"
    else:
        confidence = "Low"
        reasons.append("? Limited job data — estimate may be inaccurate")

    # Clamp and round
    final_score = max(0, min(100, int(round(score))))

    return {
        "score": final_score,
        "confidence": confidence,
        "reasons": reasons[:6],
    }


def estimate_for_application(app, session, profile: dict[str, Any]) -> dict[str, Any]:
    from database.models import Job
    job = session.query(Job).filter(Job.id == app.job_id).first()
    if not job:
        return {"score": app.score or 0, "confidence": "Low", "reasons": ["Job not found"]}
    return estimate(job, profile)


# ── TOP_TIER COMPANIES ──────────────────────────────────────────────
# (already defined above)
