"""Tests for Phase 4C: Outreach Intelligence."""

import os
from pathlib import Path
from datetime import datetime

os.environ.setdefault("JOBZO_DB_PATH", "/tmp/jobzo_outreach_test.db")
os.environ["JOBZO_SKIP_MIGRATIONS"] = "1"

from database.connection import get_engine, get_session
from database.models import Base, Contact, Interaction
from tracker.outreach import (
    outreach_summary,
    template_performance,
    company_responsiveness,
    best_contact_time,
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


def _seed_data():
    session = get_session()
    try:
        c1 = Contact(company="ReplyCo", name="Alice", role="Recruiter")
        c2 = Contact(company="GhostCo", name="Bob", role="HM")
        c3 = Contact(company="ReplyCo", name="Charlie", role="Recruiter")
        session.add_all([c1, c2, c3])
        session.commit()

        session.add_all([
            Interaction(contact_id=c1.id, type="email", direction="outbound",
                        subject="Backend role", outcome="replied",
                        occurred_at=datetime(2026, 6, 10, 10, 0)),
            Interaction(contact_id=c1.id, type="email", direction="outbound",
                        subject="Backend role", outcome="meeting_scheduled",
                        occurred_at=datetime(2026, 6, 11, 14, 0)),
            Interaction(contact_id=c2.id, type="email", direction="outbound",
                        subject="Frontend role", outcome="ignored",
                        occurred_at=datetime(2026, 6, 12, 9, 0)),
            Interaction(contact_id=c2.id, type="email", direction="outbound",
                        subject="Frontend role", outcome="sent",
                        occurred_at=datetime(2026, 6, 13, 16, 0)),
            Interaction(contact_id=c3.id, type="email", direction="outbound",
                        subject="Backend role", outcome="replied",
                        occurred_at=datetime(2026, 6, 14, 11, 0)),
            Interaction(contact_id=c1.id, type="linkedin", direction="inbound",
                        subject="", outcome="",
                        occurred_at=datetime(2026, 6, 15, 8, 0)),
        ])
        session.commit()
    finally:
        session.close()


def test_outreach_summary_counts():
    _seed_data()
    stats = outreach_summary()
    assert stats.total_sent == 5
    assert stats.total_replied == 2
    assert stats.total_meetings == 1
    assert stats.total_ignored == 1
    assert stats.reply_rate == 40.0
    assert stats.unique_contacts == 3


def test_template_performance():
    rows = template_performance()
    assert len(rows) == 2
    subjects = {r["subject"] for r in rows}
    assert "Backend role" in subjects
    assert "Frontend role" in subjects


def test_company_responsiveness():
    rows = company_responsiveness()
    assert len(rows) >= 2
    for r in rows:
        if r["company"] == "ReplyCo":
            assert r["replied"] == 2
            assert r["meetings"] == 1


def test_best_contact_time():
    info = best_contact_time()
    assert info["total_replies_analyzed"] == 2
    assert isinstance(info["best_day"], str)
    assert isinstance(info["best_hour"], int)
