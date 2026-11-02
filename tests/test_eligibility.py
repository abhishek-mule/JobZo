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
    # Should block: exceeds hard cap of 5
    r = rule.check(Job(title="Engineer", description="Requires 5+ years", company="T"), PROFILE)
    assert not r.passed
    assert "5" in r.reason

    # Should block: exceeds user_years + buffer (1 + 0 = 1)
    r = rule.check(Job(title="Engineer", description="Requires 2+ years", company="T"), PROFILE)
    assert not r.passed
    assert "2+" in r.reason

    # Should pass
    r = rule.check(Job(title="Engineer", description="0-2 years experience", company="T"), PROFILE)
    assert r.passed

    r = rule.check(Job(title="Engineer", description="No experience required", company="T"), PROFILE)
    assert r.passed

    r = rule.check(Job(title="Engineer", description="", company="T"), PROFILE)
    assert r.passed

    # Custom hard cap
    custom = ExperienceRule(hard_max=3)
    r = custom.check(Job(title="Engineer", description="Requires 3+ years", company="T"), PROFILE)
    assert not r.passed
    assert "3" in r.reason


def test_seniority_rule():
    rule = SeniorityRule()
    # Hard reject for all users
    for title in ["Staff Engineer", "Principal Engineer", "Engineering Manager",
                   "Director of Engineering", "Head of AI", "VP Engineering",
                   "Lead Developer", "Chief Architect", "Distinguished Engineer"]:
        r = rule.check(Job(title=title, description="", company="T"), PROFILE)
        assert not r.passed, f"Expected blocked: {title}"

    # Soft reject for <3yr users (PROFILE has 1yr)
    for title in ["Senior Engineer", "Senior Software Engineer", "Sr Developer"]:
        r = rule.check(Job(title=title, description="", company="T"), PROFILE)
        assert not r.passed, f"Expected blocked for <3yr: {title}"

    # Should pass for junior titles
    for title in ["Backend Engineer", "SDE-I", "Software Engineer", "Junior Developer"]:
        r = rule.check(Job(title=title, description="", company="T"), PROFILE)
        assert r.passed, f"Expected passed: {title}"

    # Senior should pass for 3+yr users
    profile_3yr = {**PROFILE, "years_experience": 3}
    r = rule.check(Job(title="Senior Engineer", description="", company="T"), profile_3yr)
    assert r.passed, "Expected senior title to pass for 3yr user"


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
        (Job(title="Senior Engineer", description="", company="T"), False),
        (Job(title="Lead Developer", description="", company="T"), False),
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


def test_company_eligibility_rule():
    """Test CompanyEligibilityRule with company-level fields."""
    from services.eligibility import CompanyEligibilityRule
    from database.models import Company

    rule = CompanyEligibilityRule()
    job = Job(title="Engineer", description="", company="T")

    # If no company in DB, should pass
    r = rule.check(job, PROFILE)
    assert r.passed


if __name__ == "__main__":
    test_experience_rule()
    test_seniority_rule()
    test_visa_rule()
    test_location_rule()
    test_engine()
    print("All eligibility tests passed")
