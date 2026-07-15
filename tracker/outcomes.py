"""Outcome Collector — structured tracking of every application's lifecycle.

Phase 4D — Stage 1 (Decision Intelligence).
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from database.models import Application, ApplicationOutcome
from database.connection import get_session

logger = logging.getLogger("jobzo.outcomes")


def get_or_create_outcome(application_id: str) -> ApplicationOutcome:
    session: Session = get_session()
    try:
        outcome = session.query(ApplicationOutcome).filter(
            ApplicationOutcome.application_id == application_id
        ).first()
        if outcome:
            return outcome
        app = session.query(Application).filter(Application.id == application_id).first()
        if not app:
            raise ValueError(f"Application {application_id} not found")
        job = app.job
        outcome = ApplicationOutcome(
            application_id=application_id,
            resume_used=app.resume_used or "",
            company=job.company if job else "",
            role=job.title if job else "",
            ats="",
            applied_at=app.applied_at,
        )
        session.add(outcome)
        session.commit()
        session.refresh(outcome)
        return outcome
    finally:
        session.close()


def update_outcome(application_id: str, **kwargs: Any) -> dict[str, Any]:
    session: Session = get_session()
    try:
        outcome = session.query(ApplicationOutcome).filter(
            ApplicationOutcome.application_id == application_id
        ).first()
        if not outcome:
            raise ValueError(f"No outcome record for {application_id}")

        for key, value in kwargs.items():
            if hasattr(outcome, key):
                setattr(outcome, key, value)

        session.commit()
        return {"status": "updated", "application_id": application_id}
    except Exception as e:
        session.rollback()
        return {"error": str(e)}
    finally:
        session.close()


def get_outcome(application_id: str) -> dict[str, Any] | None:
    session: Session = get_session()
    try:
        outcome = session.query(ApplicationOutcome).filter(
            ApplicationOutcome.application_id == application_id
        ).first()
        if not outcome:
            return None
        return {
            "id": outcome.id,
            "application_id": outcome.application_id,
            "resume_used": outcome.resume_used,
            "company": outcome.company,
            "role": outcome.role,
            "ats": outcome.ats,
            "applied_at": outcome.applied_at,
            "viewed_at": outcome.viewed_at,
            "oa_at": outcome.oa_at,
            "interview_at": outcome.interview_at,
            "offer_at": outcome.offer_at,
            "rejected_at": outcome.rejected_at,
            "ghosted_at": outcome.ghosted_at,
            "rejection_reason": outcome.rejection_reason,
            "interview_rounds": outcome.interview_rounds,
            "feedback": outcome.feedback,
            "salary": outcome.salary,
        }
    finally:
        session.close()


def auto_record_from_status(app_id: str) -> ApplicationOutcome | None:
    """Auto-create or update outcome when application status changes."""
    session: Session = get_session()
    try:
        app = session.query(Application).filter(Application.id == app_id).first()
        if not app:
            return None
        outcome = session.query(ApplicationOutcome).filter(
            ApplicationOutcome.application_id == app_id
        ).first()
        if not outcome:
            job = app.job
            outcome = ApplicationOutcome(
                application_id=app_id,
                resume_used=app.resume_used or "",
                company=job.company if job else "",
                role=job.title if job else "",
            )
            session.add(outcome)
        now = datetime.now(timezone.utc)
        outcome.applied_at = app.applied_at or outcome.applied_at
        if app.status == "rejected":
            outcome.rejected_at = outcome.rejected_at or now
        elif app.status == "interview":
            outcome.interview_at = outcome.interview_at or now
        elif app.status == "offer":
            outcome.offer_at = outcome.offer_at or now
        outcome.resume_used = app.resume_used or outcome.resume_used
        session.commit()
        session.refresh(outcome)
        return outcome
    except Exception as e:
        session.rollback()
        logger.warning("auto_record_outcome failed: %s", e)
        return None
    finally:
        session.close()
