"""Person — the central model the entire system optimizes around.

Every provider, planner, and calibration step references the person.
Skills, projects, interview history, network, goals — unified in one place.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


class EmploymentStatus(str, Enum):
    EMPLOYED = "employed"
    NOTICE_PERIOD = "notice_period"
    OPEN_TO_WORK = "open_to_work"
    ACTIVELY_SEARCHING = "actively_searching"


@dataclass
class Project:
    """A professional project — work, open source, or personal."""
    name: str
    url: str = ""
    description: str = ""
    skills: list[str] = field(default_factory=list)
    role: str = ""
    start_date: date | None = None
    end_date: date | None = None
    highlights: list[str] = field(default_factory=list)


@dataclass
class InterviewRecord:
    """A single interview experience — outcome, feedback, timeline."""
    company: str
    role: str
    date: date | None = None
    rounds: int = 0
    result: str = ""  # "offer", "rejected", "withdrew", "ghosted"
    feedback: str = ""
    skills_tested: list[str] = field(default_factory=list)
    difficulty: str = ""  # "easy", "medium", "hard"


@dataclass
class Person:
    """The person the career optimization engine serves.

    This is the integration point for all user data:
    resume, skills, projects, interview history, network, goals.
    Every provider and the planner reference this model.
    """
    name: str = ""
    email: str = ""
    github: str = ""
    linkedin: str = ""
    current_role: str = ""
    current_company: str = ""
    years_of_experience: float = 0.0
    employment_status: EmploymentStatus = EmploymentStatus.ACTIVELY_SEARCHING
    availability_hours_per_week: int = 15

    resume_versions: list[str] = field(default_factory=list)       # resume IDs
    skills: list[str] = field(default_factory=list)                 # canonical skill names
    projects: list[Project] = field(default_factory=list)
    interview_history: list[InterviewRecord] = field(default_factory=list)
    network_size: int = 0
    goal: str = "Get placed ASAP"

    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def skill_count(self) -> int:
        return len(self.skills)

    @property
    def interview_count(self) -> int:
        return len(self.interview_history)

    @property
    def offer_rate(self) -> float:
        if not self.interview_history:
            return 0.0
        offers = sum(1 for i in self.interview_history if i.result == "offer")
        return offers / len(self.interview_history)

    def skill_gaps(self, target_skills: list[str]) -> list[str]:
        """Skills the person doesn't have yet but might need."""
        return [s for s in target_skills if s not in self.skills]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "email": self.email,
            "current_role": self.current_role,
            "current_company": self.current_company,
            "years_of_experience": self.years_of_experience,
            "employment_status": self.employment_status.value,
            "availability_hours_per_week": self.availability_hours_per_week,
            "skills": self.skills,
            "network_size": self.network_size,
            "goal": self.goal,
            "skill_count": self.skill_count,
            "interview_count": self.interview_count,
            "offer_rate": round(self.offer_rate, 2),
        }
