"""Tests for Person model."""

import os
os.environ.setdefault("JOBZO_DB_PATH", "/tmp/jobzo_person_test.db")
os.environ["JOBZO_SKIP_MIGRATIONS"] = "1"

from database.connection import get_engine
from database.models import Base
from domain.person import Person, Project, InterviewRecord, EmploymentStatus


def setup_module():
    engine = get_engine(os.environ["JOBZO_DB_PATH"])
    Base.metadata.create_all(engine)


def test_person_defaults():
    p = Person()
    assert p.name == ""
    assert p.employment_status == EmploymentStatus.ACTIVELY_SEARCHING
    assert p.availability_hours_per_week == 15
    assert p.skills == []


def test_person_with_skills():
    p = Person(name="Alice", skills=["Python", "Django", "PostgreSQL"])
    assert p.skill_count == 3


def test_person_skill_gaps():
    p = Person(skills=["Python"])
    gaps = p.skill_gaps(["Python", "Django", "Kubernetes"])
    assert gaps == ["Django", "Kubernetes"]


def test_person_no_gaps():
    p = Person(skills=["Python", "Django"])
    assert p.skill_gaps(["Python", "Django"]) == []


def test_person_offer_rate_no_history():
    p = Person()
    assert p.offer_rate == 0.0


def test_person_offer_rate():
    p = Person(interview_history=[
        InterviewRecord(company="A", role="Eng", result="offer"),
        InterviewRecord(company="B", role="Eng", result="rejected"),
        InterviewRecord(company="C", role="Eng", result="offer"),
    ])
    assert p.offer_rate == 2 / 3


def test_person_interview_count():
    p = Person(interview_history=[
        InterviewRecord(company="A", role="Eng"),
        InterviewRecord(company="B", role="Eng"),
    ])
    assert p.interview_count == 2


def test_project_dataclass():
    proj = Project(
        name="JobZo",
        description="Career optimization engine",
        skills=["Python", "SQLAlchemy"],
        role="Sole developer",
        url="https://github.com/test/jobzo",
    )
    assert proj.name == "JobZo"
    assert "Python" in proj.skills


def test_person_to_dict():
    p = Person(name="Bob", skills=["Go"], years_of_experience=3.0)
    d = p.to_dict()
    assert d["name"] == "Bob"
    assert d["skill_count"] == 1
    assert d["years_of_experience"] == 3.0


def test_employment_status_values():
    assert EmploymentStatus.EMPLOYED.value == "employed"
    assert EmploymentStatus.NOTICE_PERIOD.value == "notice_period"
    assert EmploymentStatus.OPEN_TO_WORK.value == "open_to_work"
    assert EmploymentStatus.ACTIVELY_SEARCHING.value == "actively_searching"
