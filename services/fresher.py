"""Fresher classification — is this role realistic for a fresher?

Five honest states. We NEVER collapse "unknown experience" into
"fresher-friendly" — that exact bug flooded the feed with YC roles. And
company-level signals NEVER override explicit job-level evidence.

  FRESHER_CONFIRMED       🎓 explicit entry evidence (graduate / campus /
                             intern / trainee / 0–1yr requirement)
  FRESHER_HIGH            🟢 no explicit YOE, but junior-track role AND the
                             company demonstrably hires graduates
  FRESHER_PLAUSIBLE       🟡 company hires graduates only (no job-level
                             evidence) — corroboration, never confirmation
  FRESHER_EXPERIENCED     🔴 explicit 2+ years required, or senior title
  FRESHER_INSUFFICIENT    ⚪ sparse JD, no evidence either way

Evidence precedence (highest wins; an explicit requirement always beats a
company hint):
  1. explicit experience requirement  (experience_required → title → JD text)
  2. title signals                    (entry → confirmed; senior → experienced)
  3. JD entry signals
  4. company graduate-hiring history + junior-track role  → at most HIGH
  5. company graduate-hiring history only                 → at most PLAUSIBLE
  6. nothing                                              → INSUFFICIENT

Examples:
  PhonePe SRE (2+ Years)      → EXPERIENCED   (explicit 2+ yrs)
  PhonePe BA (2-4 years)      → EXPERIENCED   (explicit 2–4 yrs)
  PhonePe SWE, Android        → HIGH          (no explicit YOE, hires grads)
  Razorpay SDE (Malaysia)     → same job-level rules; India filter is separate
"""

import re
from dataclasses import dataclass, field

from services.experience import parse_experience

FRESHER_CONFIRMED = "confirmed"
FRESHER_HIGH = "high"
FRESHER_PLAUSIBLE = "plausible"
FRESHER_EXPERIENCED = "experienced"
FRESHER_INSUFFICIENT = "insufficient"

SIGNAL_HIGH = "high"
SIGNAL_MEDIUM = "medium"
SIGNAL_NEGATIVE = "negative"
SIGNAL_NONE = "none"

# Subjective but defensible probability map per state, used for display only.
PROBABILITY = {
    FRESHER_CONFIRMED: 0.95,
    FRESHER_HIGH: 0.80,
    FRESHER_PLAUSIBLE: 0.60,
    FRESHER_INSUFFICIENT: 0.40,
    FRESHER_EXPERIENCED: 0.10,
}

STATUS_ORDER = {
    FRESHER_CONFIRMED: 0,
    FRESHER_HIGH: 1,
    FRESHER_PLAUSIBLE: 2,
    FRESHER_INSUFFICIENT: 3,
    FRESHER_EXPERIENCED: 4,
}
SIGNAL_ORDER = {SIGNAL_HIGH: 0, SIGNAL_MEDIUM: 1, SIGNAL_NONE: 2, SIGNAL_NEGATIVE: 3}


@dataclass
class FresherResult:
    status: str = FRESHER_INSUFFICIENT
    signal: str = SIGNAL_NONE
    evidence: list[str] = field(default_factory=list)
    negatives: list[str] = field(default_factory=list)
    min_years: int | None = None
    max_years: int | None = None

    @property
    def is_fresher(self) -> bool:
        return self.status in (FRESHER_CONFIRMED, FRESHER_HIGH, FRESHER_PLAUSIBLE)

    @property
    def prob(self) -> float:
        return PROBABILITY.get(self.status, 0.0)


# Title patterns that unambiguously mean "entry level".
CONFIRM_TITLE_PATTERNS = [
    r"\b(?:graduate|new[\s-]?grad|campus|fresher|intern|internship|apprentice)\b",
    r"\b(?:entry[\s-]?level|early[\s-]?career)\b",
    r"\b0[-–]?[12]\s*(?:years?|yrs?)\b",
    r"\bjr\.?\b",
    r"\btrainee\b",
]

# Title patterns that strongly indicate a senior role.
EXPERIENCED_TITLE_PATTERNS = [
    r"\b(?:senior|sr\.?|staff|principal|lead|head|architect|director|manager|vp|chief)\b",
]

# Junior-track generic role words. A generic role + a graduate-hiring company
# is a strong (not explicit) fresher signal.
JUNIOR_TRACK_WORDS = (
    "software", "sde", "developer", "engineer", "analyst", "associate",
    "specialist", "representative", "solutions", "support", "qa", "design",
    "operations", "executive", "sales", "marketing", "hr",
)

# Description patterns that confirm an entry-level opening.
CONFIRM_DESC_PATTERNS = [
    r"\b(?:fresh|new)\s+graduate",
    r"\bfresher",
    r"\bcampus\s+(?:hire|recruit|drive|program)",
    r"\bentry[\s-]?level",
    r"\bearly[\s-]?career",
    r"\bno\s+(?:prior\s+)?experience",
]

# Description patterns that indicate a senior-level role without a number.
EXPERIENCED_DESC_PATTERNS = [
    r"\b(?:senior|staff|principal|lead|head)\s+level",
    r"\bleadership\s+role",
    r"\b(?:managerial|director[- ]?level)\b",
]


def _title_signal(title: str) -> tuple[str, str, str]:
    """Return (status, signal, evidence_label) from title only."""
    t = (title or "").lower()
    for pat in EXPERIENCED_TITLE_PATTERNS:
        if re.search(pat, t):
            return FRESHER_EXPERIENCED, SIGNAL_NEGATIVE, f"senior title ({pat[1:-1]})"
    for pat in CONFIRM_TITLE_PATTERNS:
        if re.search(pat, t):
            return FRESHER_CONFIRMED, SIGNAL_HIGH, f"entry title ({pat[1:-1]})"
    return "", SIGNAL_NONE, ""


def _is_junior_track_title(title: str) -> bool:
    t = (title or "").lower()
    return any(re.search(rf"\b{re.escape(w)}\b", t) for w in JUNIOR_TRACK_WORDS)


def _desc_signal(description: str) -> tuple[str, str, str]:
    d = (description or "").lower()
    for pat in CONFIRM_DESC_PATTERNS:
        if re.search(pat, d):
            return FRESHER_CONFIRMED, SIGNAL_HIGH, f"entry JD ({pat[1:-1]})"
    for pat in EXPERIENCED_DESC_PATTERNS:
        if re.search(pat, d):
            return FRESHER_EXPERIENCED, SIGNAL_NEGATIVE, f"senior JD ({pat[1:-1]})"
    return "", SIGNAL_NONE, ""


def _explicit_experience(title: str, description: str,
                         experience_required: str) -> FresherResult | None:
    """Explicit YOE requirement → decisive. Returns a result or None."""
    for text in (experience_required or "", title or "", description or ""):
        if not text or not text.strip():
            continue
        exp = parse_experience(text)
        if exp.confidence < 0.5 or exp.min_years is None:
            continue
        r = FresherResult(min_years=exp.min_years, max_years=exp.max_years)
        label = f"explicit requirement: {exp.min_years}–{exp.max_years or '+'} yrs"
        if exp.min_years >= 2:
            r.status = FRESHER_EXPERIENCED
            r.signal = SIGNAL_NEGATIVE
            r.negatives.append("✗ " + label)
        else:
            r.status = FRESHER_CONFIRMED
            r.signal = SIGNAL_HIGH
            r.evidence.append("✓ " + label)
        return r
    return None


def classify_fresher(*, title: str, description: str = "",
                     experience_required: str = "",
                     company=None) -> FresherResult:
    """Classify a job into the five-state fresher model.

    company-level fresher friendliness can never confirm or deny a job; it
    only corroborates (HIGH / PLAUSIBLE) once job-level evidence is exhausted.
    """
    result = FresherResult()

    # 1. Explicit experience requirement — always decisive.
    r = _explicit_experience(title, description, experience_required)
    if r is not None:
        return r

    # 2. Title signals.
    status, signal, label = _title_signal(title)
    if status:
        result.status, result.signal = status, signal
        result.evidence.append(("✓ " if status == FRESHER_CONFIRMED else "✗ ") + label)
        return result

    # 3. Description signals.
    status, signal, label = _desc_signal(description)
    if status:
        result.status, result.signal = status, signal
        result.evidence.append(("✓ " if status == FRESHER_CONFIRMED else "✗ ") + label)
        return result

    # 4. Company graduate-hiring history = corroboration ONLY.
    company_hint = False
    if company is not None:
        try:
            company_hint = bool(getattr(company, "fresher_friendly", False)) or \
                bool(getattr(company, "graduate_roles", False)) or \
                bool(getattr(company, "internship", False))
        except Exception:
            company_hint = False

    if company_hint:
        hint = "company hires graduates (company-level signal only)"
        if _is_junior_track_title(title):
            result.status = FRESHER_HIGH
            result.signal = SIGNAL_MEDIUM
            result.evidence.append("✓ junior-track role")
        else:
            result.status = FRESHER_PLAUSIBLE
            result.signal = SIGNAL_MEDIUM
        result.evidence.append("✓ " + hint)
        return result

    # 5. Nothing conclusive.
    result.status = FRESHER_INSUFFICIENT
    result.signal = SIGNAL_NONE
    result.evidence.append("? no fresher or senior signal in JD")
    return result


# ── Funnel report over the live supply ───────────────────────────────────────

def company_by_name() -> dict[str, object]:
    from services.company_registry import get_all
    try:
        return {c.name: c for c in get_all(active_only=True)}
    except Exception:
        return {}


def is_india_location(location: str) -> bool:
    loc = (location or "").lower()
    return any(city in loc for city in ("india", "bengaluru", "bangalore", "pune",
                                        "mumbai", "chennai", "hyderabad", "gurgaon",
                                        "gurugram", "noida", "kochi", "ahmedabad"))


def _local_rows(session, local_only: bool):
    from database.models import Company, Job
    from sqlalchemy import or_
    q = session.query(Job, Company).outerjoin(Company, Company.name == Job.company)
    if local_only:
        from services.dig import LOCAL_CATEGORIES
        q = q.filter(or_(*(Company.primary_category == c for c in LOCAL_CATEGORIES)))
    return q.all()


def _priority_for(job, profile: dict) -> float:
    """Apply-priority estimate (0–10). Uses scored app when present."""
    from services.probability import estimate
    try:
        return round(estimate(job, profile).get("score", 50) / 10.0, 1)
    except Exception:
        return 5.0


def fresher_funnel(local_only: bool = True) -> dict:
    """Staged funnel over the live supply — one number per stage, in order.

      collected → india_visible → fresher → profile_match → high_priority
      → apply_now

    Classification is computed across the full collected set; the India
    filter happens at the india_visible stage (a Malaysia job is classified
    honestly, then hidden by the location stage).
    """
    from database.connection import get_session
    from services.trust import TIER_SUSPICIOUS, trust_for_job
    from services.freshness import freshness_band
    from services.user_profile import load_profile

    profile = load_profile()
    session = get_session()
    try:
        rows = _local_rows(session, local_only)
    finally:
        session.close()

    statuses = {s: 0 for s in STATUS_ORDER}
    totals = {"collected": len(rows), "india_visible": 0}
    eligibility = {"eligible": 0, "not_eligible": 0, "unchecked": 0}

    india_fresher = 0
    india_fresher_eligible = 0
    india_fresher_eligible_high = 0
    india_fresher_eligible_high_fresh = 0

    for job, company in rows:
        r = classify_fresher(title=job.title, description=job.description,
                             experience_required=job.experience_required,
                             company=company)
        statuses[r.status] += 1

        if job.eligible is None:
            eligibility["unchecked"] += 1
        elif job.eligible:
            eligibility["eligible"] += 1
        else:
            eligibility["not_eligible"] += 1

        if not is_india_location(job.location):
            continue
        totals["india_visible"] += 1
        if not r.is_fresher:
            continue
        india_fresher += 1
        if job.eligible is not True:
            continue
        india_fresher_eligible += 1
        if _priority_for(job, profile) < 6.0:
            continue
        india_fresher_eligible_high += 1
        try:
            trust = trust_for_job(job)
            if trust.tier == TIER_SUSPICIOUS:
                continue
            band = freshness_band(job.posted_at)
            if band.get("level") not in ("very_fresh", "fresh"):
                continue
        except Exception:
            continue
        india_fresher_eligible_high_fresh += 1

    return {
        "totals": totals,
        "experience": statuses,
        "eligibility": eligibility,
        "funnel": {
            "collected": totals["collected"],
            "india_visible": totals["india_visible"],
            "fresher": india_fresher,
            "profile_match": india_fresher_eligible,
            "high_priority": india_fresher_eligible_high,
            "apply_now": india_fresher_eligible_high_fresh,
        },
    }


def top_fresher_jobs(limit: int = 30, local_only: bool = True) -> list[dict]:
    """Realistic fresher opportunities, best-first, auditable.

    local_only ⇒ only India-visible + eligible jobs. Ranks by fresher status,
    then signal, then apply priority, then trust, then freshness.
    """
    from database.connection import get_session
    from services.freshness import freshness_band
    from services.trust import trust_for_job
    from services.user_profile import load_profile

    profile = load_profile()
    session = get_session()
    try:
        rows = _local_rows(session, local_only)
    finally:
        session.close()

    out = []
    for job, company in rows:
        if local_only and not is_india_location(job.location):
            continue
        if job.eligible is not True:
            continue
        r = classify_fresher(title=job.title, description=job.description,
                             experience_required=job.experience_required,
                             company=company)
        try:
            trust = trust_for_job(job)
        except Exception:
            trust = None
        try:
            fresh = freshness_band(job.posted_at)
        except Exception:
            fresh = {}
        priority = _priority_for(job, profile)
        out.append({
            "job": job,
            "company": company,
            "fresher": r,
            "india": is_india_location(job.location),
            "priority": priority,
            "trust": trust,
            "freshness": fresh,
            "order": (STATUS_ORDER[r.status] * 10000
                      + SIGNAL_ORDER[r.signal] * 1000
                      - int(priority * 100)
                      - (trust.score if trust else 0)),
        })
    out.sort(key=lambda x: x["order"])
    return out[:limit]
