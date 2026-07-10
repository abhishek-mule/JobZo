from datetime import date, datetime
from sqlalchemy import (
    Column, String, Text, Integer, Boolean, DateTime, Date,
    ForeignKey, JSON,
)
from sqlalchemy.orm import DeclarativeBase, relationship


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
    created_at = Column(DateTime, default=datetime.utcnow)

    application = relationship("Application", back_populates="job", uselist=False)


class Application(Base):
    __tablename__ = "applications"

    id = Column(String, primary_key=True, default=gen_uuid)
    job_id = Column(String, ForeignKey("jobs.id"), unique=True, nullable=False)
    status = Column(String, default="drafted", index=True)
    resume_used = Column(String, default="")
    cover_letter = Column(Text, default="")
    score = Column(Integer, default=0)
    strategy = Column(String, default="")
    applied_at = Column(DateTime, nullable=True)
    interview_date = Column(DateTime, nullable=True)
    response_date = Column(DateTime, nullable=True)
    first_response_at = Column(DateTime, nullable=True)
    last_activity_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    job = relationship("Job", back_populates="application")


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
