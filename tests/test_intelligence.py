"""Tests for Phase 4A: Application Intelligence."""

import os
from pathlib import Path

os.environ.setdefault("JOBZO_DB_PATH", "/tmp/jobzo_intel_test.db")
os.environ["JOBZO_SKIP_MIGRATIONS"] = "1"

from database.connection import get_engine
from database.models import Base, Application, Job
from tracker.intelligence import (
    compute_quality_score,
    ATSSuccessStats,
    company_stats,
    resume_effectiveness,
)
from resumes.registry import get_registry


def _clean_db():
    db_path = Path(os.environ["JOBZO_DB_PATH"])
    if db_path.exists():
        db_path.unlink()


def setup_module():
    _clean_db()
    engine = get_engine(os.environ["JOBZO_DB_PATH"])
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_quality_score_with_resume():
    registry = get_registry()
    meta = registry.get("java_v1")
    assert meta is not None

    job = Job(
        company="BrowserStack",
        title="Backend Engineer",
        description="Java Spring Boot engineer with PostgreSQL and Kafka experience",
    )

    qs = compute_quality_score(job, meta)
    assert qs.overall > 0
    assert isinstance(qs.keyword_match, (int, float))
    assert isinstance(qs.expected_interview_probability, (int, float))
    assert len(qs.matched_skills) > 0 or len(qs.missing_skills) > 0


def test_quality_score_with_frontend_jd():
    registry = get_registry()
    meta = registry.get("java_v1")
    assert meta is not None

    job = Job(
        company="TestCo",
        title="Frontend Developer",
        description="React TypeScript CSS HTML TailwindCSS frontend role",
    )

    qs = compute_quality_score(job, meta)
    assert len(qs.missing_skills) > 0


def test_quality_score_no_resume():
    job = Job(company="TestCo", title="Engineer", description="Some job description")
    qs = compute_quality_score(job, None)
    assert qs.overall == 0
    assert len(qs.suggestions) > 0


def test_ats_success_stats_empty():
    stats = company_stats("NonexistentCompany")
    assert stats.total_applications == 0
    assert stats.interview_rate == 0.0
    assert stats.ghost_rate == 0.0


def test_ats_success_stats_format():
    stats = ATSSuccessStats(
        total_applications=10,
        interviews=3,
        rejections=4,
        ghosted=3,
        interview_rate=30.0,
        ghost_rate=30.0,
    )
    text = stats.format_text()
    assert "Applications: 10" in text
    assert "Interview: 30%" in text


def test_resume_effectiveness_empty():
    results = resume_effectiveness("nonexistent_resume")
    assert isinstance(results, dict)


def test_quality_score_suggestions():
    registry = get_registry()
    meta = registry.get("frontend_v2")
    assert meta is not None

    job = Job(
        company="BackendCo",
        title="Backend Engineer",
        description="Java Spring Boot Kafka Docker PostgreSQL microservices backend role",
    )

    qs = compute_quality_score(job, meta)
    assert len(qs.suggestions) > 0
    has_add_suggestion = any("Add" in s for s in qs.suggestions)
    assert has_add_suggestion, f"No 'Add' suggestions found: {qs.suggestions}"
