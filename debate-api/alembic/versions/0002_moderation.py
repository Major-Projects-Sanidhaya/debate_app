"""moderation: reports, blocks, moderation_events, users.flagged, matches.ended_reason

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-20

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def _uuid_fk(name: str, target: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, UUID, sa.ForeignKey(target), nullable=nullable)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("flagged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("matches", sa.Column("ended_reason", sa.Text(), nullable=True))

    op.create_table(
        "reports",
        sa.Column("id", UUID, primary_key=True),
        _uuid_fk("match_id", "matches.id"),
        _uuid_fk("reporter_id", "users.id"),
        _uuid_fk("reported_id", "users.id"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        _created_at(),
        sa.UniqueConstraint("reporter_id", "match_id", name="uq_reports_reporter_match"),
    )
    op.create_index("ix_reports_reported_created", "reports", ["reported_id", "created_at"])

    op.create_table(
        "blocks",
        _uuid_fk("blocker_id", "users.id"),
        _uuid_fk("blocked_id", "users.id"),
        _created_at(),
        sa.PrimaryKeyConstraint("blocker_id", "blocked_id"),
    )

    op.create_table(
        "moderation_events",
        sa.Column("id", UUID, primary_key=True),
        _uuid_fk("match_id", "matches.id"),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("stance", sa.Text(), nullable=False),
        _uuid_fk("user_id", "users.id", nullable=True),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False, server_default=sa.text("''")),
        _created_at(),
    )
    op.create_index("ix_moderation_events_match", "moderation_events", ["match_id"])


def downgrade() -> None:
    op.drop_table("moderation_events")
    op.drop_table("blocks")
    op.drop_table("reports")
    op.drop_column("matches", "ended_reason")
    op.drop_column("users", "flagged")
