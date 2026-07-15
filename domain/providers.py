"""Task Providers — generate TaskNode objects from DecisionSnapshots.

Every provider is a pure function:
  (context, opportunities) → ProviderResult

No SQL. No filesystem. No network.
"""

from __future__ import annotations
from typing import Protocol

from domain.models import TaskNode, MissionContext, OpportunitySnapshot, ProviderResult


class TaskProvider(Protocol):
    """Interface every task provider must satisfy.

    Providers declare capabilities via priority() and supports()
    so the registry can filter them without calling build().
    """

    def kind(self) -> str:
        """Unique kind string, e.g. 'apply', 'followup', 'interview'."""
        ...

    def version(self) -> str:
        """Provider version for provenance tracking."""
        ...

    def priority(self) -> int:
        """Lower = runs earlier. Controls dependency ordering between providers."""
        ...

    def supports(self, context: MissionContext) -> bool:
        """True if this provider can generate tasks for the given context.

        A networking provider might return False for an "ASAP" goal.
        A learning provider might return False if no skill gaps exist.
        """
        return True

    def build(self, context: MissionContext, opportunities: list[OpportunitySnapshot]) -> ProviderResult:
        """Generate tasks from the given opportunities.

        Pure function — no side effects, no I/O.
        """
        ...


class ApplyTaskProvider:
    """Creates 'apply' tasks for high-scoring opportunities that haven't been applied to."""

    def kind(self) -> str:
        return "apply"

    def version(self) -> str:
        return "1"

    def priority(self) -> int:
        return 10

    def supports(self, context: MissionContext) -> bool:
        return True

    def build(self, context: MissionContext, opportunities: list[OpportunitySnapshot]) -> ProviderResult:
        result = ProviderResult(
            provider=self.kind(),
            provider_version=self.version(),
        )
        for opp in opportunities:
            task = self._build_task(opp, context)
            if task:
                result.tasks.append(task)
        result.statistics = {
            "opportunities_scanned": len(opportunities),
            "tasks_created": len(result.tasks),
            "total_value": round(result.total_estimated_value, 1),
        }
        return result

    def _build_task(self, opp: OpportunitySnapshot, context: MissionContext) -> TaskNode | None:
        from domain.models import Dependency, DependencyKind

        min_score = context.preferences.get("apply_min_score", 60)
        if opp.score < min_score:
            return None

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
        if opp.confidence == "High":
            task.add_why("High confidence estimate")
        elif opp.confidence == "Low":
            task.add_why("Low confidence — may need more data")

        return task

    def _expected_value(self, opp: OpportunitySnapshot, context: MissionContext) -> float:
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

    def _uncertainty(self, opp: OpportunitySnapshot) -> float:
        confidence_map = {"High": 2.0, "Medium": 8.0, "Low": 15.0}
        return confidence_map.get(opp.confidence, 10.0)
