"""Tests for Phase 5: Personal Intelligence."""

import os
import uuid
from pathlib import Path
from datetime import datetime, timezone

os.environ.setdefault("JOBZO_DB_PATH", "/tmp/jobzo_personal_test.db")
os.environ["JOBZO_SKIP_MIGRATIONS"] = "1"

from database.connection import get_engine, get_session
from database.models import Base, Application, Job, ApplicationOutcome, Interaction, Contact
from tracker.personal import (
    learn_weights,
    resume_stats,
    resume_detail,
    company_intelligence,
    ats_intelligence,
    timing_intelligence,
    skill_intelligence,
    personal_predict,
    simulate,
    PersonalWeights,
    ResumeStat,
    SimulationResult,
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
        u = uuid.uuid4().hex[:6]
        jobs = [
            Job(company="CompA", title="Backend", description="Java Spring Boot",
                url=f"https://greenhouse.io/a{u}", source="test"),
            Job(company="CompB", title="Frontend", description="React TypeScript",
                url=f"https://lever.co/b{u}", source="test"),
            Job(company="CompB", title="Fullstack", description="Python React",
                url=f"https://greenhouse.io/c{u}", source="test"),
        ]
        session.add_all(jobs)
        session.commit()

        for j in jobs:
            app = Application(job_id=j.id, status="submitted", resume_used="backend_v3",
                              score=75, created_at=datetime.now(timezone.utc))
            session.add(app)
            session.commit()
            outcome = ApplicationOutcome(
                application_id=app.id,
                resume_used="backend_v3",
                company=j.company,
                role=j.title,
                ats="Greenhouse" if "greenhouse" in j.url else "Lever",
                applied_at=datetime.now(timezone.utc),
            )
            if j.company == "CompA":
                outcome.interview_at = datetime.now(timezone.utc)
            session.add(outcome)
            session.commit()

        contact = Contact(company="CompA", name="Test", role="Recruiter")
        session.add(contact)
        session.commit()

        app = session.query(Application).first()
        interaction = Interaction(
            contact_id=contact.id,
            application_id=app.id,
            type="email", direction="outbound",
            subject="Test", outcome="replied",
            occurred_at=datetime.now(timezone.utc),
        )
        session.add(interaction)
        session.commit()
    finally:
        session.close()


def test_learn_weights_low_confidence_when_no_data():
    w = learn_weights()
    assert isinstance(w, PersonalWeights)
    assert w.confidence == "Low"


def test_learn_weights_with_data():
    _seed_data()
    w = learn_weights()
    assert isinstance(w.resume_fit, float)
    assert w.resume_fit > 0
    assert w.confidence in ("Low", "Medium", "High")


def test_resume_stats():
    rows = resume_stats()
    assert len(rows) >= 1
    r = rows[0]
    assert isinstance(r, ResumeStat)
    assert r.applications >= 1


def test_resume_detail():
    r = resume_detail("backend_v3")
    assert r is not None
    assert r.name == "backend_v3"
    assert r.applications >= 1


def test_resume_detail_nonexistent():
    r = resume_detail("nonexistent")
    assert r is None


def test_company_intelligence():
    rows = company_intelligence()
    assert len(rows) >= 1
    c = rows[0]
    assert c.applications >= 1


def test_company_intelligence_filtered():
    rows = company_intelligence("CompA")
    assert len(rows) >= 1
    assert rows[0].company == "CompA"


def test_ats_intelligence():
    rows = ats_intelligence()
    assert len(rows) >= 1
    assert "ats" in rows[0]
    assert rows[0]["interview_rate"] >= 0


def test_timing_intelligence():
    info = timing_intelligence()
    assert "by_day" in info
    assert "by_hour" in info
    assert isinstance(info["by_day"], list)


def test_skill_intelligence():
    rows = skill_intelligence()
    assert isinstance(rows, list)


def test_personal_predict():
    session = get_session()
    try:
        app = session.query(Application).first()
        pred = personal_predict(app, weights=learn_weights())
        assert "score" in pred
        assert 0 <= pred["score"] <= 100
        assert "breakdown" in pred
        assert len(pred["breakdown"]) == 5
    finally:
        session.close()


def test_simulate():
    session = get_session()
    try:
        app = session.query(Application).first()
        changes = [{"kind": "skill", "skill": "Redis", "count": 1}]
        job = app.job
        result = simulate(app, changes, job)
        assert isinstance(result, SimulationResult)
        assert result.current_score >= 0
    finally:
        session.close()


def test_simulate_resume():
    session = get_session()
    try:
        app = session.query(Application).first()
        job = app.job
        changes = [{"kind": "resume", "value": "frontend_v2"}]
        result = simulate(app, changes, job)
        assert isinstance(result, SimulationResult)
    finally:
        session.close()


def test_personal_weights_format():
    w = PersonalWeights(resume_fit=0.28, experience=0.18, company_history=0.32,
                        ats_history=0.15, application_timing=0.05, skill_gap_penalty=0.02,
                        confidence="Medium")
    text = w.format_text()
    assert "28%" in text
    assert "Medium" in text


def test_resume_stat_format():
    r = ResumeStat(name="backend_v3", applications=31, interviews=8, interview_rate=25.8, confidence="High")
    text = r.format_text()
    assert "backend_v3" in text
    assert "31" in text
    assert "25.8%" in text


def test_simulation_format():
    s = SimulationResult(
        current_score=29.0,
        changes=[("Use backend_v3", 6.0), ("Mention Redis", 4.0)],
        final_score=39.0,
        breakdown=[{"label": "Resume Fit", "weight": 0.35, "score": 71.0}],
    )
    text = s.format_text()
    assert "29%" in text
    assert "+6%" in text
    assert "39%" in text
