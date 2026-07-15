"""Tests for services/eligibility.py — all rules."""

from database.models import Job
from services.eligibility import (
    EligibilityEngine,
    ExperienceRule,
    SeniorityRule,
    VisaRule,
    LocationRule,
    RemotePolicyRule,
)

PROFILE = {"years_experience": 1, "preferred_locations": "Remote (India)"}


def test_experience_rule():
    rule = ExperienceRule()
    # Should block
    r = rule.check(Job(title="Engineer", description="Requires 5+ years", company="T"), PROFILE)
    assert not r.passed
    assert "5+" in r.reason

    # Should pass
    r = rule.check(Job(title="Engineer", description="0-2 years experience", company="T"), PROFILE)
    assert r.passed

    r = rule.check(Job(title="Engineer", description="No experience required", company="T"), PROFILE)
    assert r.passed

    r = rule.check(Job(title="Engineer", description="", company="T"), PROFILE)
    assert r.passed


def test_seniority_rule():
    rule = SeniorityRule()
    # Should block
    for title in ["Staff Engineer", "Principal Engineer", "Engineering Manager",
                   "Director of Engineering", "Head of AI", "VP Engineering"]:
        r = rule.check(Job(title=title, description="", company="T"), PROFILE)
        assert not r.passed, f"Expected blocked: {title}"

    # Should pass
    for title in ["Backend Engineer", "SDE-I", "Software Engineer", "Junior Developer"]:
        r = rule.check(Job(title=title, description="", company="T"), PROFILE)
        assert r.passed, f"Expected passed: {title}"


def test_visa_rule():
    rule = VisaRule()
    r = rule.check(Job(title="Engineer", description="US Citizen required", company="T"), PROFILE)
    assert not r.passed

    r = rule.check(Job(title="Engineer", description="Security clearance required", company="T"), PROFILE)
    assert not r.passed

    r = rule.check(Job(title="Engineer", description="Green card required", company="T"), PROFILE)
    assert not r.passed

    r = rule.check(Job(title="Engineer", description="Java developer needed", company="T"), PROFILE)
    assert r.passed


def test_location_rule():
    rule = LocationRule()
    r = rule.check(Job(title="Engineer", description="", location="San Francisco, USA", company="T"), PROFILE)
    assert not r.passed

    r = rule.check(Job(title="Engineer", description="", location="Remote", company="T"), PROFILE)
    assert r.passed

    r = rule.check(Job(title="Engineer", description="", location="Bangalore, India", company="T"), PROFILE)
    assert r.passed

    r = rule.check(Job(title="Engineer", description="Must be located in the United States", location="", company="T"), PROFILE)
    assert not r.passed


def test_engine():
    engine = EligibilityEngine()

    # Ineligible cases
    cases = [
        (Job(title="Staff Engineer", description="10+ years required", company="T"), False),
        (Job(title="Backend Engineer", description="US Citizen only", company="T"), False),
        (Job(title="VP Engineering", description="", company="T"), False),
        (Job(title="Engineer", description="On-site in San Francisco", company="T"), False),
    ]
    for job, expected_eligible in cases:
        r = engine.check(job, PROFILE)
        assert r.passed == expected_eligible, f"Mismatch for {job.title}: expected eligible={expected_eligible}, got {r.passed} ({r.reason})"

    # Eligible cases
    good = [
        Job(title="Backend Engineer", description="1-2 years Java experience", company="T"),
        Job(title="SDE-I", description="Building APIs", location="Bangalore", company="T"),
        Job(title="Software Engineer", description="0-2 years Python", location="Remote", company="T"),
    ]
    for job in good:
        r = engine.check(job, PROFILE)
        assert r.passed, f"Expected eligible for {job.title}: {r.reason}"


if __name__ == "__main__":
    test_experience_rule()
    test_seniority_rule()
    test_visa_rule()
    test_location_rule()
    test_engine()
    print("All eligibility tests passed")
