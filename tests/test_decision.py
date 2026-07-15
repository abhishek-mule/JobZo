"""Tests for Phase 4D: Decision Intelligence."""

import os
import uuid
from pathlib import Path
from datetime import datetime

os.environ.setdefault("JOBZO_DB_PATH", "/tmp/jobzo_decision_test.db")
os.environ["JOBZO_SKIP_MIGRATIONS"] = "1"

from database.connection import get_engine, get_session
from database.models import Base, Application, Job, ApplicationOutcome
from tracker.outcomes import get_or_create_outcome, update_outcome, get_outcome, auto_record_from_status
from tracker.features import extract_features, extract_ats_from_url
from tracker.decision import predict_interview, counterfactual, Prediction, Counterfactual


def _clean_db():
    db_path = Path(os.environ["JOBZO_DB_PATH"])
    if db_path.exists():
        db_path.unlink()


def setup_module():
    _clean_db()
    engine = get_engine(os.environ["JOBZO_DB_PATH"])
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def _seed_app():
    uid = uuid.uuid4().hex[:8]
    session = get_session()
    try:
        job = Job(
            company="DecisionCorp",
            title="Backend Engineer",
            description="Java Spring Boot Kafka PostgreSQL microservices",
            url=f"https://greenhouse.io/jobs/{uid}",
            source="test",
        )
        session.add(job)
        session.commit()
        app = Application(
            job_id=job.id,
            status="drafted",
            resume_used="java_v1",
            score=75,
            created_at=datetime.utcnow(),
        )
        session.add(app)
        session.commit()
        return app.id, job.id
    finally:
        session.close()


def test_extract_ats_from_url():
    assert extract_ats_from_url("https://greenhouse.io/jobs/123") == "Greenhouse"
    assert extract_ats_from_url("https://jobs.lever.co/acme") == "Lever"
    assert extract_ats_from_url("https://company.com/careers") == "Unknown"


def test_get_or_create_outcome_creates():
    app_id, _ = _seed_app()
    outcome = get_or_create_outcome(app_id)
    assert outcome is not None
    assert outcome.company == "DecisionCorp"
    assert outcome.resume_used == "java_v1"


def test_update_outcome():
    app_id, _ = _seed_app()
    get_or_create_outcome(app_id)
    result = update_outcome(app_id, rejection_reason="Too senior", interview_rounds=3)
    assert "error" not in result
    data = get_outcome(app_id)
    assert data["rejection_reason"] == "Too senior"
    assert data["interview_rounds"] == 3


def test_auto_record_from_status():
    app_id, _ = _seed_app()
    session = get_session()
    try:
        app = session.get(Application, app_id)
        app.status = "rejected"
        session.commit()
        app_id = app.id
    finally:
        session.close()
    outcome = auto_record_from_status(app_id)
    assert outcome is not None
    assert outcome.rejected_at is not None


def test_extract_features():
    app_id, _ = _seed_app()
    session = get_session()
    try:
        app = session.get(Application, app_id)
        fv = extract_features(app)
        assert fv.ats == "Greenhouse"
        assert fv.company_tier in ("Startup", "Mid", "Enterprise")
        assert fv.application_age_hours >= 0
    finally:
        session.close()


def test_predict_interview():
    app_id, _ = _seed_app()
    session = get_session()
    try:
        app = session.get(Application, app_id)
        pred = predict_interview(app)
        assert isinstance(pred, Prediction)
        assert 0 <= pred.score <= 100
        assert pred.confidence in ("Low", "Medium", "High")
        assert len(pred.breakdown) == 5
    finally:
        session.close()


def test_counterfactual():
    app_id, _ = _seed_app()
    session = get_session()
    try:
        app = session.get(Application, app_id)
        cf = counterfactual(app)
        assert isinstance(cf, Counterfactual)
        assert cf.current_probability >= 0
    finally:
        session.close()


def test_prediction_format_text():
    p = Prediction(
        score=34.0,
        confidence="Medium",
        breakdown=[
            {"label": "Resume Fit", "weight": 0.35, "score": 89.0},
            {"label": "Experience", "weight": 0.25, "score": 60.0},
        ],
        reasons_positive=["Resume matches (89%)"],
        reasons_negative=["Missing 2 skills"],
    )
    text = p.format_text()
    assert "34%" in text
    assert "Medium" in text
    assert "89/100" in text


def test_outcome_get_nonexistent():
    data = get_outcome("nonexistent-id")
    assert data is None
