from datetime import datetime, timezone, timedelta

# Freshness affects priority, not eligibility. An older posting is still
# actionable — it just loses the early-applicant advantage.
FRESHNESS_BANDS = [
    ("very_fresh", "Very Fresh", "🔥", 24, 10),
    ("fresh", "Fresh", "🟢", 72, 8),
    ("recent", "Recent", "🟡", 168, 5),
    ("active", "Active", "⚪", 720, 3),
    ("stale", "Stale", "🔴", None, 0),
]

FRESHNESS_LEVELS = {band[0]: band for band in FRESHNESS_BANDS}


def _coerce_utc(posted_at: datetime | None) -> datetime | None:
    if posted_at is None:
        return None
    if posted_at.tzinfo is None:
        return posted_at.replace(tzinfo=timezone.utc)
    return posted_at


def freshness_band(posted_at: datetime | None) -> dict:
    """Classify a posting into one of five freshness bands.

    Returns {level, label, emoji, hours_ago, days_ago, score}.
    Score is 0-10 and is meant to weight priority — never to gate.
    """
    posted_at = _coerce_utc(posted_at)
    if posted_at is None:
        return {"level": "unknown", "label": "Unknown", "emoji": "—",
                "hours_ago": None, "days_ago": None, "score": 0}

    delta = datetime.now(timezone.utc) - posted_at
    hours = max(0, delta.total_seconds() / 3600)

    for level, label, emoji, cutoff_hours, score in FRESHNESS_BANDS:
        if cutoff_hours is None or hours < cutoff_hours:
            return {
                "level": level,
                "label": label,
                "emoji": emoji,
                "hours_ago": round(hours, 1),
                "days_ago": round(hours / 24, 1),
                "score": score,
            }
    return {"level": "stale", "label": "Stale", "emoji": "🔴",
            "hours_ago": round(hours, 1), "days_ago": round(hours / 24, 1),
            "score": 0}


def freshness_score(posted_at: datetime | None) -> float:
    if posted_at is None:
        return 0.5

    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    delta = now - posted_at

    if delta < timedelta(hours=24):
        return 1.0
    elif delta < timedelta(days=2):
        return 0.8
    elif delta < timedelta(days=3):
        return 0.6
    elif delta < timedelta(days=7):
        return 0.4
    elif delta < timedelta(days=14):
        return 0.2
    else:
        return 0.05
