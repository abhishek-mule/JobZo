"""Planner v1 — greedy scheduler.

The planner receives a list of TaskNode objects and a MissionContext,
and produces a Mission sorted by expected value per minute with
dependency resolution.

No SQL. No filesystem. No network. Pure function.
"""

from __future__ import annotations
from datetime import datetime
from uuid import uuid4

from domain.models import TaskNode, Mission, MissionContext, DependencyKind


class GreedyPlanner:
    """Greedy scheduler — sorts by value density, respects dependencies and time budget.

    This is intentionally simple. Once we have real usage data we can
    upgrade to knapsack, uncertainty-aware, or multi-objective optimization.
    """

    def plan(self, tasks: list[TaskNode], context: MissionContext) -> Mission:
        sorted_tasks = self._rank_within_levels(tasks)
        packed = self._pack_into_budget(sorted_tasks, context.time_budget)

        total_minutes = sum(t.estimated_minutes for t in packed)
        total_gain = sum(t.expected_value for t in packed)
        confidence = self._compute_confidence(packed)

        return Mission(
            id=str(uuid4()),
            generated_at=datetime.utcnow(),
            objective=context.goal,
            estimated_minutes=total_minutes,
            expected_gain=round(total_gain, 1),
            confidence=confidence,
            tasks=packed,
            state="active",
        )

    def _rank_within_levels(self, tasks: list[TaskNode]) -> list[TaskNode]:
        """Sort by value density within dependency levels.

        This does a stable topological sort: tasks are grouped by their
        dependency depth, and within each group they're sorted by value density.
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

    def _pack_into_budget(self, tasks: list[TaskNode], budget_minutes: int) -> list[TaskNode]:
        """Greedy knapsack — take highest value-density tasks that fit."""
        packed: list[TaskNode] = []
        used = 0
        for t in tasks:
            if used + t.estimated_minutes <= budget_minutes:
                packed.append(t)
                used += t.estimated_minutes
        return packed

    def _compute_confidence(self, tasks: list[TaskNode]) -> str:
        if not tasks:
            return "Low"
        avg_uncertainty = sum(t.uncertainty for t in tasks) / len(tasks)
        if avg_uncertainty <= 3.0:
            return "High"
        elif avg_uncertainty <= 10.0:
            return "Medium"
        return "Low"
