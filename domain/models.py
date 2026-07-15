"""Phase 3.0 — Core domain model for the execution layer.

Every component above this layer (providers, planner, execution engine)
operates on these objects. No SQL, no filesystem, no network.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any

import json


# ── Dependency types ─────────────────────────────────────────────────────────

class DependencyKind(str, Enum):
    BLOCKS = "blocks"          # This task cannot start until dependency completes
    REQUIRES = "requires"      # Dependency must exist/completed for this to make sense
    RECOMMENDS = "recommends"  # Soft dependency — nice to have
    UNLOCKS = "unlocks"        # Completing this unlocks the dependency


@dataclass
class Dependency:
    task_id: str
    kind: DependencyKind = DependencyKind.REQUIRES


# ── Task ─────────────────────────────────────────────────────────────────────

_TASK_STATES = ("pending", "active", "completed", "skipped", "deferred", "failed")


@dataclass
class TaskNode:
    """A single unit of work the planner reasons about.

    The task knows how to execute itself, explain itself, and manage
    its own lifecycle. The planner never executes — the task does.
    """
    id: str
    kind: str
    title: str
    description: str
    source: str

    opportunity_id: str | None = None
    estimated_minutes: int = 15
    expected_value: float = 0.0
    uncertainty: float = 0.0
    urgency: str = "medium"
    deadline: datetime | None = None
    dependencies: list[Dependency] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    _state: str = "pending"
    _why_lines: list[str] = field(default_factory=list)

    # ── State ────────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    @state.setter
    def state(self, value: str) -> None:
        if value not in _TASK_STATES:
            raise ValueError(f"Invalid task state: {value}")
        self._state = value

    @property
    def is_completed(self) -> bool:
        return self._state == "completed"

    @property
    def is_pending(self) -> bool:
        return self._state == "pending"

    @property
    def is_actionable(self) -> bool:
        return self._state in ("pending", "deferred")

    @property
    def value_density(self) -> float:
        """Expected value per minute — planner's primary sort key."""
        if self.estimated_minutes <= 0:
            return self.expected_value
        return self.expected_value / self.estimated_minutes

    # ── Lifecycle ────────────────────────────────────────────────────────

    def can_execute(self) -> bool:
        """Check all BLOCKS / REQUIRES dependencies are completed."""
        for dep in self.dependencies:
            if dep.kind in (DependencyKind.BLOCKS, DependencyKind.REQUIRES):
                if dep.task_id not in getattr(self, "_completed_ids", set()):
                    return False
        return True

    def execute(self) -> bool:
        if not self.can_execute():
            return False
        self._state = "active"
        return True

    def complete(self) -> None:
        self._state = "completed"

    def skip(self, reason: str = "") -> None:
        self._state = "skipped"
        if reason:
            self._why_lines.append(f"Skipped: {reason}")

    def defer(self) -> None:
        self._state = "deferred"

    def fail(self, reason: str = "") -> None:
        self._state = "failed"
        if reason:
            self._why_lines.append(f"Failed: {reason}")

    # ── Explanation ──────────────────────────────────────────────────────

    def why(self) -> list[str]:
        """Human-readable justification for this task."""
        lines = list(self._why_lines)
        if not lines:
            lines.append(f"{self.title}")
            if self.expected_value > 0:
                lines.append(f"  Expected value: {self.expected_value:.1f}")
            if self.uncertainty > 0:
                lines.append(f"  Uncertainty: ±{self.uncertainty:.1f}")
        return lines

    def add_why(self, line: str) -> None:
        self._why_lines.append(line)

    # ── Serialization ────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "source": self.source,
            "opportunity_id": self.opportunity_id,
            "estimated_minutes": self.estimated_minutes,
            "expected_value": self.expected_value,
            "uncertainty": self.uncertainty,
            "urgency": self.urgency,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "dependencies": [{"task_id": d.task_id, "kind": d.kind.value} for d in self.dependencies],
            "blockers": self.blockers,
            "metadata": self.metadata,
            "state": self._state,
            "why": self._why_lines,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TaskNode:
        deps = [Dependency(task_id=d["task_id"], kind=DependencyKind(d["kind"])) for d in data.get("dependencies", [])]
        deadline = datetime.fromisoformat(data["deadline"]) if data.get("deadline") else None
        node = cls(
            id=data["id"],
            kind=data["kind"],
            title=data["title"],
            description=data.get("description", ""),
            source=data.get("source", ""),
            opportunity_id=data.get("opportunity_id"),
            estimated_minutes=data.get("estimated_minutes", 15),
            expected_value=data.get("expected_value", 0.0),
            uncertainty=data.get("uncertainty", 0.0),
            urgency=data.get("urgency", "medium"),
            deadline=deadline,
            dependencies=deps,
            blockers=data.get("blockers", []),
            metadata=data.get("metadata", {}),
        )
        node._state = data.get("state", "pending")
        node._why_lines = data.get("why", [])
        return node


# ── OpportunityView ──────────────────────────────────────────────────────────

@dataclass
class OpportunityView:
    """Read-model of an evaluated opportunity for task providers.

    Pure data — no DB references. Providers build tasks from this.
    """
    snapshot_id: str
    opportunity_id: str
    job_id: str
    company: str
    title: str
    url: str
    score: int
    tier: str
    interview_probability: int
    confidence: str
    risk: str
    effort_minutes: int
    canonical_role: str
    seniority: str
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    raw_description: str = ""
    source: str = ""

    @classmethod
    def from_snapshot(cls, snapshot, app=None, job=None) -> OpportunityView:
        """Build from a DecisionSnapshot DB row + optional related objects."""
        import json
        company = ""
        title = ""
        url = ""
        source = ""
        raw_desc = ""
        if job:
            company = job.company
            title = job.title
            url = job.url
            source = job.source
        if snapshot.details_json:
            try:
                details = json.loads(snapshot.details_json)
                raw_desc = details.get("raw_description", "")
            except (json.JSONDecodeError, TypeError):
                details = {}
        else:
            details = {}
        missing_raw = details.get("missing_skills", [])
        missing_skills = [s[0] if isinstance(s, (list, tuple)) else s for s in missing_raw]
        return cls(
            snapshot_id=snapshot.id,
            opportunity_id=snapshot.application_id if app else "",
            job_id=snapshot.application_id if app else "",
            company=company,
            title=title,
            url=url,
            score=snapshot.composite_score,
            tier=snapshot.tier,
            interview_probability=snapshot.interview_probability,
            confidence=snapshot.confidence,
            risk=snapshot.risk,
            effort_minutes=snapshot.effort_minutes,
            canonical_role=snapshot.canonical_role or "",
            seniority=snapshot.seniority or "",
            matched_skills=details.get("matched_skills", []),
            missing_skills=missing_skills,
            raw_description=raw_desc,
            source=source,
        )


# ── Mission ──────────────────────────────────────────────────────────────────

_MISSION_STATES = ("active", "paused", "failed", "completed")


@dataclass
class Mission:
    """A collection of tasks to execute within a time budget.

    The planner produces a Mission. The execution engine runs it.
    A Mission can be paused and resumed.
    """
    id: str
    generated_at: datetime
    objective: str
    estimated_minutes: int
    expected_gain: float
    confidence: str
    tasks: list[TaskNode] = field(default_factory=list)
    state: str = "active"
    completed_task_ids: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.state not in _MISSION_STATES:
            raise ValueError(f"Invalid mission state: {self.state}")

    @property
    def progress(self) -> float:
        if not self.tasks:
            return 0.0
        completed = sum(1 for t in self.tasks if t.is_completed)
        return completed / len(self.tasks)

    @property
    def active_tasks(self) -> list[TaskNode]:
        return [t for t in self.tasks if t.is_actionable and t.can_execute()]

    @property
    def blocked_tasks(self) -> list[TaskNode]:
        return [t for t in self.tasks if t.is_actionable and not t.can_execute()]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "generated_at": self.generated_at.isoformat(),
            "objective": self.objective,
            "estimated_minutes": self.estimated_minutes,
            "expected_gain": self.expected_gain,
            "confidence": self.confidence,
            "tasks": [t.to_dict() for t in self.tasks],
            "state": self.state,
            "completed_task_ids": list(self.completed_task_ids),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Mission:
        return cls(
            id=data["id"],
            generated_at=datetime.fromisoformat(data["generated_at"]),
            objective=data["objective"],
            estimated_minutes=data["estimated_minutes"],
            expected_gain=data["expected_gain"],
            confidence=data.get("confidence", "Medium"),
            tasks=[TaskNode.from_dict(t) for t in data.get("tasks", [])],
            state=data.get("state", "active"),
            completed_task_ids=set(data.get("completed_task_ids", [])),
        )


# ── MissionContext ───────────────────────────────────────────────────────────

@dataclass
class MissionContext:
    """Immutable context shared across all task providers and the planner.

    No globals. Every component receives the same context.
    """
    time_budget: int = 60          # daily minutes available for mission tasks
    goal: str = "Get placed ASAP"
    today: date = field(default_factory=date.today)
    timezone: str = "UTC"
    preferences: dict = field(default_factory=dict)

    def with_budget(self, minutes: int) -> MissionContext:
        return MissionContext(
            time_budget=minutes,
            goal=self.goal,
            today=self.today,
            timezone=self.timezone,
            preferences=self.preferences,
        )
