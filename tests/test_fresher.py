import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.fresher import (
    FRESHER_CONFIRMED,
    FRESHER_EXPERIENCED,
    FRESHER_HIGH,
    FRESHER_INSUFFICIENT,
    FRESHER_PLAUSIBLE,
    classify_fresher,
    is_india_location,
)


class _Company:
    def __init__(self, fresher_friendly=False, graduate_roles=False, internship=False):
        self.fresher_friendly = fresher_friendly
        self.graduate_roles = graduate_roles
        self.internship = internship


def test_graduate_trainee_confirmed():
    r = classify_fresher(title="Graduate Trainee", description="", experience_required="")
    assert r.status == FRESHER_CONFIRMED


def test_intern_confirmed():
    r = classify_fresher(title="Intern - Data Science", description="")
    assert r.status == FRESHER_CONFIRMED


def test_staff_engineer_experienced():
    r = classify_fresher(title="Staff Engineer")
    assert r.status == FRESHER_EXPERIENCED


def test_senior_title_experienced():
    r = classify_fresher(title="Senior Backend Engineer")
    assert r.status == FRESHER_EXPERIENCED


def test_two_plus_years_experienced():
    # THE reported bug: "SRE (2+ Years)" was confirmed. Explicit 2+ must be experienced.
    r = classify_fresher(title="Site Reliability Engineer (2+ Years)", description="")
    assert r.status == FRESHER_EXPERIENCED
    assert any("2" in n for n in r.negatives)


def test_two_to_four_years_experienced():
    # "Business Analyst (2-4 years)" must be EXPERIENCED, not plausible/confirmed.
    r = classify_fresher(title="Business Analyst", description="Requires 2-4 years of experience")
    assert r.status == FRESHER_EXPERIENCED


def test_explicit_two_plus_beats_company_hint():
    # Company being fresher-friendly must NEVER override an explicit 2+ requirement.
    c = _Company(fresher_friendly=True, graduate_roles=True)
    r = classify_fresher(title="Site Reliability Engineer (2+ Years)", description="", company=c)
    assert r.status == FRESHER_EXPERIENCED


def test_zero_to_one_year_confirmed():
    r = classify_fresher(title="Backend Engineer", description="0-1 years of experience")
    assert r.status == FRESHER_CONFIRMED


def test_description_fresher_signal_confirmed():
    r = classify_fresher(title="Backend Engineer",
                         description="We are looking for a fresher with strong Java skills")
    assert r.status == FRESHER_CONFIRMED


def test_generic_role_at_grad_company_is_high():
    # No explicit evidence, but junior-track role + graduate-hiring company = HIGH.
    c = _Company(fresher_friendly=True)
    r = classify_fresher(title="Software Engineer, Android", description="", company=c)
    assert r.status == FRESHER_HIGH


def test_non_junior_title_at_grad_company_is_plausible():
    c = _Company(graduate_roles=True)
    r = classify_fresher(title="Content Writer", description="", company=c)
    assert r.status == FRESHER_PLAUSIBLE


def test_no_signal_insufficient():
    r = classify_fresher(title="Software Engineer", description="")
    assert r.status == FRESHER_INSUFFICIENT


def test_missing_yoe_is_not_fresher():
    r = classify_fresher(title="Platform Engineer", description="", company=None)
    assert r.status == FRESHER_INSUFFICIENT


def test_probability_map():
    assert classify_fresher(title="Graduate Trainee").prob == 0.95
    assert classify_fresher(title="Staff Engineer").prob == 0.10
    assert classify_fresher(title="Software Engineer").prob == 0.40


def test_is_india_location():
    assert is_india_location("Bengaluru, India")
    assert is_india_location("Hyderabad")
    assert not is_india_location("London, UK")
    assert not is_india_location("")
    assert not is_india_location("New York, United States")
    assert not is_india_location("Kuala Lumpur, Malaysia")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))