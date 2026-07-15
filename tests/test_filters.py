import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_retriever_skill_matching():
    """Test that the retriever matches skills from job descriptions."""
    from ai.retriever import retrieve, _match_skills

    user_skills = ["spring boot", "postgresql", "docker", "react"]
    job_skills = ["spring boot", "postgresql", "docker"]

    expanded_resume = {"spring boot": 1.0, "postgresql": 1.0, "docker": 1.0, "react": 1.0}
    expanded_job = {"spring boot": 1.0, "postgresql": 1.0, "docker": 1.0}

    matched = _match_skills(user_skills, job_skills, expanded_resume, expanded_job)
    assert matched["overlap"] > 0.5, f"Expected > 0.5 overlap, got {matched['overlap']}"
    assert "spring boot" in matched["matched"]
    print(f"Skill matching: overlap={matched['overlap']}, matched={matched['matched']}")


def test_retriever_experience_fit():
    """Test experience fit scoring in the retriever."""
    from ai.retriever import _experience_match

    match, reason = _experience_match("0-2 years experience required", "0-2 years", 1)
    assert match > 0.5, f"Expected > 0.5 for 1yr vs 0-2yr, got {match}"
    assert "your 1yr matches" in reason, f"Expected match in reason, got: {reason}"
    print(f"Experience: match={match}, reason={reason}")

    # Overqualified
    over, over_reason = _experience_match("0-2 years experience", "0-2 years", 5)
    assert over < 1.0, f"Expected < 1.0 for overqualified, got {over}"
    print(f"Overqualified: match={over}, reason={over_reason}")


def test_retriever_location_fit():
    """Test location fit scoring in the retriever."""
    from ai.retriever import _location_match

    remote_match, reason = _location_match("Remote", True)
    assert remote_match == 1.0, f"Expected 1.0 for remote, got {remote_match}"
    print(f"Remote: match={remote_match}, reason={reason}")

    onsite_match, reason = _location_match("Mumbai, India", False)
    assert onsite_match == 0.5, f"Expected 0.5 for on-site, got {onsite_match}"
    print(f"On-site: match={onsite_match}, reason={reason}")


def test_retriever_full_pipeline():
    """Test the full retrieval pipeline returns RankedOpportunity."""
    from ai.retriever import retrieve
    from database.models import Job

    job = Job(
        company="TestCo",
        title="Backend Engineer (Spring Boot)",
        description="Looking for a Spring Boot developer with React experience. Backend role with microservices.",
        skills=["Spring Boot", "Java"],
        experience_required="0-2 years",
        location="Remote",
        remote=True,
    )

    opp = retrieve(job, ["spring boot", "postgresql", "docker", "react"], 1)
    assert opp is not None, "Pipeline should retrieve matching job"
    assert opp.composite_score() > 0, f"Expected positive score, got {opp.composite_score()}"
    assert opp.tier() != "ignore", f"Expected non-ignore tier, got {opp.tier()}"
    assert "spring boot" in opp.matched_skills or "Spring Boot" in opp.matched_skills
    print(f"Pipeline: {opp.company} - {opp.title}: score={opp.composite_score()}, tier={opp.tier()}")


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
    test_retriever_skill_matching()
    test_retriever_experience_fit()
    test_retriever_location_fit()
    test_retriever_full_pipeline()
    test_freshness()
    test_dedup_key()
    print("\nAll filter tests passed!")
