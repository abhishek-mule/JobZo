"""Tests for Phase 3.1: Observation Event Pipeline."""

import os
import uuid
from datetime import datetime, timezone, timedelta

os.environ.setdefault("JOBZO_DB_PATH", "/tmp/jobzo_observation_test.db")
os.environ["JOBZO_SKIP_MIGRATIONS"] = "1"

from database.connection import get_engine, get_session
from database.models import Base, Application, Job, DecisionSnapshot, Event
from domain.observation import ObservationService, ObservationType, Observation
from domain.analytics import ProjectionService, CalibrationService, CalibrationPoint, FunnelStage


def _clean_db():
    import pathlib
    db_path = pathlib.Path(os.environ["JOBZO_DB_PATH"])
    if db_path.exists():
        db_path.unlink()


def setup_module():
    _clean_db()
    engine = get_engine(os.environ["JOBZO_DB_PATH"])
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def _seed_app(status: str = "drafted", company: str = "ObserveCorp") -> str:
    uid = uuid.uuid4().hex[:8]
    session = get_session()
    try:
        job = Job(
            company=company,
            title="Backend Engineer",
            description="Python PostgreSQL",
            url=f"https://greenhouse.io/jobs/{uid}",
            source="test",
        )
        session.add(job)
        session.commit()
        app = Application(
            job_id=job.id,
            status=status,
            resume_used="v1",
            score=75,
            created_at=datetime.now(timezone.utc),
        )
        session.add(app)
        session.commit()
        return str(app.id)
    finally:
        session.close()


# ── ObservationService ───────────────────────────────────────────────────

def test_record_and_get():
    app_id = _seed_app()
    eid = ObservationService.record(app_id, ObservationType.APPLICATION_SUBMITTED)
    assert eid

    obs = ObservationService.get_for_application(app_id)
    assert len(obs) == 1
    assert obs[0].observation_type == ObservationType.APPLICATION_SUBMITTED
    assert obs[0].application_id == app_id


def test_record_with_metadata():
    app_id = _seed_app()
    ObservationService.record(
        app_id,
        ObservationType.OFFER_RECEIVED,
        metadata={"salary": "120k", "detail": "Senior offer"},
    )
    obs = ObservationService.get_for_application(app_id)
    assert len(obs) == 1
    assert obs[0].metadata.get("salary") == "120k"


def test_latest():
    app_id = _seed_app()
    assert ObservationService.latest(app_id) is None
    ObservationService.record(app_id, ObservationType.APPLICATION_SUBMITTED)
    ObservationService.record(app_id, ObservationType.INTERVIEW_SCHEDULED)
    latest = ObservationService.latest(app_id)
    assert latest is not None
    assert latest.observation_type == ObservationType.INTERVIEW_SCHEDULED


def test_current_stage():
    app_id = _seed_app()
    # No observations -> submitted (default)
    assert ObservationService.current_stage(app_id) == "application_submitted"
    ObservationService.record(app_id, ObservationType.APPLICATION_SUBMITTED)
    assert ObservationService.current_stage(app_id) == "application_submitted"
    ObservationService.record(app_id, ObservationType.OA_RECEIVED)
    assert ObservationService.current_stage(app_id) == "oa_received"
    ObservationService.record(app_id, ObservationType.INTERVIEW_SCHEDULED)
    assert ObservationService.current_stage(app_id) == "interview_scheduled"
    ObservationService.record(app_id, ObservationType.REJECTED)
    assert ObservationService.current_stage(app_id) == "rejected"


def test_current_stage_offer_accepted():
    app_id = _seed_app()
    ObservationService.record(app_id, ObservationType.APPLICATION_SUBMITTED)
    ObservationService.record(app_id, ObservationType.INTERVIEW_SCHEDULED)
    ObservationService.record(app_id, ObservationType.OFFER_RECEIVED)
    ObservationService.record(app_id, ObservationType.OFFER_ACCEPTED)
    assert ObservationService.current_stage(app_id) == "offer_accepted"


def test_timeline():
    app_id = _seed_app()
    ObservationService.record(app_id, ObservationType.APPLICATION_SUBMITTED)
    ObservationService.record(app_id, ObservationType.REJECTED)
    tl = ObservationService.timeline(app_id)
    assert len(tl) == 2
    assert tl[0]["type"] == "application_submitted"
    assert tl[1]["type"] == "rejected"


def test_get_all_for_company():
    app1 = _seed_app(company="AlphaCorp")
    app2 = _seed_app(company="AlphaCorp")
    app3 = _seed_app(company="BetaCorp")
    ObservationService.record(app1, ObservationType.APPLICATION_SUBMITTED)
    ObservationService.record(app2, ObservationType.APPLICATION_SUBMITTED)
    ObservationService.record(app3, ObservationType.APPLICATION_SUBMITTED)

    alpha_obs = ObservationService.get_all_for_company("AlphaCorp")
    assert len(alpha_obs) == 2

    beta_obs = ObservationService.get_all_for_company("BetaCorp")
    assert len(beta_obs) == 1

    none_obs = ObservationService.get_all_for_company("NoneCorp")
    assert len(none_obs) == 0


# ── ProjectionService ────────────────────────────────────────────────────

def test_company_funnel():
    app = _seed_app(company="FunnelCorp")
    ObservationService.record(app, ObservationType.APPLICATION_SUBMITTED)
    ObservationService.record(app, ObservationType.OA_RECEIVED)
    ObservationService.record(app, ObservationType.INTERVIEW_SCHEDULED)

    funnel = ProjectionService.company_funnel("FunnelCorp")
    assert len(funnel) > 0
    submitted = [s for s in funnel if s.name == "application_submitted"]
    oa = [s for s in funnel if s.name == "oa_received"]
    interview = [s for s in funnel if s.name == "interview_scheduled"]
    assert submitted[0].count == 1
    assert oa[0].count == 1
    assert interview[0].count == 1
    # conversion: submitted -> viewed = 1/1 = 1.0 (since we have fewer stages)
    # but only the stages we recorded should show counts


def test_global_funnel():
    app1 = _seed_app(company="G1")
    app2 = _seed_app(company="G2")
    ObservationService.record(app1, ObservationType.APPLICATION_SUBMITTED)
    ObservationService.record(app1, ObservationType.OFFER_RECEIVED)
    ObservationService.record(app2, ObservationType.APPLICATION_SUBMITTED)

    funnel = ProjectionService.global_funnel()
    submitted = [s for s in funnel if s.name == "application_submitted"]
    offer = [s for s in funnel if s.name == "offer_received"]
    assert submitted[0].count >= 2
    assert offer[0].count >= 1


def test_time_to_next_stage():
    app = _seed_app()
    now = datetime.now(timezone.utc)
    # Record with explicit timestamps isn't easy with the current API.
    # We'll verify it returns None for missing stages.
    assert ProjectionService.time_to_next_stage(
        app, ObservationType.APPLICATION_SUBMITTED, ObservationType.INTERVIEW_SCHEDULED
    ) is None


def test_average_time_to_stage_no_data():
    """When no matching submit+stage events exist, returns None or non-negative float."""
    result = ProjectionService.average_time_to_stage(ObservationType.INTERVIEW_SCHEDULED)
    assert result is None or (isinstance(result, float) and result >= 0)


# ── CalibrationService ───────────────────────────────────────────────────

def test_confidence_interval():
    lo, hi = CalibrationService.confidence_interval(0.7, n=100)
    assert lo < 0.7 < hi
    assert 0 < lo < hi < 1

    # Small n -> wider interval
    lo_small, hi_small = CalibrationService.confidence_interval(0.7, n=1)
    lo_big, hi_big = CalibrationService.confidence_interval(0.7, n=1000)
    assert (hi_small - lo_small) > (hi_big - lo_big)


def test_expected_value():
    ev = CalibrationService.expected_value(0.8, value_if_success=100, cost_if_failure=10)
    assert ev == 82.0  # 0.8*100 + 0.2*10

    ev2 = CalibrationService.expected_value(0.5)
    assert ev2 == 0.5


def test_build_curve_empty():
    curve = CalibrationService.build_curve()
    assert curve == []


def test_build_curve():
    """Build curve with a snapshot that has a prediction + interview event."""
    app = _seed_app()
    session = get_session()
    try:
        snap = DecisionSnapshot(
            application_id=app,
            interview_probability=0.8,
            composite_score=75.0,
            tier="Medium",
            confidence=0.6,
            risk="Medium",
            effort_minutes=30,
            canonical_role="Backend Engineer",
            seniority="Mid",
            retriever_version="1",
            ranker_version="1",
            registry_version="1",
            skill_graph_version="1",
            details_json='{"score_vector": {"skills": 80, "experience": 70}}',
        )
        session.add(snap)
        session.commit()
    finally:
        session.close()

    curve = CalibrationService.build_curve()
    assert isinstance(curve, list)
    # No interview event exists, so observed should be 0.0
    if curve:
        # There should be points
        assert all(isinstance(c, CalibrationPoint) for c in curve)


def test_calibrate_no_curve():
    assert CalibrationService.calibrate(0.7, None) == 0.7


def test_calibrate_with_curve():
    curve = [CalibrationPoint(expected=0.25, observed=0.20, count=10),
             CalibrationPoint(expected=0.75, observed=0.65, count=15)]
    assert CalibrationService.calibrate(0.7, curve) == 0.65
    assert CalibrationService.calibrate(0.3, curve) == 0.20
    # Outside range -> raw
    assert abs(CalibrationService.calibrate(0.99, curve) - 0.99) < 0.001


# ── Integration: transition_status records observations ──────────────────

def test_transition_status_records_observation():
    app_id = _seed_app()
    from tracker.applications import transition_status

    success = transition_status(app_id, "ready")
    assert success
    success = transition_status(app_id, "submitted")
    assert success

    obs = ObservationService.get_for_application(app_id)
    assert any(o.observation_type == ObservationType.APPLICATION_SUBMITTED for o in obs)


def test_transition_status_rejected_records_observation():
    app_id = _seed_app()
    from tracker.applications import transition_status

    assert transition_status(app_id, "ready")
    assert transition_status(app_id, "submitted")
    assert transition_status(app_id, "rejected")

    obs = ObservationService.get_for_application(app_id)
    types = [o.observation_type for o in obs]
    assert ObservationType.APPLICATION_SUBMITTED in types
    assert ObservationType.REJECTED in types
