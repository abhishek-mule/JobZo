"""Tests for scenario comparison in the simulation engine."""

import os
os.environ.setdefault("JOBZO_DB_PATH", "/tmp/jobzo_scenario_test.db")
os.environ["JOBZO_SKIP_MIGRATIONS"] = "1"

from database.connection import get_engine
from database.models import Base
from domain.simulation import (
    Scenario, simulate_scenario, compare_scenarios,
    _kind_time, _kind_title,
)
from domain.capital import CapitalProfile


def setup_module():
    engine = get_engine(os.environ["JOBZO_DB_PATH"])
    Base.metadata.create_all(engine)


def test_scenario_dataclass():
    s = Scenario(
        name="Apply heavy",
        description="Prioritize applications",
        task_mix={"apply": 0.8, "outreach": 0.2},
    )
    assert s.name == "Apply heavy"
    assert s.task_mix["apply"] == 0.8


def test_kind_time():
    assert _kind_time("apply") == 15
    assert _kind_time("outreach") == 6
    assert _kind_time("learning") == 30
    assert _kind_time("followup") == 5
    assert _kind_time("unknown") == 15


def test_kind_title():
    assert "Apply to" in _kind_title("apply", "Stripe")
    assert "Contact recruiter" in _kind_title("outreach", "Stripe")
    assert "Learn" in _kind_title("learning", "Stripe")


def test_simulate_scenario_runs():
    s = Scenario(
        name="Test scenario",
        description="Quick test",
        task_mix={"apply": 1.0},
    )
    result = simulate_scenario(s, total_hours=2.0, days=5, runs=2)
    assert len(result.runs) == 2
    assert result.scenario.name == "Test scenario"


def test_scenario_result_average():
    s = Scenario(name="Avg test", description="", task_mix={"apply": 1.0})
    result = simulate_scenario(s, total_hours=2.0, days=5, runs=3)
    avg = result.average
    assert "total_applications" in avg
    assert "total_interviews" in avg
    assert "total_offers" in avg
    assert "capital_score" in avg
    assert avg["total_applications"] >= 0


def test_scenario_result_capital_scoring():
    profile = CapitalProfile.for_goal("Get placed ASAP")
    s = Scenario(name="Capital test", description="", task_mix={"apply": 1.0})
    result = simulate_scenario(s, total_hours=2.0, days=5, runs=2, capital_profile=profile)
    avg = result.average
    assert avg["capital_score"] > 0


def test_compare_scenarios_returns_recommendation():
    scenarios = [
        Scenario(name="Apply only", description="Just apply", task_mix={"apply": 1.0}),
        Scenario(name="Mix", description="Apply + outreach", task_mix={"apply": 0.7, "outreach": 0.3}),
    ]
    result = compare_scenarios(scenarios, total_hours=2.0, days=5, runs=2)
    assert "scenarios" in result
    assert len(result["scenarios"]) == 2
    assert "recommendation" in result
    assert result["recommendation"] in ("Apply only", "Mix")


def test_compare_scenarios_with_capital():
    profile = CapitalProfile.for_goal("Build network")
    scenarios = [
        Scenario(name="Apply", description="", task_mix={"apply": 1.0}),
        Scenario(name="Network", description="", task_mix={"outreach": 1.0}),
    ]
    result = compare_scenarios(scenarios, total_hours=2.0, days=5, runs=2, capital_profile=profile)
    assert "recommendation" in result


def test_scenario_result_compare_to():
    s1 = Scenario(name="S1", description="", task_mix={"apply": 1.0})
    s2 = Scenario(name="S2", description="", task_mix={"outreach": 1.0})
    r1 = simulate_scenario(s1, total_hours=2.0, days=5, runs=2)
    r2 = simulate_scenario(s2, total_hours=2.0, days=5, runs=2)
    deltas = r1.compare_to(r2)
    if deltas:
        assert "total_applications" in deltas
