"""Baseline – aktuelles Schema als erste Migration.

Revision ID: 001_baseline
Revises: None
Create Date: 2026-04-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tabellen nur erstellen wenn sie noch nicht existieren
    # (für bestehende Installationen die von create_all kommen)
    op.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id UUID PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            api_key VARCHAR(64) UNIQUE NOT NULL,
            ai_provider VARCHAR(20) DEFAULT 'anthropic',
            ai_model VARCHAR(100) DEFAULT 'claude-sonnet-4-5-20250514',
            dream_interval_hours INTEGER DEFAULT 24,
            min_sessions_for_dream INTEGER DEFAULT 5,
            quick_extract BOOLEAN DEFAULT TRUE,
            local_path VARCHAR(500),
            last_extracted_session_id VARCHAR(36),
            dream_cli_session_id VARCHAR(100),
            source_tool VARCHAR(20) DEFAULT 'claude',
            ollama_custom_model_name VARCHAR(200),
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES projects(id),
            messages_json TEXT NOT NULL,
            outcome VARCHAR(20) DEFAULT 'neutral',
            is_consolidated BOOLEAN DEFAULT FALSE,
            metadata_json TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES projects(id),
            key VARCHAR(200) NOT NULL,
            content TEXT NOT NULL,
            memory_type VARCHAR(20) DEFAULT 'project',
            confidence FLOAT DEFAULT 0.5,
            source_count INTEGER DEFAULT 1,
            last_consolidated_at TIMESTAMPTZ DEFAULT NOW(),
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_memory_project_key UNIQUE (project_id, key)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS dreams (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES projects(id),
            sessions_reviewed INTEGER DEFAULT 0,
            memories_created INTEGER DEFAULT 0,
            memories_updated INTEGER DEFAULT 0,
            memories_deleted INTEGER DEFAULT 0,
            summary TEXT,
            tokens_used INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            status VARCHAR(20) DEFAULT 'completed',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS dream_locks (
            project_id UUID PRIMARY KEY REFERENCES projects(id),
            locked_by VARCHAR(200),
            locked_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Indizes nur erstellen wenn sie fehlen
    op.execute("CREATE INDEX IF NOT EXISTS ix_projects_api_key ON projects(api_key)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_sessions_project_id ON sessions(project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_memories_project_id ON memories(project_id)")


def downgrade() -> None:
    op.drop_table("dream_locks")
    op.drop_table("dreams")
    op.drop_table("memories")
    op.drop_table("sessions")
    op.drop_table("projects")
