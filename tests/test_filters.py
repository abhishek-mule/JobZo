import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_keyword_scorer():
    from ai.scorer import _keyword_pre_score
    from database.models import Job

    job = Job(
        company="TestCo",
        title="Backend Engineer (Spring Boot)",
        description="Looking for a Spring Boot developer with React experience. Backend role with microservices.",
        skills=["Spring Boot", "Java"],
    )

    score = _keyword_pre_score(job)
    assert score >= 30, f"Expected >= 30, got {score}"
    print(f"Keyword score: {score}")


def test_skill_overlap():
    from ai.scorer import _skill_overlap
    from database.models import Job

    job = Job(
        company="TestCo",
        title="Backend Engineer",
        description="Spring Boot, PostgreSQL, Docker",
        skills=["Spring Boot", "PostgreSQL"],
    )

    overlap, matched = _skill_overlap(job, ["spring boot", "postgresql", "docker", "react"])
    assert overlap > 0.5, f"Expected > 0.5, got {overlap}"
    assert len(matched) >= 2, f"Expected >= 2 matched skills, got {matched}"
    print(f"Skill overlap: {overlap}, matched: {matched}")


def test_experience_match():
    from ai.scorer import _experience_match
    from database.models import Job

    job = Job(
        company="TestCo",
        title="Junior Developer",
        description="0-2 years experience required",
        experience_required="0-2 years",
    )

    match, reason = _experience_match(job, user_experience_years=1)
    assert match > 0.5, f"Expected > 0.5, got {match}"
    assert "0-2" in reason, f"Expected reason to mention 0-2, got: {reason}"
    print(f"Experience match: {match}, reason: {reason}")


def test_freshness():
    from services.freshness import freshness_score
    from datetime import timedelta, timezone

    now = datetime.now(timezone.utc)

    fresh = freshness_score(now - timedelta(hours=6))
    assert fresh == 1.0, f"Fresh job should score 1.0, got {fresh}"

    old = freshness_score(now - timedelta(days=30))
    assert old < 0.1, f"Old job should score near 0, got {old}"

    print(f"Freshness: fresh={fresh}, old={old}")


def test_dedup_key():
    from services.collector import _make_dedup_key

    k1 = _make_dedup_key("Atlassian", "Backend Engineer", "Remote")
    k2 = _make_dedup_key("atlassian  ", "  backend engineer", "remote")
    k3 = _make_dedup_key("Atlassian", "Backend Engineer", "Bangalore")

    assert k1 == k2, "Same job should produce same dedup key"
    assert k1 != k3, "Different location should produce different key"
    print("Dedup key: PASS")


if __name__ == "__main__":
    test_keyword_scorer()
    test_skill_overlap()
    test_experience_match()
    test_freshness()
    test_dedup_key()
    print("\nAll filter tests passed!")
