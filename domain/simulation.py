"""Planner simulation — Monte Carlo evaluation of scheduling strategies.

Simulates N days of random interview/offer/reject outcomes to measure
expected interviews, offers, and salary before shipping planner changes.
Supports scenario comparison: "Should I learn Redis, apply, or network?"
"""

from __future__ import annotations
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from domain.models import TaskNode, MissionContext
from domain.planner import GreedyPlanner
from domain.capital import CapitalProfile, KIND_CONTRIBUTIONS, capital_value


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


# ── Scenario comparison ──────────────────────────────────────────────────

@dataclass
class Scenario:
    """A career strategy to simulate and compare."""
    name: str
    description: str
    task_mix: dict[str, float]  # kind -> proportion (e.g. {"apply": 0.7, "outreach": 0.3})


@dataclass
class ScenarioResult:
    """Outcome of a single scenario simulation."""
    scenario: Scenario
    runs: list[SimulationResult] = field(default_factory=list)
    capital_profile: CapitalProfile | None = None

    @property
    def average(self) -> dict[str, Any]:
        if not self.runs:
            return {}
        avg: dict[str, float] = {}
        keys = ["total_applications", "total_interviews", "total_offers", "total_salary_lpa"]
        for k in keys:
            avg[k] = sum(getattr(r, k, 0) for r in self.runs) / len(self.runs)
        # Capital score
        if self.capital_profile:
            capital_scores = []
            for r in self.runs:
                apply_count = sum(1 for log in r.daily_logs for _ in range(log.get("applied", 0)))
                score = 0.0
                for kind, weight in self.scenario.task_mix.items():
                    contrib = KIND_CONTRIBUTIONS.get(kind)
                    if contrib:
                        score += contrib.dot(self.capital_profile.weights) * apply_count * weight
                capital_scores.append(score)
            avg["capital_score"] = round(sum(capital_scores) / len(capital_scores), 1)
        else:
            avg["capital_score"] = 0.0
        avg["placement_probability"] = round(
            avg["total_offers"] / max(avg["total_applications"], 1) * 100, 1
        )
        return avg

    def compare_to(self, other: ScenarioResult) -> dict[str, Any]:
        a = self.average
        b = other.average
        if not a or not b:
            return {}
        deltas = {}
        for k in a:
            if k in b:
                deltas[k] = round(a[k] - b[k], 1)
        return deltas


def simulate_scenario(
    scenario: Scenario,
    total_hours: float = 8.0,
    days: int = 30,
    runs: int = 10,
    seed: int = 42,
    capital_profile: CapitalProfile | None = None,
) -> ScenarioResult:
    """Simulate a single career scenario over multiple runs.

    Args:
        scenario: Which task mix to simulate.
        total_hours: Total time budget for the simulation period.
        days: Number of days to simulate.
        runs: Number of Monte Carlo runs.
        seed: Random seed.
        capital_profile: Optional capital weights for scoring.

    Returns:
        ScenarioResult with aggregated statistics.
    """
    cfg = SimulationConfig(
        days=days,
        daily_budget=int(total_hours * 60 / days),
        seed=seed,
    )
    planner = GreedyPlanner()
    scenario_runs: list[SimulationResult] = []

    for run in range(runs):
        cfg.seed = seed + run
        pool = _scenario_task_pool(scenario, cfg)
        result = simulate(planner, cfg, pool)
        scenario_runs.append(result)

    return ScenarioResult(
        scenario=scenario,
        runs=scenario_runs,
        capital_profile=capital_profile,
    )


def _scenario_task_pool(
    scenario: Scenario,
    config: SimulationConfig,
) -> list[TaskTemplate]:
    """Generate a task pool matching a scenario's task mix."""
    rng = random.Random(config.seed + 999)
    companies = [
        "Stripe", "BrowserStack", "Postman", "Nubank", "Razorpay",
        "Groww", "Zerodha", "Freshworks", "Chargebee", "Hasura",
    ]
    pool: list[TaskTemplate] = []
    total = 50
    for kind, proportion in scenario.task_mix.items():
        count = int(total * proportion)
        for i in range(count):
            company = rng.choice(companies)
            prob = rng.uniform(*config.interview_probability_range)
            mins = _kind_time(kind)
            pool.append(TaskTemplate(
                id=f"{kind}-{i}",
                kind=kind,
                title=_kind_title(kind, company),
                estimated_minutes=mins,
                expected_value=round(prob * 100, 1),
            ))
    rng.shuffle(pool)
    return pool


def _kind_time(kind: str) -> int:
    return {"apply": 15, "outreach": 6, "learning": 30, "followup": 5}.get(kind, 15)


def _kind_title(kind: str, company: str) -> str:
    titles = {
        "apply": f"Apply to {company}",
        "outreach": f"Contact recruiter at {company}",
        "learning": "Learn Redis + Kafka",
        "followup": f"Follow up on {company} application",
    }
    return titles.get(kind, f"Task at {company}")


def compare_scenarios(
    scenarios: list[Scenario],
    total_hours: float = 8.0,
    days: int = 30,
    runs: int = 10,
    capital_profile: CapitalProfile | None = None,
) -> dict[str, Any]:
    """Compare multiple career scenarios side by side.

    Args:
        scenarios: List of scenarios to compare.
        total_hours: Time budget to split across scenarios.
        days: Simulation horizon.
        runs: Monte Carlo runs per scenario.

    Returns:
        Comparison dict with averages, deltas, and recommendation.
    """
    results: list[ScenarioResult] = []
    for sc in scenarios:
        result = simulate_scenario(sc, total_hours, days, runs, capital_profile=capital_profile)
        results.append(result)

    comparison = {
        "total_hours": total_hours,
        "days": days,
        "scenarios": [],
    }

    best_result = max(results, key=lambda r: r.average.get("capital_score", 0))
    for r in results:
        avg = r.average
        entry = {
            "name": r.scenario.name,
            "description": r.scenario.description,
            "task_mix": r.scenario.task_mix,
            **avg,
        }
        if r is not best_result:
            delta = best_result.average.get("capital_score", 0) - avg.get("capital_score", 0)
            entry["vs_best"] = round(delta, 1)
        else:
            entry["vs_best"] = 0.0
            entry["recommended"] = True
        comparison["scenarios"].append(entry)

    comparison["recommendation"] = best_result.scenario.name
    comparison["recommendation_reason"] = (
        f"Scenario '{best_result.scenario.name}' maximizes capital accumulation "
        f"with {best_result.average.get('capital_score', 0)} capital score, "
        f"{best_result.average.get('total_offers', 0):.1f} expected offers."
    )
    return comparison
