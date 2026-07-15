"""Observation Event Pipeline — typed, queryable wrappers over the event store.

Every user action or system transition in the application lifecycle produces
an Observation stored as an immutable Event row. Observations are the raw
material for funnel analytics, probability calibration, and the career graph.

The Event table is the append-only store. Observation types provide typed
access patterns on top of it.
"""

from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from database.models import Event
from database.connection import get_session
from tracker.events import record_event

logger = logging.getLogger("jobzo.observation")


# ── Observation types ────────────────────────────────────────────────────────

class ObservationType(str, Enum):
    """Every stage in the application lifecycle that can be observed."""
    APPLICATION_SUBMITTED = "application_submitted"
    APPLICATION_VIEWED = "application_viewed"
    RECRUITER_REPLIED = "recruiter_replied"
    OA_RECEIVED = "oa_received"
    OA_COMPLETED = "oa_completed"
    INTERVIEW_SCHEDULED = "interview_scheduled"
    INTERVIEW_PASSED = "interview_passed"
    OFFER_RECEIVED = "offer_received"
    OFFER_ACCEPTED = "offer_accepted"
    OFFER_DECLINED = "offer_declined"
    REJECTED = "rejected"
    GHOSTED = "ghosted"
    APPLICATION_SKIPPED = "application_skipped"
    APPLICATION_DEFERRED = "application_deferred"
    EMAIL_SENT = "email_sent"
    EMAIL_OPENED = "email_opened"
    EMAIL_REPLIED = "email_replied"
    REFERRAL_REQUESTED = "referral_requested"
    REFERRAL_RECEIVED = "referral_received"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    MISSION_ACCEPTED = "mission_accepted"
    MISSION_REJECTED = "mission_rejected"


# ── Observation domain object ────────────────────────────────────────────────

@dataclass
class Observation:
    """A single observed event in the lifecycle of an application.

    Pure data — wraps an Event row with typed access.
    """
    id: str
    application_id: str
    observation_type: ObservationType
    occurred_at: datetime
    metadata: dict = field(default_factory=dict)
    source: str = "user"

    @classmethod
    def from_event(cls, event: Event) -> Observation | None:
        try:
            obs_type = ObservationType(event.event_type)
        except ValueError:
            return None
        return cls(
            id=str(event.id),
            application_id=event.entity_id,
            observation_type=obs_type,
            occurred_at=event.occurred_at,
            metadata=json.loads(event.metadata_json) if event.metadata_json else {},
            source=event.actor,
        )


# ── ObservationService ───────────────────────────────────────────────────────

class ObservationService:
    """Record and query observations (typed events).

    Every method accepts an optional Session for callers with open transactions.
    """

    @staticmethod
    def record(
        application_id: str,
        obs_type: ObservationType,
        metadata: dict[str, Any] | None = None,
        actor: str = "user",
        session: Session | None = None,
    ) -> str:
        """Record an observation. Returns the event ID."""
        return record_event(
            event_type=obs_type.value,
            entity_type="application",
            entity_id=application_id,
            actor=actor,
            metadata=metadata,
            session=session,
        )

    @staticmethod
    def get_for_application(
        application_id: str,
        session: Session | None = None,
    ) -> list[Observation]:
        """Get all observations for an application, oldest first."""
        own_session = False
        if session is None:
            session = get_session()
            own_session = True
        try:
            rows = (
                session.query(Event)
                .filter(
                    Event.entity_type == "application",
                    Event.entity_id == application_id,
                )
                .order_by(Event.occurred_at.asc())
                .all()
            )
            return [o for e in rows if (o := Observation.from_event(e))]
        finally:
            if own_session:
                session.close()

    @staticmethod
    def timeline(
        application_id: str,
        session: Session | None = None,
    ) -> list[dict[str, Any]]:
        """Get a timeline of observation summaries for display."""
        obs = ObservationService.get_for_application(application_id, session)
        return [
            {
                "type": o.observation_type.value,
                "at": o.occurred_at.isoformat() if o.occurred_at else "",
                "detail": o.metadata.get("detail", ""),
                "source": o.source,
            }
            for o in obs
        ]

    @staticmethod
    def latest(
        application_id: str,
        session: Session | None = None,
    ) -> Observation | None:
        """Get the most recent observation for an application."""
        own_session = False
        if session is None:
            session = get_session()
            own_session = True
        try:
            row = (
                session.query(Event)
                .filter(
                    Event.entity_type == "application",
                    Event.entity_id == application_id,
                )
                .order_by(Event.occurred_at.desc())
                .first()
            )
            return Observation.from_event(row) if row else None
        finally:
            if own_session:
                session.close()

    @staticmethod
    def current_stage(
        application_id: str,
        session: Session | None = None,
    ) -> str:
        """Determine the current lifecycle stage from observations.

        Stages are ordered: submitted → viewed → oa → interview → offer → accepted.
        Returns the most advanced stage observed.
        """
        obs = ObservationService.get_for_application(application_id, session)
        stages = [
            ObservationType.APPLICATION_SUBMITTED,
            ObservationType.APPLICATION_VIEWED,
            ObservationType.OA_RECEIVED,
            ObservationType.OA_COMPLETED,
            ObservationType.INTERVIEW_SCHEDULED,
            ObservationType.INTERVIEW_PASSED,
            ObservationType.OFFER_RECEIVED,
            ObservationType.OFFER_ACCEPTED,
        ]
        terminal = {
            ObservationType.REJECTED,
            ObservationType.GHOSTED,
            ObservationType.OFFER_DECLINED,
            ObservationType.APPLICATION_SKIPPED,
        }

        seen_types = {o.observation_type for o in obs}
        if seen_types & terminal:
            terminal_type = next(t for t in terminal if t in seen_types)
            return terminal_type.value

        current = ObservationType.APPLICATION_SUBMITTED
        for stage in stages:
            if stage in seen_types:
                current = stage
        return current.value

    @staticmethod
    def get_all_for_company(
        company: str,
        session: Session | None = None,
    ) -> list[Observation]:
        """Get observations for all applications at a given company."""
        own_session = False
        if session is None:
            session = get_session()
            own_session = True
        try:
            from database.models import Application

            app_ids = (
                session.query(Application.id)
                .join(Application.job)
                .filter(Application.job.has(company=company))
                .all()
            )
            ids = [r[0] for r in app_ids]
            if not ids:
                return []
            rows = (
                session.query(Event)
                .filter(
                    Event.entity_type == "application",
                    Event.entity_id.in_(ids),
                )
                .order_by(Event.occurred_at.asc())
                .all()
            )
            return [o for e in rows if (o := Observation.from_event(e))]
        finally:
            if own_session:
                session.close()
