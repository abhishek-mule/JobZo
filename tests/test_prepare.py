"""Tests for Phase 3: Career Preparation Engine."""

from resumes.prepare import prepare


BACKEND_JD = """
Senior Backend Engineer - Java/Spring Boot

We are looking for a Senior Backend Engineer with strong Java and Spring Boot experience.
You will build REST APIs, work with PostgreSQL and Redis, and deploy on AWS using Docker and Kubernetes.

Requirements:
- 5+ years of Java backend development
- Experience with microservices architecture
- Knowledge of Kafka and message queues
"""

FRONTEND_JD = """
Frontend Developer - React

Build beautiful UIs with React, TypeScript, and TailwindCSS.
Requirements:
- Strong React and TypeScript skills
- CSS and HTML expertise
- Experience with REST APIs
"""


def test_prepare_returns_plan():
    plan = prepare("TestCo", "Backend Engineer", BACKEND_JD)
    assert plan.company == "TestCo"
    assert plan.title == "Backend Engineer"
    assert plan.analysis is not None
    assert len(plan.analysis.skills) > 0


def test_prepare_generates_sections():
    plan = prepare("TestCo", "Backend Engineer", BACKEND_JD)
    assert len(plan.sections) > 0
    topics = [s.topic for s in plan.sections]
    assert "Java" in topics
    assert "Spring Boot" in topics


def test_prepare_estimated_time_positive():
    plan = prepare("TestCo", "Backend Engineer", BACKEND_JD)
    assert plan.total_estimated_minutes > 0


def test_prepare_checklist():
    plan = prepare("TestCo", "Backend Engineer", BACKEND_JD)
    assert len(plan.checklist) >= 3


def test_prepare_senior_adds_system_design():
    plan = prepare("TestCo", "Senior Backend Engineer", BACKEND_JD)
    topics = [s.topic for s in plan.sections]
    assert "System Design" in topics


def test_prepare_frontend():
    plan = prepare("TestCo", "Frontend Developer", FRONTEND_JD)
    assert len(plan.sections) > 0
    assert plan.total_estimated_minutes > 0


def test_prepare_empty_jd():
    plan = prepare("TestCo", "Unknown", "")
    assert plan.analysis is not None
    # Should still generate generic sections
    assert len(plan.sections) >= 1


def test_prepare_adds_behavioral():
    plan = prepare("TestCo", "Backend Engineer", BACKEND_JD)
    topics = [s.topic for s in plan.sections]
    assert "Behavioral" in topics


def test_prepare_format_text():
    plan = prepare("TestCo", "Backend Engineer", BACKEND_JD)
    text = plan.format_text()
    assert "Preparing for" in text
    assert "TestCo" in text
    assert "Study Plan" in text
    assert "Estimated Prep Time" in text
    assert "Checklist" in text
