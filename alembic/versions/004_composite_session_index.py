"""Composite-Index auf sessions(project_id, is_consolidated).

Optimiert die haeufigste Dream-Query: 'Alle unkonsolidierten Sessions eines Projekts'.
Ersetzt die separaten Single-Column-Indexes nicht, ergaenzt sie als Composite.

Revision ID: 004_composite_session_index
Revises: 003_dream_provider
Create Date: 2026-04-08
"""

from alembic import op

revision = "004_composite_session_index"
down_revision = "003_dream_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Erstellt Composite-Index fuer die Dream-Pipeline-Query."""
    op.create_index(
        "ix_sessions_project_consolidated",
        "sessions",
        ["project_id", "is_consolidated"],
        if_not_exists=True,
    )


def downgrade() -> None:
    """Entfernt den Composite-Index."""
    op.drop_index("ix_sessions_project_consolidated", table_name="sessions")
