"""DecisionSnapshot — persist, read, and manage immutable decision records.

Every snapshot captures the full state of a retriever + ranker decision:
structured columns for queryable fields + details_json for the full payload.
Snapshots are never modified — only created.
"""

from __future__ import annotations
import json
from datetime import datetime

from sqlalchemy.orm import Session

from database.models import DecisionSnapshot, Application
from ai.models import RankedOpportunity
from ai import (
    RETRIEVER_VERSION,
    RANKER_VERSION,
    SKILL_GRAPH_VERSION,
    REGISTRY_VERSION,
)

from tracker.events import record_event, PREDICTION_MADE


def persist(
    session: Session,
    app: Application,
    opp: RankedOpportunity,
) -> DecisionSnapshot:
    """Create and return a DecisionSnapshot from a RankedOpportunity.

    Links the snapshot to the application and sets it as the active decision.
    """
    details = {
        "score_vector": opp.score_vector.to_dict(),
        "matched_skills": opp.matched_skills,
        "missing_skills": [(s, w, r) for s, w, r in opp.missing_skills],
        "expanded_skills": opp.expanded_skills,
        "retrieval_score": opp.retrieval_score,
        "skill_overlap": opp.skill_overlap,
        "freshness": opp.freshness,
        "raw_description": opp.raw_description[:500] if opp.raw_description else "",
        "explanations": opp.score_vector.explanations,
    }

    snapshot = DecisionSnapshot(
        application_id=app.id,
        composite_score=opp.composite_score(),
        tier=opp.tier(),
        interview_probability=opp.interview_probability,
        confidence=opp.confidence,
        risk=opp.risk,
        effort_minutes=opp.effort_minutes,
        canonical_role=opp.canonical_role,
        role_confidence=opp.role_confidence,
        seniority=opp.seniority,
        retriever_version=RETRIEVER_VERSION,
        ranker_version=RANKER_VERSION,
        registry_version=REGISTRY_VERSION,
        skill_graph_version=SKILL_GRAPH_VERSION,
        generated_at=datetime.utcnow(),
        details_json=json.dumps(details),
    )
    session.add(snapshot)
    session.flush()

    app.current_decision_id = snapshot.id
    app.score = opp.composite_score()
    app.tier = opp.tier()

    record_event(
        PREDICTION_MADE,
        "application",
        app.id,
        actor="system",
        metadata={
            "snapshot_id": snapshot.id,
            "score": opp.composite_score(),
            "tier": opp.tier(),
            "probability": opp.interview_probability,
            "confidence": opp.confidence,
        },
        session=session,
    )

    return snapshot


def get_active(app: Application, session: Session | None = None) -> DecisionSnapshot | None:
    """Get the active DecisionSnapshot for an application."""
    if app.current_decision_id:
        if session:
            return session.get(DecisionSnapshot, app.current_decision_id)
    return None


def get_all_for(app_id: str, session: Session) -> list[DecisionSnapshot]:
    """Get all snapshots for an application, newest first."""
    return (
        session.query(DecisionSnapshot)
        .filter(DecisionSnapshot.application_id == app_id)
        .order_by(DecisionSnapshot.generated_at.desc())
        .all()
    )


def snapshot_to_inbox_data(snapshot: DecisionSnapshot) -> dict:
    """Extract inbox-friendly display data from a snapshot."""
    details = json.loads(snapshot.details_json) if snapshot.details_json else {}

    score_vector = details.get("score_vector", {})
    matched = details.get("matched_skills", [])
    missing = details.get("missing_skills", [])
    explanations = details.get("explanations", {})

    breakdown = {}
    for key in ("role_fit", "skill_fit", "experience_fit", "company_fit", "location_fit", "growth_value"):
        val = score_vector.get(key, 0)
        if isinstance(val, float):
            breakdown[key.replace("_fit", "").replace("_value", "").title()] = int(val * 100)

    return {
        "score": snapshot.composite_score,
        "tier": snapshot.tier,
        "interview_probability": snapshot.interview_probability,
        "confidence": snapshot.confidence,
        "risk": snapshot.risk,
        "effort_minutes": snapshot.effort_minutes,
        "canonical_role": snapshot.canonical_role,
        "role_confidence": snapshot.role_confidence,
        "matched_skills": matched,
        "missing_skills": [s[0] if isinstance(s, (list, tuple)) else s for s in missing],
        "explanations": explanations,
        "score_breakdown": breakdown,
    }
