"""Outreach Intelligence — template analytics, reply tracking, and timing analysis.

Phase 4C of the Career OS.
"""

from __future__ import annotations
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, case
from sqlalchemy.orm import Session

from database.models import Contact, Interaction
from database.connection import get_session


@dataclass
class OutreachStats:
    total_sent: int = 0
    total_replied: int = 0
    total_meetings: int = 0
    total_ignored: int = 0
    reply_rate: float = 0.0
    meeting_rate: float = 0.0
    unique_companies: int = 0
    unique_contacts: int = 0
    top_subjects: list[tuple[str, int]] = field(default_factory=list)
    top_companies: list[tuple[str, int, float]] = field(default_factory=list)

    def format_text(self) -> str:
        lines = [
            f"  Outreach Summary",
            f"  Emails Sent:    {self.total_sent}",
            f"  Replies:        {self.total_replied}  ({self.reply_rate:.0f}%)",
            f"  Meetings:       {self.total_meetings}  ({self.meeting_rate:.0f}%)",
            f"  Ignored:        {self.total_ignored}",
            f"  Companies:      {self.unique_companies}",
            f"  Contacts:       {self.unique_contacts}",
        ]
        if self.top_subjects:
            lines.append(f"  Best Subjects")
            for subj, count in self.top_subjects[:5]:
                lines.append(f"    {count}x  {subj[:60]}")
        if self.top_companies:
            lines.append(f"  Most Responsive Companies")
            for company, replies, rate in self.top_companies[:5]:
                lines.append(f"    {company:20s}  {replies} replies ({rate:.0f}%)")
        return "\n".join(lines)


def outreach_summary() -> OutreachStats:
    session: Session = get_session()
    try:
        sent = session.query(func.count(Interaction.id)).filter(
            Interaction.direction == "outbound",
        ).scalar() or 0
        replied = session.query(func.count(Interaction.id)).filter(
            Interaction.outcome == "replied",
        ).scalar() or 0
        meetings = session.query(func.count(Interaction.id)).filter(
            Interaction.outcome == "meeting_scheduled",
        ).scalar() or 0
        ignored = session.query(func.count(Interaction.id)).filter(
            Interaction.outcome == "ignored",
        ).scalar() or 0

        companies = session.query(func.count(func.distinct(Contact.company))).filter(
            Contact.id == Interaction.contact_id,
            Interaction.direction == "outbound",
        ).scalar() or 0
        contacts = session.query(func.count(func.distinct(Interaction.contact_id))).filter(
            Interaction.direction == "outbound",
        ).scalar() or 0

        # Top subjects by reply count
        subject_rows = session.query(
            Interaction.subject,
            func.count(Interaction.id).label("cnt"),
        ).filter(
            Interaction.direction == "outbound",
            Interaction.outcome == "replied",
            Interaction.subject != "",
        ).group_by(Interaction.subject).order_by(func.count(Interaction.id).desc()).limit(10).all()
        top_subjects = [(r.subject, r.cnt) for r in subject_rows]

        # Per-company reply rates
        company_rows = session.query(
            Contact.company,
            func.count(Interaction.id).label("total"),
            func.sum(
                case((Interaction.outcome == "replied", 1), else_=0)
            ).label("replies"),
        ).join(Contact, Interaction.contact_id == Contact.id).filter(
            Interaction.direction == "outbound",
        ).group_by(Contact.company).having(
            func.count(Interaction.id) >= 1
        ).order_by(
            func.sum(
                case((Interaction.outcome == "replied", 1), else_=0)
            ).desc()
        ).limit(10).all()
        top_companies = [
            (row.company, row.replies or 0, (row.replies or 0) / row.total * 100)
            for row in company_rows
        ]

        total_responded = replied + meetings + ignored
        reply_rate = replied / sent * 100 if sent else 0
        meeting_rate = meetings / sent * 100 if sent else 0

        return OutreachStats(
            total_sent=sent,
            total_replied=replied,
            total_meetings=meetings,
            total_ignored=ignored,
            reply_rate=reply_rate,
            meeting_rate=meeting_rate,
            unique_companies=companies,
            unique_contacts=contacts,
            top_subjects=top_subjects,
            top_companies=top_companies,
        )
    finally:
        session.close()


def template_performance() -> list[dict[str, Any]]:
    session: Session = get_session()
    try:
        rows = session.query(
            Interaction.subject,
            func.count(Interaction.id).label("sent"),
            func.sum(
                case((Interaction.outcome == "replied", 1), else_=0)
            ).label("replied"),
            func.sum(
                case((Interaction.outcome == "meeting_scheduled", 1), else_=0)
            ).label("meetings"),
        ).filter(
            Interaction.direction == "outbound",
            Interaction.subject != "",
        ).group_by(Interaction.subject).order_by(
            func.count(Interaction.id).desc()
        ).all()

        results = []
        for r in rows:
            total = r.sent or 0
            replies = r.replied or 0
            meetings = r.meetings or 0
            results.append({
                "subject": r.subject,
                "sent": total,
                "replied": replies,
                "meetings": meetings,
                "reply_rate": replies / total * 100 if total else 0,
            })
        return results
    finally:
        session.close()


def company_responsiveness() -> list[dict[str, Any]]:
    session: Session = get_session()
    try:
        rows = session.query(
            Contact.company,
            func.count(Interaction.id).label("sent"),
            func.sum(
                case((Interaction.outcome == "replied", 1), else_=0)
            ).label("replied"),
            func.sum(
                case((Interaction.outcome == "meeting_scheduled", 1), else_=0)
            ).label("meetings"),
        ).join(Contact, Interaction.contact_id == Contact.id).filter(
            Interaction.direction == "outbound",
        ).group_by(Contact.company).having(
            func.count(Interaction.id) >= 1
        ).order_by(
            func.sum(
                case((Interaction.outcome == "replied", 1), else_=0)
            ).desc()
        ).all()

        results = []
        for r in rows:
            total = r.sent or 0
            replies = r.replied or 0
            meetings = r.meetings or 0
            results.append({
                "company": r.company,
                "sent": total,
                "replied": replies,
                "meetings": meetings,
                "reply_rate": replies / total * 100 if total else 0,
            })
        return results
    finally:
        session.close()


def best_contact_time() -> dict[str, Any]:
    """Analyze what days/times get the most replies."""
    session: Session = get_session()
    try:
        rows = session.query(Interaction).filter(
            Interaction.outcome == "replied",
            Interaction.occurred_at.isnot(None),
        ).all()

        day_counts: Counter[str] = Counter()
        hour_counts: Counter[int] = Counter()

        for r in rows:
            dt = r.occurred_at
            day_counts[dt.strftime("%A")] += 1
            hour_counts[dt.hour] += 1

        best_day = day_counts.most_common(1)[0] if day_counts else ("", 0)
        best_hour = hour_counts.most_common(1)[0] if hour_counts else (0, 0)

        return {
            "total_replies_analyzed": len(rows),
            "best_day": best_day[0],
            "best_day_count": best_day[1],
            "best_hour": best_hour[0],
            "best_hour_count": best_hour[1],
            "day_distribution": day_counts.most_common(),
            "hour_distribution": sorted(hour_counts.items()),
        }
    finally:
        session.close()
