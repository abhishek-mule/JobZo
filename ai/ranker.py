"""Ranker — estimates interview probability and produces final rankings.

Consumes RankedOpportunity objects (from the retriever) and populates:
  - interview_probability: 0-100 estimate
  - confidence: Low/Medium/High based on data quality
  - risk: Easy/Medium/Hard based on competition
  - tier: apply_now/strong_match/worth_trying/stretch/ignore
"""

from ai.models import RankedOpportunity
from ai.normalizer import SENIORITY_MULTIPLIER


def rank(opportunities: list[RankedOpportunity], user_skills: list[str] | None = None, user_experience_years: int = 1) -> list[RankedOpportunity]:
    """Rank a list of RankedOpportunities by estimated interview probability.

    Populates ranking fields (interview_probability, confidence, risk, effort)
    and returns the list sorted highest to lowest.
    """
    from services.company_registry import get_by_alias

    for opp in opportunities:
        company_data = {}
        try:
            company = get_by_alias(opp.company)
            if company:
                company_data = {
                    "fresher_friendly": company.fresher_friendly,
                    "priority": company.priority,
                    "backend_stack": company.backend_stack or [],
                }
        except Exception:
            pass

        opp.interview_probability = _estimate_interview_probability(opp, company_data, user_experience_years)
        opp.confidence = _estimate_confidence(opp)
        opp.risk = _estimate_risk(opp)
        opp.effort_minutes = _estimate_effort(opp)

    opportunities.sort(key=lambda o: o.interview_probability, reverse=True)
    return opportunities


def retrieve_and_rank(
    jobs: list,
    user_skills: list[str],
    user_experience_years: int = 1,
    profile: dict | None = None,
) -> list[RankedOpportunity]:
    """Convenience: run retrieval + ranking in one call."""
    from ai.retriever import retrieve

    opportunities = []
    for job in jobs:
        try:
            opp = retrieve(job, user_skills, user_experience_years, profile)
            if opp:
                opportunities.append(opp)
        except Exception:
            continue

    return rank(opportunities, user_skills, user_experience_years)


# ── Internal estimators ─────────────────────────────────────────────────


def _estimate_interview_probability(
    opp: RankedOpportunity,
    company_data: dict,
    user_experience_years: int,
) -> int:
    """Estimate interview probability (0-100) using the score vector and company signals."""
    base = opp.score_vector.composite()
    seniority_mult = SENIORITY_MULTIPLIER.get(opp.seniority, 0.5)
    adjusted = base * seniority_mult
    if company_data.get("fresher_friendly") and user_experience_years <= 2:
        adjusted = min(adjusted + 0.1, 1.0)
    if opp.missing_skills:
        penalty = min(len(opp.missing_skills) * 0.05, 0.2)
        adjusted = max(adjusted - penalty, 0.0)
    return int(min(adjusted * 100, 100))


def _estimate_confidence(opp: RankedOpportunity) -> str:
    """How confident are we in this estimate?"""
    signals = 0
    if opp.role_confidence >= 0.8:
        signals += 1
    if opp.matched_skills:
        signals += 1
    if opp.source != "unknown":
        signals += 1
    if opp.score_vector.composite() > 0.5:
        signals += 1
    if signals >= 3:
        return "High"
    elif signals >= 2:
        return "Medium"
    return "Low"


def _estimate_risk(opp: RankedOpportunity) -> str:
    """How competitive is this application?"""
    if opp.seniority in ("senior", "manager") and opp.score_vector.composite() < 0.5:
        return "Hard"
    if opp.company in ("Google", "Microsoft", "Amazon", "Stripe", "OpenAI", "Anthropic"):
        return "Hard"
    score = opp.score_vector.composite()
    if score >= 0.8:
        return "Easy"
    if score >= 0.5:
        return "Medium"
    return "Hard"


def _estimate_effort(opp: RankedOpportunity) -> int:
    """Estimate time needed to complete this application (minutes)."""
    effort = 15
    if opp.source == "company_pages":
        effort += 5
    return effort
