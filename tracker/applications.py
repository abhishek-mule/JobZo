import logging
from datetime import datetime, date
from typing import Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload

from database.models import Application, Job, Task
from database.connection import get_session
from tracker.events import record_event, APPLICATION_CREATED, STATUS_CHANGED, APPLICATION_SKIPPED

logger = logging.getLogger("jobzo.tracker")

VALID_TRANSITIONS = {
    "drafted": ["ready", "skipped"],
    "ready": ["submitted", "drafted"],
    "submitted": ["interview", "rejected", "offered"],
    "interview": ["offer", "rejected"],
    "rejected": [],
    "offer": ["accepted", "declined"],
}


def list_applications(status: str | None = None) -> Sequence:
    session: Session = get_session()
    try:
        query = select(Application).options(
            joinedload(Application.job)
        ).order_by(Application.created_at.desc())

        if status:
            query = query.where(Application.status == status)

        return session.execute(query).scalars().all()
    finally:
        session.close()


def get_application(app_id: str) -> Application | None:
    session: Session = get_session()
    try:
        return session.execute(
            select(Application).where(Application.id == app_id)
        ).scalar_one_or_none()
    finally:
        session.close()


def transition_status(app_id: str, new_status: str) -> bool:
    session: Session = get_session()
    try:
        app = session.execute(
            select(Application).where(Application.id == app_id)
        ).scalar_one_or_none()

        if not app:
            logger.error("Application %s not found", app_id)
            return False

        if app.status == new_status:
            logger.debug("Already %s — no-op", new_status)
            return True

        allowed = VALID_TRANSITIONS.get(app.status, [])
        if new_status not in allowed:
            logger.error(
                "Invalid transition: %s -> %s (allowed: %s)",
                app.status, new_status, allowed,
            )
            return False

        now = datetime.utcnow()
        prev_status = app.status
        app._prev_status = prev_status
        app.status = new_status
        app.last_activity_at = now

        if new_status == "submitted":
            app.applied_at = now
        elif new_status == "interview":
            app.response_date = now
            if not app.first_response_at:
                app.first_response_at = now
            _create_interview_task(session, app)
        elif new_status == "rejected":
            app.response_date = now
            if not app.first_response_at:
                app.first_response_at = now

        session.commit()

        from tracker.events import record_event, STATUS_CHANGED, APPLICATION_SUBMITTED, OFFER_RECEIVED, REJECTED, INTERVIEW_SCHEDULED
        ev_type = STATUS_CHANGED
        if new_status == "submitted":
            ev_type = APPLICATION_SUBMITTED
        elif new_status == "interview":
            ev_type = INTERVIEW_SCHEDULED
        elif new_status == "offer":
            ev_type = OFFER_RECEIVED
        elif new_status == "rejected":
            ev_type = REJECTED
        record_event(ev_type, "application", app.id, actor="user", metadata={
            "from_status": getattr(app, '_prev_status', None),
            "to_status": new_status,
            "company": app.job.company if app.job else "",
        })

        from tracker.outcomes import auto_record_from_status
        auto_record_from_status(app.id)

        logger.info("Application %s -> %s", app_id, new_status)
        return True
    except Exception as e:
        session.rollback()
        logger.error("Transition error: %s", e)
        return False
    finally:
        session.close()


def _create_interview_task(session: Session, app: Application):
    task = Task(
        application_id=app.id,
        type="interview_prep",
        title=f"Prepare for interview: {app.job.company if app.job else 'Unknown'}",
        due_date=date.today(),
        notes="",
    )
    session.add(task)
