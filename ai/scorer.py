"""Scorer — thin façade over the retriever + ranker pipeline.

All logic lives in:
  - ai/retriever.py  → filters + normalizes + scores
  - ai/ranker.py     → estimates interview probability + sorts
  - ai/models.py     → RankedOpportunity (single canonical object)

This module is the public API consumed by CLI, mission engine, and dashboard.
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Job, Application
from database.connection import get_session
from services.eligibility import EligibilityEngine
from services.config import Config
from ai.models import RankedOpportunity
from tracker.events import record_event, APPLICATION_CREATED

logger = logging.getLogger("jobzo.scorer")

# ── Tier constants (re-exported for mission/engine.py) ──────────────────

TIER_APPLY_NOW = "apply_now"
TIER_STRONG_MATCH = "strong_match"
TIER_WORTH_TRYING = "worth_trying"
TIER_STRETCH = "stretch"
TIER_IGNORE = "ignore"

TIER_LABELS = {
    TIER_APPLY_NOW: "\u2605\u2605\u2605\u2605\u2605 Apply Now",
    TIER_STRONG_MATCH: "\u2605\u2605\u2605\u2605\u2606 Strong Match",
    TIER_WORTH_TRYING: "\u2605\u2605\u2605\u2606\u2606 Worth Trying",
    TIER_STRETCH: "\u2605\u2606\u2606\u2606\u2606 Stretch Goal",
    TIER_IGNORE: "\u26aa Ignore",
}

TIER_ORDER = [TIER_APPLY_NOW, TIER_STRONG_MATCH, TIER_WORTH_TRYING, TIER_STRETCH, TIER_IGNORE]


def _assign_tier(score: int) -> str:
    """Map 0-100 score to recommendation tier (standalone, for DB values)."""
    if score >= 90:
        return TIER_APPLY_NOW
    elif score >= 75:
        return TIER_STRONG_MATCH
    elif score >= 60:
        return TIER_WORTH_TRYING
    elif score >= 45:
        return TIER_STRETCH
    return TIER_IGNORE


# ── Default skills ──────────────────────────────────────────────────────
SKILL_KEYWORDS = [
    "spring boot", "spring", "springboot", "java", "jpa", "hibernate",
    "jdbc", "maven", "gradle", "microservices", "microservice",
    "python", "django", "flask", "fastapi", "fast api",
    "react", "typescript", "javascript", "node", "nodejs", "express",
    "nextjs", "tailwind",
    "postgresql", "postgres", "mysql", "sql", "mongodb", "mongo",
    "redis", "cassandra", "dynamodb", "elasticsearch",
    "docker", "kubernetes", "k8s", "aws", "gcp", "azure",
    "terraform", "ci/cd", "jenkins", "github actions",
    "kafka", "rabbitmq", "pub/sub", "grpc", "graphql", "rest",
    "rest api", "restful",
    "golang", "go", "ruby", "rails", "ruby on rails", "scala",
    "kotlin", "c++", "cpp", "c", "rust", "swift",
    "git", "linux", "algorithm", "data structure", "system design",
    "multithreading", "concurrency", "oop",
]


def score_job(job: Job, user_skills: list[str] | None = None, user_experience_years: int = 1, profile: dict | None = None) -> RankedOpportunity | None:
    """Score a single job through the full pipeline.

    Returns a fully populated RankedOpportunity, or None if the job
    should be excluded (eligibility gate, no role match, no skill overlap).
    """
    from ai.retriever import retrieve
    from ai.ranker import rank

    if user_skills is None:
        user_skills = SKILL_KEYWORDS
    if profile is None:
        browser_cfg = Config.browser_config()
        profile = browser_cfg.get("profile", {})

    opp = retrieve(job, user_skills, user_experience_years, profile)
    if opp is None:
        return None
    ranked = rank([opp], user_skills, user_experience_years)
    return ranked[0] if ranked else None


async def score_pending_jobs(
    user_skills: list[str] | None = None,
    user_experience_years: int = 1,
    profile: dict | None = None,
) -> dict:
    """Score all unscored jobs through the retriever + ranker pipeline.

    Returns pipeline accounting dict: {discovered, hidden, hidden_reasons, scored}.
    """
    from ai.retriever import retrieve
    from ai.ranker import rank

    if user_skills is None:
        user_skills = SKILL_KEYWORDS

    session: Session = get_session()
    cfg = Config.resume_config()
    max_llm = cfg.get("scoring", {}).get("max_llm_calls_per_run", 50)

    if profile is None:
        browser_cfg = Config.browser_config()
        profile = browser_cfg.get("profile", {})
    eligibility_engine = EligibilityEngine()

    from services.decision_snapshot import persist as persist_snapshot

    try:
        unscored = session.execute(
            select(Job).where(
                Job.is_active == True,
                Job.eligible == True,
                ~Job.id.in_(select(Application.job_id)),
            ).order_by(Job.created_at.desc()).limit(200)
        ).scalars().all()

        discovered = len(unscored)
        scored = 0
        hidden = 0
        hidden_reasons: dict[str, int] = {}

        opportunities: list[RankedOpportunity] = []
        for job in unscored:
            eligibility_result = eligibility_engine.check(job, profile)
            if not eligibility_result.passed:
                job.eligible = False
                job.eligibility_reason = eligibility_result.reason
                hidden += 1
                reason_key = eligibility_result.reason.split(":")[0].split("(")[0].strip()[:30]
                hidden_reasons[reason_key] = hidden_reasons.get(reason_key, 0) + 1
                continue

            opp = retrieve(job, user_skills, user_experience_years, profile)
            if opp:
                opportunities.append((job, opp))

        opportunities.sort(key=lambda x: x[1].score_vector.composite(), reverse=True)
        top_opportunities = opportunities[:max_llm]

        for job, opp in top_opportunities:
            ranked = rank([opp], user_skills, user_experience_years)
            if not ranked:
                continue
            r = ranked[0]

            score = r.composite_score()
            tier = r.tier()
            strategy = "skip" if score < 50 else "watch"

            app = Application(
                job_id=job.id,
                status="drafted",
                score=score,
                tier=tier,
                strategy=strategy,
            )
            session.add(app)
            session.flush()

            persist_snapshot(session, app, r)

            record_event(APPLICATION_CREATED, "application", app.id, actor="system", metadata={
                "job_id": job.id,
                "company": job.company,
                "score": score,
                "strategy": strategy,
            }, session=session)
            scored += 1
            logger.info(
                "Scored %s - %s: %d/100 (%s) [p(interview)=%d%%]",
                job.company, job.title, score, tier, r.interview_probability,
            )

        session.commit()
    except Exception as e:
        session.rollback()
        logger.error("Scoring error: %s", e)
        raise
    finally:
        session.close()

    logger.info(
        "Pipeline: %d discovered, %d hidden, %d scored — balance: %s",
        discovered, hidden, scored,
        "✓" if discovered == hidden + scored else f"✗ off by {discovered - hidden - scored}",
    )
    if hidden_reasons:
        reasons_summary = ", ".join(f"{k}: {v}" for k, v in sorted(hidden_reasons.items(), key=lambda x: -x[1]))
        logger.info("Hidden reasons: %s", reasons_summary)
    return {
        "discovered": discovered, "hidden": hidden, "hidden_reasons": hidden_reasons,
        "scored": scored,
    }
