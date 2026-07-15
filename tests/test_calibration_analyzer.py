"""Tests for root-cause calibration analyzer."""

import os
os.environ.setdefault("JOBZO_DB_PATH", "/tmp/jobzo_calibrate_analysis_test.db")
os.environ["JOBZO_SKIP_MIGRATIONS"] = "1"

from database.connection import get_engine
from database.models import Base
from domain.analytics import CalibrationAnalyzer, DimensionError, RootCause


def setup_module():
    engine = get_engine(os.environ["JOBZO_DB_PATH"])
    Base.metadata.create_all(engine)


def test_analyze_no_dimensions():
    rc = CalibrationAnalyzer.analyze(
        predicted_probability=0.5,
        score_vector={},
        actual_outcome=0.0,
    )
    assert rc.top_cause == "no_dimension_data"


def test_analyze_correct_prediction():
    rc = CalibrationAnalyzer.analyze(
        predicted_probability=0.0,
        score_vector={"skills": 50, "experience": 30},
        actual_outcome=0.0,
    )
    assert len(rc.dimensions) == 2


def test_analyze_overweight_dimension():
    """When predicted is too high and a dimension contributed most."""
    rc = CalibrationAnalyzer.analyze(
        predicted_probability=0.8,
        score_vector={"skills": 80, "experience": 20},
        actual_outcome=0.0,
    )
    assert rc.error < 0  # predicted > observed
    assert len(rc.dimensions) == 2
    # Some dimension should be "overweight"
    impacts = [d.impact for d in rc.dimensions]
    assert "overweight" in impacts


def test_analyze_underweight_dimension():
    """When predicted is too low and a dimension contributed too little."""
    rc = CalibrationAnalyzer.analyze(
        predicted_probability=0.1,
        score_vector={"skills": 30, "experience": 70},
        actual_outcome=1.0,
    )
    assert rc.error > 0  # predicted < observed
    assert len(rc.dimensions) == 2


def test_root_cause_summary():
    rc = RootCause(
        predicted_probability=0.7,
        observed_probability=0.2,
        error=-0.5,
        dimensions=[
            DimensionError("skills", 0.4, 0.1, -0.3, "overweight"),
            DimensionError("experience", 0.3, 0.1, -0.2, "overweight"),
        ],
        top_cause="skills",
        recommendation="Reduce skills weight.",
    )
    s = rc.summary()
    assert s["predicted"] == 0.7
    assert s["top_cause"] == "skills"
    assert len(s["dimensions"]) == 2


def test_batch_analyze_empty():
    results = CalibrationAnalyzer.batch_analyze([])
    assert results == []


def test_batch_analyze_skips_no_app_id():
    results = CalibrationAnalyzer.batch_analyze([
        {"predicted_probability": 0.5, "score_vector": {"skills": 50}},
    ])
    assert results == []
