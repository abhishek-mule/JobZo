from __future__ import annotations
import json
from datetime import datetime, timedelta
from pathlib import Path

QUOTA_FILE = Path(__file__).parent.parent / "config" / "quotas.json"

PLATFORM_LIMITS = {
    "yc_workatastartup": 5,
    "yc": 5,
    "rss": None,
    "company_pages": None,
    "manual": None,
}

def _ensure_file():
    QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not QUOTA_FILE.exists():
        QUOTA_FILE.write_text("[]")

def _load() -> list[dict]:
    _ensure_file()
    try:
        return json.loads(QUOTA_FILE.read_text())
    except (json.JSONDecodeError, FileNotFoundError):
        return []

def _save(entries: list[dict]):
    QUOTA_FILE.write_text(json.dumps(entries, indent=2))

def record_submission(platform: str, company: str = "", role: str = ""):
    entries = _load()
    entries.append({
        "platform": platform,
        "company": company,
        "role": role,
        "timestamp": datetime.utcnow().isoformat(),
    })
    _save(entries)

def quota_usage() -> list[dict]:
    entries = _load()
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)

    recent = [e for e in entries if datetime.fromisoformat(e["timestamp"]) >= week_ago]

    usage: dict[str, int] = {}
    for e in recent:
        p = e["platform"]
        usage[p] = usage.get(p, 0) + 1

    results = []
    for platform, limit in PLATFORM_LIMITS.items():
        used = usage.get(platform, 0)
        remaining = max(0, (limit or 999) - used) if limit else None
        results.append({
            "platform": platform,
            "used": used,
            "limit": limit,
            "remaining": remaining,
            "exhausted": limit is not None and used >= limit,
        })
    return results

def is_exhausted(platform: str) -> bool:
    for q in quota_usage():
        if q["platform"] == platform:
            return q["exhausted"]
    return False

def reset_quotas():
    if QUOTA_FILE.exists():
        QUOTA_FILE.write_text("[]")