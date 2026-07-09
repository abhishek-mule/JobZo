from datetime import datetime, timezone, timedelta


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
