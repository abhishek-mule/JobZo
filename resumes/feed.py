"""Opportunity Feed — the default output of `jobzo`. Morning briefing showing top opportunities, follow-ups, interviews, and weekly progress.

This replaces `jobzo daily` as the main user-facing view.
It's instant (reads from DB), while `jobzo sync` refreshes data.
"""

from __future__ import annotations
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from database.models import Job, Application, Task
from database.connection import get_session
from resumes.registry import ResumeRegistry
from resumes.scorer import DEFAULT_WEIGHTS
from resumes.jd_analyzer import analyze as analyze_jd

logger = logging.getLogger("jobzo.feed")


@dataclass
class OpportunityItem:
    rank: int = 0
    company: str = ""
    title: str = ""
    score: int = 0
    strategy: str = ""
    job_id: str = ""
    fit_summary: str = ""


@dataclass
class FollowUpItem:
    company: str = ""
    title: str = ""
    days_ago: int = 0
    action: str = ""


@dataclass
class InterviewItem:
    company: str = ""
    title: str = ""
    date: str = ""
    days_until: int = 0


@dataclass
class WeeklyProgress:
    applications_this_week: int = 0
    weekly_target: int = 25
    interviews_scheduled: int = 0
    interviews_completed: int = 0


@dataclass
class OpportunityFeed:
    greeting: str = ""
    date: str = ""
    new_jobs_today: int = 0
    top_opportunities: list[OpportunityItem] = field(default_factory=list)
    follow_ups: list[FollowUpItem] = field(default_factory=list)
    interviews: list[InterviewItem] = field(default_factory=list)
    progress: WeeklyProgress = field(default_factory=WeeklyProgress)
    today_recommendation: str = ""

    def format_text(self) -> str:
        lines: list[str] = []
        lines.append(f"{'='*60}")
        lines.append(f"  {self.greeting}")
        lines.append(f"  {self.date}")
        lines.append(f"{'='*60}")

        if self.new_jobs_today > 0:
            lines.append(f"\n  📥 {self.new_jobs_today} new jobs found")
        else:
            lines.append(f"\n  📥 No new jobs since last sync")

        if self.top_opportunities:
            lines.append(f"\n{'─'*60}")
            lines.append(f"  Top Opportunities")
            lines.append(f"{'─'*60}")
            for opp in self.top_opportunities[:5]:
                stars = "★★★★★"[:opp.rank] + "☆☆☆☆☆"[opp.rank:]
                lines.append(f"  {stars} {opp.company:20s} {opp.title}")
                if opp.fit_summary:
                    lines.append(f"       {opp.fit_summary}")

        if self.follow_ups:
            lines.append(f"\n{'─'*60}")
            lines.append(f"  Follow-ups Due")
            lines.append(f"{'─'*60}")
            for fup in self.follow_ups[:5]:
                lines.append(f"  ⏰ {fup.company:20s} {fup.title} ({fup.days_ago}d ago)")

        if self.interviews:
            lines.append(f"\n{'─'*60}")
            lines.append(f"  Upcoming Interviews")
            lines.append(f"{'─'*60}")
            for iv in self.interviews[:3]:
                lines.append(f"  🎯 {iv.company:20s} {iv.title} ({iv.days_until}d away)")

        lines.append(f"\n{'─'*60}")
        lines.append(f"  Weekly Progress")
        lines.append(f"{'─'*60}")
        bar_len = 20
        done = min(self.progress.applications_this_week, self.progress.weekly_target)
        bar = "█" * int(bar_len * done / max(self.progress.weekly_target, 1))
        lines.append(f"  Applications: {done}/{self.progress.weekly_target}  {bar}")
        if self.progress.interviews_scheduled:
            lines.append(f"  Interviews Scheduled: {self.progress.interviews_scheduled}")
        if self.progress.interviews_completed:
            lines.append(f"  Interviews Completed: {self.progress.interviews_completed}")

        if self.today_recommendation:
            lines.append(f"\n  💡 Today's Recommendation")
            lines.append(f"     {self.today_recommendation}")

        lines.append(f"\n  Run `jobzo sync` to refresh data.")
        lines.append(f"{'='*60}")
        return "\n".join(lines)


def build_feed(registry: ResumeRegistry) -> OpportunityFeed:
    """Build the opportunity feed from current database state."""
    session: Session = get_session()

    feed = OpportunityFeed()

    now = datetime.utcnow()
    today_str = now.strftime("%A, %B %d, %Y")
    hour = now.hour
    if hour < 12:
        greeting = "Good Morning"
    elif hour < 17:
        greeting = "Good Afternoon"
    else:
        greeting = "Good Evening"

    profile_name = "there"
    try:
        import yaml
        from pathlib import Path
        profile_path = Path(__file__).parent.parent / "resumes" / "master" / "profile.yaml"
        if profile_path.exists():
            with open(profile_path) as f:
                p = yaml.safe_load(f)
            if p and p.get("name"):
                name_parts = p["name"].split()
                profile_name = name_parts[0] if name_parts else "there"
    except Exception:
        pass

    feed.greeting = f"{greeting} {profile_name}"
    feed.date = today_str

    try:
        # New jobs today
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        new_count = session.execute(
            select(func.count(Job.id)).where(Job.created_at >= today_start)
        ).scalar() or 0
        feed.new_jobs_today = new_count

        # Top opportunities: highest scored applications with status = recommended
        top_apps = session.execute(
            select(Application).where(
                Application.status == "recommended",
                Application.score > 0,
            ).order_by(Application.score.desc()).limit(5)
        ).scalars().all()

        for i, app in enumerate(top_apps):
            job = session.get(Job, app.job_id)
            if job:
                opp = OpportunityItem(
                    rank=i + 1,
                    company=job.company,
                    title=job.title,
                    score=app.score or 0,
                    strategy=app.strategy or "",
                    job_id=job.id,
                )
                # Quick fit summary using JD analyzer
                if job.description:
                    analysis = analyze_jd(job.description)
                    if analysis.skills:
                        opp.fit_summary = f"Skills: {', '.join(analysis.skills[:4])}"
                feed.top_opportunities.append(opp)

        # Follow-ups: applications sent >7 days ago with no response
        seven_days_ago = now - timedelta(days=7)
        follow_apps = session.execute(
            select(Application).join(Job, Application.job_id == Job.id).where(
                Application.status.in_(["applied", "submitted"]),
                Application.applied_at <= seven_days_ago,
                Application.response_date.is_(None),
            ).order_by(Application.applied_at).limit(5)
        ).scalars().all()

        for app in follow_apps:
            job = session.get(Job, app.job_id)
            if job and app.applied_at:
                days = (now - app.applied_at).days
                feed.follow_ups.append(FollowUpItem(
                    company=job.company,
                    title=job.title,
                    days_ago=days,
                    action=f"Send follow-up email (day {days})",
                ))

        # Upcoming interviews
        upcoming = session.execute(
            select(Application).join(Job, Application.job_id == Job.id).where(
                Application.interview_date.isnot(None),
                Application.interview_date >= now,
            ).order_by(Application.interview_date).limit(5)
        ).scalars().all()

        for app in upcoming:
            job = session.get(Job, app.job_id)
            if job and app.interview_date:
                iv_date = app.interview_date
                if isinstance(iv_date, str):
                    from datetime import datetime as dt
                    iv_date = dt.fromisoformat(iv_date)
                days = (iv_date - now).days
                feed.interviews.append(InterviewItem(
                    company=job.company,
                    title=job.title,
                    date=iv_date.strftime("%b %d"),
                    days_until=days,
                ))

        # Weekly progress
        week_ago = now - timedelta(days=7)
        wk_apps = session.execute(
            select(func.count(Application.id)).where(
                Application.created_at >= week_ago,
                Application.status.in_(["applied", "submitted"]),
            )
        ).scalar() or 0

        wk_interviews_scheduled = session.execute(
            select(func.count(Application.id)).where(
                Application.interview_date.isnot(None),
                Application.interview_date >= week_ago,
            )
        ).scalar() or 0

        feed.progress = WeeklyProgress(
            applications_this_week=wk_apps,
            weekly_target=25,
            interviews_scheduled=wk_interviews_scheduled,
        )

        # Today's recommendation
        if feed.top_opportunities:
            best = feed.top_opportunities[0]
            feed.today_recommendation = (
                f"Apply to {best.company} ({best.title}) first. "
                f"Score: {best.score}/100."
            )
        elif feed.follow_ups:
            fup = feed.follow_ups[0]
            feed.today_recommendation = (
                f"Send follow-up to {fup.company} ({fup.days_ago}d since application)."
            )
        else:
            feed.today_recommendation = "Run `jobzo sync` to discover new opportunities."

    finally:
        session.close()

    return feed
