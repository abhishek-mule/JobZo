"""add events table for immutable event log

Revision ID: 3b9c5d6e7f8a
Revises: 2a8b1c4d5e6f
Create Date: 2026-07-12 03:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3b9c5d6e7f8a"
down_revision: Union[str, None] = "2a8b1c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_events_event_type"), "events", ["event_type"], unique=False)
    op.create_index(op.f("ix_events_entity_type"), "events", ["entity_type"], unique=False)
    op.create_index(op.f("ix_events_entity_id"), "events", ["entity_id"], unique=False)


def downgrade() -> None:
    op.drop_table("events")
