"""Skills Knowledge Base — centralized skill registry with aliases, categories, and weights."""

import re
import yaml
from pathlib import Path
from typing import Any

SKILLS_DIR = Path(__file__).parent


def _load_yaml(name: str) -> dict[str, Any]:
    with open(SKILLS_DIR / name) as f:
        return yaml.safe_load(f)


_SKILL_DB: dict[str, dict] | None = None


def _build_db():
    global _SKILL_DB
    if _SKILL_DB is not None:
        return _SKILL_DB
    entries = _load_yaml("dictionary.yaml")
    db = {}
    for entry in entries:
        canonical = entry["name"]
        aliases = entry.get("aliases", [])
        all_names = [canonical.lower()] + [a.lower() for a in aliases]
        record = {
            "name": canonical,
            "category": entry.get("category", "Unknown"),
            "weight": entry.get("weight", 5),
        }
        for alias in all_names:
            db[alias] = record
    _SKILL_DB = db
    return db


def resolve_skill(text: str) -> str | None:
    """Resolve a raw skill mention to its canonical name. Returns None if unknown."""
    db = _build_db()
    key = text.strip().lower()
    record = db.get(key)
    if record:
        return record["name"]
    return None


def canonical_name(skill: str) -> str:
    """Get canonical name; returns input unchanged if not found."""
    return resolve_skill(skill) or skill


def skill_weight(skill: str) -> int:
    db = _build_db()
    key = skill.strip().lower()
    record = db.get(key)
    return record["weight"] if record else 1


def skill_category(skill: str) -> str:
    db = _build_db()
    key = skill.strip().lower()
    record = db.get(key)
    return record["category"] if record else "Unknown"


def all_canonical() -> list[str]:
    db = _build_db()
    seen: set[str] = set()
    result: list[str] = []
    for v in db.values():
        if v["name"] not in seen:
            seen.add(v["name"])
            result.append(v["name"])
    return result


def all_aliases() -> list[str]:
    db = _build_db()
    return list(db.keys())


def skill_patterns() -> list[tuple[re.Pattern, str]]:
    """Return list of (compiled_regex, canonical_name) for efficient JD scanning."""
    db = _build_db()
    seen: set[str] = set()
    patterns: list[tuple[re.Pattern, str]] = []
    for alias, record in db.items():
        if record["name"] in seen:
            continue
        seen.add(record["name"])
        names = [record["name"]] + [
            a for a, r in db.items()
            if r["name"] == record["name"] and a != record["name"].lower()
        ]
        # Build regex that matches any alias of this skill
        # Escape special regex chars in skill names
        escaped = [re.escape(n) for n in names]
        # Sort by length descending to match longer forms first
        escaped.sort(key=len, reverse=True)
        pattern = re.compile(r"(?<!\w)(" + "|".join(escaped) + r")(?!\w)", re.IGNORECASE)
        patterns.append((pattern, record["name"]))
    return patterns
