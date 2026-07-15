"""Personal Intelligence — personalized weights, recommendations, and simulation.

Phase 5 of the Career OS.

Every recommendation shows evidence and confidence. Nothing is black-box.
"""

from __future__ import annotations
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, case
from sqlalchemy.orm import Session

from database.models import Application, Job, ApplicationOutcome, Interaction
from database.connection import get_session
from tracker.features import extract_ats_from_url, KNOWN_ATS
from tracker.decision import WEIGHTS as GLOBAL_WEIGHTS, _confidence_label

# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class PersonalWeights:
    resume_fit: float = 0.35
    experience: float = 0.25
    company_history: float = 0.15
    ats_history: float = 0.10
    application_timing: float = 0.10
    skill_gap_penalty: float = 0.05
    confidence: str = "Low"

    def to_dict(self) -> dict[str, float]:
        return {
            "resume_fit": self.resume_fit,
            "experience": self.experience,
            "company_history": self.company_history,
            "ats_history": self.ats_history,
            "application_timing": self.application_timing,
            "skill_gap_penalty": self.skill_gap_penalty,
        }

    def format_text(self) -> str:
        lines = [f"  Personal Weights  (Confidence: {self.confidence})"]
        lines.append(f"    Resume Fit          {self.resume_fit:.0%}")
        lines.append(f"    Experience          {self.experience:.0%}")
        lines.append(f"    Company History     {self.company_history:.0%}")
        lines.append(f"    ATS History         {self.ats_history:.0%}")
        lines.append(f"    Timing              {self.application_timing:.0%}")
        lines.append(f"    Skill Gap Penalty   {self.skill_gap_penalty:.0%}")
        return "\n".join(lines)


@dataclass
class ResumeStat:
    name: str = ""
    applications: int = 0
    interviews: int = 0
    interview_rate: float = 0.0
    confidence: str = "Low"

    def format_text(self) -> str:
        return (
            f"  {self.name}\n"
            f"    Applications: {self.applications}\n"
            f"    Interviews:   {self.interviews}\n"
            f"    Rate:         {self.interview_rate:.1f}%\n"
            f"    Confidence:   {self.confidence}"
        )


@dataclass
class CompanyIntelligence:
    company: str = ""
    applications: int = 0
    replies: int = 0
    interviews: int = 0
    offers: int = 0
    avg_reply_days: float = 0.0
    best_resume: str = ""
    best_resume_rate: float = 0.0

    def format_text(self) -> str:
        lines = [f"  {self.company}"]
        lines.append(f"    Applications:  {self.applications}")
        lines.append(f"    Replies:       {self.replies}")
        if self.avg_reply_days:
            lines.append(f"    Avg Reply:    {self.avg_reply_days:.0f} days")
        lines.append(f"    Interviews:    {self.interviews}")
        lines.append(f"    Offers:        {self.offers}")
        if self.best_resume:
            lines.append(f"    Best Resume:   {self.best_resume} ({self.best_resume_rate:.0f}%)")
        return "\n".join(lines)


@dataclass
class SimulationResult:
    current_score: float = 0.0
    changes: list[tuple[str, float]] = field(default_factory=list)
    final_score: float = 0.0
    breakdown: list[dict[str, Any]] = field(default_factory=list)

    def format_text(self) -> str:
        lines = [
            f"  Current Probability:  {self.current_score:.0f}%",
            f"",
            f"  What If",
        ]
        for label, delta in self.changes:
            arrow = "+" if delta >= 0 else ""
            lines.append(f"    {label:25s}  {arrow}{delta:.0f}%")
        lines.append(f"")
        lines.append(f"  Expected:  {self.final_score:.0f}%")
        if self.breakdown:
            lines.append(f"  Breakdown")
            for b in self.breakdown:
                lines.append(f"    {b['label']:25s}  {b['weight']:4.0%}  {b['score']:.0f}/100")
        return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rate_confidence(n: int) -> str:
    if n >= 10:
        return "High"
    elif n >= 3:
        return "Medium"
    return "Low"


def _base_interview_rate(session: Session) -> float:
    total = session.query(func.count(ApplicationOutcome.id)).scalar() or 0
    if total == 0:
        return 0.15
    interviewed = session.query(func.count(ApplicationOutcome.id)).filter(
        ApplicationOutcome.interview_at.isnot(None)
    ).scalar() or 0
    return interviewed / total


# ── 1. Weight Learning ────────────────────────────────────────────────────────

def learn_weights() -> PersonalWeights:
    session: Session = get_session()
    try:
        total = session.query(func.count(ApplicationOutcome.id)).scalar() or 0
        if total < 5:
            return PersonalWeights(confidence="Low")

        interviewed = session.query(func.count(ApplicationOutcome.id)).filter(
            ApplicationOutcome.interview_at.isnot(None)
        ).scalar() or 0
        base_rate = interviewed / total if total else 0.15

        # Resume fit weight: variance in interview rates across resumes
        resume_rows = session.query(
            ApplicationOutcome.resume_used,
            func.count(ApplicationOutcome.id).label("total"),
            func.count(case((ApplicationOutcome.interview_at.isnot(None), 1))).label("interviews"),
        ).filter(
            ApplicationOutcome.resume_used != "",
        ).group_by(ApplicationOutcome.resume_used).having(
            func.count(ApplicationOutcome.id) >= 2
        ).all()

        resume_variance = 0.0
        resume_count = 0
        for r in resume_rows:
            rate = (r.interviews or 0) / r.total
            resume_variance += abs(rate - base_rate)
            resume_count += 1
        resume_fit = min(0.35 + resume_variance * 0.15 if resume_count else 0.35, 0.50)

        # Company history weight: variance across companies
        company_rows = session.query(
            ApplicationOutcome.company,
            func.count(ApplicationOutcome.id).label("total"),
            func.count(case((ApplicationOutcome.interview_at.isnot(None), 1))).label("interviews"),
        ).group_by(ApplicationOutcome.company).having(
            func.count(ApplicationOutcome.id) >= 2
        ).all()

        company_variance = 0.0
        company_count = 0
        for r in company_rows:
            rate = (r.interviews or 0) / r.total
            company_variance += abs(rate - base_rate)
            company_count += 1
        company_history = min(0.15 + company_variance * 0.10 if company_count else 0.15, 0.35)

        # Timing weight: variance by day of week
        timing_rows = session.query(
            func.strftime("%w", ApplicationOutcome.applied_at).label("dow"),
            func.count(ApplicationOutcome.id).label("total"),
            func.count(case((ApplicationOutcome.interview_at.isnot(None), 1))).label("interviews"),
        ).filter(
            ApplicationOutcome.applied_at.isnot(None),
        ).group_by("dow").having(
            func.count(ApplicationOutcome.id) >= 2
        ).all()

        timing_variance = 0.0
        timing_count = 0
        for r in timing_rows:
            rate = (r.interviews or 0) / r.total
            timing_variance += abs(rate - base_rate)
            timing_count += 1
        timing = min(0.10 + timing_variance * 0.08 if timing_count else 0.10, 0.25)

        # Experience weight: adjust based on base_rate
        experience = max(0.15, min(0.25 - company_variance * 0.05, 0.30))

        # Normalize to sum to ~0.95 (skill gap gets 0.05)
        raw = resume_fit + experience + company_history + GLOBAL_WEIGHTS["ats_history"] + timing
        scale = 0.95 / raw

        conf = _rate_confidence(total)

        return PersonalWeights(
            resume_fit=round(resume_fit * scale, 2),
            experience=round(experience * scale, 2),
            company_history=round(company_history * scale, 2),
            ats_history=round(GLOBAL_WEIGHTS["ats_history"] * scale, 2),
            application_timing=round(timing * scale, 2),
            skill_gap_penalty=0.05,
            confidence=conf,
        )
    finally:
        session.close()


# ── 2. Resume Intelligence ────────────────────────────────────────────────────

def resume_stats() -> list[ResumeStat]:
    session: Session = get_session()
    try:
        rows = session.query(
            ApplicationOutcome.resume_used,
            func.count(ApplicationOutcome.id).label("total"),
            func.count(case((ApplicationOutcome.interview_at.isnot(None), 1))).label("interviews"),
        ).filter(
            ApplicationOutcome.resume_used != "",
        ).group_by(ApplicationOutcome.resume_used).order_by(
            func.count(case((ApplicationOutcome.interview_at.isnot(None), 1))).desc()
        ).all()

        return [
            ResumeStat(
                name=r.resume_used,
                applications=r.total,
                interviews=r.interviews or 0,
                interview_rate=(r.interviews or 0) / r.total * 100,
                confidence=_rate_confidence(r.total),
            )
            for r in rows
        ]
    finally:
        session.close()


def resume_detail(name: str) -> ResumeStat | None:
    session: Session = get_session()
    try:
        total = session.query(func.count(ApplicationOutcome.id)).filter(
            ApplicationOutcome.resume_used == name,
        ).scalar() or 0
        if total == 0:
            return None
        interviews = session.query(func.count(ApplicationOutcome.id)).filter(
            ApplicationOutcome.resume_used == name,
            ApplicationOutcome.interview_at.isnot(None),
        ).scalar() or 0
        return ResumeStat(
            name=name,
            applications=total,
            interviews=interviews,
            interview_rate=interviews / total * 100,
            confidence=_rate_confidence(total),
        )
    finally:
        session.close()


# ── 3. Company Intelligence ────────────────────────────────────────────────────

def company_intelligence(company: str | None = None) -> list[CompanyIntelligence]:
    session: Session = get_session()
    try:
        query = session.query(
            ApplicationOutcome.company,
            func.count(ApplicationOutcome.id).label("total"),
            func.count(case((ApplicationOutcome.interview_at.isnot(None), 1))).label("interviews"),
            func.count(case((ApplicationOutcome.offer_at.isnot(None), 1))).label("offers"),
        ).filter(ApplicationOutcome.company != "")

        if company:
            query = query.filter(ApplicationOutcome.company == company)

        rows = query.group_by(ApplicationOutcome.company).order_by(
            func.count(ApplicationOutcome.id).desc()
        ).limit(20).all()

        results = []
        for r in rows:
            # Count replies via interactions
            reply_count = session.query(func.count(Interaction.id)).join(
                Application, Interaction.application_id == Application.id
            ).join(
                Job, Application.job_id == Job.id
            ).filter(
                Job.company == r.company,
                Interaction.outcome == "replied",
            ).scalar() or 0

            # Average reply time
            avg_days = 0.0
            reply_rows = session.query(
                Application.applied_at,
                Interaction.occurred_at,
            ).join(
                Interaction, Interaction.application_id == Application.id
            ).join(
                Job, Application.job_id == Job.id
            ).filter(
                Job.company == r.company,
                Interaction.outcome == "replied",
                Interaction.occurred_at.isnot(None),
                Application.applied_at.isnot(None),
            ).all()
            if reply_rows:
                days = [
                    abs((i.occurred_at - a.applied_at).total_seconds() / 86400)
                    for a, i in reply_rows
                ]
                avg_days = sum(days) / len(days) if days else 0.0

            # Best resume for this company
            resume_rows = session.query(
                ApplicationOutcome.resume_used,
                func.count(ApplicationOutcome.id).label("total"),
                func.count(case((ApplicationOutcome.interview_at.isnot(None), 1))).label("interviews"),
            ).filter(
                ApplicationOutcome.company == r.company,
                ApplicationOutcome.resume_used != "",
            ).group_by(ApplicationOutcome.resume_used).having(
                func.count(ApplicationOutcome.id) >= 1
            ).order_by(
                func.count(case((ApplicationOutcome.interview_at.isnot(None), 1))).desc()
            ).all()

            best_resume = ""
            best_rate = 0.0
            if resume_rows:
                best = resume_rows[0]
                best_resume = best.resume_used
                best_rate = (best.interviews or 0) / best.total * 100 if best.total else 0

            results.append(CompanyIntelligence(
                company=r.company,
                applications=r.total,
                replies=reply_count,
                interviews=r.interviews or 0,
                offers=r.offers or 0,
                avg_reply_days=round(avg_days, 1),
                best_resume=best_resume,
                best_resume_rate=round(best_rate, 1),
            ))

        return results
    finally:
        session.close()


# ── 4. ATS Intelligence ──────────────────────────────────────────────────────

def ats_intelligence() -> list[dict[str, Any]]:
    session: Session = get_session()
    try:
        rows = session.query(
            ApplicationOutcome.ats,
            func.count(ApplicationOutcome.id).label("total"),
            func.count(case((ApplicationOutcome.interview_at.isnot(None), 1))).label("interviews"),
        ).filter(
            ApplicationOutcome.ats != "",
        ).group_by(ApplicationOutcome.ats).having(
            func.count(ApplicationOutcome.id) >= 2
        ).order_by(
            func.count(case((ApplicationOutcome.interview_at.isnot(None), 1))).desc()
        ).all()

        return [
            {
                "ats": r.ats,
                "applications": r.total,
                "interviews": r.interviews or 0,
                "interview_rate": (r.interviews or 0) / r.total * 100,
            }
            for r in rows
        ]
    finally:
        session.close()


# ── 5. Timing Intelligence ────────────────────────────────────────────────────

def timing_intelligence() -> dict[str, Any]:
    session: Session = get_session()
    try:
        rows = session.query(ApplicationOutcome).filter(
            ApplicationOutcome.applied_at.isnot(None),
        ).all()

        day_counts: Counter[str] = Counter()
        day_interviews: Counter[str] = Counter()
        hour_counts: Counter[int] = Counter()
        hour_interviews: Counter[int] = Counter()

        for r in rows:
            dt = r.applied_at
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            day = dt.strftime("%A")
            hour = dt.hour
            day_counts[day] += 1
            hour_counts[hour] += 1
            if r.interview_at:
                day_interviews[day] += 1
                hour_interviews[hour] += 1

        days = []
        for d in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
            total = day_counts.get(d, 0)
            ivals = day_interviews.get(d, 0)
            days.append({
                "day": d,
                "applications": total,
                "interviews": ivals,
                "rate": ivals / total * 100 if total else 0,
                "confidence": _rate_confidence(total),
            })

        hours = []
        for h in range(24):
            total = hour_counts.get(h, 0)
            ivals = hour_interviews.get(h, 0)
            hours.append({
                "hour": h,
                "applications": total,
                "interviews": ivals,
                "rate": ivals / total * 100 if total else 0,
            })

        return {
            "by_day": days,
            "by_hour": hours,
            "best_day": max(days, key=lambda d: d["rate"]) if days else {},
            "best_hour": max(hours, key=lambda h: h["rate"]) if hours else {},
        }
    finally:
        session.close()


# ── 6. Skill Intelligence ─────────────────────────────────────────────────────

def skill_intelligence() -> list[dict[str, Any]]:
    """Extract skills from successful applications' job descriptions."""
    session: Session = get_session()
    try:
        successful = session.query(ApplicationOutcome).filter(
            ApplicationOutcome.interview_at.isnot(None),
        ).all()

        app_ids = [s.application_id for s in successful if s.application_id]
        if not app_ids:
            return []

        apps = session.query(Application).filter(
            Application.id.in_(app_ids),
        ).all()

        from resumes.jd_analyzer import analyze as analyze_jd

        skill_counter: Counter[str] = Counter()
        for app in apps:
            job = app.job
            if job and job.description:
                analysis = analyze_jd(f"{job.title}\n{job.company}\n{job.description}")
                for s in analysis.skills:
                    skill_counter[s] += 1

        total = len(successful)
        return [
            {
                "skill": skill,
                "count": count,
                "frequency": count / total * 100 if total else 0,
            }
            for skill, count in skill_counter.most_common(20)
        ]
    finally:
        session.close()


# ── 7. Personalized Prediction ────────────────────────────────────────────────

def personal_predict(
    app: Application,
    job: Job | None = None,
    jd_text: str = "",
    weights: PersonalWeights | None = None,
) -> dict[str, Any]:
    """Predict interview probability using personalized weights."""
    if weights is None:
        weights = learn_weights()

    from tracker.features import extract_features

    session = get_session()
    try:
        fresh_app = session.merge(app) if app not in session else app
        if job:
            fresh_job = session.merge(job) if job not in session else job
        else:
            fresh_job = fresh_app.job
            if not fresh_job:
                return {"score": 0, "confidence": "Low", "breakdown": [], "reasons": [], "risks": [], "weights": weights.to_dict()}
        features = extract_features(fresh_app, jd_text)

        resume_fit = features.resume_match
        experience = features.experience_match
        company_history = features.company_history_score * 100
        timing = max(0, 100 - features.application_age_hours * 0.5)
        skill_penalty = min(features.skill_gap * 5, 30)

        raw = (
            resume_fit * weights.resume_fit
            + experience * weights.experience
            + company_history * weights.company_history
            + GLOBAL_WEIGHTS["ats_history"] * weights.ats_history
            + timing * weights.application_timing
        )
        score = max(0, min(raw - skill_penalty * weights.skill_gap_penalty, 100))

        breakdown = [
            {"label": "Resume Fit", "weight": weights.resume_fit, "score": round(resume_fit, 1)},
            {"label": "Experience", "weight": weights.experience, "score": round(experience, 1)},
            {"label": "Company History", "weight": weights.company_history, "score": round(company_history, 1)},
            {"label": "ATS History", "weight": weights.ats_history, "score": round(GLOBAL_WEIGHTS["ats_history"] * 100, 1)},
            {"label": "Timing", "weight": weights.application_timing, "score": round(timing, 1)},
        ]

        reasons = []
        if features.resume_match >= 70:
            reasons.append(f"+ Resume matches ({features.resume_match:.0f}%)")
        if features.company_history_score >= 0.5:
            reasons.append("+ Company responded before")
        if features.application_age_hours <= 24:
            reasons.append("+ Applied within 24 hours")

        risks = []
        if features.skill_gap > 0:
            risks.append(f"- Missing {features.skill_gap} skills")
        if features.resume_match < 40:
            risks.append("- Resume is a weak fit")

        return {
            "score": round(score, 1),
            "confidence": weights.confidence,
            "breakdown": breakdown,
            "reasons": reasons,
            "risks": risks,
            "weights": weights.to_dict(),
        }
    finally:
        session.close()


# ── 8. Simulation ─────────────────────────────────────────────────────────────

def simulate(
    app: Application,
    changes: list[dict[str, Any]],
    job: Job | None = None,
    jd_text: str = "",
) -> SimulationResult:
    """Simulate what-if scenarios."""
    weights = learn_weights()
    current = personal_predict(app, job, jd_text, weights)
    base_score = current["score"]

    from tracker.features import extract_features
    features = extract_features(app, jd_text)

    score = base_score
    change_details: list[tuple[str, float]] = []

    for change in changes:
        kind = change.get("kind", "")
        delta = 0.0

        if kind == "resume":
            new_resume = change.get("value", "")
            if new_resume:
                from resumes.registry import get_registry
                registry = get_registry()
                meta = registry.get(new_resume)
                if meta:
                    from tracker.intelligence import compute_quality_score
                    text = jd_text or f"{job.title}\n{job.company}\n{job.description}"
                    qs = compute_quality_score(job, meta, text)
                    if qs:
                        new_resume_fit = qs.resume_match
                        old_resume_fit = features.resume_match
                        delta = (new_resume_fit - old_resume_fit) * weights.resume_fit
                        change_details.append((f"Use {new_resume}", delta))

        elif kind == "skill":
            # Adding a skill reduces skill gap
            if features.skill_gap > 0:
                reduction = change.get("count", 1)
                delta = min(reduction * 4, features.skill_gap * 5) * weights.skill_gap_penalty
                change_details.append((f"Mention {change.get('skill', 'key skill')}", delta))

        elif kind == "timing":
            if features.application_age_hours > 24:
                delta = min(8, features.application_age_hours * 0.2) * weights.application_timing
                change_details.append((f"Apply sooner (morning)", delta))

    final_score = min(score + sum(d for _, d in change_details), 100)

    return SimulationResult(
        current_score=base_score,
        changes=change_details,
        final_score=round(final_score, 1),
        breakdown=current["breakdown"],
    )
