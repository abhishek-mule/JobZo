"""Tests for Phase 4B: Recruiter CRM (reach module)."""

import os
from pathlib import Path

os.environ.setdefault("JOBZO_DB_PATH", "/tmp/jobzo_reach_test.db")
os.environ["JOBZO_SKIP_MIGRATIONS"] = "1"

from database.connection import get_engine, get_session
from database.models import Base, Contact, Interaction
from tracker.reach import (
    generate_email_draft,
    find_contact,
    log_interaction,
    get_contact_interactions,
)


def _clean_db():
    db_path = Path(os.environ["JOBZO_DB_PATH"])
    if db_path.exists():
        db_path.unlink()


def setup_module():
    _clean_db()
    engine = get_engine(os.environ["JOBZO_DB_PATH"])
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_generate_email_draft():
    draft = generate_email_draft("AcmeCorp", "Backend Engineer", "Jane Doe")
    assert "AcmeCorp" in draft["subject"]
    assert "Backend Engineer" in draft["subject"]
    assert "Jane Doe" in draft["body"]
    assert "AcmeCorp" in draft["body"]


def test_find_contact_returns_none_when_empty():
    session = get_session()
    try:
        c = find_contact(session, "NonexistentCo")
        assert c is None
    finally:
        session.close()


def test_find_contact_finds_existing():
    session = get_session()
    try:
        contact = Contact(company="TestCo", name="Alice", role="Recruiter")
        session.add(contact)
        session.commit()
        found = find_contact(session, "TestCo")
        assert found is not None
        assert found.name == "Alice"
    finally:
        session.close()


def test_log_interaction_creates_record():
    session = get_session()
    contact_id = None
    try:
        contact = Contact(company="LogCo", name="Bob", role="HM")
        session.add(contact)
        session.commit()
        contact_id = contact.id
    finally:
        session.close()

    interaction_id = log_interaction(
        contact_id, application_id=None,
        subject="Test Subject",
        body="Test body content",
        direction="outbound",
        outcome="sent",
    )
    assert interaction_id is not None

    session = get_session()
    try:
        stored = session.get(Interaction, interaction_id)
        assert stored is not None
        assert stored.subject == "Test Subject"
        assert stored.body == "Test body content"
        assert stored.type == "email"
    finally:
        session.close()


def test_get_contact_interactions():
    session = get_session()
    contact_id = None
    try:
        contact = Contact(company="InteractCo", name="Carol", role="Recruiter")
        session.add(contact)
        session.commit()
        contact_id = contact.id

        for i in range(3):
            interaction = Interaction(
                contact_id=contact_id,
                type="email",
                direction="outbound",
                subject=f"Message {i}",
                body=f"Body {i}",
            )
            session.add(interaction)
        session.commit()
    finally:
        session.close()

    interactions = get_contact_interactions(contact_id)
    assert len(interactions) == 3
    assert interactions[0]["subject"] == "Message 2"
