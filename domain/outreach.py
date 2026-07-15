"""Outreach Intelligence Engine — contact discovery, ranking, and strategy.

Phase 3.2 — not "email automation" but data-driven outreach optimization.
Every component is pure data. No I/O, no SQL, no email sending.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


# ── Contact types ───────────────────────────────────────────────────────

class ContactRole(str, Enum):
    """Who the contact is — determines outreach strategy."""
    RECRUITER = "recruiter"
    HIRING_MANAGER = "hiring_manager"
    ENGINEERING_MANAGER = "engineering_manager"
    TEAM_LEAD = "team_lead"
    FOUNDER = "founder"
    EMPLOYEE = "employee"        # referral candidate
    HR_GENERAL = "hr_general"
    UNKNOWN = "unknown"


class ContactSource(str, Enum):
    """Where the contact was discovered — affects confidence."""
    COMPANY_CAREERS_PAGE = "company_careers_page"
    COMPANY_RECRUITING_EMAIL = "company_recruiting_email"
    LINKEDIN_PROFILE = "linkedin_profile"
    LINKEDIN_JOB_POSTING = "linkedin_job_posting"
    WEBSITE_CONTACT = "website_contact"
    GITHUB_PROFILE = "github_profile"
    EMPLOYEE_DIRECTORY = "employee_directory"
    EXISTING_RELATIONSHIP = "existing_relationship"
    USER_PROVIDED = "user_provided"


class Relationship(str, Enum):
    """How the user is connected to this contact."""
    NONE = "none"                  # No prior connection
    SECOND_DEGREE = "second_degree"  # Shared connection
    ALUMNI = "alumni"              # Same school
    FORMER_COLLEAGUE = "former_colleague"
    REFERRAL = "referral"          # Introduced by someone
    EXISTING = "existing"          # Already in touch


@dataclass
class Contact:
    """A person the user could reach out to.

    Stores metadata for ranking and strategy decisions, not just an email.
    """
    id: str
    name: str
    role: ContactRole
    source: ContactSource
    relationship: Relationship = Relationship.NONE

    email: str = ""
    linkedin_url: str = ""
    github_username: str = ""
    title: str = ""                # Full job title (e.g. "Senior Engineering Manager")
    company: str = ""
    team: str = ""                 # "Backend", "Infrastructure", etc.

    confidence: float = 0.5        # How sure we are about role/email accuracy
    hiring_authority: bool = False  # Can this person make hiring decisions?
    likely_referral: bool = False   # Would they refer? (employees)

    last_contacted: date | None = None
    response_status: str = ""      # "sent", "opened", "replied", "meeting_scheduled", "ignored"
    interaction_count: int = 0

    notes: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def priority_score(self) -> float:
        """Higher = more likely to get a response."""
        base = self.confidence
        if self.hiring_authority:
            base += 0.2
        if self.relationship in (Relationship.EXISTING, Relationship.REFERRAL):
            base += 0.3
        elif self.relationship == Relationship.ALUMNI:
            base += 0.1
        if self.response_status == "replied":
            base += 0.2
        return min(base, 1.0)

    @property
    def contactable(self) -> bool:
        """True if we have enough info to attempt contact."""
        return bool(self.email or self.linkedin_url)


# ── Outreach strategy ────────────────────────────────────────────────────

class CompanyTier(str, Enum):
    """Company size/stage classification for strategy selection."""
    STARTUP = "startup"
    GROWTH = "growth"
    MID_SIZE = "mid_size"
    LARGE_MNC = "large_mnc"
    FAANG = "faang"
    UNKNOWN = "unknown"


@dataclass
class OutreachStrategy:
    """Recommended outreach approach for a given company tier + role combination."""
    company_tier: CompanyTier
    primary_contact: ContactRole     # Who to contact first
    secondary_contact: ContactRole | None = None
    suggest_referral: bool = False
    suggest_founder_email: bool = False
    suggest_recruiter_email: bool = True
    strategy_summary: str = ""


# ── Default strategies ───────────────────────────────────────────────────

DEFAULT_STRATEGIES: dict[CompanyTier, OutreachStrategy] = {
    CompanyTier.STARTUP: OutreachStrategy(
        company_tier=CompanyTier.STARTUP,
        primary_contact=ContactRole.FOUNDER,
        suggest_referral=False,
        suggest_founder_email=True,
        suggest_recruiter_email=False,
        strategy_summary="Apply then email founder directly. Startups value initiative.",
    ),
    CompanyTier.GROWTH: OutreachStrategy(
        company_tier=CompanyTier.GROWTH,
        primary_contact=ContactRole.HIRING_MANAGER,
        secondary_contact=ContactRole.ENGINEERING_MANAGER,
        suggest_referral=True,
        suggest_founder_email=False,
        suggest_recruiter_email=True,
        strategy_summary="Apply, find hiring manager or engineer, request referral.",
    ),
    CompanyTier.MID_SIZE: OutreachStrategy(
        company_tier=CompanyTier.MID_SIZE,
        primary_contact=ContactRole.RECRUITER,
        secondary_contact=ContactRole.EMPLOYEE,
        suggest_referral=True,
        suggest_founder_email=False,
        suggest_recruiter_email=True,
        strategy_summary="Apply then connect with recruiter. Employee referral if possible.",
    ),
    CompanyTier.LARGE_MNC: OutreachStrategy(
        company_tier=CompanyTier.LARGE_MNC,
        primary_contact=ContactRole.RECRUITER,
        secondary_contact=ContactRole.EMPLOYEE,
        suggest_referral=True,
        suggest_founder_email=False,
        suggest_recruiter_email=True,
        strategy_summary="Recruiter is primary. Employee referral is secondary. No founder email.",
    ),
    CompanyTier.FAANG: OutreachStrategy(
        company_tier=CompanyTier.FAANG,
        primary_contact=ContactRole.EMPLOYEE,
        secondary_contact=ContactRole.RECRUITER,
        suggest_referral=True,
        suggest_founder_email=False,
        suggest_recruiter_email=False,
        strategy_summary="Referral preferred. Connect with employee first, recruiter second.",
    ),
    CompanyTier.UNKNOWN: OutreachStrategy(
        company_tier=CompanyTier.UNKNOWN,
        primary_contact=ContactRole.RECRUITER,
        suggest_referral=False,
        suggest_founder_email=False,
        suggest_recruiter_email=True,
        strategy_summary="Default: contact recruiter.",
    ),
}


# ── Contact ranking ──────────────────────────────────────────────────────

@dataclass
class ContactRanking:
    """A contact scored for a specific opportunity."""
    contact: Contact
    opportunity_id: str
    relevance_score: float      # How relevant is this contact to this role?
    estimated_value: float      # Expected value if contacted
    estimated_minutes: int      # Time to find + draft + send
    strategy: OutreachStrategy | None = None
    why_lines: list[str] = field(default_factory=list)

    def to_task_node(self) -> Any:
        """Convert to a TaskNode for the planner."""
        from domain.models import TaskNode
        task_id = f"outreach-{self.opportunity_id}-{self.contact.id}"
        return TaskNode(
            id=task_id,
            kind="outreach",
            title=f"Contact {self.contact.name} at {self.contact.company}",
            description=f"Reach out to {self.contact.name} ({self.contact.role.value})",
            source="outreach_provider",
            opportunity_id=self.opportunity_id,
            estimated_minutes=self.estimated_minutes,
            expected_value=self.estimated_value,
            urgency="medium" if self.estimated_value > 10 else "low",
            metadata={
                "contact_id": self.contact.id,
                "contact_name": self.contact.name,
                "contact_role": self.contact.role.value,
                "company": self.contact.company,
            },
        )
