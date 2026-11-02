"""Hiring weight — derived priority for each company/channel.

Weight is computed from operational data, not manually configured.
Higher weight = more frequent polling, higher ranking priority.

Factors:
  - Jobs found per poll (recent 14 days)
  - Poll success rate (recent 14 days)
  - Fresher/graduate eligibility rate
  - Recency of hiring activity
"""

from collections import defaultdict
from datetime import datetime, timedelta

from database.connection import get_session
from database.models import Company, HiringChannel, PollRun, Job
from sqlalchemy import func


def compute_company_weight(company_id: str, days: int = 14) -> float:
    """Compute hiring weight for a company. Returns 0.0–10.0."""
    session = get_session()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days)

        # Recent polls
        polls = (
            session.query(PollRun)
            .filter(
                PollRun.company_id == company_id,
                PollRun.finished_at >= cutoff,
            )
            .all()
        )

        total_polls = len(polls)
        if total_polls == 0:
            return 0.5  # default minimum

        successful = sum(1 for p in polls if p.status == "success")
        total_jobs = sum(p.jobs_new for p in polls)

        # Success rate (0-1)
        success_rate = successful / max(total_polls, 1)

        # Average jobs per successful poll
        avg_jobs = total_jobs / max(successful, 1)

        # Recency: more recent activity = higher weight
        latest = max((p.finished_at for p in polls if p.finished_at), default=None)
        recency_bonus = 0
        if latest:
            hours_since = (datetime.utcnow() - latest).total_seconds() / 3600
            if hours_since < 24:
                recency_bonus = 2.0
            elif hours_since < 72:
                recency_bonus = 1.0
            elif hours_since < 168:
                recency_bonus = 0.5

        # Combine: baseline 1.0 + jobs bonus + recency bonus, capped at 10.0
        jobs_factor = min(avg_jobs / 2.0, 5.0)
        weight = 1.0 + jobs_factor + recency_bonus
        if success_rate < 0.3:
            weight *= 0.5

        return round(max(0.5, min(10.0, weight)), 1)

    finally:
        session.close()


def compute_channel_weight(company_id: str, channel_name: str, days: int = 14) -> float:
    """Compute weight for a specific hiring channel."""
    session = get_session()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days)

        polls = (
            session.query(PollRun)
            .filter(
                PollRun.company_id == company_id,
                PollRun.parser == channel_name,
                PollRun.finished_at >= cutoff,
            )
            .all()
        )

        if not polls:
            return 0.5

        successful = sum(1 for p in polls if p.status == "success")
        total_jobs = sum(p.jobs_new for p in polls)
        avg_jobs = total_jobs / max(successful, 1)
        success_rate = successful / len(polls)

        weight = 1.0 + min(avg_jobs / 2.0, 5.0)
        if success_rate < 0.3:
            weight *= 0.5

        return round(max(0.5, min(10.0, weight)), 1)

    finally:
        session.close()


def poll_interval_from_weight(weight: float, base_hours: int = 24) -> int:
    """Map hiring weight to poll interval in hours.

    Higher weight = shorter interval.
    """
    if weight >= 8:
        return max(1, base_hours // 6)  # every 4h
    elif weight >= 6:
        return max(2, base_hours // 4)  # every 6h
    elif weight >= 4:
        return max(4, base_hours // 3)  # every 8h
    elif weight >= 2:
        return base_hours // 2  # every 12h
    else:
        return base_hours  # every 24h


def weight_label(weight: float) -> str:
    if weight >= 8:
        return "🔴 High"
    elif weight >= 5:
        return "🟡 Medium"
    elif weight >= 2:
        return "🟢 Low"
    else:
        return "⚪ Minimal"


def rank_by_weight(limit: int = 20) -> list[dict]:
    """Rank companies by derived hiring weight for prioritization."""
    session = get_session()
    try:
        active = session.query(Company).filter(Company.is_active == True).all()
        ranked = []
        for c in active:
            w = compute_company_weight(c.id)
            ranked.append({
                "id": c.id,
                "name": c.name,
                "category": c.primary_category,
                "weight": w,
                "interval_h": poll_interval_from_weight(w),
                "label": weight_label(w),
            })
        ranked.sort(key=lambda x: -x["weight"])
        return ranked[:limit]
    finally:
        session.close()
