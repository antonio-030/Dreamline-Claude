"""Tests fuer app/services/codex_watcher.py – Pfad-Normalisierung, Tracker, Sync-Logik."""

import json
import time
from pathlib import Path

import pytest

from app.services.codex_watcher import (
    _load_synced_set,
    _normalize_path,
    _save_synced,
)


# ─── Pfad-Normalisierung ─────────────────────────────────────────

class TestNormalizePath:
    """Tests fuer _normalize_path() – plattformuebergreifender Pfadvergleich."""

    def test_unix_path_unchanged(self):
        """Unix-Pfad wird nur zu Kleinbuchstaben."""
        assert _normalize_path("/Users/dev/project") == "/users/dev/project"

    def test_windows_backslashes_to_forward(self):
        """Windows-Backslashes werden zu Forward-Slashes."""
        assert _normalize_path("C:\\Users\\dev\\project") == "c:/users/dev/project"

    def test_trailing_slash_removed(self):
        """Trailing Slash wird entfernt."""
        assert _normalize_path("/Users/dev/project/") == "/users/dev/project"

    def test_case_insensitive(self):
        """Pfadvergleich ist case-insensitive."""
        assert _normalize_path("/Users/Dev/Project") == _normalize_path("/users/dev/project")

    def test_mixed_separators(self):
        """Gemischte Pfad-Trennzeichen werden normalisiert."""
        assert _normalize_path("C:\\Users/dev\\project/") == "c:/users/dev/project"

    def test_empty_path(self):
        """Leerer Pfad bleibt leer."""
        assert _normalize_path("") == ""

    def test_root_path(self):
        """Root-Pfad wird korrekt normalisiert."""
        assert _normalize_path("/") == ""  # rstrip("/") entfernt den einzigen Slash


# ─── Tracker (Synced-Set) ────────────────────────────────────────

class TestSyncedTracker:
    """Tests fuer _load_synced_set() und _save_synced()."""

    def test_load_empty_tracker(self, tmp_path):
        """Nicht-existierende Tracker-Datei gibt leeres Set zurueck."""
        tracker = tmp_path / ".dreamline-synced"
        result = _load_synced_set(tracker)
        assert result == set()

    def test_save_and_load(self, tmp_path):
        """Gespeicherte Eintraege werden korrekt geladen."""
        tracker = tmp_path / ".dreamline-synced"

        _save_synced(tracker, "session-001.jsonl")
        _save_synced(tracker, "session-002.jsonl")

        result = _load_synced_set(tracker)
        assert "session-001.jsonl" in result
        assert "session-002.jsonl" in result

    def test_save_appends(self, tmp_path):
        """Neue Eintraege werden angehaengt, nicht ueberschrieben."""
        tracker = tmp_path / ".dreamline-synced"

        _save_synced(tracker, "first.jsonl")
        _save_synced(tracker, "second.jsonl")

        content = tracker.read_text()
        assert "first.jsonl" in content
        assert "second.jsonl" in content

    def test_load_with_empty_lines(self, tmp_path):
        """Leere Zeilen in der Tracker-Datei werden toleriert."""
        tracker = tmp_path / ".dreamline-synced"
        tracker.write_text("session-001.jsonl\n\nsession-002.jsonl\n")

        result = _load_synced_set(tracker)
        assert "session-001.jsonl" in result
        assert "session-002.jsonl" in result
        # Leerer String kann im Set sein, ist aber harmlos
        assert len(result) >= 2

    def test_save_creates_parent_dirs(self, tmp_path):
        """Parent-Verzeichnisse werden erstellt wenn noetig."""
        tracker = tmp_path / "sub" / "dir" / ".dreamline-synced"
        _save_synced(tracker, "session.jsonl")

        assert tracker.exists()
        assert "session.jsonl" in tracker.read_text()


# ─── Integration: Codex-Watcher Workflow ─────────────────────────

class TestCodexWatcherWorkflow:
    """Integration-Tests fuer den Codex-Watcher-Workflow (ohne DB)."""

    def _create_codex_session_file(self, sessions_dir, filename, cwd="/tmp/project"):
        """Erstellt eine Codex-Session-JSONL-Datei."""
        filepath = sessions_dir / filename
        lines = [
            {"type": "session_meta", "payload": {
                "id": f"sess-{filename}", "cwd": cwd, "originator": "codex-cli",
            }},
            {"type": "response_item", "payload": {
                "role": "user",
                "content": [{"type": "input_text", "text": f"Frage aus {filename}"}],
            }},
            {"type": "response_item", "payload": {
                "role": "assistant",
                "content": [{"type": "output_text", "text": f"Antwort aus {filename}"}],
            }},
        ]
        filepath.write_text("\n".join(json.dumps(l) for l in lines))
        # Setze mtime in die Vergangenheit (> MIN_FILE_AGE_SECONDS)
        old_time = time.time() - 120
        import os
        os.utime(filepath, (old_time, old_time))
        return filepath

    def test_tracker_prevents_reimport(self, tmp_path):
        """Bereits gesynctete Dateien werden nicht erneut importiert."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        self._create_codex_session_file(sessions_dir, "session-001.jsonl")

        tracker = tmp_path / ".dreamline-synced"

        # Erste Runde: Datei ist neu
        synced = _load_synced_set(tracker)
        assert "session-001.jsonl" not in synced

        # Markiere als gesynct
        _save_synced(tracker, "session-001.jsonl")

        # Zweite Runde: Datei ist jetzt gesynct
        synced = _load_synced_set(tracker)
        assert "session-001.jsonl" in synced

    def test_path_matching_exact(self):
        """Exakter Pfad-Match funktioniert."""
        project_path = _normalize_path("/Users/dev/my-project")
        session_cwd = _normalize_path("/Users/dev/my-project")
        assert project_path == session_cwd

    def test_path_matching_subpath(self):
        """Subpfad-Match funktioniert (cwd ist Unterordner des Projekts)."""
        project_path = _normalize_path("/Users/dev/my-project")
        session_cwd = _normalize_path("/Users/dev/my-project/src/components")
        assert session_cwd.startswith(project_path)

    def test_path_matching_no_match(self):
        """Kein Match wenn Pfade verschieden sind."""
        project_path = _normalize_path("/Users/dev/project-a")
        session_cwd = _normalize_path("/Users/dev/project-b")
        assert not session_cwd.startswith(project_path)

    def test_path_matching_case_insensitive(self):
        """Pfad-Match ist case-insensitive."""
        project_path = _normalize_path("/Users/Dev/MyProject")
        session_cwd = _normalize_path("/users/dev/myproject")
        assert project_path == session_cwd

    def test_path_matching_windows_vs_unix(self):
        """Windows- und Unix-Pfade werden gleich normalisiert."""
        windows_path = _normalize_path("C:\\Users\\dev\\project")
        unix_path = _normalize_path("c:/Users/dev/project")
        assert windows_path == unix_path

    def test_codex_session_file_parseable(self, tmp_path):
        """Erstellte Codex-Session-Dateien sind vom Parser lesbar."""
        from app.services.session_parser import parse_session_file

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        filepath = self._create_codex_session_file(
            sessions_dir, "test-session.jsonl", cwd="/home/user/project",
        )

        result = parse_session_file(filepath, source_tool="codex")
        assert result is not None
        assert result.cwd == "/home/user/project"
        assert result.source_tool == "codex"
        assert len(result.messages) == 2
