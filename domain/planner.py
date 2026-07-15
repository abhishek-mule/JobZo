"""Planner v1 — greedy scheduler with dependency-respecting value ranking.

The planner receives ProviderResults (from the registry), resolves
dependencies, ranks by value density within dependency levels, and
packs into the time budget. Tasks that don't fit are returned as
rejected_tasks so the caller can explain why they were excluded.

No SQL. No filesystem. No network. Pure function.
"""

from __future__ import annotations
from datetime import datetime
from uuid import uuid4

from domain.models import (
    TaskNode, Mission, MissionContext, DependencyKind,
    ProviderResult,
)


class GreedyPlanner:
    """Greedy scheduler — sorts by value density within dependency depth,
    then packs into time budget. Rejected tasks are returned for debugging.

    This is intentionally simple. Once we have real usage data we can
    upgrade to knapsack, uncertainty-aware, or multi-objective optimization.
    """

    def plan(
        self,
        provider_results: list[ProviderResult],
        context: MissionContext,
    ) -> Mission:
        """Plan a mission from provider results and context."""
        all_tasks = []
        for pr in provider_results:
            all_tasks.extend(pr.tasks)

        sorted_tasks = self._rank_within_levels(all_tasks)
        packed, rejected = self._pack_into_budget(sorted_tasks, context.time_budget)

        total_minutes = sum(t.estimated_minutes for t in packed)
        total_gain = sum(t.expected_value for t in packed)
        confidence = self._compute_confidence(packed)

        # Provenance for traceability
        provider_map = {r.provider: r.provider_version for r in provider_results}
        provenance = {
            "planner": "greedy_v1",
            "planned_at": datetime.utcnow().isoformat(),
            "budget_minutes": context.time_budget,
            "goal": context.goal,
            "total_candidates": len(all_tasks),
            "accepted": len(packed),
            "rejected": len(rejected),
            "providers": provider_map,
        }

        return Mission(
            id=str(uuid4()),
            generated_at=datetime.utcnow(),
            objective=context.goal,
            estimated_minutes=total_minutes,
            expected_gain=round(total_gain, 1),
            confidence=confidence,
            tasks=packed,
            state="active",
            rejected_tasks=rejected,
            provider_results=provider_results,
            plan_provenance=provenance,
        )

    def rank_tasks(self, tasks: list[TaskNode], budget_minutes: int) -> tuple[list[TaskNode], list[TaskNode]]:
        """Public helper — rank and pack tasks without provider result wrapping."""
        sorted_tasks = self._rank_within_levels(tasks)
        return self._pack_into_budget(sorted_tasks, budget_minutes)

    def _rank_within_levels(self, tasks: list[TaskNode]) -> list[TaskNode]:
        """Sort by value density within dependency depth.

        Tasks that depend on others come after their dependencies,
        regardless of value density. Within the same depth level,
        higher value density wins.
        """
        task_map = {t.id: t for t in tasks}
        depth: dict[str, int] = {}

        def _depth_of(task_id: str, seen: set[str] | None = None) -> int:
            if seen is None:
                seen = set()
            if task_id in seen:
                return 0
            seen.add(task_id)
            t = task_map.get(task_id)
            if not t:
                return 0
            max_dep = 0
            for dep in t.dependencies:
                if dep.kind in (DependencyKind.BLOCKS, DependencyKind.REQUIRES):
                    max_dep = max(max_dep, _depth_of(dep.task_id, seen) + 1)
            depth[task_id] = max_dep
            return max_dep

        for t in tasks:
            _depth_of(t.id)

        return sorted(tasks, key=lambda t: (depth.get(t.id, 0), -t.value_density))

    def _pack_into_budget(
        self, tasks: list[TaskNode], budget_minutes: int,
    ) -> tuple[list[TaskNode], list[TaskNode]]:
        """Greedy knapsack — take highest value-density tasks that fit.

        Returns (accepted, rejected).
        """
        packed: list[TaskNode] = []
        rejected: list[TaskNode] = []
        used = 0
        for t in tasks:
            if used + t.estimated_minutes <= budget_minutes:
                packed.append(t)
                used += t.estimated_minutes
            else:
                rejected.append(t)
        return packed, rejected

    def _compute_confidence(self, tasks: list[TaskNode]) -> str:
        if not tasks:
            return "Low"
        avg_uncertainty = sum(t.uncertainty for t in tasks) / len(tasks)
        if avg_uncertainty <= 3.0:
            return "High"
        elif avg_uncertainty <= 10.0:
            return "Medium"
        return "Low"
