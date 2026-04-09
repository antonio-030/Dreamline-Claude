"""Separater Dream-Provider pro Projekt.

Revision ID: 003_dream_provider
Revises: 002_optimizations
Create Date: 2026-04-07
"""

from alembic import op
import sqlalchemy as sa

revision = "003_dream_provider"
down_revision = "002_optimizations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Fügt dream_provider und dream_model Spalten hinzu."""
    op.add_column(
        "projects",
        sa.Column("dream_provider", sa.String(20), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("dream_model", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    """Entfernt dream_provider und dream_model Spalten."""
    op.drop_column("projects", "dream_model")
    op.drop_column("projects", "dream_provider")
