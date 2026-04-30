"""create deep research checkpoints

Revision ID: 202604290005
Revises: 202604290004
Create Date: 2026-04-29 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "202604290005"
down_revision: str | None = "202604290004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deep_research_checkpoints",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("phase", sa.String(length=40), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("max_iterations", sa.Integer(), nullable=False),
        sa.Column("state_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ui_state_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("final_report", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'paused', 'completed', 'failed', 'cancelled')",
            name="ck_deep_research_checkpoints_status",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_deep_research_checkpoints_session_id"),
        "deep_research_checkpoints",
        ["session_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_deep_research_checkpoints_user_id"),
        "deep_research_checkpoints",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_deep_research_checkpoints_user_id"),
        table_name="deep_research_checkpoints",
    )
    op.drop_index(
        op.f("ix_deep_research_checkpoints_session_id"),
        table_name="deep_research_checkpoints",
    )
    op.drop_table("deep_research_checkpoints")
