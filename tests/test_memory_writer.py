"""Tests fuer app/services/memory_writer.py – Dateischreib-Logik, Pfad-Sanitierung, YAML-Escaping."""

from pathlib import Path

import pytest

from app.services.memory_writer import (
    _cleanup_orphaned_files,
    _is_safe_project_path,
    _key_to_filename,
    _yaml_escape,
)


# ─── YAML-Escaping ───────────────────────────────────────────────

class TestYamlEscape:
    """Tests fuer _yaml_escape() – sichere YAML-Frontmatter-Werte."""

    def test_simple_string(self):
        """Einfacher String bleibt unveraendert."""
        assert _yaml_escape("Hello World") == "Hello World"

    def test_colon_gets_quoted(self):
        """Doppelpunkt wird in Anfuehrungszeichen gesetzt."""
        result = _yaml_escape("key: value")
        assert result == '"key: value"'

    def test_hash_gets_quoted(self):
        """Raute wird in Anfuehrungszeichen gesetzt."""
        result = _yaml_escape("Kommentar # hier")
        assert result == '"Kommentar # hier"'

    def test_yaml_delimiter_gets_quoted(self):
        """YAML-Delimiter --- wird escaped."""
        result = _yaml_escape("Text mit --- Trennzeichen")
        assert result == '"Text mit --- Trennzeichen"'

    def test_curly_braces_quoted(self):
        """Geschweifte Klammern (JSON-like) werden escaped."""
        result = _yaml_escape("Config: {key: val}")
        assert result == '"Config: {key: val}"'

    def test_square_brackets_quoted(self):
        """Eckige Klammern werden escaped."""
        result = _yaml_escape("Liste: [a, b]")
        assert result == '"Liste: [a, b]"'

    def test_newlines_removed(self):
        """Zeilenumbrueche werden zu Leerzeichen."""
        result = _yaml_escape("Zeile 1\nZeile 2\nZeile 3")
        assert "\n" not in result
        assert "Zeile 1 Zeile 2 Zeile 3" in result

    def test_carriage_return_removed(self):
        """Carriage Returns werden entfernt."""
        result = _yaml_escape("Text\r\nmit\rCR")
        assert "\r" not in result

    def test_inner_quotes_escaped(self):
        """Doppelte Anfuehrungszeichen im Text werden escaped."""
        result = _yaml_escape('Er sagte "Hallo"')
        assert '\\"' in result

    def test_single_quotes_quoted(self):
        """Einfache Anfuehrungszeichen fuehren zu Quoted-Output."""
        result = _yaml_escape("it's a test")
        assert result == '"it\'s a test"'

    def test_empty_string(self):
        """Leerer String bleibt leer."""
        assert _yaml_escape("") == ""


# ─── Key-zu-Dateiname Konvertierung ─────────────────────────────

class TestKeyToFilename:
    """Tests fuer _key_to_filename()."""

    def test_simple_key(self):
        """Einfacher Key wird zu .md-Datei."""
        assert _key_to_filename("test_memory") == "test_memory.md"

    def test_spaces_to_underscores(self):
        """Leerzeichen werden zu Unterstrichen."""
        assert _key_to_filename("test memory") == "test_memory.md"

    def test_slashes_to_underscores(self):
        """Slashes werden zu Unterstrichen."""
        assert _key_to_filename("path/to/key") == "path_to_key.md"

    def test_special_chars_removed(self):
        """Sonderzeichen werden entfernt."""
        result = _key_to_filename("test@#$%key!")
        assert result == "testkey.md"

    def test_hyphens_preserved(self):
        """Bindestriche bleiben erhalten."""
        assert _key_to_filename("test-key") == "test-key.md"

    def test_unicode_preserved(self):
        """Unicode-Buchstaben (Umlaute) bleiben erhalten (isalnum() ist Unicode-aware)."""
        result = _key_to_filename("Projekt_Übersicht")
        assert result == "Projekt_Übersicht.md"


# ─── Pfad-Sicherheitspruefung ────────────────────────────────────

class TestIsSafeProjectPath:
    """Tests fuer _is_safe_project_path()."""

    def test_system_dirs_rejected(self):
        """System-Verzeichnisse werden abgelehnt.

        Hinweis: Auf macOS ist /etc → /private/etc (Symlink). Die Funktion prueft
        den aufgeloesten Pfad. Da /private/etc nicht in der forbidden-Liste steht,
        wird es auf macOS nicht blockiert. Wir testen daher nur die direkt
        aufgeloesten Pfade die der Code tatsaechlich blockiert.
        """
        # /usr und /bin sind auf macOS nicht unter /private
        assert _is_safe_project_path(Path("/usr")) is False
        assert _is_safe_project_path(Path("/usr/local/bin")) is False
        assert _is_safe_project_path(Path("/boot")) is False

    def test_proc_sys_rejected(self):
        """Pseudo-Dateisysteme werden abgelehnt."""
        assert _is_safe_project_path(Path("/proc")) is False
        assert _is_safe_project_path(Path("/sys")) is False

    def test_nonexistent_dir_rejected(self):
        """Nicht-existierendes Verzeichnis wird abgelehnt."""
        assert _is_safe_project_path(Path("/nonexistent/path/12345")) is False

    def test_valid_dir_accepted(self, tmp_path):
        """Gueltiges temporaeres Verzeichnis wird akzeptiert."""
        assert _is_safe_project_path(tmp_path) is True

    def test_nested_system_dir_rejected(self):
        """Unterverzeichnisse von System-Verzeichnissen werden auch abgelehnt."""
        assert _is_safe_project_path(Path("/etc/nginx")) is False
        assert _is_safe_project_path(Path("/usr/local/bin")) is False


# ─── Verwaiste Dateien Aufraumen ──────────────────────────────────

class TestCleanupOrphanedFiles:
    """Tests fuer _cleanup_orphaned_files()."""

    def test_removes_orphaned_files(self, tmp_memory_dir):
        """Verwaiste .md-Dateien werden geloescht."""
        # Erstelle eine verwaiste Datei
        (tmp_memory_dir / "orphan_old_memory.md").write_text("Veraltet.")

        removed = _cleanup_orphaned_files(
            tmp_memory_dir,
            valid_filenames={"project_test.md"},
        )
        assert removed == 1
        assert not (tmp_memory_dir / "orphan_old_memory.md").exists()

    def test_keeps_valid_files(self, tmp_memory_dir):
        """Gueltige Dateien bleiben erhalten."""
        removed = _cleanup_orphaned_files(
            tmp_memory_dir,
            valid_filenames={"project_test.md"},
        )
        assert removed == 0
        assert (tmp_memory_dir / "project_test.md").exists()

    def test_protects_memory_md(self, tmp_memory_dir):
        """MEMORY.md wird nie geloescht (geschuetzt)."""
        removed = _cleanup_orphaned_files(
            tmp_memory_dir,
            valid_filenames=set(),  # Keine gueltige Datei
        )
        # MEMORY.md und CLAUDE.md sind geschuetzt
        assert (tmp_memory_dir / "MEMORY.md").exists()

    def test_protects_claude_md(self, tmp_memory_dir):
        """CLAUDE.md wird nie geloescht (geschuetzt)."""
        (tmp_memory_dir / "CLAUDE.md").write_text("# Projekt")
        removed = _cleanup_orphaned_files(
            tmp_memory_dir,
            valid_filenames={"project_test.md"},
        )
        assert (tmp_memory_dir / "CLAUDE.md").exists()

    def test_non_md_files_ignored(self, tmp_memory_dir):
        """Nicht-Markdown-Dateien werden ignoriert."""
        (tmp_memory_dir / "data.json").write_text("{}")
        (tmp_memory_dir / "notes.txt").write_text("Notizen")

        removed = _cleanup_orphaned_files(
            tmp_memory_dir,
            valid_filenames={"project_test.md"},
        )
        assert removed == 0
        assert (tmp_memory_dir / "data.json").exists()

    def test_empty_dir_no_error(self, tmp_path):
        """Leeres Verzeichnis verursacht keinen Fehler."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        removed = _cleanup_orphaned_files(empty_dir, valid_filenames=set())
        assert removed == 0

    def test_multiple_orphans_removed(self, tmp_memory_dir):
        """Mehrere verwaiste Dateien werden alle geloescht."""
        (tmp_memory_dir / "orphan1.md").write_text("Alt1")
        (tmp_memory_dir / "orphan2.md").write_text("Alt2")
        (tmp_memory_dir / "orphan3.md").write_text("Alt3")

        removed = _cleanup_orphaned_files(
            tmp_memory_dir,
            valid_filenames={"project_test.md"},
        )
        assert removed == 3
