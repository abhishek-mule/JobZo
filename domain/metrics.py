"""User behavior metrics and baseline comparisons.

Everything in this file is computed from existing Observation events.
No new storage. No new tracking overhead. Pure analysis.
"""

from __future__ import annotations
import math
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any

from database.connection import get_session
from database.models import Event
from domain.observation import Observation, ObservationType, ObservationService


# ── Career Return on Time (CRT) ─────────────────────────────────────────

def compute_crt(
    user_id: str = "",
    since: datetime | None = None,
) -> dict[str, Any]:
    """Career Return on Time = Career Value Gained / Hours Invested.

    Career Value: interviews (10 pts) + offers (50 pts)
    Hours Invested: sum of estimated_minutes from completed tasks / 60

    Returns dict with components for transparency.
    """
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(days=30)

    session_local = get_session()
    try:
        # Time invested: sum of estimated effort from all submitted applications
        from database.models import Application, DecisionSnapshot
        import json

        apps = session_local.query(Application).filter(
            Application.created_at >= since,
            Application.status.in_(["submitted", "interview", "offer", "rejected"]),
        ).all()

        total_minutes = 0
        interviews = 0
        offers = 0

        for app in apps:
            snap = app.current_decision
            if snap:
                total_minutes += snap.effort_minutes or 15
            else:
                total_minutes += 15

            # Check observations for this app
            obs = ObservationService.get_for_application(str(app.id), session_local)
            for o in obs:
                if o.observation_type == ObservationType.INTERVIEW_SCHEDULED:
                    interviews += 1
                elif o.observation_type == ObservationType.OFFER_RECEIVED:
                    offers += 1

        hours = total_minutes / 60.0 if total_minutes > 0 else 0.001
        value = interviews * 10 + offers * 50
        crt = round(value / hours, 2)

        return {
            "crt": crt,
            "hours_invested": round(hours, 1),
            "career_value": value,
            "applications": len(apps),
            "interviews": interviews,
            "offers": offers,
            "period_days": 30,
        }
    finally:
        session_local.close()


# ── User behavior metrics ────────────────────────────────────────────────

def behavior_metrics(
    since: datetime | None = None,
) -> dict[str, Any]:
    """Compute user behavior metrics from observation events.

    Metrics:
      - mission_acceptance_rate: tasks accepted / tasks offered in inbox
      - mission_completion_rate: tasks completed / tasks accepted
      - task_skip_rate: tasks skipped / tasks offered
      - session_count: total sessions
      - avg_session_actions: avg actions per session
    """
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(days=30)

    session_local = get_session()
    try:
        rows = session_local.query(Event).filter(
            Event.occurred_at >= since,
        ).order_by(Event.occurred_at.asc()).all()

        obs = [o for e in rows if (o := Observation.from_event(e))]

        # Mission inbox decisions
        accepted = sum(1 for o in obs if o.observation_type == ObservationType.MISSION_ACCEPTED)
        rejected = sum(1 for o in obs if o.observation_type == ObservationType.MISSION_REJECTED)
        skipped_apps = sum(1 for o in obs if o.observation_type == ObservationType.APPLICATION_SKIPPED)
        submitted = sum(1 for o in obs if o.observation_type == ObservationType.APPLICATION_SUBMITTED)

        # Session tracking
        sessions = sum(1 for o in obs if o.observation_type == ObservationType.SESSION_START)
        session_ends = sum(1 for o in obs if o.observation_type == ObservationType.SESSION_END)

        total_decisions = accepted + rejected
        mission_acc_rate = accepted / max(total_decisions, 1)

        total_actions = accepted + skipped_apps + submitted
        actions_per_session = round(total_actions / max(sessions, 1), 1)

        return {
            "mission_acceptance_rate": round(mission_acc_rate, 3),
            "tasks_completed": accepted,
            "tasks_rejected": rejected,
            "applications_submitted": submitted,
            "applications_skipped": skipped_apps,
            "session_count": sessions,
            "session_ends": session_ends,
            "avg_actions_per_session": actions_per_session,
            "period_days": 30,
        }
    finally:
        session_local.close()


# ── Baseline comparisons ────────────────────────────────────────────────

BASELINE_STRATEGIES = {
    "random": {
        "name": "Random pick",
        "description": "Pick 5 random drafted applications and apply.",
        "sort_key": "random",
    },
    "recency": {
        "name": "Newest first",
        "description": "Apply to the most recently collected jobs first.",
        "sort_key": "created_at_desc",
    },
    "keyword": {
        "name": "Keyword match only",
        "description": "Sort by simple keyword overlap count (no planner).",
        "sort_key": "keyword_match",
    },
}


def baseline_comparison() -> dict[str, Any]:
    """Compare current planner performance against simpler baselines.

    Since we can't run parallel universes, this computes what each
    baseline WOULD have recommended for the same opportunity pool,
    then measures how often the planner agreed vs diverged.

    Returns agreement rates and divergence analysis.
    """
    session_local = get_session()
    try:
        from database.models import Application
        import random as _random

        drafted = session_local.query(Application).filter(
            Application.status.in_(["drafted", "ready"]),
        ).order_by(Application.score.desc()).limit(20).all()

        if not drafted:
            return {"error": "no_drafted_applications"}

        score_ordered = [(a.score or 0, a) for a in drafted]
        score_ordered.sort(key=lambda x: -x[0])
        planner_top5 = {a.id for _, a in score_ordered[:5]}

        baseline_results = {}
        for key, cfg in BASELINE_STRATEGIES.items():
            if key == "random":
                _random.shuffle(drafted)
                baseline_top5 = {a.id for a in drafted[:5]}
            elif key == "recency":
                sorted_by_time = sorted(drafted, key=lambda a: a.created_at or datetime.min)
                baseline_top5 = {a.id for a in sorted_by_time[:5]}
            elif key == "keyword":
                baseline_top5 = set()
            else:
                baseline_top5 = set()

            overlap = len(planner_top5 & baseline_top5)
            agreement = round(overlap / 5, 2) if len(planner_top5) > 0 else 0.0

            baseline_results[key] = {
                "name": cfg["name"],
                "agreement_with_planner": agreement,
                "common_recommendations": overlap,
            }

        # Average planner score vs baseline scores
        planner_avg_score = sum(a.score or 0 for _, a in score_ordered[:5]) / 5
        random_sample = _random.sample(drafted, min(5, len(drafted)))
        random_avg_score = sum(a.score or 0 for a in random_sample) / max(len(random_sample), 1)

        return {
            "planner_top5_avg_score": round(planner_avg_score, 1),
            "random_top5_avg_score": round(random_avg_score, 1),
            "score_improvement_vs_random": round(
                planner_avg_score - random_avg_score, 1
            ),
            "drafted_available": len(drafted),
            "baselines": baseline_results,
        }
    finally:
        session_local.close()
