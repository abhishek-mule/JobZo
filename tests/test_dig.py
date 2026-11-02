import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.freshness import freshness_band
from services.dig import DigItem, qualifies
from services.trust import (
    TIER_SUSPICIOUS,
    TIER_TRUSTED,
    TIER_UNVERIFIED,
    TIER_VERIFIED,
    TrustResult,
)


def _trust(tier: str) -> TrustResult:
    return TrustResult(score=90.0, tier=tier)


def _item(tier: str, priority: float = 7.0, days_ago: float = 0.5,
          score: int = 80) -> DigItem:
    app = None
    job = None
    return DigItem(
        app=app,
        job=job,
        score=score,
        tier="",
        trust=_trust(tier),
        priority=priority,
        freshness={
            "level": freshness_band(datetime.now(timezone.utc) - timedelta(days=days_ago))["level"],
            "days_ago": days_ago,
        },
    )


def test_freshness_bands():
    now = datetime.now(timezone.utc)
    assert freshness_band(now - timedelta(hours=6))["level"] == "very_fresh"
    assert freshness_band(now - timedelta(days=2))["level"] == "fresh"
    assert freshness_band(now - timedelta(days=5))["level"] == "recent"
    assert freshness_band(now - timedelta(days=15))["level"] == "active"
    assert freshness_band(now - timedelta(days=40))["level"] == "stale"


def test_freshness_affects_priority_not_eligibility():
    """A 20-day-old posting is still valid — it just scores lower on freshness."""
    band = freshness_band(datetime.now(timezone.utc) - timedelta(days=20))
    assert band["level"] in ("active", "stale")
    assert band["score"] < 5


def test_suspicious_is_blocked():
    assert qualifies(_item(TIER_SUSPICIOUS)) is False


def test_unverified_is_allowed_by_default():
    """The critical fresher case: unknown local startups must remain diggable."""
    assert qualifies(_item(TIER_UNVERIFIED)) is True


def test_strict_excludes_unverified():
    assert qualifies(_item(TIER_UNVERIFIED), strict=True) is False
    assert qualifies(_item(TIER_TRUSTED), strict=True) is True
    assert qualifies(_item(TIER_VERIFIED), strict=True) is True


def test_fresh_only_filters_older_postings():
    assert qualifies(_item(TIER_TRUSTED, days_ago=1), fresh_only=True) is True
    assert qualifies(_item(TIER_TRUSTED, days_ago=5), fresh_only=True) is False


def test_min_priority_filter():
    assert qualifies(_item(TIER_TRUSTED, priority=7.0), min_priority=6.0) is True
    assert qualifies(_item(TIER_TRUSTED, priority=4.0), min_priority=6.0) is False


def test_max_days_filter():
    assert qualifies(_item(TIER_TRUSTED, days_ago=2), max_days=7) is True
    assert qualifies(_item(TIER_TRUSTED, days_ago=10), max_days=7) is False


def test_priority_is_ranking_not_gate():
    """Trust is the safety gate, priority is the ranking. Unverified can rank top."""
    low_trust_high_priority = qualifies(_item(TIER_UNVERIFIED, priority=8.5))
    assert low_trust_high_priority is True


if __name__ == "__main__":
    test_freshness_bands()
    test_freshness_affects_priority_not_eligibility()
    test_suspicious_is_blocked()
    test_unverified_is_allowed_by_default()
    test_strict_excludes_unverified()
    test_fresh_only_filters_older_postings()
    test_min_priority_filter()
    test_max_days_filter()
    test_priority_is_ranking_not_gate()
    print("\nAll dig tests passed!")