"""Decision Intelligence — rule-based interview probability prediction.

Phase 4D — Stage 3+4 (Decision Intelligence).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from database.models import Application, Job, ApplicationOutcome
from database.connection import get_session
from tracker.features import extract_features, FeatureVector
from tracker.events import record_event, PREDICTION_MADE

# ── Prediction ────────────────────────────────────────────────────────────────

@dataclass
class Prediction:
    score: float = 0.0
    confidence: str = "Low"
    breakdown: list[dict[str, Any]] = field(default_factory=list)
    reasons_positive: list[str] = field(default_factory=list)
    reasons_negative: list[str] = field(default_factory=list)

    def format_text(self) -> str:
        lines = [
            f"  Interview Probability: {self.score:.0f}%",
            f"  Confidence: {self.confidence}",
            f"",
            f"  Breakdown",
        ]
        for b in self.breakdown:
            lines.append(f"    {b['label']:25s}  {b['weight']:4.0%}  {b['score']:.0f}/100")
        if self.reasons_positive:
            lines.append(f"  Why This Helps")
            for r in self.reasons_positive[:4]:
                lines.append(f"    + {r}")
        if self.reasons_negative:
            lines.append(f"  Risks")
            for r in self.reasons_negative[:4]:
                lines.append(f"    - {r}")
        return "\n".join(lines)


@dataclass
class Counterfactual:
    current_probability: float = 0.0
    improved_probability: float = 0.0
    action: str = ""
    reason: str = ""
    recommendation: str = ""


WEIGHTS = {
    "resume_fit": 0.35,
    "experience": 0.25,
    "company_history": 0.15,
    "ats_history": 0.10,
    "application_timing": 0.10,
    "skill_gap_penalty": 0.05,
}


def _confidence_label(application_count: int) -> str:
    if application_count >= 50:
        return "High"
    elif application_count >= 10:
        return "Medium"
    return "Low"


def predict_interview(app: Application, job: Job | None = None, jd_text: str = "") -> Prediction:
    session = get_session()
    try:
        if not job:
            job = app.job

        features = extract_features(app, jd_text)
        total_apps = session.query(ApplicationOutcome).count()

        # Component scores (0-100)
        resume_fit = features.resume_match
        experience = features.experience_match
        company_history = features.company_history_score * 100
        ats_history = features.ats_history_score * 100
        timing = max(0, 100 - features.application_age_hours * 0.5)  # decays over time
        skill_penalty = min(features.skill_gap * 5, 30)

        # Weighted sum
        raw = (
            resume_fit * WEIGHTS["resume_fit"]
            + experience * WEIGHTS["experience"]
            + company_history * WEIGHTS["company_history"]
            + ats_history * WEIGHTS["ats_history"]
            + timing * WEIGHTS["application_timing"]
        )
        score = max(0, min(raw - skill_penalty * WEIGHTS["skill_gap_penalty"], 100))

        breakdown = [
            {"label": "Resume Fit", "weight": WEIGHTS["resume_fit"], "score": round(resume_fit, 1)},
            {"label": "Experience", "weight": WEIGHTS["experience"], "score": round(experience, 1)},
            {"label": "Company History", "weight": WEIGHTS["company_history"], "score": round(company_history, 1)},
            {"label": "ATS History", "weight": WEIGHTS["ats_history"], "score": round(ats_history, 1)},
            {"label": "Application Timing", "weight": WEIGHTS["application_timing"], "score": round(timing, 1)},
        ]

        reasons_positive = []
        reasons_negative = []

        if features.resume_match >= 70:
            reasons_positive.append(f"Resume matches ({features.resume_match:.0f}%)")
        if features.company_history_score >= 0.5:
            reasons_positive.append("Company responded before")
        if features.ats_history_score >= 0.5:
            reasons_positive.append(f"ATS = {features.ats} (good history)")
        if features.application_age_hours <= 24:
            reasons_positive.append("Applied within 24 hours")
        if features.experience_match >= 70:
            reasons_positive.append(f"Experience matches ({features.experience_match:.0f}%)")

        if features.skill_gap > 0:
            reasons_negative.append(f"Missing {features.skill_gap} skills")
        if features.resume_match < 40:
            reasons_negative.append("Resume is a weak fit")
        if features.experience_match < 40:
            reasons_negative.append("Experience gap")

        result = Prediction(
            score=round(score, 1),
            confidence=_confidence_label(total_apps),
            breakdown=breakdown,
            reasons_positive=reasons_positive,
            reasons_negative=reasons_negative,
        )

        record_event(PREDICTION_MADE, "application", app.id, actor="system", metadata={
            "score": round(score, 1),
            "confidence": result.confidence,
            "company": job.company if job else "",
            "resume_match": features.resume_match,
            "skill_gap": features.skill_gap,
        })

        return result
    finally:
        session.close()


# ── Counterfactual Analysis ───────────────────────────────────────────────────

def counterfactual(app: Application, job: Job | None = None, jd_text: str = "") -> Counterfactual:
    session = get_session()
    try:
        if not job:
            job = app.job
        if not job:
            return Counterfactual()

        base = predict_interview(app, job, jd_text)
        features = extract_features(app, jd_text)

        # Check for most impactful improvement
        if features.skill_gap > 0:
            improved_score = min(base.score + features.skill_gap * 4, 100)
            return Counterfactual(
                current_probability=base.score,
                improved_probability=round(improved_score, 1),
                action="Add missing skills to resume",
                reason=f"Missing {features.skill_gap}: {', '.join(getattr(features, 'features', {}).get('missing_skills', []))}",
                recommendation=f"If resume improved, expected probability: {improved_score:.0f}%",
            )

        if features.experience_match < 40:
            return Counterfactual(
                current_probability=base.score,
                improved_probability=base.score,
                action="Do not apply",
                reason=f"Required experience exceeds yours",
                recommendation="Do not apply to similar roles",
            )

        if features.application_age_hours > 168:
            improved_score = min(base.score + 10, 100)
            return Counterfactual(
                current_probability=base.score,
                improved_probability=round(improved_score, 1),
                action="Apply sooner",
                reason=f"Application is {features.application_age_hours:.0f}h old",
                recommendation=f"Apply within 24h for ~+10% probability",
            )

        return Counterfactual(
            current_probability=base.score,
            improved_probability=base.score,
            action="No significant improvement identified",
            reason="",
            recommendation="",
        )
    finally:
        session.close()
