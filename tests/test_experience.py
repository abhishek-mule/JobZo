"""Tests for services/experience.py — 50+ cases."""

from services.experience import parse_experience


def check(text, exp_min, exp_max, exp_conf=None):
    r = parse_experience(text)
    assert r.min_years == exp_min, f"'{text}': expected min={exp_min}, got {r.min_years}"
    assert r.max_years == exp_max, f"'{text}': expected max={exp_max}, got {r.max_years}"
    if exp_conf is not None:
        assert r.confidence >= exp_conf, f"'{text}': expected conf>={exp_conf}, got {r.confidence}"


def test_no_experience():
    check("", None, None)
    check("   ", None, None)
    check(None, None, None)
    check("Bachelor's degree in Computer Science", None, None)
    check("PhD in Physics preferred", None, None)
    check("Strong problem solving skills", None, None)
    check("Excellent communication skills", None, None)


def test_entry_level():
    check("Entry level position", 0, 1)
    check("Entry-level backend engineer", 0, 1)
    check("Fresh graduate welcome", 0, 1)
    check("Fresher opportunity", 0, 1)
    check("No experience required", 0, 1)
    check("No prior experience necessary", 0, 1)


def test_minimum_plus():
    check("5+ years of experience", 5, None)
    check("8+ years", 8, None)
    check("10+ yrs", 10, None)
    check("15+ years of experience in Java", 15, None)
    check("3+ years experience with Spring Boot", 3, None)


def test_minimum_explicit():
    check("Minimum 5 years of experience", 5, None)
    check("min 3 years", 3, None)
    check("At least 2 years of experience", 2, None)
    check("at least 5 yrs", 5, None)
    check("Must have 3 years of experience", 3, None)
    check("Requires 7 years experience", 7, None)
    check("Needs 2 years of experience", 2, None)
    check("Looking for 4+ years of experience", 4, None)
    check("Seeks 5 years experience", 5, None)


def test_exact_years():
    check("5 years of experience", 5, None)
    check("3 yrs experience", 3, None)
    check("2 years work experience", 2, None)
    check("10 years professional experience", 10, None)
    check("1 year of experience", 1, None)


def test_ranges():
    check("4-6 years", 4, 6)
    check("4–6 years", 4, 6)
    check("2-3 yrs", 2, 3)
    check("5-7 years of experience", 5, 7)
    check("8 - 10 years", 8, 10)
    check("1 to 3 years", 1, 3)
    check("0-2 years experience", 0, 2)
    check("1-2 years", 1, 2)
    check("3 to 5 years", 3, 5)


def test_word_numbers():
    check("five years of experience", 5, None)
    check("three years experience", 3, None)
    check("minimum five years", 5, None)
    check("at least seven years", 7, None)
    check("three to five years", 3, 5)


def test_preferred():
    check("10+ years preferred", 10, None)
    check("5 years preferred", 5, None)


def test_education_plus():
    check("BS + 5 years of experience", 5, None)
    check("MS + 3 years", 3, None)
    check("PhD + 0 years experience", 0, None)


def test_uncertain():
    r = parse_experience("Several years of experience")
    assert r.min_years == 3
    assert r.confidence == 0.3

    r = parse_experience("Multiple years of experience")
    assert r.min_years == 3
    assert r.confidence == 0.3


def test_low_confidence():
    r = parse_experience("years of experience required")
    assert r.min_years is None
    assert r.max_years is None
    assert r.confidence == 0.2


def test_noise_within_phrases():
    check("5+ years of experience in Java with Spring Boot", 5, None)
    check("3-5 years of hands-on experience", 3, 5)
    check("Minimum 8 years of professional software development experience", 8, None)
    check("At least 2+ years of experience", 2, None)


# ── Collect common test patterns ─────────────────────────────────────────────

ALL_TESTS = [
    # (text, min, max)
    # No experience
    ("", None, None),
    ("Bachelor's degree required", None, None),
    ("Entry level", 0, 1),
    ("Fresh graduate", 0, 1),
    ("Fresher", 0, 1),
    ("No experience required", 0, 1),
    # Minimum +
    ("5+ years", 5, None),
    ("8+ yrs", 8, None),
    ("10+ years of experience", 10, None),
    ("3+ years", 3, None),
    # Minimum explicit
    ("Minimum 5 years", 5, None),
    ("min 3 years", 3, None),
    ("At least 2 years", 2, None),
    ("Must have 3 years", 3, None),
    ("Requires 7 years", 7, None),
    # Exact
    ("5 years of experience", 5, None),
    ("3 yrs experience", 3, None),
    # Ranges
    ("4-6 years", 4, 6),
    ("2-3 yrs", 2, 3),
    ("5-7 years of experience", 5, 7),
    ("0-2 years", 0, 2),
    ("3 to 5 years", 3, 5),
    # Word numbers
    ("five years of experience", 5, None),
    ("three years", 3, None),
    ("minimum five years", 5, None),
    # Preferred
    ("10+ years preferred", 10, None),
    ("5 years preferred", 5, None),
    # Education +
    ("BS + 5 years", 5, None),
    ("MS + 3 years", 3, None),
    # Uncertain
    ("Several years", 3, None),
    ("Multiple years", 3, None),
    # Low confidence
    ("years of experience required", None, None),
    # Edge cases
    ("0 years of experience", 0, 1),
    ("1-2 years of React experience", 1, 2),
    ("7+ years of software engineering experience", 7, None),
    ("6 years of professional experience as a backend engineer", 6, None),
]

if __name__ == "__main__":
    failed = 0
    for text, exp_min, exp_max in ALL_TESTS:
        try:
            check(text, exp_min, exp_max)
        except AssertionError as e:
            print(f"FAIL: {e}")
            failed += 1

    if failed:
        print(f"\n{len(ALL_TESTS) - failed}/{len(ALL_TESTS)} passed, {failed} failed")
    else:
        print(f"All {len(ALL_TESTS)} tests passed")
