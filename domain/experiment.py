"""Experiment Framework — measure whether changes actually improve outcomes.

Freeze features. Start running experiments.

Every decision (planner version, capital profile, provider config, calibration
curve) becomes a dimension we can A/B test. Results accumulate in an evidence
store — the beginning of proprietary knowledge.
"""

from __future__ import annotations
import json
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from enum import Enum
from typing import Any, Callable

from database.connection import get_session
from database.models import Event

logger = logging.getLogger("jobzo.experiment")


# ── Metrics ─────────────────────────────────────────────────────────────

class Metric(str, Enum):
    """What we measure in an experiment."""
    INTERVIEW_RATE = "interview_rate"
    OFFER_RATE = "offer_rate"
    TIME_TO_INTERVIEW = "time_to_interview"  # days
    TIME_TO_OFFER = "time_to_offer"          # days
    APPLICATIONS_PER_WEEK = "applications_per_week"
    CAPITAL_ACCUMULATED = "capital_accumulated"
    REPLY_RATE = "reply_rate"                # outreach reply rate
    USER_SATISFACTION = "user_satisfaction"   # 1-5


# ── Hypothesis ───────────────────────────────────────────────────────────

@dataclass
class Hypothesis:
    """What we believe and how we test it."""
    name: str
    description: str
    metric: Metric
    expected_improvement: float  # e.g. 0.15 = 15% improvement
    min_sample_size: int = 30
    confidence_threshold: float = 0.95  # p-value threshold


# ── Treatment ────────────────────────────────────────────────────────────

@dataclass
class Treatment:
    """A variant in an experiment — always has a control."""
    name: str
    config: dict[str, Any]  # The specific change being tested
    is_control: bool = False

    @classmethod
    def control(cls, name: str = "control") -> Treatment:
        return cls(name=name, config={}, is_control=True)


# ── Experiment ───────────────────────────────────────────────────────────

class AssignmentStrategy(str, Enum):
    """How users are assigned to treatment vs control."""
    RANDOM = "random"               # Pure random assignment
    USER_ID_HASH = "user_id_hash"   # Deterministic by user ID
    OPT_IN = "opt_in"               # User chooses


@dataclass
class Experiment:
    """A controlled experiment comparing two or more treatments."""
    id: str
    hypothesis: Hypothesis
    treatments: list[Treatment]
    assignment_strategy: AssignmentStrategy = AssignmentStrategy.USER_ID_HASH
    active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def control(self) -> Treatment | None:
        return next((t for t in self.treatments if t.is_control), None)

    @property
    def variants(self) -> list[Treatment]:
        return [t for t in self.treatments if not t.is_control]


# ── Observation ──────────────────────────────────────────────────────────

@dataclass
class ExperimentObservation:
    """A single data point from an experiment — one prediction + outcome."""
    experiment_id: str
    treatment: str
    user_id: str
    metric: Metric
    predicted_value: float
    actual_value: float | None  # None = pending
    observed_at: datetime | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "treatment": self.treatment,
            "user_id": self.user_id,
            "metric": self.metric.value,
            "predicted": self.predicted_value,
            "actual": self.actual_value,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "metadata": self.metadata,
        }


# ── Result ───────────────────────────────────────────────────────────────

@dataclass
class ExperimentResult:
    """Statistical result of an experiment."""
    experiment_id: str
    hypothesis: Hypothesis
    control_metric: float
    treatment_metric: float
    improvement: float           # relative improvement
    effect_size: float           # Cohen's d
    p_value: float
    significant: bool
    sample_size: int
    recommendation: str

    def summary(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "hypothesis": self.hypothesis.name,
            "metric": self.hypothesis.metric.value,
            "control": round(self.control_metric, 3),
            "treatment": round(self.treatment_metric, 3),
            "improvement": f"{self.improvement:+.1%}",
            "effect_size": round(self.effect_size, 2),
            "p_value": round(self.p_value, 4),
            "significant": self.significant,
            "sample_size": self.sample_size,
            "recommendation": self.recommendation,
        }


# ── ExperimentService ────────────────────────────────────────────────────

class ExperimentService:
    """Register experiments, assign treatments, record observations, analyze results.

    This is the evidence engine. Every A/B test, every planner comparison,
    every capital profile variant runs through here.
    """

    def __init__(self) -> None:
        self._experiments: dict[str, Experiment] = {}
        self._observations: list[ExperimentObservation] = []

    def register(self, experiment: Experiment) -> None:
        self._experiments[experiment.id] = experiment
        logger.info("Experiment registered: %s (%d treatments)", experiment.id, len(experiment.treatments))

    def get(self, experiment_id: str) -> Experiment | None:
        return self._experiments.get(experiment_id)

    def list_active(self) -> list[Experiment]:
        return [e for e in self._experiments.values() if e.active]

    def assign(self, experiment_id: str, user_id: str) -> Treatment:
        """Assign a user to a treatment group for an experiment."""
        exp = self._experiments.get(experiment_id)
        if not exp or not exp.active:
            return Treatment.control(experiment_id)

        if exp.assignment_strategy == AssignmentStrategy.RANDOM:
            import random as _random
            idx = _random.randint(0, len(exp.treatments) - 1)
            return exp.treatments[idx]

        # USER_ID_HASH: deterministic assignment
        hash_val = hash(f"{experiment_id}:{user_id}")
        idx = abs(hash_val) % len(exp.treatments)
        return exp.treatments[idx]

    def record_observation(self, obs: ExperimentObservation) -> None:
        """Record a data point for an experiment."""
        self._observations.append(obs)

    def batch_record(self, observations: list[ExperimentObservation]) -> None:
        self._observations.extend(observations)

    def observations_for(self, experiment_id: str) -> list[ExperimentObservation]:
        return [o for o in self._observations if o.experiment_id == experiment_id]

    def analyze(self, experiment_id: str) -> ExperimentResult | None:
        """Run statistical analysis on an experiment's observations."""
        exp = self._experiments.get(experiment_id)
        if not exp:
            return None

        obs = self.observations_for(experiment_id)
        control_obs = [o for o in obs if o.treatment == "control" and o.actual_value is not None]
        treatment_obs = [o for o in obs if o.treatment != "control" and o.actual_value is not None]

        if len(control_obs) < 2 or len(treatment_obs) < 2:
            return None

        control_vals = [o.actual_value for o in control_obs]
        treatment_vals = [o.actual_value for o in treatment_obs]

        mean_c = sum(control_vals) / len(control_vals)
        mean_t = sum(treatment_vals) / len(treatment_vals)
        improvement = (mean_t - mean_c) / max(abs(mean_c), 0.001)

        # Cohen's d
        var_c = sum((v - mean_c) ** 2 for v in control_vals) / len(control_vals)
        var_t = sum((v - mean_t) ** 2 for v in treatment_vals) / len(treatment_vals)
        pooled_std = math.sqrt((var_c + var_t) / 2)
        effect = (mean_t - mean_c) / max(pooled_std, 0.001)

        # Welch's t-test approximation
        se = math.sqrt(var_c / len(control_vals) + var_t / len(treatment_vals))
        t_stat = (mean_t - mean_c) / max(se, 0.001)
        df = len(control_vals) + len(treatment_vals) - 2
        p_value = self._approximate_p(t_stat, df)

        significant = p_value < (1 - exp.hypothesis.confidence_threshold) and \
            len(control_obs) + len(treatment_obs) >= exp.hypothesis.min_sample_size

        if significant and improvement > 0:
            recommendation = f"Deploy '{exp.variants[0].name}' — {improvement:+.1%} improvement in {exp.hypothesis.metric.value}"
        elif significant and improvement <= 0:
            recommendation = f"Keep control — treatment underperformed by {abs(improvement):.1%}"
        else:
            recommendation = "Inconclusive — collect more data"

        return ExperimentResult(
            experiment_id=experiment_id,
            hypothesis=exp.hypothesis,
            control_metric=round(mean_c, 3),
            treatment_metric=round(mean_t, 3),
            improvement=improvement,
            effect_size=effect,
            p_value=round(p_value, 4),
            significant=significant,
            sample_size=len(control_obs) + len(treatment_obs),
            recommendation=recommendation,
        )

    def compare_all_active(self) -> list[dict[str, Any]]:
        """Analyze all active experiments and return summaries."""
        results = []
        for exp in self.list_active():
            result = self.analyze(exp.id)
            if result:
                results.append(result.summary())
        return results

    @staticmethod
    def _approximate_p(t_stat: float, df: int) -> float:
        """Approximate p-value from t-statistic using normal approximation.

        For large df (>30), t-distribution ≈ normal.
        For smaller df, this is a rough approximation.
        """
        from math import erf
        x = t_stat / math.sqrt(2)
        try:
            return 1.0 - abs(erf(x))
        except (OverflowError, ValueError):
            return 0.5


# ── Built-in experiments ─────────────────────────────────────────────────

def create_default_experiments() -> list[Experiment]:
    """Create the experiments JobZo should run by default."""
    return [
        Experiment(
            id="planner_v1_vs_capital",
            hypothesis=Hypothesis(
                name="Career Capital beats Interview Probability",
                description="Planner optimized for career capital produces more offers than planner optimized for interview probability alone.",
                metric=Metric.OFFER_RATE,
                expected_improvement=0.15,
            ),
            treatments=[
                Treatment.control("interview_probability"),
                Treatment(name="career_capital", config={"objective": "capital", "profile": "Get placed ASAP"}),
            ],
        ),
        Experiment(
            id="outreach_effectiveness",
            hypothesis=Hypothesis(
                name="Outreach tasks improve interview rate",
                description="Adding outreach tasks (contacting recruiters/founders) alongside applications increases interview rate.",
                metric=Metric.INTERVIEW_RATE,
                expected_improvement=0.10,
            ),
            treatments=[
                Treatment.control("applications_only"),
                Treatment(name="applications_plus_outreach", config={"providers": ["apply", "outreach"]}),
            ],
        ),
        Experiment(
            id="capital_profile_asap_vs_growth",
            hypothesis=Hypothesis(
                name="Short-term vs long-term capital weighting",
                description="Users optimizing for 'Get placed ASAP' vs 'Career growth (long-term)' produce different offer rates.",
                metric=Metric.OFFER_RATE,
                expected_improvement=0.0,  # Direction unknown — exploratory
            ),
            treatments=[
                Treatment.control("asap"),
                Treatment(name="long_term_growth", config={"profile": "Career growth (long-term)"}),
            ],
        ),
    ]


# ── Planner instrumentation ──────────────────────────────────────────────

def instrument_mission(mission, experiment_service: ExperimentService, user_id: str) -> None:
    """Record observations from a planned mission into the experiment framework.

    Call this after every mission plan to track planner performance.
    """
    provenance = mission.plan_provenance or {}
    planner_version = provenance.get("planner", "unknown")

    obs = ExperimentObservation(
        experiment_id="planner_v1_vs_capital",
        treatment=planner_version,
        user_id=user_id,
        metric=Metric.APPLICATIONS_PER_WEEK,
        predicted_value=mission.expected_gain,
        actual_value=None,
        metadata={
            "task_count": len(mission.tasks),
            "rejected_count": len(mission.rejected_tasks),
            "objective": mission.objective,
        },
    )
    experiment_service.record_observation(obs)


def instrument_outcome(
    application_id: str,
    experiment_service: ExperimentService,
    user_id: str,
    treatment: str,
    had_interview: bool,
    had_offer: bool,
) -> None:
    """Record outcome observations when an application resolves."""
    if had_interview:
        experiment_service.record_observation(ExperimentObservation(
            experiment_id="outreach_effectiveness",
            treatment=treatment,
            user_id=user_id,
            metric=Metric.INTERVIEW_RATE,
            predicted_value=0.0,
            actual_value=1.0,
            metadata={"application_id": application_id},
        ))
    if had_offer:
        experiment_service.record_observation(ExperimentObservation(
            experiment_id="planner_v1_vs_capital",
            treatment=treatment,
            user_id=user_id,
            metric=Metric.OFFER_RATE,
            predicted_value=0.0,
            actual_value=1.0,
            metadata={"application_id": application_id},
        ))
