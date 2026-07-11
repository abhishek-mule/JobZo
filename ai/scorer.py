import logging
import re
from typing import Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from database.models import Job, Application
from database.connection import get_session
from services.freshness import freshness_score
from services.config import Config
from ai.llm import ask

logger = logging.getLogger("jobzo.scorer")


SKILL_KEYWORDS = [
    "spring boot", "spring", "java", "react", "typescript", "javascript",
    "postgresql", "mysql", "sql", "docker", "kubernetes", "aws", "gcp",
    "azure", "kafka", "redis", "graphql", "rest", "microservices",
    "python", "node", "fastapi", "flask", "django", "git", "ci/cd",
]


def _skill_overlap(job: Job, resume_skills: list[str]) -> tuple[float, list[str]]:
    desc_lower = (job.description + " " + job.title + " " + " ".join(job.skills)).lower()
    matched_skills = []
    for skill in resume_skills:
        if skill.lower() in desc_lower:
            matched_skills.append(skill)

    total = max(len(resume_skills), 1)
    return min(len(matched_skills) / total, 1.0), matched_skills


def _experience_match(job: Job, user_experience_years: int = 1) -> tuple[float, str]:
    desc_lower = (job.description + " " + job.experience_required).lower()
    patterns = [
        (r"(\d+)\s*-\s*(\d+)\s*(?:years?|yrs?)", "range"),
        (r"(\d+)\+?\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience)?", "single"),
    ]

    max_years = 10
    reason = "no experience requirement found"
    for pattern, kind in patterns:
        matches = re.findall(pattern, desc_lower)
        if matches:
            if kind == "range":
                max_years = int(matches[0][1])
                reason = f"requires 0-{max_years} years"
            else:
                max_years = int(matches[0][0]) if isinstance(matches[0], str) else int(matches[0][0])
                reason = f"requires {max_years}+ years"
            break

    if max_years <= 0:
        return 0.5, "no experience requirement found"

    ratio = user_experience_years / max_years
    if ratio >= 0.5 and ratio <= 1.5:
        return 1.0, f"your {user_experience_years}yr matches {reason}"
    elif ratio > 0 and ratio < 0.5:
        return ratio * 2, f"your {user_experience_years}yr is below {reason}"
    else:
        return max(0, 1.0 - (ratio - 1.5)), f"your {user_experience_years}yr exceeds {reason}"


def _location_match(job: Job, preferred: str = "remote") -> tuple[float, str]:
    loc = (job.location + " " + job.description).lower()
    if "remote" in loc or "anywhere" in loc:
        return 1.0, "remote friendly"
    if job.remote:
        return 1.0, "remote position"
    if preferred.lower() in loc:
        return 1.0, f"location matches ({job.location})"
    if job.location and job.location != "remote":
        return 0.5, f"on-site ({job.location})"
    return 0.5, "location unknown"


SENIOR_TITLES = [
    "senior", "sr ", "sr.", "staff", "principal", "lead", "architect",
    "manager", "director", "head of", "vp ", "vice president",
]

JUNIOR_TITLES = [
    "intern", "graduate", "new grad", "entry level", "junior", "fresher",
    "campus", "university", "early career", "sde i", "sde 1",
    "software engineer i", "software engineer 1",
    "backend engineer i", "backend engineer 1",
]


def _seniority_gate(title: str, user_experience_years: int = 1) -> tuple[float, str]:
    """Hard seniority gate. Returns multiplier and reason."""
    t = title.lower()

    # Junior/entry-level titles are ideal
    for kw in JUNIOR_TITLES:
        if kw in t:
            return 1.0, f"entry-level role ({kw})"

    # Senior titles are a hard penalty for <3yr experience
    for kw in SENIOR_TITLES:
        if kw in t:
            if user_experience_years < 2:
                return 0.0, f"requires seniority ({kw}) — not competitive with {user_experience_years}yr"
            elif user_experience_years < 4:
                return 0.3, f"stretch: {kw} role with {user_experience_years}yr"

    # Mid-level roles (SDE II, Software Engineer II, etc.)
    if re.search(r"\b(ii|2|two)\b", t) and user_experience_years < 2:
        return 0.5, "mid-level role may be a stretch"

    return 1.0, "level appropriate"


def _keyword_pre_score(job: Job) -> int:
    score = 0
    text = (job.title + " " + job.description).lower()

    # Core tech stack matches (high weight)
    if "spring" in text:
        score += 25
    if "java" in text:
        score += 20
    if "react" in text:
        score += 15
    if "typescript" in text:
        score += 10
    if "postgresql" in text or "postgres" in text:
        score += 10
    if "docker" in text:
        score += 10
    if "aws" in text:
        score += 8
    if "python" in text:
        score += 8

    # Role keywords (medium weight)
    if "backend" in text or "back end" in text:
        score += 15
    if "full stack" in text or "fullstack" in text:
        score += 12
    if "software engineer" in text or "sde" in text:
        score += 10
    if "developer" in text:
        score += 8
    if "engineer" in text:
        score += 5
    if "intern" in text:
        score += 10

    # Positive signals
    if "remote" in text:
        score += 10
    if "0-2" in text or "0-3" in text or "1-2" in text or "1-3" in text:
        score += 10
    if "junior" in text:
        score += 8

    # Negative signals
    if "senior" in text or "lead" in text or "principal" in text:
        score -= 15
    if "staff" in text or "manager" in text or "director" in text or "head of" in text:
        score -= 20
    if "5+" in text or "5 year" in text or "7 year" in text or "10+" in text:
        score -= 25
    if "c++" in text or "rust" in text or "golang" in text or "go" in text:
        score -= 10

    return max(0, score)


SMALL_MODELS = {"tinyllama", "gemma2:2b", "phi:2b", "llama3.2:1b", "qwen2:0.5b"}


def _llm_score(job: Job) -> dict | None:
    llm_cfg = Config.llm_config()
    model = llm_cfg.get("ollama", {}).get("model", "tinyllama")
    if model in SMALL_MODELS:
        return None

    cfg = Config.resume_config()
    resume_meta = cfg.get("resumes", {})
    resume_names = [n for n, r in resume_meta.items() if r.get("active", True)]

    prompt = f"""Job Title: {job.title}
Company: {job.company}
Location: {job.location}
Salary: {job.salary}
Experience Required: {job.experience_required}

Description:
{job.description[:2000]}

Your resumes: {', '.join(resume_names)}

Score this job 0-100 based on match with a backend/full-stack profile.
Choose strategy: apply_now, get_referral, cold_email, skip, or watch.
List missing skills.
Recommend the best resume from the list. Keep reasoning under 3 sentences."""

    try:
        return ask("score", prompt)
    except Exception as e:
        logger.warning("LLM score failed for %s: %s", job.url, e)
        return None


async def score_pending_jobs(
    user_skills: list[str] | None = None,
    user_experience_years: int = 1,
) -> int:
    if user_skills is None:
        user_skills = SKILL_KEYWORDS

    session: Session = get_session()
    scored = 0
    cfg = Config.resume_config()
    llm_threshold = cfg.get("scoring", {}).get("llm_threshold", 70)
    max_llm = cfg.get("scoring", {}).get("max_llm_calls_per_run", 50)

    try:
        unscored = session.execute(
            select(Job).where(
                Job.is_active == True,
                ~Job.id.in_(select(Application.job_id)),
            ).order_by(Job.created_at.desc()).limit(200)
        ).scalars().all()

        scored_jobs = []
        for job in unscored:
            keyword_score = _keyword_pre_score(job)
            if keyword_score < 20:
                logger.debug("Skipping (keyword score %d): %s - %s", keyword_score, job.company, job.title)
                continue

            overlap, matched = _skill_overlap(job, user_skills)
            if overlap < 0.1:
                logger.debug("Skipping (skill overlap %.2f): %s - %s", overlap, job.company, job.title)
                continue

            exp_match, exp_reason = _experience_match(job, user_experience_years)
            if exp_match < 0.1:
                logger.debug("Skipping (exp match %.2f): %s - %s", exp_match, job.company, job.title)
                continue

            loc_match, loc_reason = _location_match(job)
            scored_jobs.append((job, keyword_score, overlap, matched, exp_match, exp_reason, loc_match, loc_reason))

        scored_jobs.sort(key=lambda x: x[1], reverse=True)
        top_jobs = scored_jobs[:max_llm]

        for job, kw_score, overlap, matched, exp_match, exp_reason, loc_match, loc_reason in top_jobs:
            seniority_mult, seniority_reason = _seniority_gate(job.title, user_experience_years)
            llm_result = _llm_score(job)
            freshness = freshness_score(job.posted_at)

            if llm_result:
                llm_score_val = llm_result.get("score", 50)
                strategy = llm_result.get("strategy", "skip")
                reasoning = llm_result.get("reasoning", "")
                resume_name = llm_result.get("recommended_resume", "")

                cfg = Config.resume_config()
                weights = cfg.get("scoring", {})
                final_score = int(
                    weights.get("skill_overlap_weight", 0.30) * (overlap * 100) +
                    weights.get("experience_weight", 0.20) * (exp_match * 100) +
                    weights.get("freshness_weight", 0.35) * (freshness * 100) +
                    weights.get("priority_weight", 0.15) * llm_score_val
                )

                if seniority_mult == 0.0:
                    strategy = "skip"
                    final_score = min(final_score, 20)
                else:
                    final_score = int(final_score * seniority_mult)

                app = Application(
                    job_id=job.id,
                    status="drafted",
                    score=final_score,
                    strategy=strategy,
                    resume_used=resume_name,
                    notes=f"{seniority_reason} | {reasoning}" if reasoning else seniority_reason,
                )
                session.add(app)
                scored += 1
                logger.info(
                    "Scored %s - %s: %d/%d (%s) [seniority: %s]",
                    job.company, job.title, final_score, 100, strategy, seniority_reason,
                )
            else:
                skill_pts = int(overlap * 50)
                fresh_pts = int(freshness * 20)
                exp_pts = int(exp_match * 20)
                loc_pts = int(loc_match * 10)
                raw_score = skill_pts + fresh_pts + exp_pts + loc_pts

                if seniority_mult == 0.0:
                    final_score = min(raw_score, 20)
                    strategy = "skip"
                else:
                    final_score = int(raw_score * seniority_mult)
                    strategy = "skip" if final_score < 50 else "watch"

                reasons = [
                    f"Skill match ({len(matched)}/{len(user_skills)}): {', '.join(matched[:5])}" if matched else "No skill matches",
                    f"Freshness: {freshness:.0%}",
                    exp_reason,
                    loc_reason,
                    seniority_reason,
                ]
                notes = " | ".join(reasons)

                app = Application(
                    job_id=job.id,
                    status="drafted",
                    score=final_score,
                    strategy=strategy,
                    notes=notes,
                )
                session.add(app)
                scored += 1

        session.commit()
    except Exception as e:
        session.rollback()
        logger.error("Scoring error: %s", e)
        raise
    finally:
        session.close()

    logger.info("Scored %d new jobs", scored)
    return scored
