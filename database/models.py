from datetime import date, datetime, timezone
from sqlalchemy import (
    Column, String, Text, Integer, Float, Boolean, DateTime, Date,
    ForeignKey, JSON,
)
from sqlalchemy.orm import DeclarativeBase, relationship, foreign


def gen_uuid():
    import uuid
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=gen_uuid)
    company = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    location = Column(String, default="")
    salary = Column(String, default="")
    experience_required = Column(String, default="")
    skills = Column(JSON, default=list)
    url = Column(String, unique=True, nullable=False)
    source = Column(String, nullable=False, index=True)
    raw_html = Column(Text, default="")
    posted_at = Column(DateTime, nullable=True, index=True)
    remote = Column(Boolean, default=False)
    dedup_key = Column(String, index=True)
    is_active = Column(Boolean, default=True)
    eligible = Column(Boolean, default=True)
    eligibility_reason = Column(String, default="")
    eligibility_checked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    application = relationship("Application", back_populates="job", uselist=False)


class Application(Base):
    __tablename__ = "applications"

    id = Column(String, primary_key=True, default=gen_uuid)
    job_id = Column(String, ForeignKey("jobs.id"), unique=True, nullable=False)
    status = Column(String, default="drafted", index=True)
    application_channel = Column(String, default="")
    resume_used = Column(String, default="")
    cover_letter = Column(Text, default="")
    score = Column(Integer, default=0)
    strategy = Column(String, default="")
    tier = Column(String, default="", index=True)
    applied_at = Column(DateTime, nullable=True)
    interview_date = Column(DateTime, nullable=True)
    response_date = Column(DateTime, nullable=True)
    first_response_at = Column(DateTime, nullable=True)
    last_activity_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    # Application verification fields
    application_id = Column(String, default="")
    portal_url = Column(String, default="")
    ats_confirmed = Column(Boolean, default=False)
    ats_confirmed_at = Column(DateTime, nullable=True)
    ats_keyword_match = Column(Integer, nullable=True)
    expected_interview_probability = Column(Integer, nullable=True)

    # Active decision snapshot (no DB-level FK — handled via primaryjoin)
    current_decision_id = Column(String, nullable=True, index=True)

    job = relationship("Job", back_populates="application")
    interactions = relationship("Interaction", back_populates="application")
    current_decision = relationship(
        "DecisionSnapshot",
        primaryjoin="foreign(Application.current_decision_id) == DecisionSnapshot.id",
        uselist=False,
    )
    decisions = relationship("DecisionSnapshot", foreign_keys="DecisionSnapshot.application_id", back_populates="application", order_by="DecisionSnapshot.generated_at.desc()")


class DecisionSnapshot(Base):
    """Immutable record of a complete retriever + ranker decision.

    One application can have many snapshots over time (as the engine improves).
    The active one is pointed to by Application.current_decision_id.
    Snapshots are never modified — only created.
    """
    __tablename__ = "decision_snapshots"

    id = Column(String, primary_key=True, default=gen_uuid)
    application_id = Column(String, ForeignKey("applications.id"), nullable=False, index=True)

    # ── Structured columns ──────────────────────────────────────────
    composite_score = Column(Integer, nullable=False)
    tier = Column(String, nullable=False)
    interview_probability = Column(Integer, nullable=False)
    confidence = Column(String, nullable=False)
    risk = Column(String, nullable=False)
    effort_minutes = Column(Integer, nullable=False)

    canonical_role = Column(String, default="")
    role_confidence = Column(Float, default=0.0)
    seniority = Column(String, default="")

    # ── Versions ────────────────────────────────────────────────────
    retriever_version = Column(String, default="1")
    ranker_version = Column(String, default="1")
    registry_version = Column(String, default="1")
    skill_graph_version = Column(String, default="1")

    # ── Provenance ──────────────────────────────────────────────────
    generated_at = Column(DateTime, default=datetime.utcnow, index=True)

    # ── JSON payload (full detail, never queried directly) ───────────
    details_json = Column(Text, default="{}")

    # ── Relationships ────────────────────────────────────────────────
    application = relationship("Application", foreign_keys=[application_id], back_populates="decisions")


class Resume(Base):
    __tablename__ = "resumes"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, unique=True, nullable=False)
    file_path = Column(String, nullable=False)
    skills = Column(JSON, default=list)
    version = Column(Integer, default=1)
    ats_score = Column(Integer, nullable=True)
    ats_analysis = Column(Text, default="")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String, primary_key=True, default=gen_uuid)
    application_id = Column(String, ForeignKey("applications.id"), nullable=True)
    type = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    due_date = Column(Date, nullable=True, index=True)
    done = Column(Boolean, default=False)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class PageCache(Base):
    __tablename__ = "page_cache"

    url = Column(String, primary_key=True)
    etag = Column(String, default="")
    last_modified = Column(String, default="")
    html_hash = Column(String, default="")
    status = Column(Integer, default=200)
    fetched_at = Column(DateTime, default=datetime.utcnow)
    parser = Column(String, default="")
    jobs_found = Column(Integer, default=0)
    html = Column(Text, default="")


class ProviderHealth(Base):
    __tablename__ = "provider_health"

    id = Column(String, primary_key=True, default=gen_uuid)
    provider = Column(String, nullable=False, index=True)
    company = Column(String, default="")
    last_success = Column(DateTime, nullable=True)
    last_error = Column(DateTime, nullable=True)
    error_message = Column(Text, default="")
    jobs_found = Column(Integer, default=0)
    avg_response_time = Column(Integer, default=0)
    failure_count = Column(Integer, default=0)
    consecutive_failures = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(String, primary_key=True, default=gen_uuid)
    company = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    role = Column(String, default="")
    email = Column(String, default="")
    linkedin = Column(String, default="")
    priority = Column(String, default="medium")
    relationship_score = Column(Integer, default=0)
    source = Column(String, default="")
    last_contacted = Column(DateTime, nullable=True)
    next_followup = Column(DateTime, nullable=True)
    reply_count = Column(Integer, default=0)
    meeting_count = Column(Integer, default=0)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    interactions = relationship("Interaction", back_populates="contact")


class Interaction(Base):
    __tablename__ = "interactions"

    id = Column(String, primary_key=True, default=gen_uuid)
    contact_id = Column(String, ForeignKey("contacts.id"), nullable=False, index=True)
    application_id = Column(String, ForeignKey("applications.id"), nullable=True, index=True)
    type = Column(String, nullable=False)  # email, linkedin, call, meeting, referral, followup, note
    direction = Column(String, default="outbound")  # outbound / inbound
    subject = Column(String, default="")
    body = Column(Text, default="")
    outcome = Column(String, default="")  # sent, replied, ignored, meeting_scheduled
    occurred_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    contact = relationship("Contact", back_populates="interactions")
    application = relationship("Application", back_populates="interactions")


class ApplicationOutcome(Base):
    __tablename__ = "application_outcomes"

    id = Column(String, primary_key=True, default=gen_uuid)
    application_id = Column(String, ForeignKey("applications.id"), unique=True, nullable=False, index=True)
    resume_used = Column(String, default="")
    company = Column(String, default="", index=True)
    role = Column(String, default="")
    ats = Column(String, default="")
    applied_at = Column(DateTime, nullable=True)
    viewed_at = Column(DateTime, nullable=True)
    oa_at = Column(DateTime, nullable=True)
    interview_at = Column(DateTime, nullable=True)
    offer_at = Column(DateTime, nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    ghosted_at = Column(DateTime, nullable=True)
    rejection_reason = Column(String, default="")
    interview_rounds = Column(Integer, nullable=True)
    feedback = Column(Text, default="")
    salary = Column(String, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    application = relationship("Application", backref="outcome", uselist=False)


class Event(Base):
    """Immutable event log — every important action captured in sequence."""
    __tablename__ = "events"

    id = Column(String, primary_key=True, default=gen_uuid)
    event_type = Column(String, nullable=False, index=True)
    entity_type = Column(String, nullable=False, index=True)  # job, application, contact, etc.
    entity_id = Column(String, nullable=False, index=True)
    actor = Column(String, default="user")  # user or system
    metadata_json = Column(Text, default="{}")  # JSON blob with event-specific data
    occurred_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ATSProfile(Base):
    """Central ATS configuration — parser metadata, not per-company.

    Companies reference ATS profiles via Company.ats (string FK).
    Lookups happen via direct query, not ORM traversal.
    """
    __tablename__ = "ats_profiles"

    name = Column(String, primary_key=True)  # "greenhouse", "lever", "workday", ...
    label = Column(String, nullable=False)
    parser_path = Column(String, default="")
    url_pattern = Column(String, default="")
    supports_resume = Column(Boolean, default=False)
    supports_cover_letter = Column(Boolean, default=False)
    confidence = Column(Float, default=0.5)
    success_rate = Column(Float, nullable=True)  # derived from poll data
    created_at = Column(DateTime, default=datetime.utcnow)


class PollRun(Base):
    """Event record of a single company poll — never overwritten."""
    __tablename__ = "poll_runs"

    id = Column(String, primary_key=True, default=gen_uuid)
    company_id = Column(String, ForeignKey("companies.id"), nullable=False, index=True)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String, default="")  # "success" | "error" | "empty" | "redirected"
    duration_ms = Column(Integer, nullable=True)
    http_status = Column(Integer, nullable=True)
    parser = Column(String, default="")
    confidence = Column(Float, nullable=True)
    jobs_found = Column(Integer, default=0)
    jobs_new = Column(Integer, default=0)
    jobs_duplicate = Column(Integer, default=0)
    parse_errors = Column(Integer, default=0)
    error_message = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    company = relationship("Company", back_populates="poll_runs")


class CompanySnapshot(Base):
    """Periodic point-in-time view of a company's job landscape."""
    __tablename__ = "company_snapshots"

    id = Column(String, primary_key=True, default=gen_uuid)
    company_id = Column(String, ForeignKey("companies.id"), nullable=False, index=True)
    snapshot_at = Column(DateTime, nullable=False, index=True)
    jobs_open = Column(Integer, default=0)
    backend_jobs = Column(Integer, default=0)
    frontend_jobs = Column(Integer, default=0)
    graduate_jobs = Column(Integer, default=0)
    remote_jobs = Column(Integer, default=0)
    fresher_jobs = Column(Integer, default=0)
    eligible_jobs = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    company = relationship("Company", back_populates="snapshots")


class HiringChannel(Base):
    """A distinct hiring pipeline within a company.

    One company can have many channels:
      - Careers page (primary job board)
      - Campus hiring (separate portal, different ATS)
      - Internships (different URL, different cadence)
      - Apprenticeships
      - Referral program

    Each channel can have its own URL and ATS.
    """
    __tablename__ = "hiring_channels"

    id = Column(String, primary_key=True, default=gen_uuid)
    company_id = Column(String, ForeignKey("companies.id"), nullable=False, index=True)
    channel_name = Column(String, nullable=False, index=True)
    careers_url = Column(String, default="")
    job_listing_url = Column(String, default="")
    ats = Column(String, default="")
    fresher_friendly = Column(Boolean, default=False)
    graduate_roles = Column(Boolean, default=False)
    poll_interval_hours = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    company = relationship("Company", back_populates="hiring_channels")


class Company(Base):
    """Identity — what a company IS, not what it's doing right now."""
    __tablename__ = "companies"

    id = Column(String, primary_key=True)  # short slug, e.g. "browserstack"
    name = Column(String, nullable=False)
    primary_category = Column(String, nullable=False, index=True)
    ats = Column(String, default="")  # primary ATS (references ATSProfile.name)
    careers_url = Column(String, default="")
    job_listing_url = Column(String, default="")
    fresher_friendly = Column(Boolean, default=False)
    internship = Column(Boolean, default=False)
    graduate_roles = Column(Boolean, default=False)
    remote_policy = Column(String, default="")
    poll_interval_hours = Column(Integer, default=24)
    tags = Column(JSON, default=list)

    # Registry provenance
    status = Column(String, default="active")  # active | paused | broken | archived
    registry_source = Column(String, default="manual")  # manual | wellfound | yc | csv | community | college | github
    registry_quality = Column(String, default="manual")  # verified | community | auto_discovered | deprecated

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    aliases = relationship("CompanyAlias", back_populates="company", cascade="all, delete-orphan")
    hiring_channels = relationship("HiringChannel", back_populates="company", cascade="all, delete-orphan")
    poll_runs = relationship("PollRun", back_populates="company", cascade="all, delete-orphan")
    snapshots = relationship("CompanySnapshot", back_populates="company", cascade="all, delete-orphan")


class CompanyAlias(Base):
    """Alternative names for a company, used for matching job postings."""
    __tablename__ = "company_aliases"

    id = Column(String, primary_key=True, default=gen_uuid)
    company_id = Column(String, ForeignKey("companies.id"), nullable=False, index=True)
    alias = Column(String, nullable=False, index=True)

    company = relationship("Company", back_populates="aliases")


class ATSDiscovery(Base):
    """Operational ATS discovery cache — kept separate from registry YAML.

    Stores the result of resolving a company's career URL to a concrete
    ATS + board token. The registry YAML stays identity-only; this table
    holds the machine-facing endpoint knowledge.
    """
    __tablename__ = "ats_discovery"

    id = Column(String, primary_key=True, default=gen_uuid)
    company_id = Column(String, index=True)
    company_name = Column(String, nullable=False, index=True)
    careers_url = Column(String, nullable=False)
    ats = Column(String, nullable=False, default="unknown")
    board_token = Column(String, default="")
    api_url = Column(String, default="")
    method = Column(String, default="")
    confidence = Column(Float, default=0.0)
    note = Column(String, default="")
    is_active = Column(Boolean, default=True)
    discovered_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── Human-approved action pipeline ──────────────────────────────────────────
#
# The tables below add the missing half of JobZo: a durable approval queue
# and an evidence trail for every consequential action (apply, cold email,
# follow-up). Nothing here sends or submits anything by itself. A record must
# move pending → approved before execution is allowed.

class ApprovalRequest(Base):
    """A consequential action waiting for (or carrying) a human decision.

    kind:    application | outreach | followup
    status:  pending | approved | rejected | expired | executed | failed | cancelled

    payload_json holds the exact preview the user approved (recipient,
    subject, body, resume, answers). evidence_json holds why JobZo proposed
    it (score factors, trust evidence, fresher evidence, contact verification).
    """
    __tablename__ = "approval_requests"

    id = Column(String, primary_key=True, default=gen_uuid)
    kind = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="pending", index=True)
    title = Column(String, default="")
    subtitle = Column(String, default="")

    application_id = Column(String, ForeignKey("applications.id"), nullable=True, index=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=True, index=True)
    contact_id = Column(String, ForeignKey("contacts.id"), nullable=True, index=True)
    outreach_draft_id = Column(String, nullable=True, index=True)

    dedupe_key = Column(String, unique=True, nullable=False, index=True)
    opportunity_score = Column(Float, nullable=True)
    trust_score = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    risk_level = Column(String, default="")
    expected_value = Column(Float, nullable=True)
    effort_minutes = Column(Integer, nullable=True)

    payload_json = Column(Text, default="{}")
    evidence_json = Column(Text, default="{}")
    decision_note = Column(Text, default="")

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    expires_at = Column(DateTime, nullable=True)
    decided_at = Column(DateTime, nullable=True)
    executed_at = Column(DateTime, nullable=True)


class ContactEvidence(Base):
    """One public, auditable source supporting a contact record."""
    __tablename__ = "contact_evidence"

    id = Column(String, primary_key=True, default=gen_uuid)
    contact_id = Column(String, ForeignKey("contacts.id"), nullable=False, index=True)
    kind = Column(String, nullable=False, index=True)
    url = Column(String, default="")
    excerpt = Column(Text, default="")
    confidence = Column(Float, default=0.5)
    captured_at = Column(DateTime, default=datetime.utcnow)


class ContactVerification(Base):
    """Separate verification dimensions — a valid address is not a recruiter.

    identity_verified      person is demonstrably employed by the company
    domain_verified        email belongs to the company's legitimate domain
    mailbox_verified       mailbox existence was confirmed by a permitted check
    relevance_verified     person is involved in hiring for the target role
    """
    __tablename__ = "contact_verifications"

    id = Column(String, primary_key=True, default=gen_uuid)
    contact_id = Column(String, ForeignKey("contacts.id"), nullable=False, unique=True, index=True)
    company_domain = Column(String, default="")
    email_domain = Column(String, default="")

    identity_verified = Column(Boolean, default=False)
    domain_verified = Column(Boolean, default=False)
    mailbox_verified = Column(Boolean, default=False)
    relevance_verified = Column(Boolean, default=False)

    status = Column(String, default="unverified", index=True)
    confidence = Column(Float, default=0.0)
    reasons_json = Column(Text, default="[]")
    verified_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


class OutreachDraft(Base):
    """A personalized message. Drafted first, sent only after approval."""
    __tablename__ = "outreach_drafts"

    id = Column(String, primary_key=True, default=gen_uuid)
    contact_id = Column(String, ForeignKey("contacts.id"), nullable=False, index=True)
    application_id = Column(String, ForeignKey("applications.id"), nullable=True, index=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=True, index=True)
    followup_id = Column(String, nullable=True, index=True)

    kind = Column(String, default="initial", index=True)  # initial | followup | recruiter_followup | final_followup
    subject = Column(String, default="")
    body = Column(Text, default="")
    status = Column(String, default="draft", index=True)
    # draft | pending_approval | approved | outbox | sent | failed | rejected | cancelled

    approval_request_id = Column(String, nullable=True, index=True)
    dedupe_key = Column(String, unique=True, nullable=False, index=True)
    evidence_json = Column(Text, default="{}")

    created_at = Column(DateTime, default=datetime.utcnow)
    approved_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    send_error = Column(Text, default="")


class FollowUp(Base):
    """A scheduled, evidence-backed follow-up for an application."""
    __tablename__ = "followups"

    id = Column(String, primary_key=True, default=gen_uuid)
    application_id = Column(String, ForeignKey("applications.id"), nullable=False, index=True)
    contact_id = Column(String, ForeignKey("contacts.id"), nullable=True, index=True)
    outreach_draft_id = Column(String, nullable=True, index=True)

    kind = Column(String, nullable=False, index=True)  # status_check | recruiter_followup | final_followup
    due_at = Column(DateTime, nullable=False, index=True)
    status = Column(String, default="scheduled", index=True)
    # scheduled | due | drafted | sent | done | skipped | cancelled

    reason = Column(String, default="")
    dedupe_key = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
