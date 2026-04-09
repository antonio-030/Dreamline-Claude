"""Tests fuer den Sessions-Router (app/routers/sessions.py).

Erster Router-Test – zeigt das Pattern fuer weitere Router-Tests.
Nutzt FastAPI TestClient mit gemockter Datenbank.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app


# ─── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def mock_project():
    """Mock-Projekt fuer Auth-Dependency."""
    p = MagicMock()
    p.id = uuid4()
    p.name = "TestProject"
    p.ai_provider = "claude-abo"
    p.ai_model = "claude-sonnet-4-5-20250514"
    p.quick_extract = False
    return p


@pytest.fixture
def client(mock_project):
    """TestClient mit gemockter Auth + DB."""
    from app.auth import get_current_project
    from app.database import get_db

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.delete = AsyncMock()

    async def override_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_project] = lambda: mock_project

    yield TestClient(app), mock_db

    app.dependency_overrides.clear()


# ─── POST /api/v1/sessions ───────────────────────────────────────

class TestCreateSession:
    """Tests fuer das Erstellen einer Session."""

    def test_create_session_success(self, client, mock_project):
        """Erfolgreiche Session-Erstellung mit Nachrichten."""
        tc, mock_db = client

        # Mock: refresh setzt id + created_at (wie die echte DB)
        async def fake_refresh(obj, **kwargs):
            obj.id = uuid4()
            obj.project_id = mock_project.id
            obj.created_at = datetime.now(timezone.utc)
            obj.is_consolidated = False
        mock_db.refresh = AsyncMock(side_effect=fake_refresh)

        response = tc.post(
            "/api/v1/sessions",
            json={
                "messages": [
                    {"role": "user", "content": "Hallo"},
                    {"role": "assistant", "content": "Hi!"},
                ],
                "outcome": "positive",
            },
            headers={"Authorization": "Bearer test"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["outcome"] == "positive"
        assert data["is_consolidated"] is False
        assert mock_db.add.called

    def test_create_session_no_messages_fails(self, client):
        """Session ohne Nachrichten wird abgelehnt (422)."""
        tc, _ = client

        response = tc.post(
            "/api/v1/sessions",
            json={"messages": []},
            headers={"Authorization": "Bearer test"},
        )

        assert response.status_code == 422  # Validation Error

    def test_create_session_too_long_content(self, client):
        """Nachricht mit >50KB Content wird abgelehnt."""
        tc, _ = client

        response = tc.post(
            "/api/v1/sessions",
            json={
                "messages": [
                    {"role": "user", "content": "x" * 60_000},
                ],
            },
            headers={"Authorization": "Bearer test"},
        )

        assert response.status_code == 422

    def test_create_session_too_many_messages(self, client):
        """Mehr als 100 Nachrichten werden abgelehnt."""
        tc, _ = client

        messages = [{"role": "user", "content": f"Msg {i}"} for i in range(101)]
        response = tc.post(
            "/api/v1/sessions",
            json={"messages": messages},
            headers={"Authorization": "Bearer test"},
        )

        assert response.status_code == 422


# ─── GET /api/v1/sessions ────────────────────────────────────────

class TestListSessions:
    """Tests fuer das Auflisten von Sessions."""

    def test_list_sessions_empty(self, client, mock_project):
        """Leere Session-Liste bei neuem Projekt."""
        tc, mock_db = client

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        response = tc.get(
            "/api/v1/sessions",
            headers={"Authorization": "Bearer test"},
        )

        assert response.status_code == 200
        assert response.json() == []

    def test_list_sessions_with_data(self, client, mock_project):
        """Session-Liste mit einem Eintrag."""
        tc, mock_db = client

        session = MagicMock()
        session.id = uuid4()
        session.outcome = "neutral"
        session.is_consolidated = False
        session.created_at = datetime.now(timezone.utc)
        session.messages_json = json.dumps([
            {"role": "user", "content": "Test-Nachricht"},
        ])

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [session]
        mock_db.execute.return_value = mock_result

        response = tc.get(
            "/api/v1/sessions",
            headers={"Authorization": "Bearer test"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["message_count"] == 1
        assert "Test-Nachricht" in data[0]["preview"]
