"""add human approval, contact verification, outreach and follow-up tables

Revision ID: 9f4e2a1b7c3d
Revises: 60d98ca7f5b2
Create Date: 2026-11-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9f4e2a1b7c3d"
down_revision: Union[str, None] = "60d98ca7f5b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # get_engine() runs Base.metadata.create_all() before Alembic. On a fresh
    # or self-healed database these tables therefore already exist; creating
    # them again would abort the whole migration chain on the first statement.
    from sqlalchemy import inspect

    existing = set(inspect(op.get_bind()).get_table_names())
    if {
        "approval_requests",
        "contact_evidence",
        "contact_verifications",
        "outreach_drafts",
        "followups",
    } <= existing:
        return

    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("subtitle", sa.String(), nullable=True),
        sa.Column("application_id", sa.String(), nullable=True),
        sa.Column("job_id", sa.String(), nullable=True),
        sa.Column("contact_id", sa.String(), nullable=True),
        sa.Column("outreach_draft_id", sa.String(), nullable=True),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("opportunity_score", sa.Float(), nullable=True),
        sa.Column("trust_score", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("risk_level", sa.String(), nullable=True),
        sa.Column("expected_value", sa.Float(), nullable=True),
        sa.Column("effort_minutes", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("evidence_json", sa.Text(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index(op.f("ix_approval_requests_kind"), "approval_requests", ["kind"])
    op.create_index(op.f("ix_approval_requests_status"), "approval_requests", ["status"])
    op.create_index(op.f("ix_approval_requests_application_id"), "approval_requests", ["application_id"])
    op.create_index(op.f("ix_approval_requests_job_id"), "approval_requests", ["job_id"])
    op.create_index(op.f("ix_approval_requests_contact_id"), "approval_requests", ["contact_id"])
    op.create_index(op.f("ix_approval_requests_outreach_draft_id"), "approval_requests", ["outreach_draft_id"])
    op.create_index(op.f("ix_approval_requests_dedupe_key"), "approval_requests", ["dedupe_key"], unique=True)
    op.create_index(op.f("ix_approval_requests_created_at"), "approval_requests", ["created_at"])

    op.create_table(
        "contact_evidence",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("contact_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("captured_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_contact_evidence_contact_id"), "contact_evidence", ["contact_id"])
    op.create_index(op.f("ix_contact_evidence_kind"), "contact_evidence", ["kind"])

    op.create_table(
        "contact_verifications",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("contact_id", sa.String(), nullable=False),
        sa.Column("company_domain", sa.String(), nullable=True),
        sa.Column("email_domain", sa.String(), nullable=True),
        sa.Column("identity_verified", sa.Boolean(), nullable=True),
        sa.Column("domain_verified", sa.Boolean(), nullable=True),
        sa.Column("mailbox_verified", sa.Boolean(), nullable=True),
        sa.Column("relevance_verified", sa.Boolean(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("reasons_json", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("contact_id"),
    )
    op.create_index(op.f("ix_contact_verifications_contact_id"), "contact_verifications", ["contact_id"], unique=True)
    op.create_index(op.f("ix_contact_verifications_status"), "contact_verifications", ["status"])

    op.create_table(
        "outreach_drafts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("contact_id", sa.String(), nullable=False),
        sa.Column("application_id", sa.String(), nullable=True),
        sa.Column("job_id", sa.String(), nullable=True),
        sa.Column("followup_id", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=True),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("approval_request_id", sa.String(), nullable=True),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("send_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index(op.f("ix_outreach_drafts_contact_id"), "outreach_drafts", ["contact_id"])
    op.create_index(op.f("ix_outreach_drafts_application_id"), "outreach_drafts", ["application_id"])
    op.create_index(op.f("ix_outreach_drafts_job_id"), "outreach_drafts", ["job_id"])
    op.create_index(op.f("ix_outreach_drafts_followup_id"), "outreach_drafts", ["followup_id"])
    op.create_index(op.f("ix_outreach_drafts_kind"), "outreach_drafts", ["kind"])
    op.create_index(op.f("ix_outreach_drafts_status"), "outreach_drafts", ["status"])
    op.create_index(op.f("ix_outreach_drafts_approval_request_id"), "outreach_drafts", ["approval_request_id"])
    op.create_index(op.f("ix_outreach_drafts_dedupe_key"), "outreach_drafts", ["dedupe_key"], unique=True)

    op.create_table(
        "followups",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("application_id", sa.String(), nullable=False),
        sa.Column("contact_id", sa.String(), nullable=True),
        sa.Column("outreach_draft_id", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index(op.f("ix_followups_application_id"), "followups", ["application_id"])
    op.create_index(op.f("ix_followups_contact_id"), "followups", ["contact_id"])
    op.create_index(op.f("ix_followups_outreach_draft_id"), "followups", ["outreach_draft_id"])
    op.create_index(op.f("ix_followups_kind"), "followups", ["kind"])
    op.create_index(op.f("ix_followups_due_at"), "followups", ["due_at"])
    op.create_index(op.f("ix_followups_status"), "followups", ["status"])
    op.create_index(op.f("ix_followups_dedupe_key"), "followups", ["dedupe_key"], unique=True)


def downgrade() -> None:
    op.drop_table("followups")
    op.drop_table("outreach_drafts")
    op.drop_table("contact_verifications")
    op.drop_table("contact_evidence")
    op.drop_table("approval_requests")
