"""Application Intelligence — quality scoring, ATS success prediction, and verification.

Phase 4A of the Career OS.

Modules:
  ApplicationQualityScore  — pre-submission analysis (keyword match, experience, interview prob)
  ATSSuccessIntelligence   — learn which resumes work at which companies
  ApplicationVerification  — track ATS confirmation, portal status
"""

from __future__ import annotations
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from database.models import Application, Job, Contact, Interaction
from database.connection import get_session
from resumes.registry import ResumeRegistry, ResumeMeta
from resumes.jd_analyzer import analyze as analyze_jd
from resumes.scorer import score_resumes
from skills import canonical_name, skill_weight, skill_category

logger = logging.getLogger("jobzo.intelligence")


# ── Application Quality Score ─────────────────────────────────────────────────

@dataclass
class ApplicationQualityScore:
    resume_match: float = 0.0       # 0-100
    keyword_match: float = 0.0      # 0-100
    experience_fit: float = 0.0     # 0-100
    project_relevance: float = 0.0  # 0-100
    overall: float = 0.0            # 0-100
    expected_interview_probability: float = 0.0
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def format_text(self) -> str:
        lines = [
            f"  Application Quality",
            f"  Overall: {self.overall:.0f}%",
            f"  Expected Interview Probability: {self.expected_interview_probability:.0f}%",
            f"",
            f"  Breakdown",
            f"    Resume Match:      {self.resume_match:.0f}%",
            f"    Keyword Match:     {self.keyword_match:.0f}%",
            f"    Experience Fit:    {self.experience_fit:.0f}%",
            f"    Project Relevance: {self.project_relevance:.0f}%",
        ]
        if self.matched_skills:
            lines.append(f"  Matched: {', '.join(self.matched_skills[:8])}")
        if self.missing_skills:
            lines.append(f"  Missing: {', '.join(self.missing_skills[:8])}")
        if self.suggestions:
            lines.append(f"  Suggestions")
            for s in self.suggestions[:4]:
                lines.append(f"    ! {s}")
        return "\n".join(lines)


def compute_quality_score(
    job: Job,
    resume_meta: ResumeMeta | None,
    jd_text: str = "",
) -> ApplicationQualityScore:
    """Compute pre-submission application quality for a job."""
    score = ApplicationQualityScore()
    text = jd_text or f"{job.title}\n{job.company}\n{job.description}"

    if not resume_meta:
        score.suggestions.append("Select a resume variant first")
        return score

    # Keyword match: JD skills vs resume skills
    analysis = analyze_jd(text)
    if analysis.skills:
        resume_skills_lower = {s.lower() for s in resume_meta.all_skill_names}
        matched = []
        missing = []
        for s in analysis.skills:
            if s.lower() in resume_skills_lower:
                matched.append(s)
            else:
                missing.append(s)

        score.matched_skills = matched
        score.missing_skills = missing

        # Weighted keyword match
        total_w = sum(skill_weight(s) for s in analysis.skills)
        matched_w = sum(skill_weight(s) for s in matched)
        score.keyword_match = round(matched_w / total_w * 100, 1) if total_w else 0

        # Resume match from full scorer
        registry = ResumeRegistry() if not hasattr(compute_quality_score, '_reg') else getattr(compute_quality_score, '_reg')
        registry = get_registry()
        scored = score_resumes(analysis, [resume_meta], text)
        if scored:
            best = scored[0]
            score.resume_match = best.composite()
            score.experience_fit = best.experience.score
            score.project_relevance = best.project.score

        # Suggestions for improvement
        if missing:
            high_value = [s for s in missing if skill_weight(s) >= 7][:3]
            for s in high_value:
                score.suggestions.append(f"Add '{s}' to resume (high demand)")

            missing_cats = set(skill_category(s) for s in missing if skill_category(s) not in ("Unknown", "Tool"))
            for c in missing_cats:
                score.suggestions.append(f"Add a {c.lower()} skill to coverage")

    # Overall score (weighted composite)
    score.overall = round(
        score.resume_match * 0.35 +
        score.keyword_match * 0.30 +
        score.experience_fit * 0.20 +
        score.project_relevance * 0.15,
        1,
    )

    # Interview probability heuristic
    ats_rate = _company_ats_success_rate(job.company)
    base_prob = score.overall / 100 * 45  # max 45% from quality
    ats_bonus = ats_rate * 20  # up to 20% from company track record
    score.expected_interview_probability = round(min(base_prob + ats_bonus, 80), 1)

    return score


# ── ATS Success Intelligence ─────────────────────────────────────────────────

@dataclass
class ATSSuccessStats:
    total_applications: int = 0
    interviews: int = 0
    rejections: int = 0
    ghosted: int = 0
    interview_rate: float = 0.0
    ghost_rate: float = 0.0

    def format_text(self) -> str:
        return (
            f"Applications: {self.total_applications}  "
            f"Interview: {self.interview_rate:.0f}%  "
            f"Ghosted: {self.ghost_rate:.0f}%"
        )


def _company_ats_success_rate(company: str) -> float:
    """Look up historical interview rate for a company."""
    session: Session = get_session()
    try:
        total = session.query(func.count(Application.id)).join(Job).where(
            Job.company == company,
            Application.status.in_(["submitted", "interview", "rejected", "offer"]),
        ).scalar() or 0
        interviews = session.query(func.count(Application.id)).join(Job).where(
            Job.company == company,
            Application.status.in_(["interview", "offer"]),
        ).scalar() or 0
        return interviews / total if total > 0 else 0.25
    finally:
        session.close()


def company_stats(company: str) -> ATSSuccessStats:
    """Return ATS success statistics for a company."""
    session: Session = get_session()
    try:
        apps = session.query(Application).join(Job).where(Job.company == company).all()
    finally:
        session.close()

    stats = ATSSuccessStats(total_applications=len(apps))
    for a in apps:
        if a.status in ("interview", "offer"):
            stats.interviews += 1
        elif a.status == "rejected":
            stats.rejections += 1
        elif a.status in ("submitted",):
            stats.ghosted += 1

    stats.interview_rate = stats.interviews / stats.total_applications * 100 if stats.total_applications else 0
    stats.ghost_rate = stats.ghosted / stats.total_applications * 100 if stats.total_applications else 0
    return stats


def resume_effectiveness(resume_name: str) -> dict[str, Any]:
    """Return per-company interview rate for a specific resume."""
    session: Session = get_session()
    try:
        from sqlalchemy import case as sql_case
        rows = session.query(
            Job.company,
            func.count(Application.id).label("total"),
            func.sum(sql_case((Application.status.in_(["interview", "offer"]), 1), else_=0)).label("interviews"),
        ).join(Application, Application.job_id == Job.id).where(
            Application.resume_used == resume_name,
        ).group_by(Job.company).all()

        results = {}
        for company, total, interviews in rows:
            rate = (interviews / total * 100) if total else 0
            results[company] = {"applications": total, "interviews": interviews, "rate": rate}
        return results
    finally:
        session.close()


# ── Application Verification ─────────────────────────────────────────────────

def verify_application(
    application_id: str,
    ats_id: str = "",
    portal_url: str = "",
    confirmed: bool = True,
) -> dict[str, Any]:
    """Record ATS confirmation for an application."""
    session: Session = get_session()
    try:
        app = session.query(Application).filter(Application.id == application_id).first()
        if not app:
            return {"error": "Application not found"}
        app.application_id = ats_id or app.application_id
        app.portal_url = portal_url or app.portal_url
        app.ats_confirmed = confirmed
        app.ats_confirmed_at = datetime.utcnow()
        session.commit()
        return {"status": "verified", "application_id": application_id}
    except Exception as e:
        session.rollback()
        return {"error": str(e)}
    finally:
        session.close()


# Lazy registry for quality score
from resumes.registry import get_registry
