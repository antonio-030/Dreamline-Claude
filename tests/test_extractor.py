"""Tests fuer den Quick-Extract-Service (app/services/extractor.py)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.extractor import _build_session_prompt, _run_extraction


# ─── Build Session Prompt ────────────────────────────────────────

class TestBuildSessionPrompt:
    """Tests fuer die Prompt-Erstellung aus einer Session."""

    def test_basic_prompt(self, sample_session):
        """Erstellt einen Prompt mit Nachrichten."""
        result = _build_session_prompt(sample_session)
        assert "## Chat Session" in result
        assert "Was ist Dreamline?" in result
        assert "6 Phasen" in result

    def test_includes_outcome(self, sample_session):
        """Outcome wird im Prompt angezeigt."""
        sample_session.outcome = "positive"
        result = _build_session_prompt(sample_session)
        assert "Outcome: positive" in result

    def test_no_outcome(self, sample_session):
        """Kein Outcome -> kein Outcome-Zeile."""
        sample_session.outcome = None
        result = _build_session_prompt(sample_session)
        assert "Outcome:" not in result

    def test_metadata_included(self, sample_session):
        """Metadaten werden als JSON angezeigt."""
        sample_session.metadata_json = json.dumps({"source": "test", "tool": "claude"})
        result = _build_session_prompt(sample_session)
        assert "Metadata:" in result
        assert "test" in result

    def test_project_context_in_metadata(self, sample_session):
        """Projektkontext aus Metadaten wird separat dargestellt."""
        sample_session.metadata_json = json.dumps({"project_context": "FastAPI Backend"})
        result = _build_session_prompt(sample_session)
        assert "## Project context" in result
        assert "FastAPI Backend" in result

    def test_long_content_truncated(self, sample_session):
        """Ueberlange Nachrichten werden gekuerzt."""
        long_msg = [{"role": "user", "content": "x" * 10000}]
        sample_session.messages_json = json.dumps(long_msg)
        result = _build_session_prompt(sample_session)
        assert "[truncated]" in result


# ─── Run Extraction ──────────────────────────────────────────────

class TestRunExtraction:
    """Tests fuer _run_extraction() – KI-Aufruf und Memory-Erstellung."""

    @pytest.mark.asyncio
    async def test_happy_path_creates_memories(self, mock_db, sample_session):
        """Erfolgreiche Extraktion erstellt neue Memories."""
        project_id = sample_session.project_id

        ai_response = json.dumps({
            "operations": [
                {"action": "create", "key": "user_role", "content": "Entwickler",
                 "type": "user", "confidence": 0.9},
                {"action": "create", "key": "tech_stack", "content": "FastAPI",
                 "type": "project", "confidence": 0.85},
            ],
            "extract_summary": "2 Fakten extrahiert",
        })

        # Mock: Keine bestehenden Keys
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_db.execute.return_value = mock_result

        with patch("app.services.extractor.ai_client") as mock_ai:
            mock_ai.complete = AsyncMock(return_value=(ai_response, 100))

            result = await _run_extraction(
                mock_db, sample_session, project_id, "claude-abo", "claude-sonnet-4-5-20250514",
            )

        assert result == "2 Fakten extrahiert"
        assert mock_db.add.call_count == 2
        assert mock_db.flush.called

    @pytest.mark.asyncio
    async def test_json_error_returns_none(self, mock_db, sample_session):
        """Ungueltige JSON-Antwort gibt None zurueck."""
        with patch("app.services.extractor.ai_client") as mock_ai:
            mock_ai.complete = AsyncMock(return_value=("kein json hier", 50))

            result = await _run_extraction(
                mock_db, sample_session, sample_session.project_id,
                "claude-abo", "claude-sonnet-4-5-20250514",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_ai_error_returns_none(self, mock_db, sample_session):
        """KI-Fehler gibt None zurueck (kein Crash)."""
        with patch("app.services.extractor.ai_client") as mock_ai:
            mock_ai.complete = AsyncMock(side_effect=RuntimeError("Provider down"))

            result = await _run_extraction(
                mock_db, sample_session, sample_session.project_id,
                "claude-abo", "claude-sonnet-4-5-20250514",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_low_confidence_filtered(self, mock_db, sample_session):
        """Fakten mit zu niedriger Konfidenz werden uebersprungen."""
        ai_response = json.dumps({
            "operations": [
                {"action": "create", "key": "unsicher", "content": "Vielleicht",
                 "type": "project", "confidence": 0.3},  # Unter Schwelle
            ],
            "extract_summary": "Nichts sicheres",
        })

        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_db.execute.return_value = mock_result

        with patch("app.services.extractor.ai_client") as mock_ai:
            mock_ai.complete = AsyncMock(return_value=(ai_response, 50))

            result = await _run_extraction(
                mock_db, sample_session, sample_session.project_id,
                "claude-abo", "claude-sonnet-4-5-20250514",
            )

        assert result == "Nichts sicheres"
        assert mock_db.add.call_count == 0  # Nichts erstellt

    @pytest.mark.asyncio
    async def test_duplicate_key_skipped(self, mock_db, sample_session):
        """Bereits existierende Keys werden nicht nochmal erstellt."""
        ai_response = json.dumps({
            "operations": [
                {"action": "create", "key": "existing_key", "content": "Duplikat",
                 "type": "project", "confidence": 0.9},
            ],
            "extract_summary": "Duplikat erkannt",
        })

        # Mock: Key existiert bereits
        mock_result = MagicMock()
        mock_result.all.return_value = [("existing_key",)]
        mock_db.execute.return_value = mock_result

        with patch("app.services.extractor.ai_client") as mock_ai:
            mock_ai.complete = AsyncMock(return_value=(ai_response, 50))

            result = await _run_extraction(
                mock_db, sample_session, sample_session.project_id,
                "claude-abo", "claude-sonnet-4-5-20250514",
            )

        assert mock_db.add.call_count == 0  # Duplikat uebersprungen

    @pytest.mark.asyncio
    async def test_invalid_type_defaults_to_reference(self, mock_db, sample_session):
        """Ungueltiger Memory-Typ wird auf 'reference' korrigiert."""
        ai_response = json.dumps({
            "operations": [
                {"action": "create", "key": "test_key", "content": "Test",
                 "type": "ungueltig", "confidence": 0.9},
            ],
            "extract_summary": "1 Fakt",
        })

        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_db.execute.return_value = mock_result

        with patch("app.services.extractor.ai_client") as mock_ai:
            mock_ai.complete = AsyncMock(return_value=(ai_response, 50))

            await _run_extraction(
                mock_db, sample_session, sample_session.project_id,
                "claude-abo", "claude-sonnet-4-5-20250514",
            )

        # Pruefen dass Memory mit type='reference' erstellt wurde
        assert mock_db.add.call_count == 1
        added_mem = mock_db.add.call_args[0][0]
        assert added_mem.memory_type == "reference"

    @pytest.mark.asyncio
    async def test_empty_operations_no_db_write(self, mock_db, sample_session):
        """Leere Operations-Liste fuehrt zu keinem DB-Schreibvorgang."""
        ai_response = json.dumps({
            "operations": [],
            "extract_summary": "Nichts Neues",
        })

        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_db.execute.return_value = mock_result

        with patch("app.services.extractor.ai_client") as mock_ai:
            mock_ai.complete = AsyncMock(return_value=(ai_response, 50))

            result = await _run_extraction(
                mock_db, sample_session, sample_session.project_id,
                "claude-abo", "claude-sonnet-4-5-20250514",
            )

        assert result == "Nichts Neues"
        assert not mock_db.flush.called

    @pytest.mark.asyncio
    async def test_markdown_codeblock_json(self, mock_db, sample_session):
        """JSON in Markdown-Codeblock wird korrekt extrahiert."""
        ai_response = '```json\n{"operations": [{"action": "create", "key": "md_test", "content": "Aus Codeblock", "type": "project", "confidence": 0.95}], "extract_summary": "1 Fakt"}\n```'

        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_db.execute.return_value = mock_result

        with patch("app.services.extractor.ai_client") as mock_ai:
            mock_ai.complete = AsyncMock(return_value=(ai_response, 50))

            result = await _run_extraction(
                mock_db, sample_session, sample_session.project_id,
                "claude-abo", "claude-sonnet-4-5-20250514",
            )

        assert result == "1 Fakt"
        assert mock_db.add.call_count == 1
