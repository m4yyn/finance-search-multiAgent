"""create long term memories

Revision ID: 202604290004
Revises: 202604280003
Create Date: 2026-04-29 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "202604290004"
down_revision: str | None = "202604280003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "long_term_memories",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("key_insights", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "milvus_ids",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_long_term_memories_created_at"),
        "long_term_memories",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_long_term_memories_session_id"),
        "long_term_memories",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_long_term_memories_user_id"),
        "long_term_memories",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_long_term_memories_user_id"), table_name="long_term_memories")
    op.drop_index(op.f("ix_long_term_memories_session_id"), table_name="long_term_memories")
    op.drop_index(op.f("ix_long_term_memories_created_at"), table_name="long_term_memories")
    op.drop_table("long_term_memories")
