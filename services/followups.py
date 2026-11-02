"""Follow-up intelligence — never let a live application go silent.

Schedule (business days after submission):
  3   status check     inspect the portal; no email yet
  6   recruiter ping   only with a verified, hiring-relevant contact
  12  final follow-up  polite close-the-loop; only with a verified contact

Sending remains approval-based. A status check is a reminder, not an email.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from database.connection import get_session
from database.models import Application, Contact, FollowUp, Job, OutreachDraft

FOLLOWUP_PLAN = (
    (3, "status_check", "Check the application portal for a status update"),
    (6, "recruiter_followup", "Follow up with the verified recruiter"),
    (12, "final_followup", "Send a final, polite follow-up"),
)

TERMINAL_STATUSES = {"rejected", "offer", "accepted", "declined", "interview"}


@dataclass
class FollowUpItem:
    followup: FollowUp
    application: Application | None = None
    job: Job | None = None
    contact: Contact | None = None

    @property
    def is_overdue(self) -> bool:
        return self.followup.due_at <= datetime.utcnow()

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.followup.id,
            "application_id": self.followup.application_id,
            "contact_id": self.followup.contact_id,
            "outreach_draft_id": self.followup.outreach_draft_id,
            "kind": self.followup.kind,
            "due_at": self.followup.due_at,
            "status": self.followup.status,
            "reason": self.followup.reason,
            "company": self.job.company if self.job else "",
            "role": self.job.title if self.job else "",
            "contact_name": self.contact.name if self.contact else "",
            "contact_email": self.contact.email if self.contact else "",
            "app_status": self.application.status if self.application else "",
        }


def _naive_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.utcnow()
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def add_business_days(start: datetime, days: int) -> datetime:
    """Add N business days (Mon-Fri), skipping weekends."""
    current = _naive_utc(start)
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def ensure_followups(application_id: str, now: datetime | None = None) -> list[dict[str, Any]]:
    """Idempotently create the follow-up schedule for a submitted application."""
    from services.contacts import best_contact_for

    now = now or datetime.utcnow()
    session = get_session()
    try:
        app = session.query(Application).options(
            joinedload(Application.job)
        ).filter(Application.id == application_id).first()
        if app is None or app.job is None:
            return []

        if app.status in TERMINAL_STATUSES:
            _cancel_pending(session, app.id, now)
            session.commit()
            return []

        if app.status != "submitted":
            return []

        contact = None
        try:
            record = best_contact_for(app.job.company, app.job.title, allow_unverified=False)
            contact = record.contact if record else None
        except Exception:
            contact = None

        base = _naive_utc(app.applied_at or app.created_at or now)
        created = []
        for offset, kind, reason in FOLLOWUP_PLAN:
            dedupe_key = f"followup:{app.id}:{kind}"
            existing = session.query(FollowUp).filter(
                FollowUp.dedupe_key == dedupe_key
            ).first()
            if existing is not None:
                # A contact discovered later can unlock a previously skipped email.
                if existing.contact_id is None and contact is not None and kind != "status_check":
                    existing.contact_id = contact.id
                created.append(_item_dict(existing, app))
                continue
            if kind != "status_check" and contact is None:
                continue

            due_at = add_business_days(base, offset)
            status = "due" if due_at <= now else "scheduled"
            followup = FollowUp(
                application_id=app.id,
                contact_id=contact.id if contact else None,
                kind=kind,
                due_at=due_at,
                status=status,
                reason=reason,
                dedupe_key=dedupe_key,
            )
            session.add(followup)
            session.flush()
            created.append(_item_dict(followup, app))

        session.commit()
        return created
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _item_dict(followup: FollowUp, app: Application | None = None) -> dict[str, Any]:
    return {
        "id": followup.id,
        "application_id": followup.application_id,
        "kind": followup.kind,
        "due_at": followup.due_at,
        "status": followup.status,
        "reason": followup.reason,
        "company": app.job.company if app and app.job else "",
        "role": app.job.title if app and app.job else "",
    }


def _cancel_pending(session, application_id: str, now: datetime) -> int:
    rows = session.query(FollowUp).filter(
        FollowUp.application_id == application_id,
        FollowUp.status.in_(("scheduled", "due", "drafted")),
    ).all()
    for row in rows:
        row.status = "cancelled"
        row.completed_at = now
    return len(rows)


def cancel_followups(application_id: str) -> int:
    session = get_session()
    try:
        count = _cancel_pending(session, application_id, datetime.utcnow())
        session.commit()
        return count
    except Exception:
        session.rollback()
        return 0
    finally:
        session.close()


def refresh_due(now: datetime | None = None) -> int:
    """Move scheduled follow-ups into due when their date arrives."""
    now = now or datetime.utcnow()
    session = get_session()
    try:
        rows = session.query(FollowUp).filter(
            FollowUp.status == "scheduled",
            FollowUp.due_at <= now,
        ).all()
        for row in rows:
            row.status = "due"
        session.commit()
        return len(rows)
    except Exception:
        session.rollback()
        return 0
    finally:
        session.close()


def backfill_followups(now: datetime | None = None) -> int:
    """Schedule cadences for submitted applications created before this feature.

    Applications that already have the full plan are left untouched; the
    per-application ensure_followups() call is idempotent.
    """
    now = now or datetime.utcnow()
    session = get_session()
    try:
        incomplete = session.query(Application.id).outerjoin(
            FollowUp, FollowUp.application_id == Application.id
        ).filter(
            Application.status == "submitted",
        ).group_by(Application.id).having(
            func.count(FollowUp.id) < len(FOLLOWUP_PLAN)
        ).all()
    finally:
        session.close()

    for (application_id,) in incomplete:
        ensure_followups(application_id, now)
    return len(incomplete)


def list_followups(*, status: str = "", application_id: str = "",
                   due_only: bool = False, now: datetime | None = None,
                   limit: int = 100) -> list[dict[str, Any]]:
    now = now or datetime.utcnow()
    backfill_followups(now)
    refresh_due(now)
    session = get_session()
    try:
        query = session.query(FollowUp)
        if status:
            query = query.filter(FollowUp.status == status)
        if application_id:
            query = query.filter(FollowUp.application_id == application_id)
        if due_only:
            query = query.filter(
                FollowUp.status.in_(("scheduled", "due")),
                FollowUp.due_at <= now,
            )
        rows = query.order_by(FollowUp.due_at.asc()).limit(limit).all()

        out = []
        for row in rows:
            app = session.query(Application).options(
                joinedload(Application.job)
            ).filter(Application.id == row.application_id).first()
            contact = session.get(Contact, row.contact_id) if row.contact_id else None
            item = FollowUpItem(
                followup=row,
                application=app,
                job=app.job if app else None,
                contact=contact,
            )
            out.append(item.as_dict())
        return out
    finally:
        session.close()


def due_followups(now: datetime | None = None) -> list[dict[str, Any]]:
    return list_followups(due_only=True, now=now)


def draft_followup(followup_id: str) -> dict[str, Any]:
    """Prepare an email draft for a recruiter/final follow-up.

    status_check has no recipient by design: it is a manual portal check.
    """
    session = get_session()
    try:
        followup = session.query(FollowUp).filter(FollowUp.id == followup_id).first()
        if followup is None:
            return {"ok": False, "error": "follow-up not found"}
        if followup.status in ("sent", "done", "skipped", "cancelled"):
            return {"ok": False, "error": f"follow-up is {followup.status}"}

        if followup.kind == "status_check":
            return {
                "ok": True,
                "needs_manual": True,
                "message": "Open the application portal and check the status. No email is due yet.",
                "followup_id": followup.id,
            }

        contact_id = followup.contact_id
        if not contact_id:
            app = session.query(Application).options(
                joinedload(Application.job)
            ).filter(Application.id == followup.application_id).first()
            if app is None or app.job is None:
                return {"ok": False, "error": "application not found"}
            from services.contacts import best_contact_for
            record = best_contact_for(app.job.company, app.job.title, allow_unverified=False)
            if record is None:
                return {"ok": False, "error": "no verified contact — add one with evidence first"}
            contact_id = record.contact.id
            followup.contact_id = contact_id
            session.commit()

        from services.cold_outreach import draft_outreach
        result = draft_outreach(
            application_id=followup.application_id,
            contact_id=contact_id,
            kind=followup.kind,
            followup_id=followup.id,
        )
        if not result.ok:
            return {"ok": False, "error": result.error}

        if result.draft and result.created:
            followup.outreach_draft_id = result.draft["id"]
            followup.status = "drafted"
            session.commit()
        return {
            "ok": True,
            "needs_manual": False,
            "draft": result.draft,
            "approval": result.approval,
            "created": result.created,
        }
    except Exception as exc:
        session.rollback()
        return {"ok": False, "error": str(exc)}
    finally:
        session.close()


def mark_followup_sent(followup_id: str) -> bool:
    session = get_session()
    try:
        row = session.query(FollowUp).filter(FollowUp.id == followup_id).first()
        if row is None:
            return False
        row.status = "sent"
        row.completed_at = datetime.utcnow()
        session.commit()
        return True
    except Exception:
        session.rollback()
        return False
    finally:
        session.close()


def complete_followup(followup_id: str, status: str = "done") -> bool:
    if status not in ("done", "skipped", "cancelled"):
        return False
    session = get_session()
    try:
        row = session.query(FollowUp).filter(FollowUp.id == followup_id).first()
        if row is None:
            return False
        row.status = status
        row.completed_at = datetime.utcnow()
        session.commit()
        return True
    except Exception:
        session.rollback()
        return False
    finally:
        session.close()


def followup_summary(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.utcnow()
    refresh_due(now)
    session = get_session()
    try:
        due = session.query(FollowUp).filter(
            FollowUp.status == "due",
            FollowUp.due_at <= now,
        ).count()
        scheduled = session.query(FollowUp).filter(
            FollowUp.status == "scheduled"
        ).count()
        drafted = session.query(FollowUp).filter(
            FollowUp.status == "drafted"
        ).count()
        sent = session.query(FollowUp).filter(
            FollowUp.status == "sent"
        ).count()
        return {"due": due, "scheduled": scheduled, "drafted": drafted, "sent": sent}
    finally:
        session.close()
