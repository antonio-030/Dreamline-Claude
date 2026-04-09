"""Tests fuer den Dream-Pipeline-Pfad mit codex-sub als Provider.

Verifiziert dass der gesamte Dream-Flow korrekt funktioniert wenn
Codex statt Claude als KI-Provider konfiguriert ist. Da die Codex CLI
nicht in der Testumgebung verfuegbar ist, wird _invoke_cli gemockt.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ai_client import (
    _complete_codex_sub,
    _estimate_tokens_from_word_count,
    complete,
    dream_with_tools,
)


# ─── Provider-Dispatch ────────────────────────────────────────────

class TestCodexProviderDispatch:
    """Verifiziert dass codex-sub korrekt geroutet wird."""

    @pytest.mark.asyncio
    async def test_complete_dispatches_to_codex_sub(self):
        """complete() mit provider='codex-sub' ruft _complete_codex_sub auf."""
        mock_response = "Codex-Antwort: 3 Memories konsolidiert."

        with patch("app.services.ai_client._complete_codex_sub") as mock_codex:
            mock_codex.return_value = (mock_response, 150)

            result_text, result_tokens = await complete(
                provider="codex-sub",
                model="ignored-for-codex",
                system_prompt="System",
                user_prompt="User",
            )

            mock_codex.assert_called_once_with("ignored-for-codex", "System", "User")
            assert result_text == mock_response
            assert result_tokens == 150

    @pytest.mark.asyncio
    async def test_dream_with_tools_falls_back_for_codex(self):
        """dream_with_tools() fuer codex-sub faellt auf complete() zurueck (kein Agent-Modus)."""
        with patch("app.services.ai_client.complete") as mock_complete:
            mock_complete.return_value = ("Dream-Ergebnis", 200)

            content, tokens, session_id = await dream_with_tools(
                provider="codex-sub",
                model="gpt-4",
                prompt="Dream-Prompt",
                memory_dir="/tmp/memory",
            )

            # Codex kann keinen Agent-Modus -> Fallback auf complete()
            mock_complete.assert_called_once_with("codex-sub", "gpt-4", "Dream-Prompt", "")
            assert content == "Dream-Ergebnis"
            assert tokens == 200
            assert session_id is None  # Kein Agent-Modus = keine Session-ID

    @pytest.mark.asyncio
    async def test_unknown_provider_raises(self):
        """Unbekannter Provider wirft ValueError."""
        with pytest.raises(ValueError, match="Unbekannter KI-Anbieter"):
            await complete(
                provider="nicht-existent",
                model="x",
                system_prompt="S",
                user_prompt="U",
            )


# ─── Codex CLI Aufruf ────────────────────────────────────────────

class TestCompleteCodexSub:
    """Tests fuer _complete_codex_sub() – CLI-Aufruf und Response-Handling."""

    @pytest.mark.asyncio
    async def test_calls_codex_exec(self):
        """Codex CLI wird mit korrekten Argumenten aufgerufen."""
        with patch("app.services.ai_client._invoke_cli") as mock_cli:
            mock_cli.return_value = '{"operations": [], "summary": "Nichts zu tun."}'

            text, tokens = await _complete_codex_sub(
                model="gpt-5.2-codex",
                system_prompt="Du bist ein Memory-Konsolidierer.",
                user_prompt="Konsolidiere diese Sessions.",
            )

            mock_cli.assert_called_once()
            call_args = mock_cli.call_args
            # Binary muss "codex" sein
            assert call_args[1].get("binary") or call_args[0][0] == "codex"
            # Args muessen exec, --full-auto, --skip-git-repo-check, -m, - enthalten
            cli_args = call_args[1].get("args") or call_args[0][1]
            assert "exec" in cli_args
            assert "--full-auto" in cli_args
            assert "--skip-git-repo-check" in cli_args
            assert "-" in cli_args

    @pytest.mark.asyncio
    async def test_combines_system_and_user_prompt(self):
        """System- und User-Prompt werden mit --- getrennt kombiniert."""
        captured_input = None

        async def capture_cli(binary, args, input_text, **kwargs):
            nonlocal captured_input
            captured_input = input_text
            return "Antwort"

        with patch("app.services.ai_client._invoke_cli", side_effect=capture_cli):
            await _complete_codex_sub(
                model="gpt-5.2-codex",
                system_prompt="SYSTEM-TEXT",
                user_prompt="USER-TEXT",
            )

        assert captured_input is not None
        assert "SYSTEM-TEXT" in captured_input
        assert "USER-TEXT" in captured_input
        assert "---" in captured_input

    @pytest.mark.asyncio
    async def test_returns_plain_text_not_json_parsed(self):
        """Codex gibt Plain-Text zurueck – wird NICHT als JSON geparst."""
        raw_text = "Ich habe 2 Memories aktualisiert und 1 geloescht."

        with patch("app.services.ai_client._invoke_cli", return_value=raw_text):
            text, tokens = await _complete_codex_sub("model", "S", "U")

        # Response ist der Rohtext, nicht JSON-geparst
        assert text == raw_text

    @pytest.mark.asyncio
    async def test_token_estimation_via_word_count(self):
        """Token-Schaetzung nutzt Wortanzahl (nicht Usage-Daten)."""
        # "eins zwei drei vier fuenf" = 5 Woerter
        raw_response = "eins zwei drei vier fuenf"

        with patch("app.services.ai_client._invoke_cli", return_value=raw_response):
            text, tokens = await _complete_codex_sub(
                model="gpt-5.2-codex",
                system_prompt="a b c",  # 3 Woerter
                user_prompt="d e",      # 2 Woerter
            )

        # full_prompt = "a b c\n\n---\n\nu v" + raw_response
        # Wortanzahl des kombinierten Prompts + Antwort
        expected_min = 5 + 5  # Mindestens Prompt + Response Woerter
        assert tokens >= expected_min

    @pytest.mark.asyncio
    async def test_codex_cli_error_propagates(self):
        """CLI-Fehler wird korrekt propagiert (fuer Retry-Logic)."""
        with patch("app.services.ai_client._invoke_cli",
                    side_effect=RuntimeError("codex CLI fehlgeschlagen: timeout")):
            with pytest.raises(RuntimeError, match="codex CLI fehlgeschlagen"):
                await _complete_codex_sub("model", "S", "U")


# ─── Codex Dream JSON-Verarbeitung ───────────────────────────────

class TestCodexDreamJsonProcessing:
    """Testet ob Codex-Responses korrekt durch _parse_dream_operations laufen."""

    def test_codex_json_response_parseable(self):
        """Codex kann gueltiges JSON zurueckgeben das Dream-Ops enthaelt."""
        from app.services.dreamer import _parse_dream_operations

        # Codex gibt Plain-Text zurueck – wenn es JSON ist, wird es geparst
        codex_response = json.dumps({
            "operations": [
                {"action": "create", "key": "codex_finding", "type": "project",
                 "content": "Aus Codex-Dream gelernt.", "confidence": 0.7},
                {"action": "update", "key": "existing_key",
                 "content": "Aktualisiert via Codex.", "confidence": 0.85},
            ],
            "summary": "Codex hat 2 Operationen durchgefuehrt.",
        })

        ops, summary = _parse_dream_operations(codex_response)
        assert len(ops) == 2
        assert ops[0]["action"] == "create"
        assert ops[0]["key"] == "codex_finding"
        assert ops[1]["action"] == "update"
        assert "Codex" in summary

    def test_codex_plain_text_without_json_fails(self):
        """Reiner Freitext ohne JSON wirft JSONDecodeError."""
        from app.services.dreamer import _parse_dream_operations

        codex_response = "Ich habe die Sessions analysiert und keine Aenderungen vorgenommen."

        with pytest.raises(json.JSONDecodeError):
            _parse_dream_operations(codex_response)

    def test_codex_json_embedded_in_freetext(self):
        """JSON eingebettet in Freitext wird korrekt extrahiert (Codex-typisch)."""
        from app.services.dreamer import _parse_dream_operations

        codex_response = (
            "Basierend auf meiner Analyse der Sessions, hier sind die Ergebnisse:\n\n"
            '{"operations": [{"action": "create", "key": "codex_result", '
            '"type": "project", "content": "Aus Codex gelernt.", "confidence": 0.7}], '
            '"summary": "1 neues Memory."}\n\n'
            "Das war meine Konsolidierung."
        )

        ops, summary = _parse_dream_operations(codex_response)
        assert len(ops) == 1
        assert ops[0]["key"] == "codex_result"
        assert summary == "1 neues Memory."

    def test_codex_json_with_leading_explanation(self):
        """JSON nach einleitender Erklaerung wird gefunden."""
        from app.services.dreamer import _parse_dream_operations

        codex_response = (
            "After reviewing the sessions, I found the following insights:\n"
            '{"operations": [{"action": "update", "key": "existing", '
            '"content": "Aktualisiert.", "confidence": 0.8}], "summary": "Update."}'
        )

        ops, summary = _parse_dream_operations(codex_response)
        assert len(ops) == 1
        assert ops[0]["action"] == "update"

    def test_codex_markdown_wrapped_json(self):
        """Codex-Response mit JSON in Markdown-Codeblock."""
        from app.services.dreamer import _parse_dream_operations

        codex_response = """Hier sind meine Ergebnisse:

```json
{
    "operations": [
        {"action": "create", "key": "codex_insight", "type": "feedback",
         "content": "Codex-spezifischer Insight.", "confidence": 0.6}
    ],
    "summary": "1 neues Memory aus Codex-Analyse."
}
```"""

        ops, summary = _parse_dream_operations(codex_response)
        assert len(ops) == 1
        assert ops[0]["key"] == "codex_insight"


# ─── End-to-End: Codex Dream mit Mock-DB ─────────────────────────

class TestCodexDreamEndToEnd:
    """Simuliert einen kompletten Dream-Durchlauf mit codex-sub."""

    @pytest.mark.asyncio
    async def test_full_codex_dream_pipeline(self, mock_db, project_id, sample_session):
        """Kompletter Dream-Flow: Sessions → Prompt → Codex-CLI → Parse → DB-Ops."""
        from unittest.mock import PropertyMock
        from app.services.dreamer import _process_result

        # Codex gibt JSON-Antwort zurueck
        codex_json = json.dumps({
            "operations": [
                {"action": "create", "key": "codex_memory", "type": "project",
                 "content": "Codex hat diese Erkenntnis gewonnen.", "confidence": 0.75},
            ],
            "summary": "1 neues Memory aus Codex-Dream.",
        })

        created, updated, deleted, summary = await _process_result(
            db=mock_db,
            project_id=project_id,
            response_text=codex_json,
            use_agent_mode=False,  # Codex hat keinen Agent-Modus
            agent_memory_dir=None,
            existing_memories=[],
            new_sessions=[sample_session],
            start_time=0,
            tokens_used=100,
        )

        assert created == 1
        assert updated == 0
        assert deleted == 0
        assert "Codex" in summary

        # Pruefen dass Memory korrekt erstellt wurde
        mem = mock_db.add.call_args[0][0]
        assert mem.key == "codex_memory"
        assert mem.memory_type == "project"
        assert mem.confidence == 0.75
        assert "Codex" in mem.content

    @pytest.mark.asyncio
    async def test_codex_dream_with_existing_memories(self, mock_db, project_id, sample_session, sample_memory):
        """Codex-Dream kann bestehende Memories aktualisieren."""
        from app.services.dreamer import _process_result

        codex_json = json.dumps({
            "operations": [
                {"action": "update", "key": "test_memory",
                 "content": "Von Codex aktualisiert: Neue Erkenntnisse.", "confidence": 0.9},
            ],
            "summary": "1 Memory von Codex aktualisiert.",
        })

        created, updated, deleted, summary = await _process_result(
            db=mock_db,
            project_id=project_id,
            response_text=codex_json,
            use_agent_mode=False,
            agent_memory_dir=None,
            existing_memories=[sample_memory],
            new_sessions=[sample_session],
            start_time=0,
            tokens_used=100,
        )

        assert updated == 1
        assert created == 0
        # Pruefen dass MemoryVersion erstellt wurde (Backup vor Update)
        assert mock_db.add.called
        # Pruefen dass Memory-Content aktualisiert wurde
        assert "Codex" in sample_memory.content

    @pytest.mark.asyncio
    async def test_codex_dream_delete_and_create(self, mock_db, project_id, sample_session, sample_memory):
        """Codex kann in einem Dream loeschen UND erstellen."""
        from app.services.dreamer import _process_result

        codex_json = json.dumps({
            "operations": [
                {"action": "delete", "key": "test_memory"},
                {"action": "create", "key": "replacement_memory", "type": "project",
                 "content": "Ersetzt das geloeschte Memory.", "confidence": 0.8},
            ],
            "summary": "Altes Memory geloescht, neues erstellt.",
        })

        created, updated, deleted, summary = await _process_result(
            db=mock_db,
            project_id=project_id,
            response_text=codex_json,
            use_agent_mode=False,
            agent_memory_dir=None,
            existing_memories=[sample_memory],
            new_sessions=[sample_session],
            start_time=0,
            tokens_used=100,
        )

        assert deleted == 1
        assert created == 1
        mock_db.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_codex_handles_parse_error_in_dream(self, mock_db, project_id, sample_session):
        """Wenn Codex Plain-Text statt JSON zurueckgibt, schlaegt _execute_dream fehl."""
        from app.services.dreamer import _process_result

        # Plain-Text (kein JSON) -> _parse_dream_operations wird in _process_result aufgerufen
        # und wirft json.JSONDecodeError
        codex_plain = "Ich habe die Sessions gelesen aber nichts Neues gefunden."

        with pytest.raises(json.JSONDecodeError):
            await _process_result(
                db=mock_db,
                project_id=project_id,
                response_text=codex_plain,
                use_agent_mode=False,
                agent_memory_dir=None,
                existing_memories=[],
                new_sessions=[sample_session],
                start_time=0,
                tokens_used=50,
            )

    @pytest.mark.asyncio
    async def test_codex_call_ai_uses_complete_not_cache(self):
        """_call_ai mit codex-sub nutzt complete(), nicht complete_with_cache()."""
        from app.services.dreamer import _call_ai

        mock_db = AsyncMock()
        project_id = uuid4()

        codex_json = json.dumps({
            "operations": [], "summary": "Nichts.",
        })

        with patch("app.services.ai_client.complete", return_value=(codex_json, 100)) as mock_complete, \
             patch("app.services.ai_client.complete_with_cache") as mock_cache:

            text, tokens = await _call_ai(
                db=mock_db,
                project_id=project_id,
                ai_provider="codex-sub",
                ai_model="gpt-4",
                user_prompt="Test-Prompt",
                existing_memories=[],
                use_agent_mode=False,
                agent_memory_dir=None,
            )

            # codex-sub muss ueber complete() gehen, NICHT ueber complete_with_cache()
            mock_complete.assert_called_once()
            mock_cache.assert_not_called()
            assert text == codex_json

    @pytest.mark.asyncio
    async def test_codex_dream_with_tools_no_agent_mode(self):
        """dream_with_tools fuer codex-sub gibt keine Session-ID zurueck."""
        with patch("app.services.ai_client.complete", return_value=("text", 50)):
            content, tokens, session_id = await dream_with_tools(
                provider="codex-sub",
                model="gpt-4",
                prompt="Dream",
                memory_dir="/tmp/mem",
                resume_session_id="some-old-session-id",  # Wird ignoriert
            )

            assert session_id is None  # Codex hat keinen Session-Resume
