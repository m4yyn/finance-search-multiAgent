"""create knowledge tables

Revision ID: 202604280003
Revises: 202604270002
Create Date: 2026-04-28 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "202604280003"
down_revision: str | None = "202604270002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("collection_name", sa.String(length=128), nullable=False),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_knowledge_bases_collection_name"),
        "knowledge_bases",
        ["collection_name"],
        unique=True,
    )
    op.create_index(
        op.f("ix_knowledge_bases_user_id"),
        "knowledge_bases",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("kb_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
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
            "status IN ('pending', 'processing', 'success', 'failed')",
            name="ck_documents_status",
        ),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledge_bases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_documents_kb_id"),
        "documents",
        ["kb_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_documents_kb_id"), table_name="documents")
    op.drop_table("documents")
    op.drop_index(op.f("ix_knowledge_bases_user_id"), table_name="knowledge_bases")
    op.drop_index(
        op.f("ix_knowledge_bases_collection_name"),
        table_name="knowledge_bases",
    )
    op.drop_table("knowledge_bases")
