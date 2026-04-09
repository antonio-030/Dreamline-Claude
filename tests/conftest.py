"""Gemeinsame Fixtures fuer die Dreamline Test-Suite."""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

# Umgebungsvariablen fuer Tests setzen BEVOR app.config importiert wird
# Dummy-PostgreSQL-URL: Engine wird erstellt aber nie verbunden (Tests nutzen Mocks)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("DREAMLINE_SECRET_KEY", "test-secret-key-do-not-use")


@pytest.fixture
def project_id():
    """Erzeugt eine zufaellige Projekt-UUID."""
    return uuid4()


@pytest.fixture
def sample_messages():
    """Beispiel-Nachrichten fuer eine Chat-Session."""
    return [
        {"role": "user", "content": "Was ist Dreamline?"},
        {"role": "assistant", "content": "Dreamline konsolidiert Wissen aus Chat-Sessions."},
        {"role": "user", "content": "Wie funktioniert der Dream-Prozess?"},
        {"role": "assistant", "content": "6 Phasen: Lock, Sessions, Memories, Prompt, AI, Result."},
    ]


@pytest.fixture
def sample_session(project_id, sample_messages):
    """Erzeugt ein Mock-Session-Objekt."""
    session = MagicMock()
    session.id = uuid4()
    session.project_id = project_id
    session.messages_json = json.dumps(sample_messages)
    session.outcome = "positive"
    session.metadata_json = None
    session.is_consolidated = False
    session.created_at = None
    return session


@pytest.fixture
def sample_memory(project_id):
    """Erzeugt ein Mock-Memory-Objekt."""
    memory = MagicMock()
    memory.id = uuid4()
    memory.project_id = project_id
    memory.key = "test_memory"
    memory.content = "Dies ist ein Test-Memory."
    memory.memory_type = "project"
    memory.confidence = 0.85
    memory.source_count = 3
    return memory


@pytest.fixture
def mock_db():
    """Erzeugt eine Mock-AsyncSession."""
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    db.delete = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.fixture
def tmp_memory_dir(tmp_path):
    """Erstellt ein temporaeres Memory-Verzeichnis mit Beispieldateien."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()

    # Beispiel-Memory-Datei
    (memory_dir / "project_test.md").write_text(
        "---\n"
        "name: test_memory\n"
        "description: Ein Test-Memory\n"
        "type: project\n"
        "confidence: 0.85\n"
        "source_count: 3\n"
        "---\n\n"
        "Dies ist ein Test-Memory.\n"
    )

    # MEMORY.md Index
    (memory_dir / "MEMORY.md").write_text(
        "- [test_memory](project_test.md) — Dies ist ein Test-Memory.\n"
    )

    return memory_dir
