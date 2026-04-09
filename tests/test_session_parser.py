"""Tests fuer app/services/session_parser.py – Claude- und Codex-JSONL-Parsing."""

import json
from pathlib import Path

import pytest

from app.services.session_parser import (
    ParsedSession,
    _is_codex_system_message,
    detect_source_tool,
    parse_session_file,
)


# ─── Source-Tool Detection ────────────────────────────────────────

class TestDetectSourceTool:
    """Tests fuer detect_source_tool()."""

    def test_codex_session_meta(self):
        """Codex-Format: type=session_meta mit originator=codex."""
        line = json.dumps({
            "type": "session_meta",
            "payload": {"id": "sess-123", "cwd": "/tmp", "originator": "codex-cli"},
        })
        assert detect_source_tool(line) == "codex"

    def test_claude_user_type(self):
        """Claude-Format: type=user auf Top-Level."""
        line = json.dumps({"type": "user", "message": {"content": "Hallo"}})
        assert detect_source_tool(line) == "claude"

    def test_claude_assistant_type(self):
        """Claude-Format: type=assistant auf Top-Level."""
        line = json.dumps({"type": "assistant", "message": {"content": []}})
        assert detect_source_tool(line) == "claude"

    def test_unknown_format(self):
        """Unbekanntes Format: Kein bekannter Typ."""
        line = json.dumps({"type": "log", "data": "..."})
        assert detect_source_tool(line) == "unknown"

    def test_invalid_json(self):
        """Ungueltiges JSON gibt 'unknown' zurueck."""
        assert detect_source_tool("kein json") == "unknown"

    def test_empty_string(self):
        """Leerer String gibt 'unknown' zurueck."""
        assert detect_source_tool("") == "unknown"

    def test_session_meta_without_codex_originator(self):
        """session_meta ohne codex-originator wird nicht als codex erkannt."""
        line = json.dumps({
            "type": "session_meta",
            "payload": {"id": "sess-123", "originator": "other-tool"},
        })
        assert detect_source_tool(line) == "unknown"


# ─── Codex System-Message Filter ──────────────────────────────────

class TestIsCodexSystemMessage:
    """Tests fuer _is_codex_system_message()."""

    def test_agents_md_detected(self):
        """AGENTS.md-Instruktionen werden erkannt."""
        assert _is_codex_system_message("# AGENTS.md instructions\nDo this and that...") is True

    def test_environment_context_detected(self):
        """Environment-Context wird erkannt."""
        assert _is_codex_system_message("<environment_context>OS: Linux...</environment_context>") is True

    def test_permissions_detected(self):
        """Permissions-Instruktionen werden erkannt."""
        assert _is_codex_system_message("<permissions instructions>Only read files</permissions>") is True

    def test_instructions_detected(self):
        """INSTRUCTIONS-Block wird erkannt."""
        assert _is_codex_system_message("<INSTRUCTIONS>Follow these rules...</INSTRUCTIONS>") is True

    def test_normal_message_not_detected(self):
        """Normale User-Nachricht wird nicht als System-Message erkannt."""
        assert _is_codex_system_message("Bitte erklaere mir wie Codex funktioniert.") is False

    def test_short_message_not_detected(self):
        """Kurze Nachricht wird nicht als System-Message erkannt."""
        assert _is_codex_system_message("Fix bug") is False

    def test_pattern_only_in_first_200_chars(self):
        """Pattern-Check beschraenkt sich auf die ersten 200 Zeichen."""
        # Pattern weit hinten im Text -> nicht erkannt
        text = "x" * 250 + "# AGENTS.md instructions"
        assert _is_codex_system_message(text) is False


# ─── Codex Session Parsing ────────────────────────────────────────

class TestParseCodexSession:
    """Tests fuer das Codex-JSONL-Format."""

    def _write_codex_session(self, tmp_path, lines):
        """Hilfsfunktion: Schreibt Codex-JSONL-Datei."""
        filepath = tmp_path / "codex-session.jsonl"
        content = "\n".join(json.dumps(l) for l in lines)
        filepath.write_text(content)
        return filepath

    def test_basic_codex_session(self, tmp_path):
        """Vollstaendige Codex-Session mit User+Assistant."""
        lines = [
            {"type": "session_meta", "payload": {
                "id": "sess-codex-1", "cwd": "/Users/dev/project", "originator": "codex-cli",
            }},
            {"type": "response_item", "payload": {
                "role": "user",
                "content": [{"type": "input_text", "text": "Erklaere mir die Architektur."}],
            }},
            {"type": "response_item", "payload": {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Die Architektur besteht aus drei Schichten..."}],
            }},
            {"type": "response_item", "payload": {
                "role": "user",
                "content": [{"type": "input_text", "text": "Und wie funktioniert das Deployment?"}],
            }},
            {"type": "response_item", "payload": {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Das Deployment laeuft ueber Docker Compose."}],
            }},
        ]
        filepath = self._write_codex_session(tmp_path, lines)
        result = parse_session_file(filepath, source_tool="codex")

        assert result is not None
        assert result.source_tool == "codex"
        assert result.session_id == "sess-codex-1"
        assert result.cwd == "/Users/dev/project"
        assert len(result.messages) == 4
        assert result.messages[0]["role"] == "user"
        assert result.messages[1]["role"] == "assistant"

    def test_codex_session_extracts_cwd(self, tmp_path):
        """CWD wird aus session_meta extrahiert."""
        lines = [
            {"type": "session_meta", "payload": {
                "id": "s1", "cwd": "/home/user/my-project", "originator": "codex-cli",
            }},
            {"type": "response_item", "payload": {
                "role": "user",
                "content": [{"type": "input_text", "text": "Frage eins"}],
            }},
            {"type": "response_item", "payload": {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Antwort eins hier"}],
            }},
        ]
        filepath = self._write_codex_session(tmp_path, lines)
        result = parse_session_file(filepath, source_tool="codex")

        assert result is not None
        assert result.cwd == "/home/user/my-project"

    def test_codex_filters_system_messages(self, tmp_path):
        """Codex-System-Messages werden herausgefiltert."""
        lines = [
            {"type": "session_meta", "payload": {
                "id": "s2", "cwd": "/tmp", "originator": "codex-cli",
            }},
            {"type": "response_item", "payload": {
                "role": "user",
                "content": [{"type": "input_text", "text": "# AGENTS.md instructions\nFolge diesen Regeln..."}],
            }},
            {"type": "response_item", "payload": {
                "role": "user",
                "content": [{"type": "input_text", "text": "Meine echte Frage lautet"}],
            }},
            {"type": "response_item", "payload": {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Hier ist meine Antwort darauf"}],
            }},
        ]
        filepath = self._write_codex_session(tmp_path, lines)
        result = parse_session_file(filepath, source_tool="codex")

        assert result is not None
        # System-Message soll gefiltert sein -> nur 2 Messages
        assert len(result.messages) == 2
        assert "AGENTS.md" not in result.messages[0]["content"]

    def test_codex_filters_developer_role(self, tmp_path):
        """Developer-Messages werden ignoriert."""
        lines = [
            {"type": "session_meta", "payload": {
                "id": "s3", "cwd": "/tmp", "originator": "codex-cli",
            }},
            {"type": "response_item", "payload": {
                "role": "developer",
                "content": [{"type": "input_text", "text": "System-Instruktionen hier"}],
            }},
            {"type": "response_item", "payload": {
                "role": "user",
                "content": [{"type": "input_text", "text": "User-Frage hier gestellt"}],
            }},
            {"type": "response_item", "payload": {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Assistant-Antwort darauf"}],
            }},
        ]
        filepath = self._write_codex_session(tmp_path, lines)
        result = parse_session_file(filepath, source_tool="codex")

        assert result is not None
        assert len(result.messages) == 2
        assert all(m["role"] in ("user", "assistant") for m in result.messages)

    def test_codex_token_extraction(self, tmp_path):
        """Token-Nutzung wird aus event_msg extrahiert."""
        lines = [
            {"type": "session_meta", "payload": {
                "id": "s4", "cwd": "/tmp", "originator": "codex-cli",
            }},
            {"type": "response_item", "payload": {
                "role": "user",
                "content": [{"type": "input_text", "text": "Kurze Frage hier"}],
            }},
            {"type": "response_item", "payload": {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Kurze Antwort hier"}],
            }},
            {"type": "event_msg", "payload": {
                "type": "token_count",
                "info": {"total_token_usage": {"total_tokens": 1500}},
            }},
        ]
        filepath = self._write_codex_session(tmp_path, lines)
        result = parse_session_file(filepath, source_tool="codex")

        assert result is not None
        assert result.total_tokens == 1500

    def test_codex_too_few_messages_returns_none(self, tmp_path):
        """Session mit weniger als 2 Messages gibt None zurueck."""
        lines = [
            {"type": "session_meta", "payload": {
                "id": "s5", "cwd": "/tmp", "originator": "codex-cli",
            }},
            {"type": "response_item", "payload": {
                "role": "user",
                "content": [{"type": "input_text", "text": "Nur eine Nachricht"}],
            }},
        ]
        filepath = self._write_codex_session(tmp_path, lines)
        result = parse_session_file(filepath, source_tool="codex")
        assert result is None

    def test_codex_message_length_truncation(self, tmp_path):
        """Lange Nachrichten werden auf MAX_MESSAGE_LENGTH gekuerzt."""
        long_text = "x" * 5000  # Ueber dem 3000-Zeichen-Limit
        lines = [
            {"type": "session_meta", "payload": {
                "id": "s6", "cwd": "/tmp", "originator": "codex-cli",
            }},
            {"type": "response_item", "payload": {
                "role": "user",
                "content": [{"type": "input_text", "text": long_text}],
            }},
            {"type": "response_item", "payload": {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Kurze Antwort darauf"}],
            }},
        ]
        filepath = self._write_codex_session(tmp_path, lines)
        result = parse_session_file(filepath, source_tool="codex")

        assert result is not None
        assert len(result.messages[0]["content"]) <= 3000

    def test_codex_empty_file_returns_none(self, tmp_path):
        """Leere Datei gibt None zurueck."""
        filepath = tmp_path / "empty.jsonl"
        filepath.write_text("")
        result = parse_session_file(filepath, source_tool="codex")
        assert result is None

    def test_codex_handles_text_block_type(self, tmp_path):
        """Generischer 'text' Block-Typ wird auch erkannt (neben input_text/output_text)."""
        lines = [
            {"type": "session_meta", "payload": {
                "id": "s7", "cwd": "/tmp", "originator": "codex-cli",
            }},
            {"type": "response_item", "payload": {
                "role": "user",
                "content": [{"type": "text", "text": "Frage mit text-Type"}],
            }},
            {"type": "response_item", "payload": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Antwort mit text-Type"}],
            }},
        ]
        filepath = self._write_codex_session(tmp_path, lines)
        result = parse_session_file(filepath, source_tool="codex")

        assert result is not None
        assert len(result.messages) == 2


# ─── Claude Session Parsing ──────────────────────────────────────

class TestParseClaudeSession:
    """Tests fuer das Claude Code JSONL-Format."""

    def _write_claude_session(self, tmp_path, lines):
        """Hilfsfunktion: Schreibt Claude-JSONL-Datei."""
        filepath = tmp_path / "claude-session.jsonl"
        content = "\n".join(json.dumps(l) for l in lines)
        filepath.write_text(content)
        return filepath

    def test_basic_claude_session(self, tmp_path):
        """Vollstaendige Claude-Session mit String-Content."""
        lines = [
            {"type": "user", "message": {"content": "Was ist FastAPI?"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "FastAPI ist ein modernes Python-Web-Framework."},
            ]}},
            {"type": "user", "message": {"content": "Und wie installiere ich es?"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Mit pip install fastapi uvicorn."},
            ]}},
        ]
        filepath = self._write_claude_session(tmp_path, lines)
        result = parse_session_file(filepath, source_tool="claude")

        assert result is not None
        assert result.source_tool == "claude"
        assert len(result.messages) == 4
        assert result.messages[0]["role"] == "user"
        assert "FastAPI" in result.messages[0]["content"]

    def test_claude_block_format_content(self, tmp_path):
        """Claude-User-Message mit Block-Array statt String."""
        lines = [
            {"type": "user", "message": {"content": [
                {"type": "text", "text": "Erklaere mir SQLAlchemy."},
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "SQLAlchemy ist ein ORM fuer Python."},
            ]}},
        ]
        filepath = self._write_claude_session(tmp_path, lines)
        result = parse_session_file(filepath, source_tool="claude")

        assert result is not None
        assert result.messages[0]["content"] == "Erklaere mir SQLAlchemy."

    def test_claude_too_few_messages_returns_none(self, tmp_path):
        """Session mit nur einer Message gibt None zurueck."""
        lines = [
            {"type": "user", "message": {"content": "Nur eine Nachricht"}},
        ]
        filepath = self._write_claude_session(tmp_path, lines)
        result = parse_session_file(filepath, source_tool="claude")
        assert result is None

    def test_claude_session_id_from_filename(self, tmp_path):
        """Session-ID wird aus dem Dateinamen extrahiert."""
        filepath = tmp_path / "abc-123-def.jsonl"
        lines = [
            {"type": "user", "message": {"content": "Frage eins hier"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Antwort eins hier"},
            ]}},
        ]
        filepath.write_text("\n".join(json.dumps(l) for l in lines))
        result = parse_session_file(filepath, source_tool="claude")

        assert result is not None
        assert result.session_id == "abc-123-def"

    def test_claude_skips_short_messages(self, tmp_path):
        """Messages unter MIN_MESSAGE_LENGTH (3 Zeichen) werden ignoriert."""
        lines = [
            {"type": "user", "message": {"content": "OK"}},  # Zu kurz
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Ja"},  # Zu kurz
            ]}},
            {"type": "user", "message": {"content": "Was macht diese Funktion hier?"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Sie berechnet die Token-Anzahl aus den Kosten."},
            ]}},
        ]
        filepath = self._write_claude_session(tmp_path, lines)
        result = parse_session_file(filepath, source_tool="claude")

        assert result is not None
        # Nur die 2 laengeren Messages sollen enthalten sein
        assert len(result.messages) == 2


# ─── Auto-Detection ──────────────────────────────────────────────

class TestAutoDetection:
    """Tests fuer die automatische Format-Erkennung."""

    def test_auto_detects_codex(self, tmp_path):
        """Auto-Detection erkennt Codex-Format korrekt."""
        lines = [
            {"type": "session_meta", "payload": {
                "id": "auto-1", "cwd": "/tmp", "originator": "codex-cli",
            }},
            {"type": "response_item", "payload": {
                "role": "user",
                "content": [{"type": "input_text", "text": "Auto-Detection Frage hier"}],
            }},
            {"type": "response_item", "payload": {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Auto-Detection Antwort hier"}],
            }},
        ]
        filepath = tmp_path / "session.jsonl"
        filepath.write_text("\n".join(json.dumps(l) for l in lines))
        result = parse_session_file(filepath)  # source_tool="auto"

        assert result is not None
        assert result.source_tool == "codex"

    def test_auto_detects_claude(self, tmp_path):
        """Auto-Detection erkennt Claude-Format korrekt."""
        lines = [
            {"type": "user", "message": {"content": "Auto-Detection Claude-Frage"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Auto-Detection Claude-Antwort"},
            ]}},
        ]
        filepath = tmp_path / "session.jsonl"
        filepath.write_text("\n".join(json.dumps(l) for l in lines))
        result = parse_session_file(filepath)

        assert result is not None
        assert result.source_tool == "claude"

    def test_nonexistent_file_returns_none(self):
        """Nicht-existierende Datei gibt None zurueck."""
        result = parse_session_file(Path("/nonexistent/session.jsonl"))
        assert result is None
