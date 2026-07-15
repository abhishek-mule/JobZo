"""Funnel analytics and probability calibration from observed outcomes.

ProjectionService  — company, skill, resume, and time funnels (observed throughput).
CalibrationService — predicted vs observed probability curves to calibrate estimates.
"""

from __future__ import annotations
import logging
import math
from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy.orm import Session

from database.connection import get_session
from database.models import Application, Event
from domain.observation import Observation, ObservationType, ObservationService

logger = logging.getLogger("jobzo.analytics")


# ── ProjectionService ────────────────────────────────────────────────────────

@dataclass
class FunnelStage:
    name: str
    count: int
    conversion: float = 0.0  # fraction that made it from previous stage


class ProjectionService:
    """Observed conversion rates through each lifecycle stage.

    Queries can be scoped to a single company, a skill group, or globally.
    """

    STAGES = [
        ObservationType.APPLICATION_SUBMITTED,
        ObservationType.APPLICATION_VIEWED,
        ObservationType.OA_RECEIVED,
        ObservationType.OA_COMPLETED,
        ObservationType.INTERVIEW_SCHEDULED,
        ObservationType.INTERVIEW_PASSED,
        ObservationType.OFFER_RECEIVED,
        ObservationType.OFFER_ACCEPTED,
    ]
    TERMINAL = {
        ObservationType.REJECTED,
        ObservationType.GHOSTED,
        ObservationType.OFFER_DECLINED,
    }

    @staticmethod
    def _stage_index(obs_type: ObservationType) -> int:
        try:
            return ProjectionService.STAGES.index(obs_type)
        except ValueError:
            return -1

    @staticmethod
    def _count_stage(
        observations: list[Observation],
        stage: ObservationType,
    ) -> int:
        return sum(1 for o in observations if o.observation_type == stage)

    @staticmethod
    def company_funnel(
        company: str,
        exclude_ghosted: bool = False,
        session: Session | None = None,
    ) -> list[FunnelStage]:
        """Observed funnel for a single company."""
        obs = ObservationService.get_all_for_company(company, session)
        return ProjectionService._build_funnel(obs, exclude_ghosted)

    @staticmethod
    def global_funnel(
        exclude_ghosted: bool = False,
        session: Session | None = None,
    ) -> list[FunnelStage]:
        """Observed funnel across all applications."""
        own_session = False
        if session is None:
            session = get_session()
            own_session = True
        try:
            rows = (
                session.query(Event)
                .filter(Event.entity_type == "application")
                .all()
            )
            obs = [o for e in rows if (o := Observation.from_event(e))]
            return ProjectionService._build_funnel(obs, exclude_ghosted)
        finally:
            if own_session:
                session.close()

    @staticmethod
    def _build_funnel(
        observations: list[Observation],
        exclude_ghosted: bool = False,
    ) -> list[FunnelStage]:
        if not observations:
            return []

        submitted = ProjectionService._count_stage(
            observations, ObservationType.APPLICATION_SUBMITTED
        )
        stages = []
        for i, stage in enumerate(ProjectionService.STAGES):
            count = ProjectionService._count_stage(observations, stage)
            if i == 0:
                conversion = 1.0
            else:
                prev_count = stages[-1].count
                conversion = count / prev_count if prev_count > 0 else 0.0
            stages.append(FunnelStage(name=stage.value, count=count, conversion=conversion))

        return stages

    @staticmethod
    def time_to_next_stage(
        application_id: str,
        from_type: ObservationType,
        to_type: ObservationType,
        session: Session | None = None,
    ) -> timedelta | None:
        """Calculate time between two observation types for an application."""
        obs = ObservationService.get_for_application(application_id, session)
        from_obs = [o for o in obs if o.observation_type == from_type]
        to_obs = [o for o in obs if o.observation_type == to_type]
        if not from_obs or not to_obs:
            return None
        from_time = from_obs[0].occurred_at
        to_time = to_obs[0].occurred_at
        if to_time and from_time and to_time > from_time:
            return to_time - from_time
        return None

    @staticmethod
    def average_time_to_stage(
        stage: ObservationType,
        session: Session | None = None,
    ) -> float | None:
        """Average days from submission to a given stage across all apps."""
        own_session = False
        if session is None:
            session = get_session()
            own_session = True
        try:
            rows = (
                session.query(Event)
                .filter(
                    Event.entity_type == "application",
                    Event.event_type == ObservationType.APPLICATION_SUBMITTED.value,
                )
                .all()
            )
            if not rows:
                return None
            total_days = 0.0
            count = 0
            for submit_event in rows:
                app_id = submit_event.entity_id
                stage_events = (
                    session.query(Event)
                    .filter(
                        Event.entity_type == "application",
                        Event.entity_id == app_id,
                        Event.event_type == stage.value,
                    )
                    .order_by(Event.occurred_at.asc())
                    .all()
                )
                if stage_events and submit_event.occurred_at:
                    delta = stage_events[0].occurred_at - submit_event.occurred_at
                    total_days += delta.total_seconds() / 86400
                    count += 1
            return round(total_days / count, 1) if count > 0 else None
        finally:
            if own_session:
                session.close()


# ── CalibrationService ───────────────────────────────────────────────────────

@dataclass
class CalibrationPoint:
    """A bucket of predictions with the same expected probability."""
    expected: float       # average predicted probability
    observed: float       # actual observed rate
    count: int            # number of predictions in this bucket


class CalibrationService:
    """Compare predicted probabilities against observed outcomes.

    Given N predictions of '70% interview chance', how many actually got an interview?
    This produces a calibration curve that can be used to adjust future estimates.
    """

    BUCKET_COUNT = 10

    @staticmethod
    def build_curve(
        session: Session | None = None,
    ) -> list[CalibrationPoint]:
        """Build a calibration curve from observed outcomes vs predictions.

        For each application with a DecisionSnapshot, compares the predicted
        interview_probability against what actually happened.
        Returns bucketed calibration points.
        """
        own_session = False
        if session is None:
            session = get_session()
            own_session = True
        try:
            from database.models import DecisionSnapshot

            predictions = (
                session.query(DecisionSnapshot)
                .filter(DecisionSnapshot.interview_probability.isnot(None))
                .all()
            )
            if not predictions:
                return []

            buckets: dict[int, list[float]] = {
                i: [] for i in range(CalibrationService.BUCKET_COUNT)
            }

            for snap in predictions:
                prob = snap.interview_probability
                if prob is None:
                    continue
                app = snap.application
                if not app:
                    continue
                # Check if the application actually got an interview
                app_id = str(app.id)
                events = (
                    session.query(Event)
                    .filter(
                        Event.entity_type == "application",
                        Event.entity_id == app_id,
                        Event.event_type.in_([
                            ObservationType.INTERVIEW_SCHEDULED.value,
                            ObservationType.REJECTED.value,
                            ObservationType.GHOSTED.value,
                        ]),
                    )
                    .all()
                )
                had_interview = any(
                    e.event_type == ObservationType.INTERVIEW_SCHEDULED.value
                    for e in events
                )
                # Assign to bucket
                bucket = min(int(prob * CalibrationService.BUCKET_COUNT), CalibrationService.BUCKET_COUNT - 1)
                buckets.setdefault(bucket, []).append(1.0 if had_interview else 0.0)

            curve = []
            for i in range(CalibrationService.BUCKET_COUNT):
                vals = buckets.get(i, [])
                if not vals:
                    continue
                mid = (i + 0.5) / CalibrationService.BUCKET_COUNT
                observed = sum(vals) / len(vals)
                curve.append(CalibrationPoint(
                    expected=round(mid, 2),
                    observed=round(observed, 2),
                    count=len(vals),
                ))
            return curve
        finally:
            if own_session:
                session.close()

    @staticmethod
    def calibrate(
        raw_probability: float,
        curve: list[CalibrationPoint] | None = None,
    ) -> float:
        """Adjust a raw probability using the calibration curve.

        Uses isotonic-style bucketed mapping. Falls back to raw if no curve.
        """
        if not curve:
            return raw_probability
        # Find the closest bucket
        closest = min(curve, key=lambda p: abs(p.expected - raw_probability))
        if abs(closest.expected - raw_probability) > (1.0 / CalibrationService.BUCKET_COUNT):
            # Outside calibration range — return raw
            return raw_probability
        return closest.observed

    @staticmethod
    def expected_value(
        probability: float,
        value_if_success: float = 1.0,
        cost_if_failure: float = 0.0,
    ) -> float:
        """Compute expected value from calibrated probability."""
        return probability * value_if_success + (1 - probability) * cost_if_failure

    @staticmethod
    def confidence_interval(
        probability: float,
        n: int,
        z: float = 1.96,
    ) -> tuple[float, float]:
        """Wilson score interval for a probability estimate.

        Wider intervals when n is small — reflects uncertainty.
        """
        if n == 0:
            return (0.0, 1.0)
        denominator = 1 + z**2 / n
        centre = (probability + z**2 / (2 * n)) / denominator
        margin = z * math.sqrt(
            (probability * (1 - probability) + z**2 / (4 * n)) / n
        ) / denominator
        return (
            round(max(0.0, centre - margin), 3),
            round(min(1.0, centre + margin), 3),
        )


# ── CalibrationAnalyzer ───────────────────────────────────────────────────────

@dataclass
class DimensionError:
    """Error contribution from a single model dimension."""
    dimension: str
    predicted_contribution: float
    actual_contribution: float
    error: float               # actual - predicted (positive = underpredicting)
    impact: str                # "overweight", "underweight", or "correct"


@dataclass
class RootCause:
    """Why a prediction was wrong — broken down by dimension."""
    predicted_probability: float
    observed_probability: float
    error: float
    dimensions: list[DimensionError] = field(default_factory=list)
    top_cause: str = ""
    recommendation: str = ""

    def summary(self) -> dict[str, Any]:
        return {
            "predicted": round(self.predicted_probability, 2),
            "observed": round(self.observed_probability, 2),
            "error": round(self.error, 2),
            "top_cause": self.top_cause,
            "recommendation": self.recommendation,
            "dimensions": [
                {"name": d.dimension, "error": round(d.error, 3), "impact": d.impact}
                for d in self.dimensions
            ],
        }


class CalibrationAnalyzer:
    """Decompose prediction errors into per-dimension root causes.

    Given a prediction with sub-scores (skill match, experience, etc.)
    and an actual outcome, identify which dimensions were most wrong.
    """

    @staticmethod
    def analyze(
        predicted_probability: float,
        score_vector: dict[str, float],
        actual_outcome: float,  # 1.0 for success, 0.0 for failure
        baseline_rate: float = 0.15,
    ) -> RootCause:
        """Analyze why a prediction was wrong.

        Args:
            predicted_probability: What the model predicted (0-1).
            score_vector: Per-dimension scores from DecisionSnapshot.details_json.
            actual_outcome: 1.0 if interview/offer happened, 0.0 otherwise.
            baseline_rate: Global observed baseline for comparison.

        Returns:
            RootCause with per-dimension error breakdown.
        """
        error = actual_outcome - predicted_probability

        if not score_vector:
            return RootCause(
                predicted_probability=predicted_probability,
                observed_probability=actual_outcome,
                error=error,
                top_cause="no_dimension_data",
                recommendation="Collect score vectors to enable root-cause analysis.",
            )

        total_score = sum(score_vector.values()) or 1.0
        dim_errors: list[DimensionError] = []

        for dim, score in score_vector.items():
            weight = score / total_score
            predicted_contrib = weight * predicted_probability
            # What contribution would suggest the actual outcome?
            actual_contrib = weight * actual_outcome if actual_outcome > 0 else weight * baseline_rate

            err = actual_contrib - predicted_contrib
            impact = "correct"
            if abs(err) > 0.03:
                impact = "overweight" if err < 0 else "underweight"

            dim_errors.append(DimensionError(
                dimension=dim,
                predicted_contribution=round(predicted_contrib, 3),
                actual_contribution=round(actual_contrib, 3),
                error=round(err, 3),
                impact=impact,
            ))

        dim_errors.sort(key=lambda d: abs(d.error), reverse=True)

        top = dim_errors[0] if dim_errors else None
        top_cause = top.dimension if top and top.impact != "correct" else "calibration_shift"
        recommendation = CalibrationAnalyzer._recommendation(top_cause, error, dim_errors)

        return RootCause(
            predicted_probability=predicted_probability,
            observed_probability=actual_outcome,
            error=error,
            dimensions=dim_errors,
            top_cause=top_cause,
            recommendation=recommendation,
        )

    @staticmethod
    def _recommendation(
        top_cause: str,
        error: float,
        dims: list[DimensionError],
    ) -> str:
        if top_cause == "calibration_shift":
            return "Global probability offset — apply calibration curve."
        for d in dims:
            if d.dimension == top_cause:
                if d.impact == "overweight":
                    return f"Reduce {top_cause} weight — it contributed {abs(d.error):.1%} too much to the prediction."
                else:
                    return f"Increase {top_cause} weight — it contributed {abs(d.error):.1%} too little."
        return "Collect more data to identify root cause."

    @staticmethod
    def batch_analyze(
        predictions: list[dict[str, Any]],
        session: Any = None,
    ) -> list[RootCause]:
        """Analyze multiple predictions against observed outcomes.

        Each prediction dict must have:
            - predicted_probability: float
            - score_vector: dict[str, float]
            - application_id: str
        """
        results: list[RootCause] = []
        for pred in predictions:
            app_id = pred.get("application_id", "")
            if not app_id:
                continue
            obs = ObservationService.get_for_application(app_id, session)
            had_interview = any(
                o.observation_type == ObservationType.INTERVIEW_SCHEDULED for o in obs
            )
            actual = 1.0 if had_interview else 0.0
            rc = CalibrationAnalyzer.analyze(
                predicted_probability=pred.get("predicted_probability", 0.5),
                score_vector=pred.get("score_vector", {}),
                actual_outcome=actual,
            )
            results.append(rc)
        return results
