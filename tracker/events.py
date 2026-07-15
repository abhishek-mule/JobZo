"""Event Store — immutable event recording for every important action.

Every transition, prediction, and user action becomes an immutable event.
This is the foundation for analytics, calibration, experiments, and the Career Graph.
Events cannot be deleted or modified — only appended.
"""

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Any

from database.connection import get_session
from database.models import Event
from sqlalchemy import func, desc
from sqlalchemy.orm import Session

logger = logging.getLogger("jobzo.events")

# ── Event type constants ────────────────────────────────────────────────

JOB_DISCOVERED = "job_discovered"
APPLICATION_CREATED = "application_created"
RESUME_RECOMMENDED = "resume_recommended"
RESUME_CHANGED = "resume_changed"
APPLICATION_SUBMITTED = "application_submitted"
CONTACT_ADDED = "contact_added"
INTERACTION_LOGGED = "interaction_logged"
FOLLOWUP_SENT = "followup_sent"
RECRUITER_REPLIED = "recruiter_replied"
OA_RECEIVED = "oa_received"
INTERVIEW_SCHEDULED = "interview_scheduled"
STATUS_CHANGED = "status_changed"
OFFER_RECEIVED = "offer_received"
REJECTED = "rejected"
GHOSTED = "ghosted"
PREDICTION_MADE = "prediction_made"
SYNC_RUN = "sync_run"
TASK_COMPLETED = "task_completed"
TASK_CREATED = "task_created"
USER_REVIEWED = "user_reviewed"
APPLICATION_SKIPPED = "application_skipped"
CAREER_GOAL_SET = "career_goal_set"

ALL_EVENT_TYPES = frozenset({
    JOB_DISCOVERED, APPLICATION_CREATED, RESUME_RECOMMENDED, RESUME_CHANGED,
    APPLICATION_SUBMITTED, CONTACT_ADDED, INTERACTION_LOGGED, FOLLOWUP_SENT,
    RECRUITER_REPLIED, OA_RECEIVED, INTERVIEW_SCHEDULED, STATUS_CHANGED,
    OFFER_RECEIVED, REJECTED, GHOSTED, PREDICTION_MADE, SYNC_RUN,
    TASK_COMPLETED, TASK_CREATED, USER_REVIEWED, APPLICATION_SKIPPED,
    CAREER_GOAL_SET,
})


def record_event(
    event_type: str,
    entity_type: str,
    entity_id: str,
    actor: str = "user",
    metadata: dict[str, Any] | None = None,
    session: Session | None = None,
) -> str:
    """Record an immutable event. Returns the event ID.

    Accepts an optional SQLAlchemy session for callers that already have
    an open transaction (avoids 'database is locked' in SQLite).
    """
    own_session = False
    if session is None:
        session = get_session()
        own_session = True
    try:
        ev = Event(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            actor=actor,
            metadata_json=json.dumps(metadata or {}),
            occurred_at=datetime.now(timezone.utc),
        )
        session.add(ev)
        if own_session:
            session.commit()
        logger.debug("Event recorded: %s (%s=%s)", event_type, entity_type, entity_id[:8])
        return str(ev.id)
    except Exception as e:
        if own_session:
            session.rollback()
        logger.error("Failed to record event %s: %s", event_type, e)
        return ""
    finally:
        if own_session:
            session.close()


def get_events(
    entity_type: str | None = None,
    entity_id: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Query events with optional filters. Returns list of event dicts."""
    session = get_session()
    try:
        query = session.query(Event).order_by(Event.occurred_at.desc())
        if entity_type:
            query = query.filter(Event.entity_type == entity_type)
        if entity_id:
            query = query.filter(Event.entity_id.startswith(entity_id))
        if event_type:
            query = query.filter(Event.event_type == event_type)
        events = query.limit(limit).all()
        return [
            {
                "id": str(e.id),
                "event_type": e.event_type,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "actor": e.actor,
                "metadata": json.loads(e.metadata_json) if e.metadata_json else {},
                "occurred_at": e.occurred_at.isoformat() if e.occurred_at else "",
            }
            for e in events
        ]
    finally:
        session.close()


def get_timeline(entity_type: str, entity_id: str) -> list[dict[str, Any]]:
    """Get full event timeline for a specific entity (e.g. application)."""
    return get_events(entity_type=entity_type, entity_id=entity_id, limit=500)


def count_events(event_type: str, since: datetime | None = None) -> int:
    """Count events of a given type, optionally since a timestamp."""
    session = get_session()
    try:
        query = session.query(func.count(Event.id)).filter(Event.event_type == event_type)
        if since:
            query = query.filter(Event.occurred_at >= since)
        return query.scalar() or 0
    finally:
        session.close()


def latest_event(entity_type: str, entity_id: str, event_type: str) -> dict[str, Any] | None:
    """Get the most recent event of a type for an entity."""
    session = get_session()
    try:
        e = session.query(Event).filter(
            Event.entity_type == entity_type,
            Event.entity_id.startswith(entity_id),
            Event.event_type == event_type,
        ).order_by(Event.occurred_at.desc()).first()
        if not e:
            return None
        return {
            "id": str(e.id),
            "event_type": e.event_type,
            "entity_type": e.entity_type,
            "entity_id": e.entity_id,
            "metadata": json.loads(e.metadata_json) if e.metadata_json else {},
            "occurred_at": e.occurred_at.isoformat() if e.occurred_at else "",
        }
    finally:
        session.close()
