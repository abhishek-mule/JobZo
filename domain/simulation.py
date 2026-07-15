"""Planner simulation — Monte Carlo evaluation of scheduling strategies.

Simulates N days of random interview/offer/reject outcomes to measure
expected interviews, offers, and salary before shipping planner changes.
"""

from __future__ import annotations
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from domain.models import TaskNode, MissionContext
from domain.planner import GreedyPlanner


@dataclass
class SimulationConfig:
    days: int = 30
    seed: int = 42
    daily_budget: int = 60
    interview_probability_range: tuple[float, float] = (0.1, 0.6)
    offer_probability_given_interview: float = 0.25
    salary_range: tuple[int, int] = (6, 30)  # LPA


@dataclass
class TaskTemplate:
    """A task template the simulation can sample from."""
    id: str
    kind: str
    title: str
    estimated_minutes: int
    expected_value: float
    uncertainty: float = 5.0


@dataclass
class SimulationResult:
    config: SimulationConfig
    total_applications: int = 0
    total_interviews: int = 0
    total_offers: int = 0
    total_salary_lpa: float = 0.0
    days_to_first_interview: int | None = None
    days_to_first_offer: int | None = None
    budget_utilization: float = 0.0
    daily_logs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def placement_probability(self) -> float:
        if self.total_applications == 0:
            return 0.0
        return self.total_offers / max(self.total_applications, 1)

    @property
    def efficiency(self) -> float:
        """Offers per 100 applications."""
        return self.total_offers / max(self.total_applications, 1) * 100

    def summary(self) -> dict[str, Any]:
        return {
            "days": self.config.days,
            "applications": self.total_applications,
            "interviews": self.total_interviews,
            "offers": self.total_offers,
            "salary_lpa": round(self.total_salary_lpa, 1),
            "placement_probability": round(self.placement_probability * 100, 1),
            "days_to_first_interview": self.days_to_first_interview,
            "days_to_first_offer": self.days_to_first_offer,
            "budget_utilization": round(self.budget_utilization * 100, 1),
        }


def generate_task_pool(
    count: int = 50,
    config: SimulationConfig | None = None,
) -> list[TaskTemplate]:
    """Generate a pool of task templates for simulation."""
    if config is None:
        config = SimulationConfig()
    rng = random.Random(config.seed)
    companies = [
        "Stripe", "BrowserStack", "Postman", "Nubank", "Razorpay",
        "Groww", "Zerodha", "Freshworks", "Chargebee", "Hasura",
        "Druva", "Whatfix", "Unacademy", "ShareChat", "CRED",
    ]
    tasks: list[TaskTemplate] = []
    for i in range(count):
        company = rng.choice(companies)
        prob = rng.uniform(*config.interview_probability_range)
        mins = rng.choice([10, 15, 15, 20, 20, 25, 30])
        tasks.append(TaskTemplate(
            id=f"sim-{i}",
            kind="apply",
            title=f"Apply to {company}",
            estimated_minutes=mins,
            expected_value=round(prob * 100, 1),
            uncertainty=round(rng.uniform(2, 15), 1),
        ))
    return tasks


def simulate(
    planner: GreedyPlanner,
    config: SimulationConfig | None = None,
    task_pool: list[TaskTemplate] | None = None,
) -> SimulationResult:
    """Run a Monte Carlo simulation of the planner over N days.

    Each day:
      1. Sample task pool for available tasks
      2. Run planner with daily budget
      3. Simulate random outcomes (interview → offer → salary)
      4. Track pipeline state across days
    """
    if config is None:
        config = SimulationConfig()
    rng = random.Random(config.seed)

    if task_pool is None:
        task_pool = generate_task_pool(50, config)

    result = SimulationResult(config=config)
    context = MissionContext(
        time_budget=config.daily_budget,
        goal="Get placed ASAP",
        today=date.today(),
    )

    pipeline: dict[str, dict[str, Any]] = {}  # task_id -> state
    total_budget_used = 0
    total_budget_available = config.days * config.daily_budget

    for day in range(config.days):
        # Available tasks: not yet applied, not yet rejected
        available_templates = [
            t for t in task_pool
            if t.id not in pipeline
        ]

        tasks = [
            TaskNode(
                id=t.id,
                kind=t.kind,
                title=t.title,
                description="",
                source="simulation",
                estimated_minutes=t.estimated_minutes,
                expected_value=t.expected_value,
                uncertainty=t.uncertainty,
            )
            for t in available_templates
        ]

        packed, rejected = planner.rank_tasks(tasks, config.daily_budget)
        total_budget_used += sum(t.estimated_minutes for t in packed)
        day_log: dict[str, Any] = {
            "day": day + 1,
            "available": len(tasks),
            "applied": len(packed),
            "rejected_budget": len(rejected),
            "interviews_scheduled": 0,
            "offers_received": 0,
        }

        for t in packed:
            pipeline[t.id] = {"applied_at": day, "status": "applied"}

        # Resolve pending outcomes for tasks applied >2 days ago
        for task_id, state in list(pipeline.items()):
            if state["status"] != "applied":
                continue
            if day - state["applied_at"] < 2:
                continue

            template = next(t for t in task_pool if t.id == task_id)
            interview_prob = template.expected_value / 100.0

            if rng.random() < interview_prob:
                state["status"] = "interview"
                state["interview_at"] = day
                result.total_interviews += 1
                day_log["interviews_scheduled"] += 1

                if rng.random() < config.offer_probability_given_interview:
                    state["status"] = "offer"
                    state["offer_at"] = day
                    salary = rng.randint(*config.salary_range)
                    state["salary"] = salary
                    result.total_offers += 1
                    result.total_salary_lpa += salary
                    day_log["offers_received"] += 1

                    result.days_to_first_offer = result.days_to_first_offer or (day + 1)
            else:
                state["status"] = "rejected"

        result.days_to_first_interview = result.days_to_first_interview or (
            day + 1 if day_log["interviews_scheduled"] > 0 else None
        )

        result.total_applications += len(packed)
        result.daily_logs.append(day_log)

        # Stop early if we have an offer and config prefers that
        if result.total_offers > 0:
            pass  # Continue simulating for full picture

    result.budget_utilization = total_budget_used / max(total_budget_available, 1)
    return result
