"""Career Capital — the planner's objective function.

Every task increases one or more forms of career capital.
The planner optimizes for long-term capital accumulation,
not short-term interview probability.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CapitalKind(str, Enum):
    """The six forms of career capital a task can build."""
    RESUME = "resume"          # Better job titles, brands, achievements
    SKILL = "skill"            # Technical/domain knowledge
    NETWORK = "network"        # People who know you exist
    INTERVIEW = "interview"    # Practice, signals, process familiarity
    REPUTATION = "reputation"  # Public proof of work (OSS, writing, speaking)
    OPPORTUNITY = "opportunity" # Active applications in pipeline


@dataclass
class CapitalVector:
    """A six-dimensional capital contribution.

    Every task produces a vector showing how it changes each capital type.
    Values range from -1.0 (destroys capital, e.g., a bad application) to
    1.0 (strongly builds capital). Most are 0.0 (no effect).
    """
    resume: float = 0.0
    skill: float = 0.0
    network: float = 0.0
    interview: float = 0.0
    reputation: float = 0.0
    opportunity: float = 0.0

    @classmethod
    def zero(cls) -> CapitalVector:
        return cls()

    def dot(self, weights: CapitalVector) -> float:
        """Weighted sum — the planner's utility value for this vector."""
        return (
            self.resume * weights.resume
            + self.skill * weights.skill
            + self.network * weights.network
            + self.interview * weights.interview
            + self.reputation * weights.reputation
            + self.opportunity * weights.opportunity
        )

    def __add__(self, other: CapitalVector) -> CapitalVector:
        return CapitalVector(
            resume=self.resume + other.resume,
            skill=self.skill + other.skill,
            network=self.network + other.network,
            interview=self.interview + other.interview,
            reputation=self.reputation + other.reputation,
            opportunity=self.opportunity + other.opportunity,
        )

    def __mul__(self, scalar: float) -> CapitalVector:
        return CapitalVector(
            resume=self.resume * scalar,
            skill=self.skill * scalar,
            network=self.network * scalar,
            interview=self.interview * scalar,
            reputation=self.reputation * scalar,
            opportunity=self.opportunity * scalar,
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "resume": round(self.resume, 3),
            "skill": round(self.skill, 3),
            "network": round(self.network, 3),
            "interview": round(self.interview, 3),
            "reputation": round(self.reputation, 3),
            "opportunity": round(self.opportunity, 3),
        }


# ── Goal-based preference profiles ──────────────────────────────────────

GOAL_PROFILES: dict[str, CapitalVector] = {
    "Get placed ASAP": CapitalVector(
        resume=0.10,
        skill=0.05,
        network=0.15,
        interview=0.10,
        reputation=0.05,
        opportunity=0.55,
    ),
    "Maximize salary": CapitalVector(
        resume=0.20,
        skill=0.20,
        network=0.10,
        interview=0.20,
        reputation=0.15,
        opportunity=0.15,
    ),
    "Crack product companies": CapitalVector(
        resume=0.20,
        skill=0.25,
        network=0.10,
        interview=0.20,
        reputation=0.15,
        opportunity=0.10,
    ),
    "Build network": CapitalVector(
        resume=0.05,
        skill=0.10,
        network=0.45,
        interview=0.05,
        reputation=0.10,
        opportunity=0.25,
    ),
    "Career growth (long-term)": CapitalVector(
        resume=0.15,
        skill=0.25,
        network=0.20,
        interview=0.05,
        reputation=0.25,
        opportunity=0.10,
    ),
}


@dataclass
class CapitalProfile:
    """A user's capital preferences — what they're optimizing for.

    Provides goal-based default weights but allows per-user calibration
    based on observed outcomes.
    """
    weights: CapitalVector
    goal: str = "Get placed ASAP"
    source: str = "goal_default"  # "goal_default", "calibrated", "manual"

    @classmethod
    def for_goal(cls, goal: str) -> CapitalProfile:
        w = GOAL_PROFILES.get(goal, GOAL_PROFILES["Get placed ASAP"])
        return cls(weights=w, goal=goal, source="goal_default")

    def rescale(self, factor: float) -> CapitalProfile:
        return CapitalProfile(
            weights=self.weights * factor,
            goal=self.goal,
            source=self.source,
        )


# ── Capital contribution catalog ─────────────────────────────────────────
# Maps (task_kind, subtask) -> CapitalVector contribution.
# These are initial heuristics — calibration will refine them.

KIND_CONTRIBUTIONS: dict[str, CapitalVector] = {
    "apply": CapitalVector(
        resume=0.0,
        skill=0.0,
        network=0.0,
        interview=0.0,
        reputation=0.0,
        opportunity=0.6,
    ),
    "outreach": CapitalVector(
        resume=0.0,
        skill=0.0,
        network=0.5,
        interview=0.05,
        reputation=0.0,
        opportunity=0.15,
    ),
    "interview_prep": CapitalVector(
        resume=0.0,
        skill=0.15,
        network=0.0,
        interview=0.4,
        reputation=0.0,
        opportunity=0.0,
    ),
    "learning": CapitalVector(
        resume=0.05,
        skill=0.5,
        network=0.0,
        interview=0.0,
        reputation=0.0,
        opportunity=0.05,
    ),
    "resume_update": CapitalVector(
        resume=0.5,
        skill=0.0,
        network=0.0,
        interview=0.0,
        reputation=0.0,
        opportunity=0.05,
    ),
    "followup": CapitalVector(
        resume=0.0,
        skill=0.0,
        network=0.2,
        interview=0.1,
        reputation=0.0,
        opportunity=0.1,
    ),
    "github_contribution": CapitalVector(
        resume=0.1,
        skill=0.2,
        network=0.0,
        interview=0.0,
        reputation=0.5,
        opportunity=0.0,
    ),
    "referral_request": CapitalVector(
        resume=0.0,
        skill=0.0,
        network=0.4,
        interview=0.1,
        reputation=0.0,
        opportunity=0.2,
    ),
}


def capital_value(
    task_kind: str,
    profile: CapitalProfile,
    base_value: float = 1.0,
) -> float:
    """Compute the capital-adjusted value of a task.

    Combines the task's capital contribution with the user's preference weights.
    This should replace raw expected_value in the planner's sort key.
    """
    contribution = KIND_CONTRIBUTIONS.get(task_kind, CapitalVector(opportunity=0.3))
    return contribution.dot(profile.weights) * base_value
