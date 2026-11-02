"""Composable eligibility rules engine.

Each rule returns Eligible() or Ineligible(reason). Runs before scoring
so the scorer never wastes time on impossible jobs.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import re

from database.models import Job
from services.experience import parse_experience


@dataclass
class Eligible:
    passed: bool = True
    reason: str = ""


@dataclass
class Ineligible:
    passed: bool = False
    reason: str = ""


class EligibilityRule(ABC):
    @abstractmethod
    def check(self, job: Job, profile: dict) -> Eligible | Ineligible:
        ...


# ── Rules ────────────────────────────────────────────────────────────────────

HARD_REJECT_TITLES = [
    "staff engineer", "principal engineer", "distinguished engineer",
    "staff software", "principal software", "distinguished software",
    "staff", "principal", "distinguished",
    "architect",
    "engineering manager", "manager",
    "director of", "director,",
    "vp ", "vice president",
    "head of",
    "chief", "fellow",
    "lead",
]

SOFT_REJECT_KEYWORDS = [
    "senior", "sr ",
]


class ExperienceRule(EligibilityRule):
    """Block jobs explicitly requiring more experience than the user has.

    Hard cap: any job requiring 5+ years is rejected regardless of user profile.
    Soft cap: if job requires more than user_years + buffer, reject.
    """

    def __init__(self, buffer: int = 0, hard_max: int = 5):
        self.buffer = buffer
        self.hard_max = hard_max

    def check(self, job: Job, profile: dict) -> Eligible | Ineligible:
        user_years = profile.get("years_experience", 1)
        text = " ".join(filter(None, [job.title, job.description, job.experience_required]))
        req = parse_experience(text)
        if req.confidence >= 0.5 and req.min_years is not None:
            if req.min_years >= self.hard_max:
                return Ineligible(reason=(
                    f"requires {req.min_years}+ years (hard cap: {self.hard_max})"
                ))
            if req.min_years > user_years + self.buffer:
                return Ineligible(reason=(
                    f"requires {req.min_years}+ years, you have {user_years}"
                ))
        return Eligible()


class SeniorityRule(EligibilityRule):
    """Block titles that imply seniority beyond a junior/mid profile.

    Hard rejects (all users): staff, principal, distinguished, architect,
    manager, director, VP, head of, chief, fellow, lead.
    Soft rejects (<3yr users): senior, sr.
    """

    def check(self, job: Job, profile: dict) -> Eligible | Ineligible:
        user_years = profile.get("years_experience", 1)
        title_lower = job.title.lower()

        for kw in HARD_REJECT_TITLES:
            if kw in title_lower:
                return Ineligible(reason=f"title requires seniority beyond your profile ({kw})")

        if user_years < 3:
            for kw in SOFT_REJECT_KEYWORDS:
                if kw in title_lower:
                    return Ineligible(reason=f"senior title ({kw}) with {user_years}yr experience")

        return Eligible()


VISA_KEYWORDS = [
    "us citizen", "u.s. citizen", "united states citizen",
    "security clearance", "clearance required", "secret clearance",
    "green card", "permanent resident",
    "must be a us person", "itar",
    "export controlled", "export control",
]


class VisaRule(EligibilityRule):
    """Block jobs requiring US citizenship, security clearance, or green card."""

    def check(self, job: Job, profile: dict) -> Eligible | Ineligible:
        text = " ".join(filter(None, [job.title, job.description, job.experience_required])).lower()
        for kw in VISA_KEYWORDS:
            if kw in text:
                return Ineligible(reason=f"requires {kw}")
        return Eligible()


class LocationRule(EligibilityRule):
    """Block jobs that are on-site outside India (for India-based candidates)."""

    def check(self, job: Job, profile: dict) -> Eligible | Ineligible:
        loc = (job.location or "").lower()
        desc = (job.description or "").lower()

        # Remote is always fine
        if job.remote or "remote" in loc or "remote" in desc:
            return Eligible()

        # Explicitly outside India
        us_locations = [
            "san francisco", "new york", "seattle", "austin", "chicago",
            "palo alto", "mountain view", "sunnyvale", "cupertino",
            "los angeles", "boston", "portland", "denver", "dallas",
            "united states", "united states of america", "usa",
        ]
        eu_locations = [
            "london", "berlin", "amsterdam", "paris", "dublin",
            "stockholm", "copenhagen", "zurich", "munich", "hamburg",
            "united kingdom", "uk", "germany", "netherlands", "france",
            "europe",
        ]

        # Check location field
        for city in us_locations + eu_locations:
            if city in loc:
                return Ineligible(reason=f"on-site in {loc} (outside India)")

        # Check description for explicit location requirements
        us_desc_pats = [
            r"must\s+be\s+(?:located\s+)?in\s+(?:the\s+)?(?:united\s+states|us|usa)",
            r"(?:on.?site|in.?office)\s+(?:in\s+)?(?:san\s+francisco|new\s+york|seattle)",
            r"relocation\s+to\s+(?:san\s+francisco|new\s+york|seattle|united\s+states|us|usa)",
        ]
        for pat in us_desc_pats:
            if re.search(pat, desc):
                return Ineligible(reason="requires on-site presence outside India")

        return Eligible()


class RemotePolicyRule(EligibilityRule):
    """Block jobs that require office presence for remote-only candidates."""

    def check(self, job: Job, profile: dict) -> Eligible | Ineligible:
        if profile.get("preferred_locations", "").lower().startswith("remote"):
            desc = " ".join(filter(None, [job.description, job.experience_required])).lower()
            loc = (job.location or "").lower()
            if "remote" in loc:
                return Eligible()
            # 5 days/week in office is a strong signal
            if re.search(r"5\s*days?\s*(?:a|per)\s*week\s*(?:in\s+)?(?:the\s+)?office", desc):
                return Ineligible(reason="requires 5 days/week in office")
            if re.search(r"(?:no|not)\s+remote", desc):
                return Ineligible(reason="explicitly not remote")
        return Eligible()


class CompanyEligibilityRule(EligibilityRule):
    """Block jobs for companies whose hiring criteria don't match the user.

    Checks graduate_roles from the Company registry (looked up via job.company name).
    Experience-based filtering happens at the job level (ExperienceRule).
    """

    def check(self, job: Job, profile: dict) -> Eligible | Ineligible:
        user_years = profile.get("years_experience", 1)
        try:
            from services.company_registry import get_by_alias
            company = get_by_alias(job.company)
            if company is None:
                return Eligible()

            if company.graduate_roles and user_years > 1:
                return Ineligible(reason="company hires fresh graduates only")
        except Exception:
            pass
        return Eligible()


# ── Engine ───────────────────────────────────────────────────────────────────

class EligibilityEngine:
    """Run all rules against a job. Returns first Ineligible or Eligible."""

    def __init__(self, rules: list[EligibilityRule] | None = None):
        self.rules = rules or [
            ExperienceRule(),
            SeniorityRule(),
            VisaRule(),
            LocationRule(),
            RemotePolicyRule(),
            CompanyEligibilityRule(),
        ]

    def check(self, job: Job, profile: dict | None = None) -> Eligible | Ineligible:
        if profile is None:
            profile = {}
        for rule in self.rules:
            result = rule.check(job, profile)
            if not result.passed:
                return result
        return Eligible()
