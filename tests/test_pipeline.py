"""Tests for the human-approved action pipeline.

Covers the trust-critical boundaries:
  * opportunity score hard rejections
  * contact verification dimensions
  * approval idempotency and state transitions
  * outreach cannot send without approval
  * duplicate/cooldown protection
  * follow-up scheduling after submission
"""

import os
import uuid
from datetime import datetime, timedelta

os.environ.setdefault("JOBZO_DB_PATH", "/tmp/jobzo_pipeline_test.db")
os.environ["JOBZO_SKIP_MIGRATIONS"] = "1"

from database.connection import get_engine, get_session
from database.models import (
    ApprovalRequest,
    Application,
    Base,
    Contact,
    ContactEvidence,
    ContactVerification,
    FollowUp,
    Interaction,
    Job,
    OutreachDraft,
)
from services.approval import (
    APPROVED,
    PENDING,
    REJECTED,
    decide_request,
    enqueue_application,
    get_request,
    list_requests,
    mark_executed,
)
from services.cold_outreach import (
    compose_outreach,
    draft_outreach,
    get_draft,
    send_draft,
)
from services.contacts import (
    base_domain,
    best_contact_for,
    domain_matches,
    parse_public_emails,
    save_contact,
    verify_contact,
)
from services.emailer import OutboxEmailSender, SendResult
from services.followups import (
    add_business_days,
    cancel_followups,
    complete_followup,
    draft_followup,
    ensure_followups,
    list_followups,
)
from services.fresher import FresherResult, FRESHER_CONFIRMED, FRESHER_EXPERIENCED
from services.opportunity_score import role_family, score_opportunity
from services.trust import TIER_SUSPICIOUS, TIER_VERIFIED, TrustResult


class FakeJob:
    def __init__(self, **overrides):
        self.company = "Acme"
        self.title = "Backend Engineer"
        self.description = "Python FastAPI PostgreSQL backend systems"
        self.location = "Pune, India"
        self.salary = "12 LPA"
        self.experience_required = "0-1 years"
        self.skills = ["Python", "FastAPI", "PostgreSQL"]
        self.url = "https://boards.greenhouse.io/acme/jobs/123"
        self.source = "company_pages"
        self.posted_at = datetime.utcnow()
        self.remote = True
        self.is_active = True
        self.eligible = True
        self.eligibility_reason = ""
        for key, value in overrides.items():
            setattr(self, key, value)


def _clean_db():
    from pathlib import Path
    path = Path(os.environ["JOBZO_DB_PATH"])
    if path.exists():
        path.unlink()


def setup_module():
    _clean_db()
    engine = get_engine(os.environ["JOBZO_DB_PATH"])
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def setup_function():
    engine = get_engine(os.environ["JOBZO_DB_PATH"])
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def _seed_application(company="Acme", status="drafted", score=80,
                      title="Backend Engineer", description=None,
                      url=None, posted_at=None):
    uid = uuid.uuid4().hex[:8]
    session = get_session()
    try:
        job = Job(
            company=company,
            title=title,
            description=description or "Python FastAPI PostgreSQL backend systems. 0-1 years.",
            location="Pune, India",
            salary="12 LPA",
            experience_required="0-1 years",
            skills=["Python", "FastAPI", "PostgreSQL"],
            url=url or f"https://boards.greenhouse.io/acme/jobs/{uid}",
            source="company_pages",
            posted_at=posted_at or datetime.utcnow(),
            remote=True,
        )
        session.add(job)
        session.commit()
        app = Application(
            job_id=job.id,
            status=status,
            score=score,
            strategy="watch",
            applied_at=datetime.utcnow() if status == "submitted" else None,
        )
        session.add(app)
        session.commit()
        return str(app.id), str(job.id)
    finally:
        session.close()


def _verified_contact(company="Acme", email="jane@acme.com",
                      role="Talent Acquisition", name="Jane Doe"):
    return save_contact(
        company=company,
        name=name,
        role=role,
        email=email,
        source="official_careers_page",
        evidence_kind="official_careers_page",
        source_url="https://acme.com/careers",
        excerpt="Jane Doe — Talent Acquisition",
        company_domain="acme.com",
    )


# ── Opportunity score ───────────────────────────────────────────────────────

def test_high_quality_opportunity_scores_excellent():
    job = FakeJob()
    result = score_opportunity(
        job=job,
        match_score=90,
        trust=TrustResult(score=90, tier=TIER_VERIFIED),
        fresher=FresherResult(status=FRESHER_CONFIRMED, evidence=["entry title"]),
        profile={"desired_role_families": ["backend", "software"], "location": "India"},
        interview_probability=25,
    )
    assert result.total >= 85
    assert result.classification == "excellent"
    assert not result.hard_rejects
    assert result.roi["value_per_minute"] > 0
    assert len(result.factors) == 8
    assert abs(sum(f.weight for f in result.factors) - 100) < 0.01


def test_suspicious_job_is_hard_rejected():
    result = score_opportunity(
        job=FakeJob(),
        match_score=95,
        trust=TrustResult(score=10, tier=TIER_SUSPICIOUS, risks=[{"keyword": "registration fee"}]),
        fresher=FresherResult(status=FRESHER_CONFIRMED),
        profile={"desired_role_families": ["backend"]},
    )
    assert result.rejected
    assert result.total == 0
    assert any("suspicious" in reason.lower() for reason in result.hard_rejects)


def test_experienced_role_is_hard_rejected():
    result = score_opportunity(
        job=FakeJob(title="Senior Backend Engineer"),
        match_score=90,
        trust=TrustResult(score=85, tier=TIER_VERIFIED),
        fresher=FresherResult(status=FRESHER_EXPERIENCED, negatives=["2+ years"]),
        profile={"desired_role_families": ["backend"]},
    )
    assert result.rejected
    assert any("experienced" in reason.lower() for reason in result.hard_rejects)


def test_non_engineering_role_is_rejected_for_engineering_profile():
    result = score_opportunity(
        job=FakeJob(title="Sales Executive"),
        match_score=80,
        trust=TrustResult(score=80, tier=TIER_VERIFIED),
        fresher=FresherResult(status=FRESHER_CONFIRMED),
        profile={"desired_role_families": ["backend", "software"]},
    )
    assert result.rejected
    assert role_family("Sales Executive") == "sales"


def test_adjacent_engineering_family_is_not_rejected():
    result = score_opportunity(
        job=FakeJob(title="Full Stack Developer"),
        match_score=80,
        trust=TrustResult(score=80, tier=TIER_VERIFIED),
        fresher=FresherResult(status=FRESHER_CONFIRMED),
        profile={"desired_role_families": ["backend"]},
    )
    assert not result.rejected
    assert result.total > 0


# ── Contacts ────────────────────────────────────────────────────────────────

def test_verified_contact_has_separate_dimensions():
    record = _verified_contact()
    assert record.status == "verified"
    assert record.verification.identity_verified is True
    assert record.verification.domain_verified is True
    assert record.verification.relevance_verified is True
    assert record.verification.mailbox_verified is False
    assert record.confidence >= 0.85
    assert record.is_verified


def test_free_mail_contact_is_risky_not_verified():
    record = save_contact(
        company="Acme", name="Jane", role="Recruiter",
        email="jane@gmail.com", source="user_confirmed",
        evidence_kind="user_confirmed", source_url="https://linkedin.com/in/jane",
        company_domain="acme.com",
    )
    assert record.status == "risky"
    assert record.is_verified is False
    assert record.confidence <= 0.45


def test_contact_without_evidence_is_unverified():
    record = save_contact(company="Acme", name="Jane", role="Recruiter",
                          email="jane@acme.com", evidence_kind="manual_note")
    assert record.status == "unverified"
    assert record.confidence < 0.6


def test_duplicate_contact_is_not_created_twice():
    first = _verified_contact()
    second = _verified_contact()
    assert first.contact.id == second.contact.id
    session = get_session()
    try:
        assert session.query(Contact).count() == 1
    finally:
        session.close()


def test_best_contact_requires_verification():
    save_contact(company="Acme", name="Unverified", role="Recruiter",
                 email="person@acme.com", evidence_kind="manual_note")
    assert best_contact_for("Acme") is None
    _verified_contact()
    best = best_contact_for("Acme")
    assert best is not None
    assert best.contact.name == "Jane Doe"


def test_multi_part_public_suffix_is_not_collapsed():
    assert base_domain("https://careers.acme.co.in/jobs") == "acme.co.in"
    assert domain_matches("jane@acme.co.in", "acme.co.in") is True
    assert domain_matches("jane@other.co.in", "acme.co.in") is False


def test_parse_public_emails_extracts_mailto_and_ignores_assets():
    html = """
    <a href="mailto:talent@acme.com">Email talent</a>
    <img src="logo@2x.png">
    <p>Contact careers@acme.com for roles.</p>
    """
    emails = {e.email for e in parse_public_emails(html)}
    assert "talent@acme.com" in emails
    assert "careers@acme.com" in emails
    assert "logo@2x.png" not in emails


# ── Approval queue ──────────────────────────────────────────────────────────

def test_enqueue_application_creates_pending_request_with_evidence():
    app_id, job_id = _seed_application()
    result = enqueue_application(app_id)
    assert result["ok"] and result["queued"] and result["created"]
    req = result["request"]
    assert req["status"] == PENDING
    assert req["application_id"] == app_id
    assert req["opportunity_score"] > 0
    assert req["payload"]["resume"]["name"] == "Abhishek_Mule_Resume"
    assert req["payload"]["resume"]["file"] == "resumes/Abhishek_Mule_Resume.pdf"
    assert req["evidence"]["score"]["factors"]
    assert req["evidence"]["trust"]["tier"] in ("verified", "trusted", "unverified")
    assert req["expires_at"] is not None


def test_enqueue_is_idempotent():
    app_id, _ = _seed_application()
    first = enqueue_application(app_id)
    second = enqueue_application(app_id)
    assert first["created"] is True
    assert second["created"] is False
    assert first["request"]["id"] == second["request"]["id"]


def test_approve_application_moves_it_to_ready():
    app_id, _ = _seed_application()
    req = enqueue_application(app_id)["request"]
    result = decide_request(req["id"], APPROVED)
    assert result["ok"]
    session = get_session()
    try:
        assert session.get(Application, app_id).status == "ready"
        assert session.get(ApprovalRequest, req["id"]).status == APPROVED
    finally:
        session.close()


def test_reject_application_skips_it():
    app_id, _ = _seed_application()
    req = enqueue_application(app_id)["request"]
    result = decide_request(req["id"], REJECTED, "not a fit")
    assert result["ok"]
    session = get_session()
    try:
        assert session.get(Application, app_id).status == "skipped"
    finally:
        session.close()


def test_suspicious_application_is_auto_rejected():
    app_id, _ = _seed_application(
        description="Great job! Pay a registration fee of Rs 500 to apply. No interview.",
    )
    result = enqueue_application(app_id)
    assert result["ok"]
    assert result["queued"] is False
    assert result["request"]["status"] == REJECTED
    assert result["hard_rejects"]


def test_executed_state_is_recorded():
    app_id, _ = _seed_application()
    req = enqueue_application(app_id)["request"]
    decide_request(req["id"], APPROVED)
    assert mark_executed(req["id"], "confirmed in portal")
    stored = get_request(req["id"])
    assert stored["status"] == "executed"
    assert stored["executed_at"] is not None


# ── Outreach ────────────────────────────────────────────────────────────────

def test_compose_outreach_references_real_vacancy():
    _verified_contact()
    record = best_contact_for("Acme")
    job = FakeJob()
    app = Application(job_id="j", status="submitted", applied_at=datetime.utcnow())
    subject, body = compose_outreach(
        contact=record.contact, job=job, application=app,
        profile={"name": "Abhishek Mule", "headline": "Backend Engineer",
                 "declared_skills": ["Python", "FastAPI"], "linkedin": "https://linkedin.com/in/x"},
        kind="initial",
    )
    assert "Backend Engineer" in subject
    assert "Acme" in subject
    assert job.url in body
    assert "Python" in body


def test_compose_outreach_ignores_junk_skills_and_normalizes_display():
    _verified_contact()
    record = best_contact_for("Acme")
    job = FakeJob(description="Python FastAPI PostgreSQL backend systems")
    subject, body = compose_outreach(
        contact=record.contact, job=job, application=None,
        profile={"name": "Abhishek Mule", "headline": "Backend Engineer",
                 "declared_skills": ["g", "postgreSQL", "python"]},
        kind="initial",
    )
    assert "PostgreSQL" in body
    assert "Python" in body
    assert "in g," not in body
    assert "experience in g" not in body


def test_outreach_draft_lands_in_approval_queue():
    app_id, _ = _seed_application(status="submitted")
    _verified_contact()
    result = draft_outreach(application_id=app_id)
    assert result.ok, result.error
    assert result.draft["status"] == "pending_approval"
    assert result.approval["kind"] == "outreach"
    assert result.approval["payload"]["to"] == "jane@acme.com"
    assert result.approval["payload"]["subject"]


def test_no_outreach_without_verified_contact():
    app_id, _ = _seed_application(status="submitted")
    result = draft_outreach(application_id=app_id)
    assert result.ok is False
    assert "verified contact" in result.error.lower()


def test_send_requires_approval():
    app_id, _ = _seed_application(status="submitted")
    _verified_contact()
    draft = draft_outreach(application_id=app_id).draft
    result = send_draft(draft["id"])
    assert result["ok"] is False
    assert "approval" in result["error"].lower()


def test_approved_send_is_recorded_once():
    app_id, _ = _seed_application(status="submitted")
    _verified_contact()
    result = draft_outreach(application_id=app_id)
    decide_request(result.approval["id"], APPROVED)

    class FakeSender:
        name = "fake"
        def send(self, **kwargs):
            return SendResult(ok=True, transport="fake", message_id="fake:1")

    sent = send_draft(result.draft["id"], sender=FakeSender())
    assert sent["ok"], sent
    assert get_draft(result.draft["id"])["status"] == "sent"

    second = send_draft(result.draft["id"], sender=FakeSender())
    assert second["ok"] is False
    assert "already sent" in second["error"].lower()

    session = get_session()
    try:
        assert session.query(Interaction).count() == 1
        assert session.query(FollowUp).count() >= 1
    finally:
        session.close()


def test_outbox_send_does_not_claim_delivery(tmp_path):
    app_id, _ = _seed_application(status="submitted")
    _verified_contact()
    result = draft_outreach(application_id=app_id)
    decide_request(result.approval["id"], APPROVED)

    sent = send_draft(result.draft["id"], sender=OutboxEmailSender(tmp_path))
    assert sent["ok"] and sent["dry_run"]
    assert get_draft(result.draft["id"])["status"] == "outbox"
    session = get_session()
    try:
        assert session.query(Interaction).count() == 0
    finally:
        session.close()


def test_duplicate_draft_is_not_created():
    app_id, _ = _seed_application(status="submitted")
    _verified_contact()
    first = draft_outreach(application_id=app_id)
    second = draft_outreach(application_id=app_id)
    assert first.ok and second.ok
    assert first.created is True
    assert second.created is False
    assert first.draft["id"] == second.draft["id"]


# ── Follow-ups ──────────────────────────────────────────────────────────────

def test_add_business_days_skips_weekend():
    friday = datetime(2026, 10, 30, 10, 0)  # Friday
    assert add_business_days(friday, 1) == datetime(2026, 11, 2, 10, 0)  # Monday
    assert add_business_days(friday, 3) == datetime(2026, 11, 4, 10, 0)  # Wednesday


def test_followups_scheduled_for_submitted_application():
    app_id, _ = _seed_application(status="submitted")
    _verified_contact()
    rows = ensure_followups(app_id)
    assert {r["kind"] for r in rows} == {"status_check", "recruiter_followup", "final_followup"}
    assert len({r["id"] for r in rows}) == 3


def test_followups_backfill_for_existing_submitted_application():
    app_id, _ = _seed_application(status="submitted")
    _verified_contact()
    rows = list_followups(application_id=app_id)
    assert {r["kind"] for r in rows} == {"status_check", "recruiter_followup", "final_followup"}
    # A second read must not duplicate the plan.
    again = list_followups(application_id=app_id)
    assert len(again) == 3


def test_followups_without_contact_only_status_check():
    app_id, _ = _seed_application(status="submitted")
    rows = ensure_followups(app_id)
    assert [r["kind"] for r in rows] == ["status_check"]


def test_followups_are_idempotent():
    app_id, _ = _seed_application(status="submitted")
    _verified_contact()
    first = ensure_followups(app_id)
    second = ensure_followups(app_id)
    assert len(first) == len(second) == 3
    session = get_session()
    try:
        assert session.query(FollowUp).count() == 3
    finally:
        session.close()


def test_due_followups_and_status_check_manual_flow():
    app_id, _ = _seed_application(
        status="submitted",
        posted_at=datetime.utcnow() - timedelta(days=20),
    )
    _verified_contact()
    rows = ensure_followups(app_id)
    status_check = next(r for r in rows if r["kind"] == "status_check")
    result = draft_followup(status_check["id"])
    assert result["ok"] and result["needs_manual"]

    recruiter = next(r for r in rows if r["kind"] == "recruiter_followup")
    result = draft_followup(recruiter["id"])
    assert result["ok"]
    assert result["draft"]["kind"] == "recruiter_followup"
    assert result["draft"]["subject"].startswith("Re: Application:")
    assert result["approval"]["kind"] == "followup"


def test_rejection_cancels_followups():
    app_id, _ = _seed_application(status="submitted")
    _verified_contact()
    ensure_followups(app_id)
    assert cancel_followups(app_id) == 3
    session = get_session()
    try:
        assert session.query(FollowUp).filter(FollowUp.status == "cancelled").count() == 3
    finally:
        session.close()


def test_complete_followup_marks_status():
    app_id, _ = _seed_application(status="submitted")
    _verified_contact()
    rows = ensure_followups(app_id)
    assert complete_followup(rows[0]["id"], "done")
    session = get_session()
    try:
        assert session.get(FollowUp, rows[0]["id"]).status == "done"
    finally:
        session.close()


# ── Briefing ────────────────────────────────────────────────────────────────

def test_brief_does_not_count_manual_rejections_as_auto_filtered():
    from services.brief import build_brief
    app_id, _ = _seed_application()
    req = enqueue_application(app_id)["request"]
    decide_request(req["id"], REJECTED, "not interested")
    data = build_brief()
    assert data["auto_rejected"]["count"] == 0


def test_brief_reports_pending_decisions():
    from services.brief import build_brief
    app_id, _ = _seed_application()
    req = enqueue_application(app_id)["request"]
    data = build_brief()
    assert data["counts"]["pending_approvals"] >= 1
    assert data["counts"]["new_jobs"] >= 1
    assert any(item.get("request_id") == req["id"] for item in data["best_opportunities"])
    assert data["recommendation"]["action"] == "approve"
