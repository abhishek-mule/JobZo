"""Daily briefing — the 15-minute decision surface.

One screen answers:
  * what is worth applying to today?
  * what is waiting for my approval?
  * which recruiters/follow-ups are due?
  * what did JobZo reject automatically, and why?
  * what is the single highest-leverage next action?
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func

from database.connection import get_session
from database.models import (
    Application,
    ApprovalRequest,
    Contact,
    ContactVerification,
    FollowUp,
    Job,
    OutreachDraft,
)
from services.approval import (
    KIND_APPLICATION,
    KIND_FOLLOWUP,
    KIND_OUTREACH,
    PENDING,
    REJECTED,
    list_requests,
    pending_summary,
)
from services.followups import due_followups, followup_summary


def _start_of_today(now: datetime) -> datetime:
    return datetime(now.year, now.month, now.day)


def build_brief(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.utcnow()
    today = _start_of_today(now)
    week_ago = now - timedelta(days=7)

    session = get_session()
    try:
        new_jobs = session.query(func.count(Job.id)).filter(Job.created_at >= today).scalar() or 0
        eligible_new = session.query(func.count(Job.id)).filter(
            Job.created_at >= today,
            Job.eligible == True,
        ).scalar() or 0
        high_match = session.query(func.count(Application.id)).filter(
            Application.status.in_(("drafted", "ready")),
            Application.score >= 70,
        ).scalar() or 0

        pipeline = {
            "discovered": session.query(func.count(Job.id)).scalar() or 0,
            "shortlisted": session.query(func.count(Application.id)).filter(
                Application.status.in_(("drafted", "ready")),
                Application.score >= 55,
            ).scalar() or 0,
            "approved": session.query(func.count(Application.id)).filter(
                Application.status == "ready"
            ).scalar() or 0,
            "applied": session.query(func.count(Application.id)).filter(
                Application.status.in_(("submitted", "interview", "offer", "accepted", "declined", "rejected"))
            ).scalar() or 0,
            "interview": session.query(func.count(Application.id)).filter(
                Application.status.in_(("interview", "offer", "accepted"))
            ).scalar() or 0,
        }

        total_contacts = session.query(func.count(Contact.id)).scalar() or 0
        verified_contacts = session.query(func.count(ContactVerification.id)).filter(
            ContactVerification.status.in_(("verified", "verified_domain"))
        ).scalar() or 0
        pending_drafts = session.query(func.count(OutreachDraft.id)).filter(
            OutreachDraft.status.in_(("pending_approval", "approved", "outbox"))
        ).scalar() or 0

        week_apps = session.query(func.count(Application.id)).filter(
            Application.status.in_(("submitted", "interview", "offer", "rejected")),
            Application.applied_at >= week_ago,
        ).scalar() or 0
        week_interviews = session.query(func.count(Application.id)).filter(
            Application.status.in_(("interview", "offer")),
            Application.applied_at >= week_ago,
        ).scalar() or 0
    finally:
        session.close()

    approvals = pending_summary()
    followups = followup_summary(now)
    due = due_followups(now)

    # Best opportunities: pending approvals first, then the current dig top 5.
    best = []
    for req in list_requests(PENDING, kind=KIND_APPLICATION, limit=8):
        p = req.get("payload", {})
        best.append({
            "source": "approval",
            "request_id": req["id"],
            "company": p.get("company", ""),
            "role": p.get("role", ""),
            "score": req.get("opportunity_score") or p.get("match_score") or 0,
            "trust": req.get("trust_score"),
            "effort": req.get("effort_minutes"),
            "kind": "application",
        })

    try:
        from services.dig import dig_jobs
        pending_app_ids = {r.get("application_id") for r in list_requests(PENDING, kind=KIND_APPLICATION)}
        dig = dig_jobs(fresh_only=False, strict=False, min_priority=4.0, local_only=False)
        for item in dig.items:
            if item.app.id in pending_app_ids:
                continue
            best.append({
                "source": "dig",
                "application_id": str(item.app.id),
                "company": item.job.company,
                "role": item.job.title,
                "score": round(item.priority * 10, 1),
                "trust": item.trust.score,
                "effort": item.expected_value.get("effort_minutes") if item.expected_value else None,
                "kind": "candidate",
            })
            if len(best) >= 10:
                break
    except Exception:
        pass
    best.sort(key=lambda x: -(x.get("score") or 0))

    # Only hard-reject requests are "auto-rejected"; manual rejections carry
    # a normal risk level and must not inflate the filtering count.
    rejected = [
        r for r in list_requests(REJECTED, limit=100)
        if r.get("risk_level") == "reject"
    ][:30]
    reasons = Counter((r.get("decision_note") or "unspecified").split(";")[0].strip()
                      for r in rejected)
    auto_rejected = {
        "count": len(rejected),
        "top_reasons": [{"reason": r, "count": c} for r, c in reasons.most_common(5)],
        "examples": [
            {"title": r.get("title", ""), "reason": r.get("decision_note", "")}
            for r in rejected[:3]
        ],
    }

    recommendation = _recommendation(
        approvals=approvals,
        followups=followups,
        high_match=high_match,
        best=best,
        due=due,
    )

    return {
        "date": now.strftime("%A, %b %d"),
        "counts": {
            "new_jobs": new_jobs,
            "eligible_new": eligible_new,
            "high_match": high_match,
            "pending_approvals": approvals["count"],
            "pending_applications": approvals["applications"],
            "pending_outreach": approvals["outreach"],
            "pending_followups": approvals["followups"],
            "followups_due": followups["due"],
            "verified_contacts": verified_contacts,
            "total_contacts": total_contacts,
            "pending_drafts": pending_drafts,
        },
        "pipeline": pipeline,
        "approvals": approvals,
        "followups": {**followups, "due_items": due[:5]},
        "outreach": {
            "contacts": total_contacts,
            "verified_contacts": verified_contacts,
            "drafts_pending": pending_drafts,
            "followups_due": followups["due"],
        },
        "best_opportunities": best[:5],
        "auto_rejected": auto_rejected,
        "week": {"applications": week_apps, "interviews": week_interviews},
        "recommendation": recommendation,
    }


def _recommendation(*, approvals: dict, followups: dict, high_match: int,
                    best: list[dict], due: list[dict]) -> dict[str, Any]:
    if approvals.get("applications", 0):
        minutes = approvals.get("minutes") or approvals["applications"] * 10
        return {
            "action": "approve",
            "text": f"Approve {min(3, approvals['applications'])} application(s) first — ~{minutes} min.",
            "why": "Approval is the only step blocking submission.",
        }
    if due:
        return {
            "action": "followup",
            "text": f"Handle {len(due)} due follow-up(s) — ~{len(due) * 4} min.",
            "why": "Silence is not a rejection. A short follow-up recovers live opportunities.",
        }
    if approvals.get("outreach", 0):
        return {
            "action": "outreach",
            "text": f"Review {approvals['outreach']} cold-email draft(s) — ~4 min each.",
            "why": "Direct recruiter contact is the highest-leverage non-application action.",
        }
    if high_match:
        return {
            "action": "queue",
            "text": f"Queue your top {min(5, high_match)} high-match job(s) for review.",
            "why": "High-match jobs are sitting unprocessed.",
        }
    if best:
        return {
            "action": "queue",
            "text": "Queue the top opportunities and approve the best one.",
            "why": "There are scored opportunities waiting for a decision.",
        }
    return {
        "action": "improve",
        "text": "No urgent actions. Review skill gaps or add a verified recruiter contact.",
        "why": "Use the quiet period to improve future interview probability.",
    }


def format_brief_text(brief: dict[str, Any]) -> str:
    c = brief["counts"]
    p = brief["pipeline"]
    lines = [
        f"JOBZO — {brief['date']}",
        "",
        f"New jobs {c['new_jobs']}   High match {c['high_match']}   "
        f"Pending review {c['pending_approvals']}",
        "",
        "APPLICATION PIPELINE",
        f"Discovered {p['discovered']} → Shortlisted {p['shortlisted']} → "
        f"Approved {p['approved']} → Applied {p['applied']} → Interview {p['interview']}",
        "",
        "BEST OPPORTUNITIES",
    ]
    if brief["best_opportunities"]:
        for i, item in enumerate(brief["best_opportunities"], 1):
            effort = f"{item.get('effort')} min" if item.get("effort") else "—"
            lines.append(
                f"  {i}. {item['company']} — {item['role']}  |  "
                f"{item.get('score', 0):.0f} score  |  {effort}"
            )
    else:
        lines.append("  No scored opportunities yet. Run: jobzo collect && jobzo rank")

    lines.extend([
        "",
        "OUTREACH",
        f"Recruiters found {c['total_contacts']} (verified {c['verified_contacts']})",
        f"Drafts pending approval {c['pending_drafts']}",
        f"Follow-ups due {c['followups_due']}",
        "",
        "AUTO-REJECTED",
        f"{brief['auto_rejected']['count']} jobs were filtered out",
    ])
    for reason in brief["auto_rejected"]["top_reasons"][:3]:
        lines.append(f"  • {reason['count']}× {reason['reason'][:90]}")

    rec = brief["recommendation"]
    lines.extend([
        "",
        f"NEXT: {rec['text']}",
        f"Why:  {rec['why']}",
    ])
    return "\n".join(lines)
