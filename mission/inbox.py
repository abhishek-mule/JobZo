"""Inbox — everything requiring attention, with transparency and urgency.

Every inbox item answers four questions:
1. Why am I seeing this?
2. What happens if I ignore it?
3. What happens if I do it?
4. What's the next step?
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from database.connection import get_session
from database.models import Application, Job, Task, ApplicationOutcome, Interaction, DecisionSnapshot
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from tracker.features import extract_ats_from_url, company_tier, KNOWN_ATS


@dataclass
class InboxItem:
    category: str = ""          # interview, followup, apply, review, task
    priority: str = "medium"    # high, medium, low
    title: str = ""
    company: str = ""
    role: str = ""
    detail: str = ""
    action_label: str = ""

    score: int | None = None
    score_breakdown: dict[str, int] = field(default_factory=dict)

    justification: list[str] = field(default_factory=list)
    expected_outcome: str = ""
    urgency: str = ""
    expiry_days: int | None = None
    time_required: str = ""

    app_id: str | None = None
    job_id: str | None = None
    ref_id: str | None = None


@dataclass
class TimelineEvent:
    stage: str = ""
    date: str = ""
    detail: str = ""
    icon: str = ""


def _time_estimate(category: str, job: Job | None = None) -> str:
    estimates = {
        "apply": "4 min",
        "interview": "2 h",
        "followup": "45 sec",
        "review": "30 sec",
        "task": "2 min",
    }
    return estimates.get(category, "5 min")


def _urgency_label(posted_at: datetime | None, now: datetime) -> tuple[str, int]:
    if not posted_at:
        return "", 30
    days_old = (now - posted_at).days
    if days_old > 30:
        return "Likely closed", days_old
    elif days_old > 14:
        return "Closing soon", days_old
    elif days_old > 7:
        return "Expiring", days_old
    return "Fresh", days_old


def _justification_lines(app: Application, job: Job) -> list[str]:
    lines = []
    if app.score >= 80:
        lines.append("Excellent resume fit")
    elif app.score >= 60:
        lines.append("Good resume fit")
    else:
        lines.append("Moderate resume fit")

    if app.resume_used:
        lines.append(f"Resume: {app.resume_used}")

    if job.posted_at:
        days = (datetime.utcnow() - job.posted_at).days
        if days <= 3:
            lines.append("Posted recently")
        elif days <= 7:
            lines.append("Posted this week")

    tier = company_tier(job.company)
    if tier == 1:
        lines.append("Top-tier company")
    elif tier == 2:
        lines.append("Well-funded company")

    ats = extract_ats_from_url(job.url)
    if ats and ats in ("Greenhouse", "Lever", "Ashby"):
        lines.append(f"Modern ATS ({ats})")
    elif ats == "Workday":
        lines.append(f"Low response ATS ({ats})")

    if job.remote:
        lines.append("Remote position")

    return lines[:5]


def _expected_outcome(app: Application) -> str:
    if app.current_decision:
        return f"Interview probability: {app.current_decision.interview_probability}%"
    return ""


def _score_breakdown(app: Application, job: Job) -> dict[str, int]:
    """Compute transparent score breakdown from DecisionSnapshot or fallback."""
    if app.current_decision:
        from services.decision_snapshot import snapshot_to_inbox_data
        data = snapshot_to_inbox_data(app.current_decision)
        return data.get("score_breakdown", {})

    breakdown = {}

    from resumes.jd_analyzer import analyze as analyze_jd

    jd = analyze_jd(job.description or "")
    resume_skills = set()
    registry = None
    try:
        from resumes.registry import get_registry
        registry = get_registry()
        if app.resume_used:
            meta = registry.get(app.resume_used)
            if meta:
                resume_skills = meta.all_skill_names
    except Exception:
        pass

    matched = set(jd.skills or []) & resume_skills
    total = len(jd.skills) if jd.skills else 1
    breakdown["Resume Fit"] = min(round(len(matched) / max(total, 1) * 100), 100)

    tier = company_tier(job.company)
    breakdown["Company Tier"] = {1: 90, 2: 70, 3: 50}.get(tier, 50)

    ats = extract_ats_from_url(job.url)
    ats_score = 90 if ats in ("Greenhouse", "Lever", "Ashby") else 60 if ats else 70
    ats_score = 30 if ats == "Workday" else ats_score
    breakdown["ATS"] = ats_score

    if job.posted_at:
        days = (datetime.utcnow() - job.posted_at).days
        breakdown["Freshness"] = max(100 - days * 5, 0)
    else:
        breakdown["Freshness"] = 50

    return breakdown


def _build_timeline_for(app_id: str) -> list[TimelineEvent]:
    """Build a complete decision timeline for an application."""
    events: list[TimelineEvent] = []
    session = get_session()
    try:
        app = session.query(Application).options(
            joinedload(Application.job)
        ).filter(Application.id == app_id).first()
        if not app:
            return events

        job = app.job

        events.append(TimelineEvent(
            stage="Discovered",
            date=app.created_at.strftime("%b %d, %Y") if app.created_at else "",
            detail=f"{job.company} — {job.title}" if job else "",
            icon="\U0001f50d",
        ))

        if app.resume_used:
            date_str = app.last_activity_at.strftime("%b %d, %Y") if app.last_activity_at else app.created_at.strftime("%b %d, %Y") if app.created_at else ""
            events.append(TimelineEvent(
                stage="Resume Selected",
                date=date_str,
                detail=f"Resume: {app.resume_used}",
                icon="\U0001f4c4",
            ))

        if app.applied_at:
            events.append(TimelineEvent(
                stage="Applied",
                date=app.applied_at.strftime("%b %d, %Y"),
                detail=f"Score: {app.score}/100",
                icon="\U0001f680",
            ))

        # Interactions (follow-ups, replies)
        interactions = session.query(Interaction).filter(
            Interaction.application_id == app_id,
        ).order_by(Interaction.occurred_at).all()
        for ix in interactions:
            stage_map = {
                "email": "Follow-up",
                "linkedin": "LinkedIn Message",
                "call": "Call",
                "meeting": "Meeting",
                "referral": "Referral",
            }
            stage = stage_map.get(ix.type, ix.type.title())
            events.append(TimelineEvent(
                stage=stage,
                date=ix.occurred_at.strftime("%b %d, %Y") if ix.occurred_at else "",
                detail=ix.subject or "",
                icon="\U0001f4e7" if ix.direction == "outbound" else "\U0001f4e8",
            ))

        # Outcome stages
        outcome = session.query(ApplicationOutcome).filter(
            ApplicationOutcome.application_id == app_id,
        ).first()
        if outcome:
            if outcome.viewed_at:
                events.append(TimelineEvent(
                    stage="Recruiter Viewed",
                    date=outcome.viewed_at.strftime("%b %d, %Y"),
                    detail="",
                    icon="\U0001f441",
                ))
            if outcome.oa_at:
                events.append(TimelineEvent(
                    stage="Online Assessment",
                    date=outcome.oa_at.strftime("%b %d, %Y"),
                    detail="",
                    icon="\u270d\ufe0f",
                ))
            if outcome.interview_at:
                events.append(TimelineEvent(
                    stage="Interview",
                    date=outcome.interview_at.strftime("%b %d, %Y"),
                    detail=f"Round: {outcome.interview_rounds or 1}",
                    icon="\U0001f3a4",
                ))
            if outcome.offer_at:
                events.append(TimelineEvent(
                    stage="Offer",
                    date=outcome.offer_at.strftime("%b %d, %Y"),
                    detail=outcome.salary or "",
                    icon="\U0001f389",
                ))
            if outcome.rejected_at:
                events.append(TimelineEvent(
                    stage="Rejected",
                    date=outcome.rejected_at.strftime("%b %d, %Y"),
                    detail=outcome.rejection_reason or "",
                    icon="\u274c",
                ))
            if outcome.ghosted_at:
                events.append(TimelineEvent(
                    stage="Ghosted",
                    date=outcome.ghosted_at.strftime("%b %d, %Y"),
                    detail="No response",
                    icon="\U0001f47b",
                ))

        # Current status
        if app.last_activity_at and not any(e.stage in ("Offer", "Rejected", "Ghosted") for e in events):
            events.append(TimelineEvent(
                stage="Last Activity",
                date=app.last_activity_at.strftime("%b %d, %Y"),
                detail=f"Status: {app.status}",
                icon="\U0001f4ad",
            ))

    finally:
        session.close()

    return events


def build_timeline(app_id: str) -> list[TimelineEvent]:
    """Public wrapper to build timeline for any application."""
    return _build_timeline_for(app_id)


def build_inbox() -> list[InboxItem]:
    """Collect all inbox items with justifications, urgency, and time estimates."""
    items: list[InboxItem] = []
    session = get_session()
    now = datetime.utcnow()

    try:
        # ── Upcoming interviews ─────────────────────────────────────
        upcoming = session.query(Application).options(
            joinedload(Application.job)
        ).filter(
            Application.interview_date.isnot(None),
            Application.interview_date >= now,
            Application.status.in_(["interview", "offer"]),
        ).order_by(Application.interview_date).all()

        for app in upcoming:
            job = app.job
            if not job:
                continue
            days = (app.interview_date - now).days if app.interview_date else 99
            priority = "high" if days <= 1 else "medium"
            when = "today" if days == 0 else f"in {days}d"
            items.append(InboxItem(
                category="interview",
                priority=priority,
                title=f"Prepare for {job.company} interview",
                company=job.company,
                role=job.title,
                detail=f"{when} · {app.score}/100",
                action_label="Prepare",
                score=app.score,
                score_breakdown={"Preparation": 100, "Urgency": 100 if days <= 1 else 50},
                justification=[f"Interview {when}", f"Score: {app.score}/100"],
                expected_outcome="Better interview performance",
                urgency="Today" if days == 0 else f"{days}d away",
                time_required="2 h",
                app_id=str(app.id),
                job_id=job.id,
            ))

        # ── Overdue tasks ────────────────────────────────────────────
        overdue = session.query(Task).filter(
            Task.done == False, Task.due_date < now.date(),
        ).order_by(Task.due_date.asc()).all()

        for t in overdue:
            items.append(InboxItem(
                category="task",
                priority="high",
                title=t.title,
                detail=f"Overdue: {t.due_date}",
                action_label="Complete",
                urgency=f"Overdue { (now.date() - t.due_date).days }d",
                time_required="2 min",
                ref_id=str(t.id),
            ))

        # ── Submitted apps >7 days no response ──────────────────────
        seven_days_ago = now - timedelta(days=7)
        stale = session.query(Application).options(
            joinedload(Application.job)
        ).filter(
            Application.status == "submitted",
            Application.applied_at <= seven_days_ago,
            Application.response_date.is_(None),
        ).order_by(Application.applied_at).all()

        for app in stale:
            job = app.job
            if not job:
                continue
            days = (now - app.applied_at).days if app.applied_at else 0
            items.append(InboxItem(
                category="followup",
                priority="high" if days >= 14 else "medium",
                title=f"Follow up with {job.company}",
                company=job.company,
                role=job.title,
                detail=f"{days}d since application · No response",
                action_label="Email",
                score=app.score,
                justification=[f"{days} days without response", "Follow-up can increase response rate by 30%"],
                expected_outcome="Response within 3-5 days",
                urgency=f"{days}d elapsed",
                time_required="45 sec",
                app_id=str(app.id),
            ))

        # ── Pending tasks ────────────────────────────────────────────
        pending = session.query(Task).filter(Task.done == False).order_by(
            Task.due_date.asc().nullslast()
        ).all()

        for t in pending:
            if any(i.ref_id == str(t.id) for i in items):
                continue
            days = (t.due_date - now.date()).days if t.due_date else 0
            priority = "high" if days <= 0 else "medium" if days <= 3 else "low"
            detail = f"Due: {t.due_date}" if t.due_date else ""
            items.append(InboxItem(
                category="task",
                priority=priority,
                title=t.title,
                detail=detail,
                action_label="Complete",
                urgency=f"{abs(days)}d {'overdue' if days <= 0 else 'left'}",
                time_required="2 min",
                ref_id=str(t.id),
            ))

        # ── Ready to apply ───────────────────────────────────────────
        ready_apps = session.query(Application).options(
            joinedload(Application.job),
            joinedload(Application.current_decision),
        ).filter(
            Application.status == "ready", Application.strategy != "skip",
        ).order_by(Application.score.desc()).limit(10).all()

        for app in ready_apps:
            job = app.job
            if not job:
                continue
            urgency_label, days_old = _urgency_label(job.posted_at, now)
            items.append(InboxItem(
                category="apply",
                priority="high" if (app.score or 0) >= 80 else "medium",
                title=f"Apply to {job.company}",
                company=job.company,
                role=job.title,
                detail=f"{app.score}/100 · {job.source}",
                action_label="Apply",
                score=app.score,
                score_breakdown=_score_breakdown(app, job),
                justification=_justification_lines(app, job),
                expected_outcome=_expected_outcome(app),
                urgency=urgency_label,
                expiry_days=days_old,
                time_required=_time_estimate("apply"),
                app_id=str(app.id),
                job_id=job.id,
            ))

        # ── Drafted to review (skip excluded) ─────────────────────────
        drafted = session.query(Application).options(
            joinedload(Application.job),
            joinedload(Application.current_decision),
        ).filter(
            Application.status.in_(["drafted", "recommended"]),
            Application.strategy != "skip",
        ).order_by(Application.score.desc()).limit(15).all()

        for app in drafted:
            job = app.job
            if not job or any(i.job_id == job.id for i in items):
                continue
            urgency_label, days_old = _urgency_label(job.posted_at, now)
            items.append(InboxItem(
                category="review",
                priority="medium" if (app.score or 0) >= 70 else "low",
                title=f"Review {job.company} — {job.title}",
                company=job.company,
                role=job.title,
                detail=f"{app.score}/100 · {job.source}",
                action_label="Review",
                score=app.score,
                score_breakdown=_score_breakdown(app, job),
                justification=_justification_lines(app, job),
                expected_outcome=_expected_outcome(app),
                urgency=urgency_label,
                expiry_days=days_old,
                time_required=_time_estimate("review"),
                app_id=str(app.id),
                job_id=job.id,
            ))

    finally:
        session.close()

    # Sort: priority → urgency → score
    priority_order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda x: (
        priority_order.get(x.priority, 99),
        -(x.expiry_days or 30),
        -(x.score or 0),
    ))

    return items


def inbox_summary(items: list[InboxItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.category] = counts.get(item.category, 0) + 1
    return counts
