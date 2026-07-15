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


class Company(Base):
    """A company in the registry — known ATS, offices, tech stack, hiring patterns."""
    __tablename__ = "companies"

    id = Column(String, primary_key=True)  # short slug, e.g. "browserstack"
    name = Column(String, nullable=False)
    primary_category = Column(String, nullable=False, index=True)
    secondary_categories = Column(JSON, default=list)
    stage = Column(String, default="")  # Startup | Growth | Enterprise
    offices = Column(JSON, default=list)
    hiring_regions = Column(JSON, default=list)
    ats = Column(JSON, default=list)
    careers_url = Column(String, default="")
    job_listing_url = Column(String, default="")
    fresher_friendly = Column(Boolean, default=False)
    internship = Column(Boolean, default=False)
    remote_policy = Column(String, default="")  # Remote | Hybrid | On-site
    backend_stack = Column(JSON, default=list)
    hiring_patterns = Column(JSON, default=dict)
    interview_difficulty = Column(Integer, nullable=True)
    interview_oa = Column(Boolean, default=False)
    interview_system_design = Column(Boolean, default=False)
    salary_fresher_min = Column(Float, nullable=True)
    salary_fresher_max = Column(Float, nullable=True)
    salary_intern_min = Column(Float, nullable=True)
    salary_intern_max = Column(Float, nullable=True)
    priority = Column(String, default="medium")  # High | Medium | Low
    confidence = Column(Float, default=0.5)
    tags = Column(JSON, default=list)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    aliases = relationship("CompanyAlias", back_populates="company", cascade="all, delete-orphan")


class CompanyAlias(Base):
    """Alternative names for a company, used for matching job postings."""
    __tablename__ = "company_aliases"

    id = Column(String, primary_key=True, default=gen_uuid)
    company_id = Column(String, ForeignKey("companies.id"), nullable=False, index=True)
    alias = Column(String, nullable=False, index=True)

    company = relationship("Company", back_populates="aliases")
