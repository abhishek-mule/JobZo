"""add verification and outcome fields to applications

Revision ID: 2a8b1c4d5e6f
Revises: 30b159446a7f
Create Date: 2026-07-12 02:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2a8b1c4d5e6f"
down_revision: Union[str, None] = "30b159446a7f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("applications") as batch_op:
        batch_op.add_column(sa.Column("application_id", sa.String(), nullable=True, server_default=""))
        batch_op.add_column(sa.Column("portal_url", sa.String(), nullable=True, server_default=""))
        batch_op.add_column(sa.Column("ats_confirmed", sa.Boolean(), nullable=True, server_default=sa.text("0")))
        batch_op.add_column(sa.Column("ats_confirmed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("ats_keyword_match", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("expected_interview_probability", sa.Integer(), nullable=True))

    op.create_table(
        "contacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("company", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("linkedin", sa.String(), nullable=True),
        sa.Column("priority", sa.String(), nullable=True),
        sa.Column("relationship_score", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("last_contacted", sa.DateTime(), nullable=True),
        sa.Column("next_followup", sa.DateTime(), nullable=True),
        sa.Column("reply_count", sa.Integer(), nullable=True),
        sa.Column("meeting_count", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_contacts_company"), "contacts", ["company"], unique=False)

    op.create_table(
        "interactions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("contact_id", sa.String(), nullable=False),
        sa.Column("application_id", sa.String(), nullable=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("direction", sa.String(), nullable=True),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_interactions_contact_id"), "interactions", ["contact_id"], unique=False)
    op.create_index(op.f("ix_interactions_application_id"), "interactions", ["application_id"], unique=False)

    op.create_table(
        "application_outcomes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("application_id", sa.String(), nullable=False),
        sa.Column("resume_used", sa.String(), nullable=True),
        sa.Column("company", sa.String(), nullable=True),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("ats", sa.String(), nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("viewed_at", sa.DateTime(), nullable=True),
        sa.Column("oa_at", sa.DateTime(), nullable=True),
        sa.Column("interview_at", sa.DateTime(), nullable=True),
        sa.Column("offer_at", sa.DateTime(), nullable=True),
        sa.Column("rejected_at", sa.DateTime(), nullable=True),
        sa.Column("ghosted_at", sa.DateTime(), nullable=True),
        sa.Column("rejection_reason", sa.String(), nullable=True),
        sa.Column("interview_rounds", sa.Integer(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("salary", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id"),
    )
    op.create_index(op.f("ix_application_outcomes_application_id"), "application_outcomes", ["application_id"], unique=True)
    op.create_index(op.f("ix_application_outcomes_company"), "application_outcomes", ["company"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("applications") as batch_op:
        batch_op.drop_column("expected_interview_probability")
        batch_op.drop_column("ats_keyword_match")
        batch_op.drop_column("ats_confirmed_at")
        batch_op.drop_column("ats_confirmed")
        batch_op.drop_column("portal_url")
        batch_op.drop_column("application_id")
    op.drop_table("application_outcomes")
    op.drop_table("interactions")
    op.drop_table("contacts")
