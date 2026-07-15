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

SENIOR_TITLES = [
    "staff", "principal", "distinguished", "architect",
    "manager", "director", "head of", "vp ", "vice president",
    "fellow", "chief",
]

EXCLUDED_TITLES = [
    "staff engineer", "principal engineer", "distinguished engineer",
    "staff software", "principal software", "distinguished software",
    "head of", "vp ", "vice president", "chief",
    "director of", "director,", "engineering manager",
]


class ExperienceRule(EligibilityRule):
    """Block jobs explicitly requiring more experience than the user has."""

    def __init__(self, buffer: int = 1):
        self.buffer = buffer  # allow jobs asking up to user_years + buffer

    def check(self, job: Job, profile: dict) -> Eligible | Ineligible:
        user_years = profile.get("years_experience", 1)
        text = " ".join(filter(None, [job.title, job.description, job.experience_required]))
        req = parse_experience(text)
        if req.confidence >= 0.5 and req.min_years is not None:
            if req.min_years > user_years + self.buffer:
                return Ineligible(reason=(
                    f"requires {req.min_years}+ years, you have {user_years} (confidence: {req.confidence:.0%})"
                ))
        return Eligible()


class SeniorityRule(EligibilityRule):
    """Block senior/staff/principal/head roles for junior profiles."""

    def __init__(self, max_seniority_years: int = 4):
        self.max_years = max_seniority_years

    def check(self, job: Job, profile: dict) -> Eligible | Ineligible:
        user_years = profile.get("years_experience", 1)
        title_lower = job.title.lower()

        # Check excluded title patterns first
        for excl in EXCLUDED_TITLES:
            if excl in title_lower:
                return Ineligible(reason=f"seniority gate: {excl} role with {user_years}yr")

        # Check individual senior keywords for <2yr experience
        if user_years < 2:
            for kw in SENIOR_TITLES:
                if kw in title_lower:
                    return Ineligible(reason=f"seniority gate: {kw} role with {user_years}yr")

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
        ]

    def check(self, job: Job, profile: dict | None = None) -> Eligible | Ineligible:
        if profile is None:
            profile = {}
        for rule in self.rules:
            result = rule.check(job, profile)
            if not result.passed:
                return result
        return Eligible()
