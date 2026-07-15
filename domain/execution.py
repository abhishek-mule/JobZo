"""Mission execution engine — run, complete, skip, defer, resume.

The execution engine manages the lifecycle of a Mission and its tasks.
It does NOT know SQL — it operates on in-memory Mission objects.
Persistence is handled by the caller.
"""

from __future__ import annotations
from typing import Callable

from domain.models import Mission, TaskNode


class MissionExecution:
    """Lifecycle manager for a Mission.

    The caller provides a Mission and optionally an execute_task callback
    that the engine calls when a task should actually be performed
    (e.g., opening a browser, sending an email).
    """

    def __init__(self, mission: Mission, execute_task: Callable[[TaskNode], bool] | None = None):
        self.mission = mission
        self._execute_task = execute_task

    # ── Mission lifecycle ───────────────────────────────────────────────

    def start(self) -> Mission:
        """Begin executing the mission. Returns updated mission."""
        if self.mission.state != "active":
            raise ValueError(f"Cannot start mission in state: {self.mission.state}")
        return self.mission

    def pause(self) -> Mission:
        """Pause execution — all active tasks become deferred."""
        for t in self.mission.tasks:
            if t.state == "active":
                t.defer()
        self.mission.state = "paused"
        return self.mission

    def resume(self) -> Mission:
        """Resume a paused mission."""
        if self.mission.state != "paused":
            raise ValueError(f"Cannot resume mission in state: {self.mission.state}")
        self.mission.state = "active"
        return self.mission

    def complete(self) -> Mission:
        """Mark mission as completed."""
        self.mission.state = "completed"
        return self.mission

    def fail(self, reason: str = "") -> Mission:
        """Mark mission as failed."""
        self.mission.state = "failed"
        return self.mission

    # ── Task lifecycle ──────────────────────────────────────────────────

    def execute_task(self, task_id: str) -> bool:
        """Execute a single task. Returns True if successful."""
        task = self._find_task(task_id)
        if not task or not task.is_actionable:
            return False

        if not task.can_execute():
            task.fail("Dependencies not met")
            return False

        task.execute()

        if self._execute_task:
            success = self._execute_task(task)
            if not success:
                task.fail("Execution failed")
                return False

        return True

    def complete_task(self, task_id: str) -> bool:
        """Mark task as completed."""
        task = self._find_task(task_id)
        if not task:
            return False
        task.complete()
        self.mission.completed_task_ids.add(task_id)
        return True

    def skip_task(self, task_id: str, reason: str = "") -> bool:
        """Skip a task."""
        task = self._find_task(task_id)
        if not task:
            return False
        task.skip(reason)
        return True

    def defer_task(self, task_id: str) -> bool:
        """Defer a task — move it to later."""
        task = self._find_task(task_id)
        if not task:
            return False
        task.defer()
        return True

    def fail_task(self, task_id: str, reason: str = "") -> bool:
        """Mark task as failed."""
        task = self._find_task(task_id)
        if not task:
            return False
        task.fail(reason)
        return True

    # ── Query ───────────────────────────────────────────────────────────

    def next_actionable(self) -> list[TaskNode]:
        """Return tasks ready to execute right now."""
        return self.mission.active_tasks

    def blocked_reason(self, task_id: str) -> list[str]:
        """Explain why a task is blocked."""
        from domain.models import DependencyKind

        task = self._find_task(task_id)
        if not task:
            return ["Task not found"]

        reasons = []
        for dep in task.dependencies:
            if dep.kind in (DependencyKind.BLOCKS, DependencyKind.REQUIRES):
                dep_task = self._find_task(dep.task_id)
                if not dep_task or not dep_task.is_completed:
                    status = dep_task.state if dep_task else "unknown"
                    reasons.append(f"Depends on '{dep.task_id}' (status: {status})")
        return reasons

    # ── Internal ────────────────────────────────────────────────────────

    def _find_task(self, task_id: str) -> TaskNode | None:
        for t in self.mission.tasks:
            if t.id == task_id:
                return t
        return None
