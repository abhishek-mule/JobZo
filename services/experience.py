"""Parse experience requirements from job descriptions.

Normalizes free-text like "5+ years", "4-6 years", "minimum 3 yrs" into
(floor, ceiling) tuples with confidence. Single responsibility.
"""

import re
from dataclasses import dataclass

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}


@dataclass
class ExperienceRequirement:
    min_years: int | None = None
    max_years: int | None = None
    confidence: float = 1.0


def _to_number(word: str) -> int | None:
    w = word.lower().replace(",", "")
    if w.isdigit():
        return int(w)
    return NUMBER_WORDS.get(w)


def parse_experience(text: str) -> ExperienceRequirement:
    """Parse experience requirement from raw text.

    Returns (min, max, confidence). (None, None, 0.0) if no requirement found.
    """
    if not text or not text.strip():
        return ExperienceRequirement(None, None, 0.0)

    t = text.lower().strip()

    # --- "BS + 5 years" or "PhD + 0 years" (runs before no_exp to avoid 0-year override) ---
    m = re.search(r"(?:bs|ms|phd|bachelor|master|doctorate)\s*\+\s*(\d+)", t)
    if m:
        val = int(m.group(1))
        if 0 <= val <= 50:
            return ExperienceRequirement(val, None, 0.6)

    # Early exit: explicitly says no experience needed
    no_exp_patterns = [
        r"no\s+(?:prior|previous)?\s*experience\s*(?:necessary|required|needed)?",
        r"entry[\s-]*level",
        r"fresh\s+graduate",
        r"fresher",
        r"no\s+years?\s+of\s+experience",
        r"0\s+years?(?:\s+of)?\s+experience",
    ]
    for pat in no_exp_patterns:
        if re.search(pat, t):
            return ExperienceRequirement(0, 1, 0.9)

    # --- Range patterns: "X-Y years", "X to Y years" ---
    range_pats = [
        # "4-6 years", "4 – 6 years", "2-3 yrs"
        r"(\d+)\s*(?:–|-|to)\s*(\d+)\s*(?:years?|yrs?)",
        # "5 - 7 years of experience"
        r"(\d+)\s*-\s*(\d+)\s*\+\s*years?",  # "5-7+ years" (min=5, max=7)
    ]
    for pat in range_pats:
        m = re.search(pat, t)
        if m:
            lo = int(m.group(1))
            hi = int(m.group(2))
            if lo <= hi <= 50:
                return ExperienceRequirement(lo, hi, 0.95)

    # Word range: "three to five years"
    m = re.search(r"(?:(?:from|between)\s+)?(\w+)\s*(?:–|-|to)\s*(\w+)\s+(?:years?|yrs?)", t)
    if m:
        lo = _to_number(m.group(1))
        hi = _to_number(m.group(2))
        if lo is not None and hi is not None and lo <= hi <= 50:
            return ExperienceRequirement(lo, hi, 0.85)

    # --- Minimum patterns: "5+ years", "minimum 5 years", "at least 5" ---
    min_pats = [
        # "5+ years", "8+ yrs", "10+ years of"
        r"(\d+)\s*\+\s*(?:years?|yrs?)",
        # "minimum 5 years", "min 3 yrs"
        r"(?:minimum|min|at\s*least)\s*(\d+)\s*(?:years?|yrs?)",
        # "must have 5 years"
        r"(?:must\s+have|requires?|needs?|seeks?|looking\s+for)\s*(\d+)\s*(?:years?|yrs?)",
        # "5 years of experience"
        r"(?:^|\s)(\d+)\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience|work|professional)",
        # "5+ yrs" (non-digit prefix already handled above)
        # "X+ years" where X >= 1
    ]
    for pat in min_pats:
        m = re.search(pat, t)
        if m:
            val = int(m.group(1))
            if 0 <= val <= 50:
                # Check if it's actually a range like "1-5 years" (already caught above)
                return ExperienceRequirement(val, None, 0.9)

    # Word number minimum: "five years of experience"
    m = re.search(r"(?:minimum|min|at\s*least)\s*(\w+)\s+(?:years?|yrs?)", t)
    if m:
        val = _to_number(m.group(1))
        if val is not None and 0 <= val <= 50:
            return ExperienceRequirement(val, None, 0.8)

    m = re.search(r"(?:^|\s)(\w+)\s+(?:years?|yrs?)(?:\s+of\s+(?:experience|work|professional))?(?:\s+preferred)?\s*$", t)
    if m:
        val = _to_number(m.group(1))
        if val is not None and 0 <= val <= 50:
            return ExperienceRequirement(val, None, 0.7)

    # Same but with trailing content (not end-of-string anchored)
    m = re.search(r"(?:^|\s)(\w+)\s+(?:years?|yrs?)(?:\s+of\s+(?:experience|work|professional))?\b", t)
    if m:
        val = _to_number(m.group(1))
        if val is not None and 0 <= val <= 50:
            return ExperienceRequirement(val, None, 0.7)

    # --- "X years preferred" (soft requirement) ---
    m = re.search(r"(\d+)\s*(?:years?|yrs?)\s+preferred", t)
    if m:
        val = int(m.group(1))
        if 0 <= val <= 50:
            return ExperienceRequirement(val, None, 0.5)

    # --- Several / multiple years (low confidence) ---
    if re.search(r"(?:several|multiple)\s+years?", t):
        return ExperienceRequirement(3, None, 0.3)

    # --- Years mentioned but no specific number ---
    if re.search(r"(?:years?|yrs?)\s+of\s+experience", t):
        return ExperienceRequirement(None, None, 0.2)

    return ExperienceRequirement(None, None, 0.0)
