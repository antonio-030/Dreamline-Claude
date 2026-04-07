"""Optimierungen: Neue Spalten, Indexes, Provider-Fehler-Tracking, Versionierung, TTL.

Revision ID: 002_optimizations
Revises: 001_baseline
Create Date: 2026-04-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "002_optimizations"
down_revision = "001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Dream-Tabelle: Provider-Fehler sichtbar machen
    op.add_column("dreams", sa.Column("error_detail", sa.Text(), nullable=True))
    op.add_column("dreams", sa.Column("ai_provider_used", sa.String(20), nullable=True))

    # Project-Tabelle: Extractor-State persistieren
    op.add_column("projects", sa.Column("last_extract_at", sa.DateTime(timezone=True), nullable=True))

    # Memory-Tabelle: TTL/Expiration
    op.add_column("memories", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))

    # Memory-Versionierung
    op.create_table(
        "memory_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("memory_id", UUID(as_uuid=True), sa.ForeignKey("memories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), default=0.5),
        sa.Column("changed_by", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_memory_versions_memory_id", "memory_versions", ["memory_id"])

    # Runtime-Settings (Key-Value-Tabelle für UI-konfigurierbare Einstellungen)
    op.create_table(
        "runtime_settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Performance-Indexes
    op.create_index("ix_sessions_created_at", "sessions", ["created_at"])
    op.create_index("ix_sessions_project_consolidated", "sessions", ["project_id", "is_consolidated"])
    op.create_index("ix_memories_memory_type", "memories", ["memory_type"])


def downgrade() -> None:
    op.drop_table("runtime_settings")
    op.drop_index("ix_memories_memory_type", table_name="memories")
    op.drop_index("ix_sessions_project_consolidated", table_name="sessions")
    op.drop_index("ix_sessions_created_at", table_name="sessions")
    op.drop_index("ix_memory_versions_memory_id", table_name="memory_versions")
    op.drop_table("memory_versions")
    op.drop_column("memories", "expires_at")
    op.drop_column("projects", "last_extract_at")
    op.drop_column("dreams", "ai_provider_used")
    op.drop_column("dreams", "error_detail")
