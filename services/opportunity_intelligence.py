"""Opportunity Intelligence — per-job depth for informed decisions.

Core output: Apply Priority (0.0–10.0) — a single actionable score
answering "should I apply to this now?"

Supporting signals: expected value, trends, skill gaps, competition,
hiring intensity, company memory, portfolio risk.
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from database.connection import get_session
from database.models import Job, Company, PollRun, Event, Application
from sqlalchemy import func


def enrich(application_id: str, user_skills: list[str] | None = None) -> dict[str, Any]:
    """Compute full intelligence for a single opportunity.

    Returns a dict with Apply Priority + all supporting signals.
    """
    session = get_session()
    try:
        app = session.query(Application).filter(Application.id == application_id).first()
        if not app:
            return {}
        job = session.query(Job).filter(Job.id == app.job_id).first()
        if not job:
            return {}

        company = _resolve_company(job.company, session)
        basic = {
            "company": job.company,
            "company_id": company.id if company else "",
            "title": job.title,
            "score": app.score,
        }

        if not company:
            return {**basic, "apply_priority": app.score / 10.0, "reasons": []}

        # ── Compute all signals ────────────────────────────────────────
        signals = {
            "freshness": _freshness(job.posted_at),
            "hiring_intensity": _hiring_intensity(company.id, job.company, session),
            "hiring_trend": _hiring_trend(company.id, job.company, session),
            "competition": _competition(job, company, session),
            "expected_response_days": _expected_response(company.id, session),
            "graduate_friendly": _graduate_friendliness(company),
            "skill_gaps": _skill_gaps(job, user_skills, session),
            "learning_roi": _learning_roi(job, user_skills, session),
            "company_memory": _company_memory(company.id, session),
        }

        # Mark confidence on signals that need historical data
        signals["freshness"]["confidence"] = "measured"
        signals["hiring_intensity"]["confidence"] = "measured" if signals["hiring_intensity"]["polls_30d"] >= 3 else "estimated"
        signals["hiring_trend"]["confidence"] = "measured" if signals["hiring_trend"]["last_week"] > 0 and signals["hiring_trend"]["this_week"] > 0 else "estimated"
        signals["competition"]["confidence"] = "measured" if signals["competition"]["similar_applicants"] >= 5 else "estimated"
        signals["expected_response_days"] = signals["expected_response_days"]  # None if no data
        signals["company_memory"]["confidence"] = "measured" if signals["company_memory"]["total_applicants"] >= 10 else "estimated"

        # ── Apply Priority ──────────────────────────────────────────────
        priority, reasons = _compute_apply_priority(app, company, signals, job)
        signals["apply_priority"] = priority
        signals["reasons"] = reasons

        # ── Expected Value ──────────────────────────────────────────────
        signals["expected_value"] = _expected_value(priority, job)

        return {**basic, **signals}
    finally:
        session.close()


def enrich_all(applications: list[Application], user_skills: list[str] | None = None) -> list[dict]:
    return [enrich(app.id, user_skills) for app in applications]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _resolve_company(name: str, session) -> Company | None:
    from services.company_registry import get_by_alias
    return get_by_alias(name)


def _freshness(posted_at: datetime | None) -> dict:
    if posted_at is None:
        return {"label": "Unknown", "hours_ago": None, "level": "unknown", "score": 0}

    hours = max(0, (datetime.utcnow() - posted_at).total_seconds() / 3600)

    if hours < 2:
        return {"label": "Just now", "hours_ago": hours, "level": "fresh", "score": 10}
    elif hours < 24:
        return {"label": f"{int(hours)}h ago", "hours_ago": hours, "level": "fresh", "score": 8}
    elif hours < 72:
        return {"label": f"{int(hours//24)}d ago", "hours_ago": hours, "level": "recent", "score": 5}
    elif hours < 168:
        return {"label": f"{int(hours//24)}d ago", "hours_ago": hours, "level": "aging", "score": 2}
    else:
        return {"label": f"{int(hours//24)}d ago", "hours_ago": hours, "level": "stale", "score": 0}


def _hiring_intensity(company_id: str, company_name: str, session) -> dict:
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)

    job_count = (
        session.query(func.count(Job.id))
        .filter(Job.company == company_name, Job.created_at >= thirty_days_ago)
        .scalar() or 0
    )
    poll_count = (
        session.query(func.count(PollRun.id))
        .filter(
            PollRun.company_id == company_id,
            PollRun.finished_at >= thirty_days_ago,
            PollRun.status == "success",
        )
        .scalar() or 0
    )

    avg_jobs_per_poll = round(job_count / max(poll_count, 1), 1)

    if job_count >= 20:
        level, label, bar, pct = "very_high", "Very High", "████████░░", 80
    elif job_count >= 10:
        level, label, bar, pct = "high", "High", "██████░░░░", 60
    elif job_count >= 5:
        level, label, bar, pct = "medium", "Medium", "████░░░░░░", 40
    elif job_count >= 1:
        level, label, bar, pct = "low", "Low", "██░░░░░░░░", 20
    else:
        level, label, bar, pct = "unknown", "Unknown", "░░░░░░░░░░", 0

    return {
        "level": level, "label": label, "bar": bar, "pct": pct,
        "jobs_30d": job_count, "polls_30d": poll_count,
        "avg_jobs_per_poll": avg_jobs_per_poll,
    }


def _hiring_trend(company_id: str, company_name: str, session) -> dict:
    """Week-over-week hiring change."""
    now = datetime.utcnow()
    this_week_start = now - timedelta(days=7)
    last_week_start = now - timedelta(days=14)

    this = (
        session.query(func.count(Job.id))
        .filter(Job.company == company_name, Job.created_at >= this_week_start)
        .scalar() or 0
    )
    last = (
        session.query(func.count(Job.id))
        .filter(
            Job.company == company_name,
            Job.created_at >= last_week_start,
            Job.created_at < this_week_start,
        )
        .scalar() or 0
    )

    if last == 0 and this > 0:
        direction, symbol, pct = "up", "▲", 100
    elif last == 0:
        direction, symbol, pct = "flat", "—", 0
    else:
        change = round((this - last) / last * 100)
        direction = "up" if change > 0 else "down" if change < 0 else "flat"
        symbol = "▲" if change > 0 else "▼" if change < 0 else "—"
        pct = abs(change)

    return {"direction": direction, "symbol": symbol, "pct": pct, "this_week": this, "last_week": last}


def _competition(job: Job, company: Company, session) -> dict:
    """Estimate competition level for this role."""
    similar_applied = (
        session.query(func.count(Event.id))
        .filter(
            Event.event_type == "application_submitted",
            Event.metadata_json.like(f'%{company.id}%'),
        )
        .scalar() or 0
    )
    total_apps = session.query(func.count(Application.id)).scalar() or 0

    if similar_applied > 10:
        level, label, score = "high", "High", 1
    elif similar_applied > 3:
        level, label, score = "medium", "Medium", 5
    elif total_apps > 50:
        level, label, score = "medium", "Medium", 5
    else:
        level, label, score = "low", "Low", 9

    return {"level": level, "label": label, "score": score, "similar_applicants": similar_applied}


def _expected_response(company_id: str, session) -> int | None:
    result = (
        session.query(
            func.julianday(Event.occurred_at) - func.julianday(Application.applied_at)
        )
        .select_from(Event)
        .join(Application, Event.entity_id == Application.id)
        .join(Job, Application.job_id == Job.id)
        .filter(Event.event_type == "interview_scheduled", Job.company == company_id)
        .limit(50)
        .all()
    )
    if not result:
        return None
    days = sorted([int(r[0]) for r in result if r[0] is not None])
    return days[len(days) // 2] if days else None


def _graduate_friendliness(company: Company) -> str:
    if company.graduate_roles and company.fresher_friendly:
        return "high"
    return "medium" if (company.fresher_friendly or company.graduate_roles) else "low"


def _skill_gaps(job: Job, user_skills: list[str] | None, session) -> list[dict]:
    if not user_skills or not job.skills:
        return []

    user_set = set(s.lower().strip() for s in user_skills)
    gaps = []
    for skill in (job.skills or []):
        if isinstance(skill, str) and skill.lower().strip() not in user_set:
            gaps.append({"skill": skill, "impact": _skill_impact(skill, session)})

    return sorted(gaps, key=lambda x: -x["impact"])[:5]


def _skill_impact(skill: str, session) -> int:
    count = (
        session.query(func.count(Job.id))
        .filter(Job.skills.contains(skill))
        .scalar() or 0
    )
    return min(10, max(1, count // 5))


def _learning_roi(job: Job, user_skills: list[str] | None, session) -> list[dict]:
    """Skill gaps with market frequency + estimated learn time."""
    if not user_skills or not job.skills:
        return []

    user_set = set(s.lower().strip() for s in user_skills)
    total_jobs = session.query(func.count(Job.id)).scalar() or 1
    roi = []

    for skill in (job.skills or []):
        if not isinstance(skill, str) or skill.lower().strip() in user_set:
            continue
        market_count = (
            session.query(func.count(Job.id))
            .filter(Job.skills.contains(skill))
            .scalar() or 0
        )
        market_pct = round(market_count / total_jobs * 100)
        impact = min(10, max(1, market_count // 5))

        # Rough learn time estimate
        learn_days = {"python": 5, "java": 10, "sql": 3, "docker": 7, "kubernetes": 14,
                      "aws": 14, "redis": 3, "kafka": 7, "spring": 10, "react": 10,
                      "node": 7, "typescript": 7, "go": 10, "rust": 14}.get(skill.lower(), 7)

        stars = "★" * min(5, max(1, impact // 2)) + "☆" * (5 - min(5, max(1, impact // 2)))
        roi.append({
            "skill": skill,
            "impact": impact,
            "market_pct": market_pct,
            "jobs": market_count,
            "learn_days": learn_days,
            "roi_stars": stars,
        })

    return sorted(roi, key=lambda x: -x["impact"])[:5]


def _company_memory(company_id: str, session) -> dict:
    """Historical hiring behaviour for this company."""
    # Interview events for this company
    interviews = (
        session.query(Event)
        .select_from(Event)
        .join(Application, Event.entity_id == Application.id)
        .join(Job, Application.job_id == Job.id)
        .filter(Event.event_type.in_(["interview_scheduled", "rejected"]), Job.company == company_id)
        .all()
    )

    total_events = len(interviews)
    interviews_count = sum(1 for e in interviews if e.event_type == "interview_scheduled")
    rejections = sum(1 for e in interviews if e.event_type == "rejected")

    # Estimate interview rounds from observations
    rounds = (
        session.query(func.count(Event.id))
        .select_from(Event)
        .join(Application, Event.entity_id == Application.id)
        .join(Job, Application.job_id == Job.id)
        .filter(
            Event.event_type.in_(["interview_scheduled", "interview_passed"]),
            Job.company == company_id,
        )
        .scalar() or 0
    )
    avg_rounds = round(rounds / max(interviews_count, 1), 1)

    # Common rejection reasons (from observation metadata)
    rejection_events = (
        session.query(Event)
        .filter(
            Event.event_type == "rejected",
            Event.metadata_json.like(f'%{company_id}%'),
        )
        .limit(20)
        .all()
    )

    return {
        "total_applicants": total_events,
        "interview_rate": round(interviews_count / max(total_events, 1) * 100) if total_events else None,
        "avg_interview_rounds": avg_rounds if interviews_count else None,
        "rejection_rate": round(rejections / max(total_events, 1) * 100) if total_events else None,
    }


TOP_TIER_COMPANIES = {
    "nvidia", "google", "apple", "meta", "microsoft", "amazon",
    "openai", "anthropic", "stripe", "gitlab", "vercel", "linear",
    "notion", "datadog", "databricks", "snowflake", "palantir",
    "figma", "canva", "airbnb", "uber", "netflix", "spotify",
    "coinbase", "robinhood", "samsung", "intel", "amd",
    "red hat", "redhat", "hashicorp", "mongodb", "elastic",
    "cloudflare", "atlassian", "automattic",
}


def _compute_apply_priority(app: Application, company: Company, signals: dict, job: Job,
                            profile: dict | None = None) -> tuple[float, list[str]]:
    """Compute the single Apply Priority score (0.0–10.0) with reasons."""
    reasons = []
    score = 0.0

    # ── 0. Role-family gate (career filter) ──────────────────────────────
    if profile:
        desired_families = {f.lower() for f in profile.get("desired_role_families", [])}
        if desired_families:
            job_family = None
            t = (job.title or "").lower()
            family_patterns = [
                ("frontend", r"\b(frontend|front-end|react|vue|angular|ui)\b"),
                ("backend", r"\b(backend|back-end|api|microservices)\b"),
                ("fullstack", r"\b(fullstack|full-stack|full stack)\b"),
                ("mobile", r"\b(android|ios|mobile|flutter|react-native)\b"),
                ("devops", r"\b(devops|sre|platform|infrastructure|cloud|kubernetes|aws|gcp|azure)\b"),
                ("data", r"\b(data|ml|machine learning|ai engineer|data engineer|analytics)\b"),
                ("security", r"\b(security|appsec|infosec|devsecops)\b"),
                ("qa", r"\b(qa|test|quality|automation)\b"),
                ("software", r"\b(software|sde|engineer|developer|programmer)\b"),
            ]
            for fam, pat in family_patterns:
                if re.search(pat, t):
                    job_family = fam
                    break
            if job_family and job_family not in desired_families:
                score -= 3.0
                reasons.append(f"✗ Role mismatch: {job_family} (want {', '.join(sorted(desired_families))})")
            elif job_family:
                score += 1.0
                reasons.append(f"✓ Role match: {job_family}")
            else:
                reasons.append("? Unrecognized role family")
                score -= 0.5

    # ── 1. Base from ranker score (0-3 pts) ────────────────────────────
    base_pts = app.score / 100.0 * 3.0
    score += base_pts
    if app.score >= 70:
        reasons.append("Strong overall match")
    elif app.score >= 50:
        reasons.append("Good match")

    # ── 1b. Company tier penalty — top companies unlikely for freshers ──
    company_lower = (job.company or "").lower().strip()
    is_top_tier = any(t in company_lower for t in TOP_TIER_COMPANIES) or company_lower in TOP_TIER_COMPANIES
    if is_top_tier and not (company and company.graduate_roles):
        title_lower = (job.title or "").lower()
        is_fresher_title = any(kw in title_lower for kw in ["intern", "junior", "fresher", "entry", "graduate", "campus", "trainee"])
        if not is_fresher_title:
            score -= 1.5
            reasons.append("Competitive company — likely experienced-hire role")

    # ── 2. Freshness bonus (0-2 pts) ───────────────────────────────────
    fresh = signals["freshness"]
    fresh_score = fresh.get("score", 0) / 10.0 * 2.0
    score += fresh_score
    if fresh_score >= 1.5:
        reasons.append("Fresh posting — early applicant advantage")
    elif fresh_score >= 0.8:
        reasons.append("Recently posted")

    # ── 3. Skill match bonus (0-1.5 pts) ───────────────────────────────
    gaps = signals.get("skill_gaps", [])
    if not gaps:
        score += 1.5
        reasons.append("Strong skill match — no gaps detected")
    elif len(gaps) <= 1:
        score += 0.8
        reasons.append("Good skill match — minor gaps")
    else:
        reasons.append(f"{len(gaps)} skill gaps — check learning ROI")

    # ── 4. Competition bonus (0-1.5 pts) ───────────────────────────────
    comp = signals["competition"]
    comp_score = comp.get("score", 5) / 10.0 * 1.5
    score += comp_score
    if comp["level"] == "low":
        reasons.append("Low estimated competition")
    elif comp["level"] == "high":
        reasons.append("High competition — many applicants likely")

    # ── 5. Hiring Intensity bonus (0-1 pt) ─────────────────────────────
    hi = signals["hiring_intensity"]
    if hi["level"] in ("very_high", "high"):
        score += 1.0
        reasons.append("Company is actively hiring")
    elif hi["level"] == "medium":
        score += 0.5
        reasons.append("Moderate hiring activity")

    # ── 6. Graduate friendliness bonus (0-0.5 pts) ─────────────────────
    gf = signals["graduate_friendly"]
    if gf == "high":
        score += 0.5
        reasons.append("Graduate-friendly employer")
    elif gf == "medium":
        score += 0.2

    # ── 7. Response time bonus (0-0.5 pts) ─────────────────────────────
    resp = signals.get("expected_response_days")
    if resp is not None and resp <= 7:
        score += 0.5
        reasons.append(f"Typically responds within {resp} days")

    # 🔒 Clamp to 0-10
    priority = round(max(0.0, min(10.0, score)), 1)

    # Compact reason summary line
    summary = ", ".join(reasons[:4])
    if len(reasons) > 4:
        summary += f" +{len(reasons)-4} more"

    return priority, reasons


def _expected_value(priority: float, job: Job) -> dict:
    """Expected value: priority vs. estimated effort."""
    # Rough effort estimate from title/company
    title_lower = (job.title or "").lower()
    if any(w in title_lower for w in ["senior", "staff", "principal", "lead", "manager"]):
        effort_minutes = 20
        difficulty = "High"
    elif any(w in title_lower for w in ["intern", "graduate", "fresher", "trainee"]):
        effort_minutes = 5
        difficulty = "Low"
    else:
        effort_minutes = 10
        difficulty = "Medium"

    # Expected value = priority / effort ratio
    ev_ratio = priority / effort_minutes

    if ev_ratio >= 0.8:
        ev_stars = "★★★★★"
        ev_label = "Exceptional"
    elif ev_ratio >= 0.5:
        ev_stars = "★★★★☆"
        ev_label = "High"
    elif ev_ratio >= 0.3:
        ev_stars = "★★★☆☆"
        ev_label = "Good"
    elif ev_ratio >= 0.15:
        ev_stars = "★★☆☆☆"
        ev_label = "Moderate"
    else:
        ev_stars = "★☆☆☆☆"
        ev_label = "Low"

    return {
        "stars": ev_stars,
        "label": ev_label,
        "effort_minutes": effort_minutes,
        "difficulty": difficulty,
    }
