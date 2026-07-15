"""Tests for Experiment Framework."""

import os
os.environ.setdefault("JOBZO_DB_PATH", "/tmp/jobzo_experiment_test.db")
os.environ["JOBZO_SKIP_MIGRATIONS"] = "1"

from database.connection import get_engine
from database.models import Base
from domain.experiment import (
    ExperimentService, Experiment, Hypothesis, Treatment, Metric,
    AssignmentStrategy, ExperimentObservation, ExperimentResult,
    create_default_experiments, instrument_mission,
)


def setup_module():
    engine = get_engine(os.environ["JOBZO_DB_PATH"])
    Base.metadata.create_all(engine)


def test_hypothesis_defaults():
    h = Hypothesis(name="Test", description="", metric=Metric.INTERVIEW_RATE, expected_improvement=0.1)
    assert h.min_sample_size == 30
    assert h.confidence_threshold == 0.95


def test_treatment_control():
    c = Treatment.control()
    assert c.name == "control"
    assert c.is_control
    assert c.config == {}


def test_treatment_variant():
    t = Treatment(name="variant_a", config={"planner": "capital"})
    assert not t.is_control


def test_experiment_control_property():
    exp = Experiment(
        id="test",
        hypothesis=Hypothesis("T", "", Metric.OFFER_RATE, 0.1),
        treatments=[Treatment.control(), Treatment(name="v1", config={})],
    )
    assert exp.control is not None
    assert exp.control.name == "control"
    assert len(exp.variants) == 1


def test_register_and_get():
    svc = ExperimentService()
    exp = Experiment(
        id="e1",
        hypothesis=Hypothesis("H1", "", Metric.INTERVIEW_RATE, 0.1),
        treatments=[Treatment.control(), Treatment(name="v1", config={})],
    )
    svc.register(exp)
    assert svc.get("e1") is exp
    assert svc.get("nonexistent") is None


def test_list_active():
    svc = ExperimentService()
    e1 = Experiment(id="active", hypothesis=Hypothesis("H", "", Metric.OFFER_RATE, 0.1), treatments=[Treatment.control()])
    e2 = Experiment(id="inactive", hypothesis=Hypothesis("H", "", Metric.OFFER_RATE, 0.1), treatments=[Treatment.control()], active=False)
    svc.register(e1)
    svc.register(e2)
    active = svc.list_active()
    assert len(active) == 1
    assert active[0].id == "active"


def test_deterministic_assignment():
    svc = ExperimentService()
    exp = Experiment(
        id="assign_test",
        hypothesis=Hypothesis("H", "", Metric.INTERVIEW_RATE, 0.1),
        treatments=[Treatment.control(), Treatment(name="v1", config={})],
        assignment_strategy=AssignmentStrategy.USER_ID_HASH,
    )
    svc.register(exp)
    t1 = svc.assign("assign_test", "user_a")
    t2 = svc.assign("assign_test", "user_a")
    assert t1.name == t2.name  # deterministic


def test_record_and_analyze():
    svc = ExperimentService()
    exp = Experiment(
        id="analyze_test",
        hypothesis=Hypothesis("A/B Test", "", Metric.INTERVIEW_RATE, 0.2, min_sample_size=4),
        treatments=[Treatment.control(), Treatment(name="treatment", config={})],
    )
    svc.register(exp)

    # Control: 3/5 got interviews = 0.6
    for i in range(5):
        svc.record_observation(ExperimentObservation(
            experiment_id="analyze_test",
            treatment="control",
            user_id=f"u{i}",
            metric=Metric.INTERVIEW_RATE,
            predicted_value=0.5,
            actual_value=1.0 if i < 3 else 0.0,
        ))

    # Treatment: 4/5 got interviews = 0.8
    for i in range(5):
        svc.record_observation(ExperimentObservation(
            experiment_id="analyze_test",
            treatment="treatment",
            user_id=f"v{i}",
            metric=Metric.INTERVIEW_RATE,
            predicted_value=0.5,
            actual_value=1.0 if i < 4 else 0.0,
        ))

    result = svc.analyze("analyze_test")
    assert result is not None
    assert result.control_metric == 0.6
    assert result.treatment_metric == 0.8
    assert result.improvement > 0
    assert result.sample_size == 10


def test_analyze_insufficient_data():
    svc = ExperimentService()
    exp = Experiment(
        id="low_data",
        hypothesis=Hypothesis("Low data", "", Metric.OFFER_RATE, 0.1, min_sample_size=10),
        treatments=[Treatment.control(), Treatment(name="v1", config={})],
    )
    svc.register(exp)
    svc.record_observation(ExperimentObservation("low_data", "control", "u1", Metric.OFFER_RATE, 0.0, 1.0))
    result = svc.analyze("low_data")
    assert result is None


def test_instrument_mission():
    svc = ExperimentService()
    exp = Experiment(
        id="planner_v1_vs_capital",
        hypothesis=Hypothesis("Test", "", Metric.APPLICATIONS_PER_WEEK, 0.0),
        treatments=[Treatment.control(), Treatment(name="capital", config={})],
    )
    svc.register(exp)

    from domain.models import Mission
    from datetime import datetime
    mission = Mission(
        id="m1",
        generated_at=datetime.now(),
        objective="Test",
        estimated_minutes=30,
        expected_gain=45.0,
        confidence="Medium",
        plan_provenance={"planner": "capital"},
    )
    instrument_mission(mission, svc, "user1")
    obs = svc.observations_for("planner_v1_vs_capital")
    assert len(obs) == 1
    assert obs[0].treatment == "capital"


def test_create_default_experiments():
    exps = create_default_experiments()
    assert len(exps) == 3
    ids = [e.id for e in exps]
    assert "planner_v1_vs_capital" in ids
    assert "outreach_effectiveness" in ids
    assert "capital_profile_asap_vs_growth" in ids


def test_experiment_observation_to_dict():
    obs = ExperimentObservation(
        experiment_id="e1",
        treatment="v1",
        user_id="u1",
        metric=Metric.OFFER_RATE,
        predicted_value=0.5,
        actual_value=1.0,
        metadata={"key": "val"},
    )
    d = obs.to_dict()
    assert d["experiment_id"] == "e1"
    assert d["actual"] == 1.0
    assert d["metric"] == "offer_rate"


def test_compare_all_active():
    svc = ExperimentService()
    for exp in create_default_experiments():
        svc.register(exp)
    results = svc.compare_all_active()
    # All have insufficient data, so should return empty
    assert isinstance(results, list)
