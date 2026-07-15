"""Recruiter CRM — reach-out, tracking, and interaction management.

Phase 4B of the Career OS.
"""

from __future__ import annotations
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from database.models import Contact, Interaction
from database.connection import get_session
from tracker.events import record_event, CONTACT_ADDED, INTERACTION_LOGGED, FOLLOWUP_SENT

logger = logging.getLogger("jobzo.reach")


def find_contact(session: Session, company: str) -> Contact | None:
    return session.query(Contact).filter(Contact.company == company).first()


def find_or_create_contact(session: Session, company: str) -> Contact:
    contact = find_contact(session, company)
    if not contact:
        name = input(f"  No contact found for {company}. Enter recruiter name: ").strip()
        if not name:
            name = "Hiring Team"
        role = input(f"  Role (Recruiter/HM, default: Recruiter): ").strip() or "Recruiter"
        contact = Contact(company=company, name=name, role=role, source="jobzo_reach")
        session.add(contact)
        session.commit()
        logger.info("Created contact %s at %s", name, company)
        record_event(CONTACT_ADDED, "contact", contact.id, actor="user", metadata={
            "company": company, "name": name, "role": role,
        })
    return contact


def generate_email_draft(company: str, role: str, contact_name: str) -> dict[str, str]:
    subject = f"Application for {role} at {company}"
    body = (
        f"Hi {contact_name},\n\n"
        f"I recently applied for the {role} position at {company} and wanted to "
        f"follow up on my application. I'm very excited about the opportunity to "
        f"contribute to the team.\n\n"
        f"Please let me know if you need any additional information from me.\n\n"
        f"Best regards,\n[Your Name]"
    )
    return {"subject": subject, "body": body}


def log_interaction(
    contact_id: str,
    application_id: str | None,
    subject: str,
    body: str,
    direction: str = "outbound",
    outcome: str = "sent",
) -> str:
    session: Session = get_session()
    try:
        interaction = Interaction(
            contact_id=contact_id,
            application_id=application_id,
            type="email",
            direction=direction,
            subject=subject,
            body=body,
            outcome=outcome,
            occurred_at=datetime.utcnow(),
        )
        session.add(interaction)
        session.commit()
        ev_type = FOLLOWUP_SENT if "follow" in subject.lower() else INTERACTION_LOGGED
        record_event(ev_type, "interaction", interaction.id, actor="user", metadata={
            "contact_id": contact_id,
            "application_id": application_id,
            "type": "email",
            "direction": direction,
            "outcome": outcome,
        })
        return interaction.id
    except Exception as e:
        session.rollback()
        raise
    finally:
        session.close()


def get_contact_interactions(contact_id: str) -> list[dict[str, Any]]:
    session: Session = get_session()
    try:
        rows = session.query(Interaction).filter(
            Interaction.contact_id == contact_id
        ).order_by(Interaction.occurred_at.desc()).all()
        return [
            {
                "id": r.id,
                "type": r.type,
                "direction": r.direction,
                "subject": r.subject,
                "body": r.body,
                "outcome": r.outcome,
                "occurred_at": r.occurred_at,
            }
            for r in rows
        ]
    finally:
        session.close()
