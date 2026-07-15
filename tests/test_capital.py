"""Tests for Career Capital model and root-cause calibration."""

import os
os.environ.setdefault("JOBZO_DB_PATH", "/tmp/jobzo_capital_test.db")
os.environ["JOBZO_SKIP_MIGRATIONS"] = "1"

from database.connection import get_engine
from database.models import Base
from domain.capital import (
    CapitalVector, CapitalProfile, GOAL_PROFILES, KIND_CONTRIBUTIONS,
    capital_value, CapitalKind,
)


def setup_module():
    engine = get_engine(os.environ["JOBZO_DB_PATH"])
    Base.metadata.create_all(engine)


def test_capital_vector_dot():
    v = CapitalVector(resume=0.5, skill=0.3, opportunity=0.2)
    w = CapitalVector(resume=1.0, skill=0.0, opportunity=1.0)
    assert v.dot(w) == 0.7  # 0.5*1 + 0.3*0 + 0.2*1


def test_capital_vector_add():
    a = CapitalVector(resume=0.3, skill=0.2)
    b = CapitalVector(network=0.5, opportunity=0.1)
    c = a + b
    assert c.resume == 0.3
    assert c.skill == 0.2
    assert c.network == 0.5
    assert c.opportunity == 0.1


def test_capital_vector_mul():
    v = CapitalVector(resume=0.5, skill=0.3)
    s = v * 2.0
    assert s.resume == 1.0
    assert s.skill == 0.6


def test_goal_profiles_exist():
    assert "Get placed ASAP" in GOAL_PROFILES
    assert "Maximize salary" in GOAL_PROFILES
    assert "Crack product companies" in GOAL_PROFILES
    assert "Build network" in GOAL_PROFILES
    assert "Career growth (long-term)" in GOAL_PROFILES


def test_goal_profile_asap_weights_opportunity():
    p = GOAL_PROFILES["Get placed ASAP"]
    assert p.opportunity > 0.4  # 55% weight on opportunity


def test_goal_profile_network_weights_network():
    p = GOAL_PROFILES["Build network"]
    assert p.network > 0.3  # 45% weight on network


def test_goal_profile_growth_weights_reputation():
    p = GOAL_PROFILES["Career growth (long-term)"]
    assert p.reputation > 0.2


def test_capital_profile_for_goal():
    p = CapitalProfile.for_goal("Maximize salary")
    assert p.goal == "Maximize salary"
    assert p.source == "goal_default"
    assert p.weights.skill == 0.20


def test_kind_contributions_defined():
    assert "apply" in KIND_CONTRIBUTIONS
    assert "outreach" in KIND_CONTRIBUTIONS
    assert "learning" in KIND_CONTRIBUTIONS
    assert "interview_prep" in KIND_CONTRIBUTIONS
    assert "github_contribution" in KIND_CONTRIBUTIONS


def test_kind_apply_contributes_opportunity():
    v = KIND_CONTRIBUTIONS["apply"]
    assert v.opportunity > 0.5
    assert v.skill == 0.0
    assert v.network == 0.0


def test_kind_outreach_contributes_network():
    v = KIND_CONTRIBUTIONS["outreach"]
    assert v.network > 0.3


def test_kind_learning_contributes_skill():
    v = KIND_CONTRIBUTIONS["learning"]
    assert v.skill > 0.4


def test_capital_value_computes():
    profile = CapitalProfile.for_goal("Get placed ASAP")
    val = capital_value("apply", profile, base_value=1.0)
    assert val > 0  # apply contributes opportunity which is weighted highly


def test_capital_value_outreach_for_network_goal():
    profile = CapitalProfile.for_goal("Build network")
    val = capital_value("outreach", profile)
    assert val > capital_value("apply", profile)  # outreach beats apply when networking


def test_capital_value_learning_for_growth_goal():
    profile = CapitalProfile.for_goal("Career growth (long-term)")
    val_learn = capital_value("learning", profile)
    val_apply = capital_value("apply", profile)
    assert val_learn > val_apply  # learning beats apply for long-term growth


def test_capital_vector_zero():
    z = CapitalVector.zero()
    assert z.resume == 0.0
    assert z.skill == 0.0
    assert z.network == 0.0
    assert z.interview == 0.0
    assert z.reputation == 0.0
    assert z.opportunity == 0.0
