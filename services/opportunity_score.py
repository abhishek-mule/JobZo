"""Opportunity Score — one transparent, evidence-backed decision per job.

This is deliberately *not* another keyword matcher. It composes signals JobZo
already collects (ranker match, fresher classification, trust, freshness,
eligibility, application channel) into a 100-point score with a visible
factor breakdown and hard rejection rules.

Weights are starting assumptions, not validated predictions. Every score
returned here carries its evidence so it can be audited and calibrated later.

  25  skills match
  20  fresher eligibility
  15  experience compatibility
  15  employer/job authenticity
  10  role relevance
   5  application accessibility
   5  freshness
   5  location compatibility
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from services.freshness import freshness_score
from services.trust import TIER_SUSPICIOUS

WEIGHTS = {
    "skills": 25,
    "fresher": 20,
    "experience": 15,
    "authenticity": 15,
    "role": 10,
    "accessibility": 5,
    "freshness": 5,
    "location": 5,
}

CLASSIFICATION = {
    "excellent": ("Excellent", "Apply first"),
    "strong": ("Strong", "Apply"),
    "moderate": ("Moderate", "Review selectively"),
    "weak": ("Weak", "Usually skip"),
    "reject": ("Rejected", "Do not spend time"),
}

ENGINEERING_FAMILIES = {
    "software", "backend", "frontend", "fullstack", "mobile",
    "devops", "data", "security", "qa",
}

NON_ENGINEERING_FAMILIES = {
    "sales", "marketing", "support", "hr", "finance", "design",
    "content", "operations", "legal", "admin",
}

ROLE_PATTERNS = [
    ("frontend", r"\b(frontend|front-end|react|vue|angular|ui engineer|ui developer)\b"),
    ("backend", r"\b(backend|back-end|api engineer|microservices)\b"),
    ("fullstack", r"\b(fullstack|full-stack|full stack)\b"),
    ("mobile", r"\b(android|ios|mobile|flutter|react[- ]native)\b"),
    ("devops", r"\b(devops|sre|platform engineer|infrastructure|cloud engineer|kubernetes)\b"),
    ("data", r"\b(data scientist|data engineer|machine learning|ml engineer|ai engineer|analytics)\b"),
    ("security", r"\b(security|appsec|infosec|devsecops)\b"),
    ("qa", r"\b(qa|quality assurance|test automation|sdet)\b"),
    ("software", r"\b(software|sde|engineer|developer|programmer)\b"),
    ("sales", r"\b(sales|business development|account executive)\b"),
    ("marketing", r"\b(marketing|growth|seo|social media)\b"),
    ("support", r"\b(customer support|technical support|helpdesk|customer success)\b"),
    ("hr", r"\b(recruiter|talent acquisition|human resources|people operations|hr )\b"),
    ("finance", r"\b(finance|accountant|accounting|payroll|audit)\b"),
    ("design", r"\b(designer|ux|ui design|graphic design|product design)\b"),
    ("content", r"\b(content writer|copywriter|editor|technical writer)\b"),
    ("operations", r"\b(operations|supply chain|logistics)\b"),
    ("legal", r"\b(legal|counsel|compliance)\b"),
    ("admin", r"\b(administrative|office assistant|receptionist|data entry)\b"),
]

FOREIGN_LOCATION_MARKERS = (
    "san francisco", "new york", "seattle", "austin", "chicago", "boston",
    "palo alto", "mountain view", "sunnyvale", "cupertino", "los angeles",
    "portland", "denver", "dallas", "london", "berlin", "amsterdam",
    "paris", "dublin", "stockholm", "zurich", "munich", "hamburg",
    "united states", "united states of america", "usa", "u.s.", "uk",
    "united kingdom", "germany", "netherlands", "france", "europe",
    "canada", "singapore", "australia", "dubai", "uae",
)

INDIA_MARKERS = (
    "india", "bengaluru", "bangalore", "pune", "mumbai", "chennai",
    "hyderabad", "gurgaon", "gurugram", "noida", "kochi", "ahmedabad",
    "nagpur", "delhi", "remote", "anywhere", "worldwide", "global",
)


@dataclass
class ScoreFactor:
    key: str
    label: str
    weight: int
    value: float  # 0-100
    points: float
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "weight": self.weight,
            "value": round(self.value, 1),
            "points": round(self.points, 1),
            "reasons": self.reasons,
        }


@dataclass
class OpportunityScore:
    total: float
    classification: str
    label: str
    action: str
    factors: list[ScoreFactor]
    hard_rejects: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    effort_minutes: int = 10
    interview_probability: float = 0.0
    roi: dict[str, Any] = field(default_factory=dict)

    @property
    def rejected(self) -> bool:
        return bool(self.hard_rejects)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "classification": self.classification,
            "label": self.label,
            "action": self.action,
            "hard_rejects": self.hard_rejects,
            "warnings": self.warnings,
            "effort_minutes": self.effort_minutes,
            "interview_probability": round(self.interview_probability, 4),
            "roi": self.roi,
            "factors": [f.as_dict() for f in self.factors],
        }


def role_family(title: str) -> str:
    """Best-effort role family. Specific families beat the generic 'software'."""
    t = (title or "").lower()
    for family, pattern in ROLE_PATTERNS:
        if re.search(pattern, t):
            return family
    return "other"


def role_relevance(title: str, desired_families: list[str] | None) -> tuple[float, str]:
    """Return (0-100 relevance, reason). Adjacent engineering families are not
    treated as mismatches — a fresher targeting backend can still apply to a
    full-stack or software engineering role."""
    family = role_family(title)
    desired = {f.lower().strip() for f in (desired_families or []) if f}

    if not desired:
        return 70.0, "No target role families configured"

    if family in desired:
        return 100.0, f"Role family matches target ({family})"
    if family in ENGINEERING_FAMILIES and desired & ENGINEERING_FAMILIES:
        return 80.0, f"Adjacent engineering role ({family})"
    if family == "other":
        return 55.0, "Role family not recognized"
    if family in NON_ENGINEERING_FAMILIES and desired & ENGINEERING_FAMILIES:
        return 0.0, f"Non-engineering role ({family}) vs engineering targets"
    return 40.0, f"Role family ({family}) not in targets"


def is_location_compatible(location: str, remote: bool, profile: dict[str, Any]) -> tuple[float, str]:
    loc = (location or "").lower().strip()
    pref = (profile.get("location") or "").lower()
    if remote or any(m in loc for m in ("remote", "anywhere", "worldwide", "global")):
        return 100.0, "Remote / location-flexible"
    if not loc:
        return 60.0, "Location not stated"
    if any(m in loc for m in INDIA_MARKERS):
        return 100.0, f"Location compatible ({location})"
    if any(m in loc for m in FOREIGN_LOCATION_MARKERS):
        return 0.0, f"On-site outside India ({location})"
    if "india" in pref:
        return 45.0, f"Unrecognized location ({location})"
    return 70.0, f"Location not explicitly excluded ({location})"


def application_channel(job) -> str:
    url = (getattr(job, "url", "") or "").lower()
    if "greenhouse.io" in url or "gh_jid=" in url:
        return "greenhouse"
    if "lever.co" in url or "lever-origin=" in url:
        return "lever"
    if "ashbyhq.com" in url:
        return "ashby"
    if "smartrecruiters.com" in url:
        return "smartrecruiters"
    if "myworkdayjobs.com" in url or "workday" in url:
        return "workday"
    if "linkedin.com" in url:
        return "linkedin"
    if "naukri.com" in url:
        return "naukri"
    if "mailto:" in url or "@" in url:
        return "email"
    return "company_portal"


def estimate_effort(job, channel: str | None = None) -> int:
    """Rough effort to submit an application, in minutes."""
    channel = channel or application_channel(job)
    title = (getattr(job, "title", "") or "").lower()
    if any(w in title for w in ("intern", "graduate", "fresher", "trainee")):
        base = 5
    else:
        base = 10
    if channel == "workday":
        return 20
    if channel in ("greenhouse", "lever", "ashby", "smartrecruiters"):
        return min(base, 8)
    if channel == "email":
        return min(base, 5)
    if channel in ("linkedin", "naukri"):
        return base + 2
    return base + 2


def _experience_value(fresher) -> tuple[float, str]:
    status = getattr(fresher, "status", "insufficient")
    mapping = {
        "confirmed": (100.0, "Explicit fresher / entry-level evidence"),
        "high": (85.0, "Strong fresher signal"),
        "plausible": (65.0, "Plausible for a fresher, no explicit evidence"),
        "insufficient": (45.0, "Experience level unclear"),
        "experienced": (0.0, "Explicit experienced-hire requirement"),
    }
    return mapping.get(status, (45.0, "Experience level unclear"))


def _accessibility_value(channel: str, contact_available: bool) -> tuple[float, str]:
    if channel in ("greenhouse", "lever", "ashby", "smartrecruiters"):
        value, reason = 100.0, "Structured ATS application"
    elif channel == "workday":
        value, reason = 65.0, "Workday application — longer form"
    elif channel == "email":
        value, reason = 90.0, "Direct email application"
    elif channel in ("linkedin", "naukri"):
        value, reason = 60.0, "User-assisted application flow"
    else:
        value, reason = 50.0, "Company portal — manual application"
    if contact_available:
        value = min(100.0, value + 10)
        reason += " with a verified contact"
    return value, reason


def _roi(total: float, effort_minutes: int, interview_probability: float | None) -> dict[str, Any]:
    if interview_probability is None:
        # Conservative heuristic: the opportunity score is not a probability.
        p = min(0.35, max(0.0, total / 100.0 * 0.25))
    else:
        p = max(0.0, min(1.0, interview_probability / 100.0))
    effort = max(1, effort_minutes)
    expected_value = p * 100.0
    value_per_minute = expected_value / effort
    interviews_per_10h = p * (600.0 / effort)
    if value_per_minute >= 1.5:
        label = "Exceptional"
    elif value_per_minute >= 0.8:
        label = "High"
    elif value_per_minute >= 0.4:
        label = "Moderate"
    else:
        label = "Low"
    return {
        "interview_probability": round(p, 4),
        "expected_value": round(expected_value, 2),
        "value_per_minute": round(value_per_minute, 2),
        "interviews_per_10h": round(interviews_per_10h, 2),
        "label": label,
    }


def score_opportunity(
    *,
    job,
    match_score: int | float,
    trust,
    fresher,
    profile: dict[str, Any] | None = None,
    effort_minutes: int | None = None,
    contact_available: bool = False,
    interview_probability: float | None = None,
    now: datetime | None = None,
) -> OpportunityScore:
    """Compose existing signals into an auditable 0-100 opportunity score.

    Pure function: no DB access, no network. `job`, `trust` and `fresher` are
    duck-typed so this can be tested without a database.
    """
    profile = profile or {}
    desired = profile.get("desired_role_families") or []
    channel = application_channel(job)
    effort = effort_minutes if effort_minutes is not None else estimate_effort(job, channel)

    factors: list[ScoreFactor] = []
    hard_rejects: list[str] = []
    warnings: list[str] = []

    # ── 1. Skills match (25) ────────────────────────────────────────────
    skills_value = max(0.0, min(100.0, float(match_score or 0)))
    factors.append(ScoreFactor(
        "skills", "Skills match", WEIGHTS["skills"], skills_value,
        WEIGHTS["skills"] * skills_value / 100.0,
        [f"Ranker match {skills_value:.0f}/100"],
    ))

    # ── 2. Fresher eligibility (20) ────────────────────────────────────
    fresher_prob = float(getattr(fresher, "prob", 0.0) or 0.0) * 100.0
    fresher_status = getattr(fresher, "status", "insufficient")
    factors.append(ScoreFactor(
        "fresher", "Fresher eligibility", WEIGHTS["fresher"], fresher_prob,
        WEIGHTS["fresher"] * fresher_prob / 100.0,
        list(getattr(fresher, "evidence", []) or [])[:3],
    ))
    if fresher_status == "experienced":
        hard_rejects.append("Role explicitly requires experienced candidates")

    # ── 3. Experience compatibility (15) ───────────────────────────────
    exp_value, exp_reason = _experience_value(fresher)
    factors.append(ScoreFactor(
        "experience", "Experience compatibility", WEIGHTS["experience"],
        exp_value, WEIGHTS["experience"] * exp_value / 100.0, [exp_reason],
    ))
    if fresher_status == "insufficient":
        warnings.append("No explicit fresher evidence — verify before applying")

    # ── 4. Authenticity (15) ───────────────────────────────────────────
    trust_score = float(getattr(trust, "score", 0.0) or 0.0)
    trust_tier = getattr(trust, "tier", "unverified")
    factors.append(ScoreFactor(
        "authenticity", "Employer/job authenticity", WEIGHTS["authenticity"],
        trust_score, WEIGHTS["authenticity"] * trust_score / 100.0,
        list(getattr(trust, "reasons", []) or [])[:3],
    ))
    if trust_tier == TIER_SUSPICIOUS:
        hard_rejects.append("Suspicious posting — critical scam signals")
    elif trust_tier == "unverified":
        warnings.append("Employer not independently verified")

    # ── 5. Role relevance (10) ─────────────────────────────────────────
    role_value, role_reason = role_relevance(getattr(job, "title", ""), desired)
    factors.append(ScoreFactor(
        "role", "Role relevance", WEIGHTS["role"], role_value,
        WEIGHTS["role"] * role_value / 100.0, [role_reason],
    ))
    if role_value == 0.0:
        hard_rejects.append(role_reason)

    # ── 6. Application accessibility (5) ───────────────────────────────
    access_value, access_reason = _accessibility_value(channel, contact_available)
    factors.append(ScoreFactor(
        "accessibility", "Application accessibility", WEIGHTS["accessibility"],
        access_value, WEIGHTS["accessibility"] * access_value / 100.0,
        [access_reason, f"~{effort} min effort"],
    ))

    # ── 7. Freshness (5) ───────────────────────────────────────────────
    fresh_value = max(0.0, min(1.0, freshness_score(getattr(job, "posted_at", None)))) * 100.0
    factors.append(ScoreFactor(
        "freshness", "Hiring freshness", WEIGHTS["freshness"], fresh_value,
        WEIGHTS["freshness"] * fresh_value / 100.0,
        [f"Freshness {fresh_value:.0f}/100"],
    ))
    posted = getattr(job, "posted_at", None)
    if posted is not None:
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        days_old = (reference - posted).days
        if days_old > 45:
            warnings.append(f"Posting is {days_old} days old — confirm it is still open")

    # ── 8. Location compatibility (5) ──────────────────────────────────
    loc_value, loc_reason = is_location_compatible(
        getattr(job, "location", "") or "",
        bool(getattr(job, "remote", False)),
        profile,
    )
    factors.append(ScoreFactor(
        "location", "Location compatibility", WEIGHTS["location"],
        loc_value, WEIGHTS["location"] * loc_value / 100.0, [loc_reason],
    ))
    if loc_value == 0.0:
        hard_rejects.append(loc_reason)

    # ── Hard eligibility ───────────────────────────────────────────────
    if getattr(job, "is_active", True) is False:
        hard_rejects.append("Posting is no longer active")
    if getattr(job, "eligible", True) is False:
        reason = getattr(job, "eligibility_reason", "") or "Failed hard eligibility"
        hard_rejects.append(reason)

    total = round(sum(f.points for f in factors), 1)
    if hard_rejects:
        total = 0.0
        classification = "reject"
    elif total >= 85:
        classification = "excellent"
    elif total >= 70:
        classification = "strong"
    elif total >= 55:
        classification = "moderate"
    else:
        classification = "weak"

    label, action = CLASSIFICATION[classification]
    roi = _roi(total, effort, interview_probability)
    if classification == "reject":
        roi = {**roi, "label": "None", "value_per_minute": 0.0, "interviews_per_10h": 0.0}

    return OpportunityScore(
        total=total,
        classification=classification,
        label=label,
        action=action,
        factors=factors,
        hard_rejects=hard_rejects,
        warnings=warnings,
        effort_minutes=effort,
        interview_probability=roi["interview_probability"],
        roi=roi,
    )
