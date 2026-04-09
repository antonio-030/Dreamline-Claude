"""Tests fuer app/services/dreamer.py – Dream-Pipeline, JSON-Parsing, Operationen."""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.dreamer import _parse_dream_operations


# ─── Dream-Operations Parsing ──────────────────────────────────────

class TestParseDreamOperations:
    """Tests fuer _parse_dream_operations() – JSON-Extraktion aus KI-Antworten."""

    def test_valid_json(self):
        """Gueltige JSON-Antwort mit Operationen."""
        response = json.dumps({
            "operations": [
                {"action": "create", "key": "test_key", "type": "project",
                 "content": "Testinhalt", "confidence": 0.8},
            ],
            "summary": "Ein neues Memory erstellt.",
        })
        ops, summary = _parse_dream_operations(response)
        assert len(ops) == 1
        assert ops[0]["action"] == "create"
        assert ops[0]["key"] == "test_key"
        assert summary == "Ein neues Memory erstellt."

    def test_multiple_operations(self):
        """Mehrere Operationen in einer Antwort."""
        response = json.dumps({
            "operations": [
                {"action": "create", "key": "new_mem", "type": "user",
                 "content": "Neuer User-Context", "confidence": 0.7},
                {"action": "update", "key": "existing_mem",
                 "content": "Aktualisierter Inhalt", "confidence": 0.9},
                {"action": "delete", "key": "old_mem"},
            ],
            "summary": "3 Operationen durchgeführt.",
        })
        ops, summary = _parse_dream_operations(response)
        assert len(ops) == 3
        assert ops[0]["action"] == "create"
        assert ops[1]["action"] == "update"
        assert ops[2]["action"] == "delete"

    def test_empty_operations(self):
        """Leere Operations-Liste (nichts zu tun)."""
        response = json.dumps({
            "operations": [],
            "summary": "Memories sind aktuell.",
        })
        ops, summary = _parse_dream_operations(response)
        assert len(ops) == 0
        assert summary == "Memories sind aktuell."

    def test_json_in_markdown_codeblock(self):
        """JSON in Markdown-Codeblock eingebettet (haeufig bei KI-Antworten)."""
        response = """Hier ist meine Antwort:

```json
{
    "operations": [
        {"action": "create", "key": "test", "type": "project",
         "content": "Test", "confidence": 0.5}
    ],
    "summary": "Aus Codeblock extrahiert."
}
```

Das war alles."""
        ops, summary = _parse_dream_operations(response)
        assert len(ops) == 1
        assert ops[0]["key"] == "test"
        assert summary == "Aus Codeblock extrahiert."

    def test_json_in_plain_codeblock(self):
        """JSON in Codeblock ohne Sprach-Annotation."""
        response = """```
{"operations": [{"action": "delete", "key": "old"}], "summary": "Geloescht."}
```"""
        ops, summary = _parse_dream_operations(response)
        assert len(ops) == 1
        assert ops[0]["action"] == "delete"

    def test_invalid_json_raises(self):
        """Ungueltiges JSON wirft eine Exception."""
        with pytest.raises(json.JSONDecodeError):
            _parse_dream_operations("Das ist kein JSON.")

    def test_missing_operations_key(self):
        """JSON ohne 'operations' Key gibt leere Liste."""
        response = json.dumps({"summary": "Kein operations-Key."})
        # JSON ist gueltig aber hat kein "operations" -> Strategie 2 parsed erfolgreich
        # .get("operations", []) gibt [] zurueck
        ops, summary = _parse_dream_operations(response)
        assert ops == []

    def test_missing_summary_key(self):
        """JSON ohne 'summary' Key gibt leeren String."""
        response = json.dumps({"operations": []})
        ops, summary = _parse_dream_operations(response)
        assert summary == ""

    def test_whitespace_handling(self):
        """Fuehrende/folgende Leerzeichen werden entfernt."""
        response = f"  \n  {json.dumps({'operations': [], 'summary': 'ok'})}  \n  "
        ops, summary = _parse_dream_operations(response)
        assert summary == "ok"


# ─── Dream-Pipeline Integration (mit Mocks) ──────────────────────

class TestProcessResult:
    """Tests fuer _process_result() – Memory-Operationen anwenden."""

    @pytest.mark.asyncio
    async def test_create_operation(self, mock_db, project_id, sample_session):
        """Create-Operation fuegt neues Memory hinzu."""
        from app.services.dreamer import _process_result

        response = json.dumps({
            "operations": [
                {"action": "create", "key": "new_insight", "type": "feedback",
                 "content": "User bevorzugt kurze Antworten.", "confidence": 0.85},
            ],
            "summary": "Neues Feedback-Memory erstellt.",
        })

        created, updated, deleted, summary = await _process_result(
            db=mock_db,
            project_id=project_id,
            response_text=response,
            use_agent_mode=False,
            agent_memory_dir=None,
            existing_memories=[],
            new_sessions=[sample_session],
            start_time=0,
            tokens_used=100,
        )

        assert created == 1
        assert updated == 0
        assert deleted == 0
        mock_db.add.assert_called()

    @pytest.mark.asyncio
    async def test_update_existing_memory(self, mock_db, project_id, sample_session, sample_memory):
        """Update-Operation aktualisiert bestehendes Memory + erstellt Version."""
        from app.services.dreamer import _process_result

        response = json.dumps({
            "operations": [
                {"action": "update", "key": "test_memory",
                 "content": "Aktualisierter Inhalt.", "confidence": 0.95},
            ],
            "summary": "Memory aktualisiert.",
        })

        created, updated, deleted, summary = await _process_result(
            db=mock_db,
            project_id=project_id,
            response_text=response,
            use_agent_mode=False,
            agent_memory_dir=None,
            existing_memories=[sample_memory],
            new_sessions=[sample_session],
            start_time=0,
            tokens_used=100,
        )

        assert created == 0
        assert updated == 1
        assert deleted == 0
        # Pruefen dass alte Version gespeichert wurde (MemoryVersion)
        assert mock_db.add.called

    @pytest.mark.asyncio
    async def test_update_nonexistent_creates_new(self, mock_db, project_id, sample_session):
        """Update auf nicht-existierendes Memory erstellt es neu."""
        from app.services.dreamer import _process_result

        response = json.dumps({
            "operations": [
                {"action": "update", "key": "missing_key",
                 "content": "Neuer Inhalt.", "confidence": 0.6},
            ],
            "summary": "Fallback-Create.",
        })

        created, updated, deleted, summary = await _process_result(
            db=mock_db,
            project_id=project_id,
            response_text=response,
            use_agent_mode=False,
            agent_memory_dir=None,
            existing_memories=[],
            new_sessions=[sample_session],
            start_time=0,
            tokens_used=100,
        )

        assert created == 1
        assert updated == 0

    @pytest.mark.asyncio
    async def test_delete_operation(self, mock_db, project_id, sample_session, sample_memory):
        """Delete-Operation loescht bestehendes Memory."""
        from app.services.dreamer import _process_result

        response = json.dumps({
            "operations": [
                {"action": "delete", "key": "test_memory"},
            ],
            "summary": "Veraltetes Memory geloescht.",
        })

        created, updated, deleted, summary = await _process_result(
            db=mock_db,
            project_id=project_id,
            response_text=response,
            use_agent_mode=False,
            agent_memory_dir=None,
            existing_memories=[sample_memory],
            new_sessions=[sample_session],
            start_time=0,
            tokens_used=100,
        )

        assert deleted == 1
        mock_db.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_noop(self, mock_db, project_id, sample_session):
        """Delete auf nicht-existierendes Memory ist ein No-Op."""
        from app.services.dreamer import _process_result

        response = json.dumps({
            "operations": [
                {"action": "delete", "key": "nonexistent"},
            ],
            "summary": "Nichts zu loeschen.",
        })

        created, updated, deleted, summary = await _process_result(
            db=mock_db,
            project_id=project_id,
            response_text=response,
            use_agent_mode=False,
            agent_memory_dir=None,
            existing_memories=[],
            new_sessions=[sample_session],
            start_time=0,
            tokens_used=100,
        )

        assert deleted == 0
        mock_db.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_confidence_clamping(self, mock_db, project_id, sample_session):
        """Confidence wird auf [0.0, 1.0] begrenzt."""
        from app.services.dreamer import _process_result

        response = json.dumps({
            "operations": [
                {"action": "create", "key": "too_high", "type": "project",
                 "content": "Test", "confidence": 1.5},
                {"action": "create", "key": "too_low", "type": "project",
                 "content": "Test", "confidence": -0.5},
            ],
            "summary": "Clamping-Test.",
        })

        created, updated, deleted, summary = await _process_result(
            db=mock_db,
            project_id=project_id,
            response_text=response,
            use_agent_mode=False,
            agent_memory_dir=None,
            existing_memories=[],
            new_sessions=[sample_session],
            start_time=0,
            tokens_used=100,
        )

        assert created == 2
        # Pruefen dass die Memory-Objekte geclamped wurden
        calls = mock_db.add.call_args_list
        for call in calls:
            mem = call[0][0]
            if hasattr(mem, "confidence"):
                assert 0.0 <= mem.confidence <= 1.0

    @pytest.mark.asyncio
    async def test_invalid_memory_type_defaults_to_project(self, mock_db, project_id, sample_session):
        """Ungueltiger Memory-Typ faellt auf 'project' zurueck."""
        from app.services.dreamer import _process_result

        response = json.dumps({
            "operations": [
                {"action": "create", "key": "bad_type", "type": "invalid_type",
                 "content": "Test", "confidence": 0.5},
            ],
            "summary": "Invalid-Type-Test.",
        })

        await _process_result(
            db=mock_db,
            project_id=project_id,
            response_text=response,
            use_agent_mode=False,
            agent_memory_dir=None,
            existing_memories=[],
            new_sessions=[sample_session],
            start_time=0,
            tokens_used=100,
        )

        mem = mock_db.add.call_args[0][0]
        assert mem.memory_type == "project"

    @pytest.mark.asyncio
    async def test_empty_key_skipped(self, mock_db, project_id, sample_session):
        """Operationen mit leerem Key werden uebersprungen."""
        from app.services.dreamer import _process_result

        response = json.dumps({
            "operations": [
                {"action": "create", "key": "", "content": "Kein Key"},
                {"action": "create", "key": "valid_key", "type": "project",
                 "content": "Hat Key", "confidence": 0.5},
            ],
            "summary": "Leerer Key wird ignoriert.",
        })

        created, _, _, _ = await _process_result(
            db=mock_db,
            project_id=project_id,
            response_text=response,
            use_agent_mode=False,
            agent_memory_dir=None,
            existing_memories=[],
            new_sessions=[sample_session],
            start_time=0,
            tokens_used=100,
        )

        assert created == 1  # Nur der mit gueltigem Key
