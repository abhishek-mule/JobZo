"""Human approval state machine — the gate between a recommendation and action.

Nothing in JobZo submits an application, sends a cold email, or fires a
follow-up unless an ApprovalRequest for it is `approved`. Every request stores
the exact preview the user saw and the evidence behind it.

Lifecycle:
    pending ──► approved ──► executed
       │            └──────► failed
       ├──────────► rejected
       └──────────► expired
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from database.connection import get_session
from database.models import (
    Application,
    ApprovalRequest,
    Contact,
    Job,
    OutreachDraft,
)

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
EXPIRED = "expired"
EXECUTED = "executed"
FAILED = "failed"
CANCELLED = "cancelled"

STATUSES = (PENDING, APPROVED, REJECTED, EXPIRED, EXECUTED, FAILED, CANCELLED)

KIND_APPLICATION = "application"
KIND_OUTREACH = "outreach"
KIND_FOLLOWUP = "followup"
KINDS = (KIND_APPLICATION, KIND_OUTREACH, KIND_FOLLOWUP)

APPLICATION_TTL_DAYS = 7


def _json(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _loads(value: str | None) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def request_to_dict(req: ApprovalRequest) -> dict[str, Any]:
    return {
        "id": req.id,
        "kind": req.kind,
        "status": req.status,
        "title": req.title,
        "subtitle": req.subtitle,
        "application_id": req.application_id,
        "job_id": req.job_id,
        "contact_id": req.contact_id,
        "outreach_draft_id": req.outreach_draft_id,
        "opportunity_score": req.opportunity_score,
        "trust_score": req.trust_score,
        "confidence": req.confidence,
        "risk_level": req.risk_level,
        "expected_value": req.expected_value,
        "effort_minutes": req.effort_minutes,
        "payload": _loads(req.payload_json),
        "evidence": _loads(req.evidence_json),
        "decision_note": req.decision_note,
        "created_at": req.created_at,
        "expires_at": req.expires_at,
        "decided_at": req.decided_at,
        "executed_at": req.executed_at,
    }


def create_request(
    *,
    kind: str,
    dedupe_key: str,
    title: str = "",
    subtitle: str = "",
    payload: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    application_id: str | None = None,
    job_id: str | None = None,
    contact_id: str | None = None,
    outreach_draft_id: str | None = None,
    opportunity_score: float | None = None,
    trust_score: float | None = None,
    confidence: float | None = None,
    risk_level: str = "",
    expected_value: float | None = None,
    effort_minutes: int | None = None,
    status: str = PENDING,
    decision_note: str = "",
    expires_at: datetime | None = None,
    session: Session | None = None,
) -> tuple[ApprovalRequest, bool]:
    """Idempotently create a request. Returns (request, created)."""
    own_session = session is None
    session = session or get_session()
    try:
        existing = session.query(ApprovalRequest).filter(
            ApprovalRequest.dedupe_key == dedupe_key
        ).first()
        if existing is not None:
            return existing, False

        req = ApprovalRequest(
            kind=kind,
            status=status,
            title=title,
            subtitle=subtitle,
            application_id=application_id,
            job_id=job_id,
            contact_id=contact_id,
            outreach_draft_id=outreach_draft_id,
            dedupe_key=dedupe_key,
            opportunity_score=opportunity_score,
            trust_score=trust_score,
            confidence=confidence,
            risk_level=risk_level,
            expected_value=expected_value,
            effort_minutes=effort_minutes,
            payload_json=_json(payload or {}),
            evidence_json=_json(evidence or {}),
            decision_note=decision_note,
            expires_at=expires_at,
            decided_at=datetime.utcnow() if status != PENDING else None,
        )
        session.add(req)
        session.flush()
        if own_session:
            # Callers keep using this object after the session closes.
            session.expire_on_commit = False
            session.commit()
        return req, True
    except Exception:
        session.rollback()
        raise
    finally:
        if own_session:
            session.close()


def get_request(request_id: str) -> dict[str, Any] | None:
    session = get_session()
    try:
        req = session.query(ApprovalRequest).filter(ApprovalRequest.id == request_id).first()
        return request_to_dict(req) if req else None
    finally:
        session.close()


def list_requests(status: str = PENDING, kind: str | None = None,
                  limit: int = 50) -> list[dict[str, Any]]:
    expire_stale()
    session = get_session()
    try:
        query = session.query(ApprovalRequest)
        if status:
            query = query.filter(ApprovalRequest.status == status)
        if kind:
            query = query.filter(ApprovalRequest.kind == kind)
        rows = query.order_by(
            ApprovalRequest.opportunity_score.desc().nullslast(),
            ApprovalRequest.created_at.desc(),
        ).limit(limit).all()
        return [request_to_dict(r) for r in rows]
    finally:
        session.close()


def counts() -> dict[str, int]:
    expire_stale()
    session = get_session()
    try:
        rows = session.query(
            ApprovalRequest.status,
            func.count(ApprovalRequest.id),
        ).group_by(ApprovalRequest.status).all()
        result = {status: 0 for status in STATUSES}
        result.update({row[0]: row[1] for row in rows})
        return result
    finally:
        session.close()


def expire_stale(now: datetime | None = None) -> int:
    now = now or datetime.utcnow()
    session = get_session()
    try:
        rows = session.query(ApprovalRequest).filter(
            ApprovalRequest.status == PENDING,
            ApprovalRequest.expires_at.isnot(None),
            ApprovalRequest.expires_at < now,
        ).all()
        for req in rows:
            req.status = EXPIRED
            req.decided_at = now
            req.decision_note = "Expired without a decision"
        session.commit()
        return len(rows)
    except Exception:
        session.rollback()
        return 0
    finally:
        session.close()


def decide_request(request_id: str, decision: str, note: str = "") -> dict[str, Any]:
    """Approve or reject a pending request. Does not execute it."""
    if decision not in (APPROVED, REJECTED):
        return {"ok": False, "error": "decision must be approved or rejected"}

    session = get_session()
    try:
        req = session.query(ApprovalRequest).filter(ApprovalRequest.id == request_id).first()
        if req is None:
            return {"ok": False, "error": "request not found"}
        if req.status == EXPIRED:
            return {"ok": False, "error": "request expired"}
        if req.status != PENDING:
            return {"ok": False, "error": f"request already {req.status}"}

        req.status = decision
        req.decided_at = datetime.utcnow()
        req.decision_note = note

        draft = None
        if req.outreach_draft_id:
            draft = session.query(OutreachDraft).filter(
                OutreachDraft.id == req.outreach_draft_id
            ).first()
            if draft is not None:
                draft.status = APPROVED if decision == APPROVED else REJECTED

        session.commit()

        # Keep the application state honest outside this transaction.
        if req.kind == KIND_APPLICATION and req.application_id:
            from tracker.applications import transition_status
            target = "ready" if decision == APPROVED else "skipped"
            transition_status(req.application_id, target)

        return {"ok": True, "request": request_to_dict(req)}
    except Exception as exc:
        session.rollback()
        return {"ok": False, "error": str(exc)}
    finally:
        session.close()


def mark_executed(request_id: str, note: str = "") -> bool:
    session = get_session()
    try:
        req = session.query(ApprovalRequest).filter(ApprovalRequest.id == request_id).first()
        if req is None:
            return False
        req.status = EXECUTED
        req.executed_at = datetime.utcnow()
        if note:
            req.decision_note = note
        session.commit()
        return True
    except Exception:
        session.rollback()
        return False
    finally:
        session.close()


def mark_failed(request_id: str, error: str = "") -> bool:
    session = get_session()
    try:
        req = session.query(ApprovalRequest).filter(ApprovalRequest.id == request_id).first()
        if req is None:
            return False
        req.status = FAILED
        req.decision_note = error[:2000]
        session.commit()
        return True
    except Exception:
        session.rollback()
        return False
    finally:
        session.close()


# ── Application queue ────────────────────────────────────────────────────────

def _recommend_resume(job) -> dict[str, Any]:
    try:
        from resumes.registry import get_registry
        registry = get_registry()
        text = " ".join(filter(None, [
            getattr(job, "title", ""),
            getattr(job, "description", "")[:3000],
            " ".join(getattr(job, "skills", []) or []),
        ])).lower()

        best, best_score = None, -1
        for meta in registry.all():
            score = 0
            for skill in meta.skills:
                if skill.lower() in text:
                    score += 2
            for target in meta.target_roles:
                if target.lower() in text:
                    score += 3
            for domain in meta.domains:
                if domain.lower() in text:
                    score += 1
            if score > best_score:
                best, best_score = meta, score
        if best:
            return {"name": best.name, "file": best.file, "match": best_score}
    except Exception:
        pass
    return {"name": "backend_v3", "file": "backend_v3.pdf", "match": 0}


def _application_payload(app: Application, job: Job, profile: dict[str, Any]) -> dict[str, Any]:
    from services.opportunity_score import application_channel
    resume = _recommend_resume(job)
    return {
        "company": job.company,
        "role": job.title,
        "url": job.url,
        "source": job.source,
        "location": job.location or "",
        "salary": job.salary or "",
        "experience_required": job.experience_required or "",
        "match_score": app.score or 0,
        "application_channel": application_channel(job),
        "resume": resume,
        "answers": {
            "name": profile.get("name", ""),
            "email": profile.get("email", ""),
            "phone": profile.get("phone", ""),
            "linkedin": profile.get("linkedin", ""),
            "github": profile.get("github", ""),
            "location": profile.get("location", ""),
        },
        "notes": app.notes or "",
    }


def enqueue_application(application_id: str) -> dict[str, Any]:
    """Score one application and put it in the approval queue (or reject it)."""
    from services.company_registry import get_by_alias
    from services.contacts import best_contact_for
    from services.fresher import classify_fresher
    from services.opportunity_score import score_opportunity
    from services.probability import estimate
    from services.trust import trust_for_job
    from services.user_profile import load_profile

    session = get_session()
    try:
        app = session.query(Application).options(
            joinedload(Application.job)
        ).filter(Application.id == application_id).first()
        if app is None or app.job is None:
            return {"ok": False, "error": "application or job not found"}
        if app.status not in ("drafted", "ready"):
            return {"ok": False, "error": f"application is {app.status}, not awaiting review"}

        job = app.job
        profile = load_profile()
        company = None
        try:
            company = get_by_alias(job.company)
        except Exception:
            company = None

        trust = trust_for_job(job)
        fresher = classify_fresher(
            title=job.title,
            description=job.description,
            experience_required=job.experience_required,
            company=company,
        )
        contact = best_contact_for(job.company, job.title, allow_unverified=False)
        try:
            probability = estimate(job, profile).get("score")
        except Exception:
            probability = None

        opportunity = score_opportunity(
            job=job,
            match_score=app.score or 0,
            trust=trust,
            fresher=fresher,
            profile=profile,
            contact_available=contact is not None,
            interview_probability=probability,
        )

        payload = _application_payload(app, job, profile)
        evidence = {
            "score": opportunity.as_dict(),
            "trust": {
                "score": trust.score,
                "tier": trust.tier,
                "reasons": trust.reasons,
                "risks": trust.risks,
            },
            "fresher": {
                "status": fresher.status,
                "probability": fresher.prob,
                "evidence": fresher.evidence,
                "negatives": fresher.negatives,
            },
            "contact": contact.as_dict() if contact else None,
            "eligibility": {
                "eligible": job.eligible,
                "reason": job.eligibility_reason,
            },
        }

        rejected = opportunity.rejected
        req, created = create_request(
            kind=KIND_APPLICATION,
            dedupe_key=f"application:{app.id}",
            title=f"{job.company} — {job.title}",
            subtitle=opportunity.action,
            payload=payload,
            evidence=evidence,
            application_id=app.id,
            job_id=job.id,
            contact_id=contact.contact.id if contact else None,
            opportunity_score=opportunity.total,
            trust_score=trust.score,
            confidence=opportunity.interview_probability,
            risk_level="reject" if rejected else ("high" if opportunity.total >= 85 else "normal"),
            expected_value=opportunity.roi.get("expected_value"),
            effort_minutes=opportunity.effort_minutes,
            status=REJECTED if rejected else PENDING,
            decision_note="; ".join(opportunity.hard_rejects) if rejected else "",
            expires_at=None if rejected else datetime.utcnow() + timedelta(days=APPLICATION_TTL_DAYS),
        )
        return {
            "ok": True,
            "created": created,
            "queued": not rejected,
            "request": request_to_dict(req),
            "hard_rejects": opportunity.hard_rejects,
            "warnings": opportunity.warnings,
        }
    except Exception as exc:
        session.rollback()
        return {"ok": False, "error": str(exc)}
    finally:
        session.close()


def enqueue_top_applications(
    *,
    limit: int = 10,
    min_priority: float = 5.0,
    local_only: bool = False,
    fresh_only: bool = False,
) -> dict[str, Any]:
    """Fill the approval queue from the current dig ranking."""
    from services.dig import dig_jobs

    result = dig_jobs(
        fresh_only=fresh_only,
        strict=False,
        min_priority=min_priority,
        local_only=local_only,
    )

    queued, existing, rejected, skipped = [], [], [], []
    for item in result.items:
        outcome = enqueue_application(str(item.app.id))
        if not outcome.get("ok"):
            skipped.append({"company": item.job.company, "role": item.job.title,
                            "reason": outcome.get("error", "unknown")})
            continue
        if not outcome.get("queued"):
            rejected.append({
                "title": outcome["request"]["title"],
                "reason": "; ".join(outcome.get("hard_rejects", [])),
            })
            continue
        if outcome.get("created"):
            queued.append(outcome["request"])
        else:
            existing.append(outcome["request"])
        if len(queued) + len(existing) >= limit:
            break

    return {
        "ok": True,
        "candidates": len(result.items),
        "queued": queued,
        "existing": existing,
        "rejected": rejected,
        "skipped": skipped,
    }


def pending_summary() -> dict[str, Any]:
    pending = list_requests(PENDING, limit=100)
    return {
        "count": len(pending),
        "applications": sum(1 for r in pending if r["kind"] == KIND_APPLICATION),
        "outreach": sum(1 for r in pending if r["kind"] == KIND_OUTREACH),
        "followups": sum(1 for r in pending if r["kind"] == KIND_FOLLOWUP),
        "minutes": sum(r.get("effort_minutes") or 0 for r in pending
                       if r["kind"] == KIND_APPLICATION),
    }
