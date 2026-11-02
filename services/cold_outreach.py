"""Personalized outreach — draft, approve, then send exactly once.

Rules enforced here:
  * only verified contacts with a verified company-domain email are eligible
  * every message references the actual vacancy
  * one message per contact/job/kind; a 72-hour contact cooldown
  * sending requires an approved ApprovalRequest
  * the default transport is a local .eml outbox, not a real send
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from database.connection import get_session
from database.models import (
    Application,
    Contact,
    Interaction,
    Job,
    OutreachDraft,
)
from services.approval import (
    APPROVED,
    KIND_FOLLOWUP,
    KIND_OUTREACH,
    PENDING,
    create_request,
    mark_executed,
    mark_failed,
    request_to_dict,
)
from services.contacts import (
    ContactRecord,
    best_contact_for,
    verify_contact,
)
from services.emailer import SendResult, get_sender

MIN_HOURS_BETWEEN_MESSAGES = 72
MAX_COMPANY_MESSAGES_PER_DAY = 2

OUTREACH_APPROVAL_TTL_DAYS = 5


@dataclass
class DraftResult:
    ok: bool
    draft: dict[str, Any] | None = None
    approval: dict[str, Any] | None = None
    created: bool = False
    error: str = ""


def _first_name(name: str) -> str:
    parts = [p for p in (name or "").split() if p]
    if not parts or parts[0].lower() in ("hiring", "recruiting", "talent", "team"):
        return ""
    return parts[0]


SKILL_DISPLAY = {
    "java": "Java",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "python": "Python",
    "springboot": "Spring Boot",
    "react": "React",
    "next.js": "Next.js",
    "nest.js": "Nest.js",
    "node.js": "Node.js",
    "postgresql": "PostgreSQL",
    "mongodb": "MongoDB",
    "mysql": "MySQL",
    "redis": "Redis",
    "aws": "AWS",
    "gcp": "GCP",
    "ci/cd": "CI/CD",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
}


def _display_skill(skill: str) -> str:
    clean = skill.strip()
    return SKILL_DISPLAY.get(clean.lower(), clean)


def _contains_skill(skill: str, text: str) -> bool:
    """Word-boundary match so short junk skills (e.g. "g") do not match."""
    token = skill.strip().lower()
    if len(token) < 2:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _matched_skills(job, profile: dict[str, Any], limit: int = 3) -> list[str]:
    declared = [s.strip() for s in profile.get("declared_skills", []) if s and s.strip()]
    if not declared:
        return []
    job_skills = [s for s in (job.skills or []) if isinstance(s, str) and s.strip()]
    text = f"{job.title} {job.description}".lower()
    matched: list[str] = []
    seen: set[str] = set()

    def add(skill: str) -> None:
        key = skill.strip().lower()
        if key and key not in seen:
            seen.add(key)
            matched.append(_display_skill(skill))

    for skill in job_skills:
        if len(skill.strip()) >= 2 and any(
            skill.strip().lower() == d.lower() for d in declared
        ):
            add(skill)
    for skill in declared:
        if _contains_skill(skill, text):
            add(skill)
    return matched[:limit]


def _proof_line(profile: dict[str, Any], skills: list[str]) -> str:
    summary = (profile.get("summary") or "").strip()
    if summary:
        first = re.split(r"(?<=[.!?])\s+", summary)[0].strip()
        if first and len(first) <= 220:
            return first
    if skills:
        return f"I work with {', '.join(skills[:3])} and enjoy building production-oriented systems."
    return ""


def _application_state(application: Application | None) -> tuple[bool, str]:
    if application is None:
        return False, ""
    applied = bool(application.applied_at) or application.status in (
        "submitted", "interview", "offer", "accepted", "declined",
    )
    if not applied:
        return False, ""
    when = application.applied_at
    if when is None:
        return True, ""
    return True, when.strftime("%b %d")


def compose_outreach(
    *,
    contact: Contact,
    job: Job,
    application: Application | None,
    profile: dict[str, Any],
    kind: str = "initial",
) -> tuple[str, str]:
    """Return (subject, body). Pure; no I/O, no LLM, no invented facts."""
    company = job.company
    role = job.title
    name = (profile.get("name") or "").strip()
    first = _first_name(contact.name)
    greeting = f"Hi {first}," if first else "Hi there,"
    skills = _matched_skills(job, profile)
    proof = _proof_line(profile, skills)
    headline = (profile.get("headline") or profile.get("title") or "software engineer").strip()
    applied, applied_on = _application_state(application)
    role_line = f"{role} at {company}"

    if kind in ("followup", "recruiter_followup"):
        applied_clause = f"my application for the {role_line}" + (f" on {applied_on}" if applied_on else "")
        subject = f"Re: Application: {role} at {company}"
        body = [
            greeting,
            "",
            f"I wanted to follow up on {applied_clause}. I remain interested in the role and would be glad to share anything else that helps your team evaluate my fit.",
        ]
        if skills:
            body.append(f"My recent work has focused on {', '.join(skills[:3])}.")
        if proof:
            body.append(proof)
        body.extend([
            "",
            "Thank you for your time.",
            "",
            "Best regards,",
            name or "[Your Name]",
        ])
        return subject[:110], "\n".join(body).strip()

    if kind == "final_followup":
        subject = f"Re: Application: {role} at {company}"
        body = [
            greeting,
            "",
            f"I am closing the loop on my application for the {role_line}. I understand you are busy and appreciate the time you have already spent reviewing my background.",
            "If the role is still open and my profile could be a fit, I would welcome the chance to discuss it. Otherwise, thank you for your consideration.",
            "",
            "Best regards,",
            name or "[Your Name]",
        ]
        return subject[:110], "\n".join(body).strip()

    applied_clause = ""
    if applied:
        applied_clause = f" and wanted to reach out directly" + (f" after applying on {applied_on}" if applied_on else "")
    elif application is not None:
        applied_clause = " and wanted to reach out directly"

    subject = f"{'Application' if applied else 'Interest'}: {role} at {company}"
    body = [
        greeting,
        "",
        f"I'm reaching out about the {role_line} role{applied_clause}.",
    ]
    if skills:
        body.append(f"I'm a {headline} with hands-on experience in {', '.join(skills[:3])}.")
    else:
        body.append(f"I'm a {headline} interested in the work your team is doing.")
    if proof:
        body.append(proof)
    body.append(
        "If you are the right person for this role, I would appreciate a short conversation. "
        "If not, I would be grateful if you could point me to the right person."
    )
    body.extend([
        "",
        f"Role: {job.url}",
        "",
        "Best regards,",
        name or "[Your Name]",
    ])
    if profile.get("linkedin"):
        body.append(profile["linkedin"])
    return subject[:110], "\n".join(body).strip()


def outreach_allowed(
    *,
    contact_id: str,
    job_id: str | None,
    application_id: str | None,
    kind: str = "initial",
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Check duplicate, cooldown, and daily-volume rules before drafting."""
    now = now or datetime.utcnow()
    session = get_session()
    try:
        contact = session.get(Contact, contact_id)
        if contact is None:
            return False, "contact not found"

        if contact.last_contacted:
            hours = (now - contact.last_contacted).total_seconds() / 3600
            if hours < MIN_HOURS_BETWEEN_MESSAGES:
                return False, f"contacted {hours:.0f}h ago (cooldown {MIN_HOURS_BETWEEN_MESSAGES}h)"

        if application_id and kind == "initial":
            existing = session.query(Interaction).filter(
                Interaction.contact_id == contact_id,
                Interaction.application_id == application_id,
                Interaction.direction == "outbound",
            ).first()
            if existing:
                return False, "already contacted this contact about this application"

        company = contact.company
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        company_sent = session.query(func.count(Interaction.id)).join(
            Contact, Interaction.contact_id == Contact.id
        ).filter(
            func.lower(Contact.company) == company.lower(),
            Interaction.direction == "outbound",
            Interaction.occurred_at >= day_start,
        ).scalar() or 0
        if company_sent >= MAX_COMPANY_MESSAGES_PER_DAY:
            return False, f"daily limit reached for {company}"

        return True, "ok"
    finally:
        session.close()


def draft_to_dict(draft: OutreachDraft, contact: Contact | None = None,
                  job: Job | None = None) -> dict[str, Any]:
    return {
        "id": draft.id,
        "contact_id": draft.contact_id,
        "application_id": draft.application_id,
        "job_id": draft.job_id,
        "followup_id": draft.followup_id,
        "kind": draft.kind,
        "subject": draft.subject,
        "body": draft.body,
        "status": draft.status,
        "approval_request_id": draft.approval_request_id,
        "company": contact.company if contact else "",
        "contact_name": contact.name if contact else "",
        "contact_email": contact.email if contact else "",
        "role": job.title if job else "",
        "created_at": draft.created_at,
        "sent_at": draft.sent_at,
        "send_error": draft.send_error,
        "evidence": json.loads(draft.evidence_json) if draft.evidence_json else {},
    }


def draft_outreach(
    *,
    application_id: str | None = None,
    job_id: str | None = None,
    contact_id: str | None = None,
    kind: str = "initial",
    followup_id: str | None = None,
) -> DraftResult:
    """Create a personalized draft + pending approval request."""
    from services.user_profile import load_profile

    session = get_session()
    try:
        app = None
        if application_id:
            app = session.query(Application).options(
                joinedload(Application.job)
            ).filter(Application.id == application_id).first()
            if app is None:
                return DraftResult(ok=False, error="application not found")
            job = app.job
        elif job_id:
            job = session.query(Job).filter(Job.id == job_id).first()
            if job is None:
                return DraftResult(ok=False, error="job not found")
        else:
            return DraftResult(ok=False, error="application_id or job_id required")

        if contact_id:
            contact = session.get(Contact, contact_id)
            if contact is None:
                return DraftResult(ok=False, error="contact not found")
            record = verify_contact(contact_id, session=session)
        else:
            record = best_contact_for(job.company, job.title, allow_unverified=False)
            contact = record.contact if record else None
            if contact is None:
                return DraftResult(
                    ok=False,
                    error=f"No verified contact for {job.company}. Add one with source evidence first.",
                )

        # A domain-valid address is not enough; the person must be hiring-relevant.
        if not record.confidence >= 0.6 or not record.verification or not record.verification.relevance_verified:
            return DraftResult(
                ok=False,
                error=f"Contact is not verified as hiring-relevant (status: {record.status}).",
            )
        if not contact.email:
            return DraftResult(ok=False, error="contact has no email address")

        allowed, reason = outreach_allowed(
            contact_id=contact.id,
            job_id=job.id,
            application_id=application_id,
            kind=kind,
        )
        if not allowed:
            return DraftResult(ok=False, error=reason)

        profile = load_profile()
        subject, body = compose_outreach(
            contact=contact,
            job=job,
            application=app,
            profile=profile,
            kind=kind,
        )
        dedupe_key = f"outreach:{contact.id}:{job.id}:{kind}:{followup_id or '-'}"
        existing = session.query(OutreachDraft).filter(
            OutreachDraft.dedupe_key == dedupe_key
        ).first()
        if existing is not None:
            approval = None
            if existing.approval_request_id:
                from services.approval import get_request
                approval = get_request(existing.approval_request_id)
            return DraftResult(
                ok=True,
                draft=draft_to_dict(existing, contact, job),
                approval=approval,
                created=False,
            )

        evidence = {
            "contact": record.as_dict(),
            "job": {
                "company": job.company,
                "title": job.title,
                "url": job.url,
                "source": job.source,
            },
            "matched_skills": _matched_skills(job, profile),
            "why": [
                "References the actual vacancy and role URL",
                "Only sent to a hiring-relevant, domain-verified contact",
                "One message per contact/job/type, 72-hour cooldown",
            ],
        }
        draft = OutreachDraft(
            contact_id=contact.id,
            application_id=application_id,
            job_id=job.id,
            followup_id=followup_id,
            kind=kind,
            subject=subject,
            body=body,
            status="pending_approval",
            dedupe_key=dedupe_key,
            evidence_json=json.dumps(evidence, default=str),
        )
        session.add(draft)
        session.flush()

        approval_kind = KIND_FOLLOWUP if kind.endswith("followup") else KIND_OUTREACH
        req, created = create_request(
            kind=approval_kind,
            dedupe_key=f"outreach-approval:{draft.id}",
            title=f"Email {contact.name} — {job.company}",
            subtitle=f"{kind.replace('_', ' ').title()} · {job.title}",
            payload={
                "to": contact.email,
                "contact_name": contact.name,
                "contact_role": contact.role,
                "company": job.company,
                "role": job.title,
                "subject": subject,
                "body": body,
                "application_id": application_id,
                "job_url": job.url,
            },
            evidence=evidence,
            application_id=application_id,
            job_id=job.id,
            contact_id=contact.id,
            outreach_draft_id=draft.id,
            opportunity_score=None,
            trust_score=None,
            confidence=record.confidence,
            risk_level="normal",
            expected_value=None,
            effort_minutes=4,
            expires_at=datetime.utcnow() + timedelta(days=OUTREACH_APPROVAL_TTL_DAYS),
            session=session,
        )
        draft.approval_request_id = req.id
        session.commit()

        return DraftResult(
            ok=True,
            draft=draft_to_dict(draft, contact, job),
            approval=request_to_dict(req),
            created=created,
        )
    except Exception as exc:
        session.rollback()
        return DraftResult(ok=False, error=str(exc))
    finally:
        session.close()


def update_draft(draft_id: str, *, subject: str | None = None,
                 body: str | None = None) -> dict[str, Any]:
    """Edit a draft. Any edit invalidates a prior approval and re-queues it."""
    session = get_session()
    try:
        draft = session.query(OutreachDraft).filter(OutreachDraft.id == draft_id).first()
        if draft is None:
            return {"ok": False, "error": "draft not found"}
        if draft.status in ("sent", "cancelled", "rejected"):
            return {"ok": False, "error": f"draft is {draft.status}"}

        if subject is not None:
            draft.subject = subject.strip()[:110]
        if body is not None:
            draft.body = body.strip()

        from database.models import ApprovalRequest
        req = session.query(ApprovalRequest).filter(
            ApprovalRequest.id == draft.approval_request_id
        ).first()
        if req is not None:
            payload = json.loads(req.payload_json or "{}")
            payload["subject"] = draft.subject
            payload["body"] = draft.body
            req.payload_json = json.dumps(payload, default=str, ensure_ascii=False)
            # Edited content must be approved again.
            req.status = "pending"
            req.decided_at = None
            draft.status = "pending_approval"

        session.commit()

        contact = session.get(Contact, draft.contact_id)
        job = session.get(Job, draft.job_id) if draft.job_id else None
        return {"ok": True, "draft": draft_to_dict(draft, contact, job),
                "reapproval_required": req is not None}
    except Exception as exc:
        session.rollback()
        return {"ok": False, "error": str(exc)}
    finally:
        session.close()


def get_draft(draft_id: str) -> dict[str, Any] | None:
    session = get_session()
    try:
        draft = session.query(OutreachDraft).filter(OutreachDraft.id == draft_id).first()
        if draft is None:
            return None
        contact = session.get(Contact, draft.contact_id)
        job = session.get(Job, draft.job_id) if draft.job_id else None
        return draft_to_dict(draft, contact, job)
    finally:
        session.close()


def list_drafts(status: str = "", limit: int = 50) -> list[dict[str, Any]]:
    session = get_session()
    try:
        query = session.query(OutreachDraft)
        if status:
            query = query.filter(OutreachDraft.status == status)
        rows = query.order_by(OutreachDraft.created_at.desc()).limit(limit).all()
        out = []
        for draft in rows:
            contact = session.get(Contact, draft.contact_id)
            job = session.get(Job, draft.job_id) if draft.job_id else None
            out.append(draft_to_dict(draft, contact, job))
        return out
    finally:
        session.close()


def send_draft(draft_id: str, sender=None) -> dict[str, Any]:
    """Send an approved draft exactly once. Refuses unapproved drafts."""
    sender = sender or get_sender()
    session = get_session()
    try:
        draft = session.query(OutreachDraft).filter(OutreachDraft.id == draft_id).first()
        if draft is None:
            return {"ok": False, "error": "draft not found"}
        if draft.status == "sent":
            return {"ok": False, "error": "already sent"}
        if draft.status not in ("approved", "outbox"):
            return {"ok": False, "error": f"draft is {draft.status}; approval required"}

        from database.models import ApprovalRequest
        req = session.query(ApprovalRequest).filter(
            ApprovalRequest.id == draft.approval_request_id
        ).first()
        if req is None or req.status != APPROVED:
            return {"ok": False, "error": "approval request is not approved"}

        contact = session.get(Contact, draft.contact_id)
        if contact is None:
            return {"ok": False, "error": "contact not found"}

        from services.user_profile import load_profile
        profile = load_profile()
        result: SendResult = sender.send(
            to=contact.email,
            subject=draft.subject,
            body=draft.body,
            from_name=profile.get("name", ""),
            from_email=profile.get("email", ""),
        )

        if not result.ok:
            draft.status = "failed"
            draft.send_error = result.error
            session.commit()
            mark_failed(draft.approval_request_id, result.error)
            return {"ok": False, "error": result.error, "transport": result.transport}

        if result.dry_run:
            draft.status = "outbox"
            draft.send_error = f"Saved to outbox: {result.path}"
            session.commit()
            return {
                "ok": True,
                "dry_run": True,
                "transport": result.transport,
                "path": result.path,
                "message": "Saved locally, not sent. Configure SMTP to deliver.",
            }

        draft.status = "sent"
        draft.sent_at = datetime.utcnow()
        draft.send_error = ""
        contact.last_contacted = draft.sent_at
        session.commit()

        mark_executed(draft.approval_request_id, "Email sent")
        _record_sent_interaction(draft, contact)
        if draft.followup_id:
            from services.followups import mark_followup_sent
            mark_followup_sent(draft.followup_id)
        elif draft.application_id:
            from services.followups import ensure_followups
            ensure_followups(draft.application_id)

        return {
            "ok": True,
            "dry_run": False,
            "transport": result.transport,
            "message_id": result.message_id,
        }
    except Exception as exc:
        session.rollback()
        return {"ok": False, "error": str(exc)}
    finally:
        session.close()


def _record_sent_interaction(draft: OutreachDraft, contact: Contact):
    try:
        from tracker.reach import log_interaction
        log_interaction(
            contact_id=contact.id,
            application_id=draft.application_id,
            subject=draft.subject,
            body=draft.body,
            direction="outbound",
            outcome="sent",
        )
    except Exception:
        # The send already happened; logging must not turn success into failure.
        pass
