"""Feature Store — reusable feature vector for every application.

Phase 4D — Stage 2 (Decision Intelligence).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from database.models import Application, Job
from database.connection import get_session
from tracker.intelligence import compute_quality_score, _company_ats_success_rate
from resumes.registry import get_registry

KNOWN_ATS = {
    "greenhouse.io": "Greenhouse",
    "lever.co": "Lever",
    "ashbyhq.com": "Ashby",
    "workday.com": "Workday",
    "smartrecruiters.com": "SmartRecruiters",
    "bamboohr.com": "BambooHR",
    "teamtailor.com": "Teamtailor",
    "personio.com": "Personio",
}


@dataclass
class FeatureVector:
    resume_match: float = 0.0
    jd_match: float = 0.0
    company_tier: str = ""
    ats: str = ""
    application_age_hours: float = 0.0
    remote: bool = False
    experience_match: float = 0.0
    skill_gap: int = 0
    company_history_score: float = 0.25
    ats_history_score: float = 0.25
    features: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resume_match": self.resume_match,
            "jd_match": self.jd_match,
            "company_tier": self.company_tier,
            "ats": self.ats,
            "application_age_hours": self.application_age_hours,
            "remote": self.remote,
            "experience_match": self.experience_match,
            "skill_gap": self.skill_gap,
            "company_history_score": self.company_history_score,
            "ats_history_score": self.ats_history_score,
        }


def extract_ats_from_url(url: str) -> str:
    for domain, name in KNOWN_ATS.items():
        if domain in url:
            return name
    return "Unknown"


def company_tier(company: str) -> str:
    session: Session = get_session()
    try:
        count = session.query(Job).filter(Job.company == company).count()
        if count >= 50:
            return "Enterprise"
        elif count >= 10:
            return "Mid"
        return "Startup"
    finally:
        session.close()


def extract_features(app: Application, jd_text: str = "") -> FeatureVector:
    session: Session = get_session()
    try:
        job = app.job
        if not job:
            return FeatureVector()

        ats = extract_ats_from_url(job.url)
        tier = company_tier(job.company)
        company_rate = _company_ats_success_rate(job.company)
        ats_history = _company_ats_success_rate(ats)

        age_hours = 0.0
        if app.created_at:
            created = app.created_at.replace(tzinfo=timezone.utc) if not app.created_at.tzinfo else app.created_at
            delta = datetime.now(timezone.utc) - created
            age_hours = delta.total_seconds() / 3600

        # Quality score for resume/JD match
        text = jd_text or f"{job.title}\n{job.company}\n{job.description}"
        registry = get_registry()
        resume_name = app.resume_used or ""
        meta = registry.get(resume_name) if resume_name else None
        qs = compute_quality_score(job, meta, text) if meta else None

        return FeatureVector(
            resume_match=qs.resume_match if qs else 0.0,
            jd_match=qs.keyword_match if qs else 0.0,
            company_tier=tier,
            ats=ats,
            application_age_hours=round(age_hours, 1),
            remote=job.remote or False,
            experience_match=qs.experience_fit if qs else 0.0,
            skill_gap=len(qs.missing_skills) if qs else 0,
            company_history_score=company_rate,
            ats_history_score=ats_history,
        )
    finally:
        session.close()
