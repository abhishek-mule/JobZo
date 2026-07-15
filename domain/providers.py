"""Task Providers — generate TaskNode objects from DecisionSnapshots.

Every provider is a pure function:
  (context, opportunities) → TaskNode[]

No SQL. No filesystem. No network.
"""

from __future__ import annotations
from typing import Protocol
from domain.models import TaskNode, MissionContext, OpportunityView


class TaskProvider(Protocol):
    """Interface every task provider must satisfy."""

    def kind(self) -> str:
        """Unique kind string, e.g. 'apply', 'followup', 'interview'."""
        ...

    def build(self, context: MissionContext, opportunities: list[OpportunityView]) -> list[TaskNode]:
        """Generate tasks from the given opportunities.

        Pure function — no side effects, no I/O.
        """
        ...


class ApplyTaskProvider:
    """Creates 'apply' tasks for high-scoring opportunities that haven't been applied to."""

    def kind(self) -> str:
        return "apply"

    def build(self, context: MissionContext, opportunities: list[OpportunityView]) -> list[TaskNode]:
        tasks: list[TaskNode] = []
        for opp in opportunities:
            task = self._build_task(opp, context)
            if task:
                tasks.append(task)
        return tasks

    def _build_task(self, opp: OpportunityView, context: MissionContext) -> TaskNode | None:
        from domain.models import Dependency, DependencyKind

        min_score = context.preferences.get("apply_min_score", 60)
        if opp.score < min_score:
            return None

        now = context.today

        task = TaskNode(
            id=f"apply-{opp.snapshot_id}",
            kind="apply",
            title=f"Apply to {opp.company}",
            description=f"Submit application for {opp.title} at {opp.company}",
            source="apply_provider",
            opportunity_id=opp.opportunity_id,
            estimated_minutes=opp.effort_minutes,
            expected_value=self._expected_value(opp, context),
            uncertainty=self._uncertainty(opp),
            urgency="high" if opp.score >= 80 else "medium",
            deadline=None,
            dependencies=[],
            metadata={
                "company": opp.company,
                "role": opp.title,
                "url": opp.url,
                "score": opp.score,
                "tier": opp.tier,
                "interview_probability": opp.interview_probability,
                "confidence": opp.confidence,
                "risk": opp.risk,
            },
        )

        task.add_why(f"Score: {opp.score}/100 ({opp.tier.replace('_', ' ').title()})")
        task.add_why(f"Interview probability: {opp.interview_probability}%")
        if opp.matched_skills:
            task.add_why(f"Skills matched: {len(opp.matched_skills)}")
        if opp.missing_skills:
            task.add_why(f"Skills to learn: {', '.join(opp.missing_skills[:3])}")
        if opp.confidence == "High":
            task.add_why("High confidence estimate")
        elif opp.confidence == "Low":
            task.add_why("Low confidence — may need more data")

        return task

    def _expected_value(self, opp: OpportunityView, context: MissionContext) -> float:
        """Compute expected value for an apply task.

        Combines interview probability with goal-specific weights.
        """
        goal = context.goal
        base = opp.interview_probability / 100.0

        if goal == "Get placed ASAP":
            return round(base * 100, 1)
        elif goal == "Maximize salary":
            salary_weight = {"apply_now": 1.2, "strong_match": 1.0, "worth_trying": 0.8, "stretch": 0.6}
            return round(base * 100 * salary_weight.get(opp.tier, 1.0), 1)
        elif goal == "Crack product companies":
            product_weight = 1.3 if opp.risk == "Hard" else 0.9
            return round(base * 100 * product_weight, 1)
        else:
            return round(base * 100, 1)

    def _uncertainty(self, opp: OpportunityView) -> float:
        confidence_map = {"High": 2.0, "Medium": 8.0, "Low": 15.0}
        return confidence_map.get(opp.confidence, 10.0)
