import json
from datetime import datetime
from pathlib import Path

LOG_FILE = Path(__file__).parent.parent / "logs" / "applications.jsonl"


def log_application(
    company: str = "",
    ats: str = "",
    time_seconds: int = 0,
    fields_filled: int = 0,
    fields_manual: int = 0,
    resume: str = "",
    cover_letter: str = "",
    submitted: bool = False,
    **extra,
):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.utcnow().isoformat(),
        "company": company,
        "ats": ats,
        "time_seconds": time_seconds,
        "fields_filled": fields_filled,
        "fields_manual": fields_manual,
        "resume": resume,
        "cover_letter": cover_letter,
        "submitted": submitted,
        **extra,
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def get_stats() -> dict:
    if not LOG_FILE.exists():
        return {}
    entries = []
    with open(LOG_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    if not entries:
        return {}
    total = len(entries)
    submitted = sum(1 for e in entries if e.get("submitted"))
    avg_time = sum(e.get("time_seconds", 0) for e in entries) / total if total else 0
    avg_filled = sum(e.get("fields_filled", 0) for e in entries) / total if total else 0
    return {
        "total": total,
        "submitted": submitted,
        "avg_time_seconds": round(avg_time, 1),
        "avg_fields_filled": round(avg_filled, 1),
    }
