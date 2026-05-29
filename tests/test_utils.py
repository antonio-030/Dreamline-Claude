"""Tests fuer app/services/utils.py — Pfad-Dekodierung und Hilfsfunktionen."""

from app.services.utils import (
    _decode_without_filesystem,
    decode_claude_dir_name,
    guess_display_name,
    truncate_text,
    escape_js_string,
)


# ─── _decode_without_filesystem (Docker-Fallback) ────────────────

class TestDecodeWithoutFilesystem:
    """Heuristische Pfad-Dekodierung ohne Dateisystem-Zugriff."""

    def test_simple_project(self):
        parts = ["Users", "antonio", "Desktop", "SentinelClaw"]
        assert _decode_without_filesystem(parts) == "/Users/antonio/Desktop/SentinelClaw"

    def test_hyphenated_project_name(self):
        """Bindestriche im Projektnamen bleiben erhalten."""
        parts = ["Users", "antonio", "Desktop", "Dreamline", "Claude"]
        assert _decode_without_filesystem(parts) == "/Users/antonio/Desktop/Dreamline-Claude"

    def test_nested_dev_apps(self):
        parts = ["Users", "antonio", "Desktop", "Dev", "Apps", "Techlogia"]
        assert _decode_without_filesystem(parts) == "/Users/antonio/Desktop/Dev/Apps/Techlogia"

    def test_server_subdir(self):
        parts = ["Users", "antonio", "Desktop", "Server", "TimeAM"]
        assert _decode_without_filesystem(parts) == "/Users/antonio/Desktop/Server/TimeAM"

    def test_root_level_app(self):
        parts = ["app"]
        assert _decode_without_filesystem(parts) == "/app"

    def test_root_level_tmp(self):
        parts = ["tmp"]
        assert _decode_without_filesystem(parts) == "/tmp"

    def test_private_tmp(self):
        """'private' ist ein bekanntes Top-Level-Dir."""
        parts = ["private", "tmp"]
        assert _decode_without_filesystem(parts) == "/private/tmp"

    def test_home_linux(self):
        parts = ["home", "user", "projects", "MyApp"]
        assert _decode_without_filesystem(parts) == "/home/user/projects-MyApp"

    def test_deeply_nested(self):
        parts = ["Users", "antonio", "Desktop", "Dev", "Apps", "My", "Project"]
        assert _decode_without_filesystem(parts) == "/Users/antonio/Desktop/Dev/Apps/My-Project"


# ─── guess_display_name ──────────────────────────────────────────

class TestGuessDisplayName:
    """Anzeigename aus Claude-Projektordner ableiten."""

    def test_simple(self):
        assert guess_display_name("-Users-antonio-Desktop-SentinelClaw") == "SentinelClaw"

    def test_hyphenated(self):
        assert guess_display_name("-Users-antonio-Desktop-Dreamline-Claude") in (
            "Dreamline-Claude", "Claude"  # Abhaengig von Filesystem-Verfuegbarkeit
        )

    def test_windows_path(self):
        # Windows-Dekodierung: '--' wird zu '/', einzelnes '-' bleibt
        # C--Users-max--Desktop-MeinProjekt -> C:/Users-max/Desktop-MeinProjekt
        assert guess_display_name("C--Users-max--Desktop-MeinProjekt") == "Desktop-MeinProjekt"

    def test_root_level(self):
        assert guess_display_name("-app") == "app"


# ─── truncate_text ───────────────────────────────────────────────

class TestTruncateText:
    def test_short_text(self):
        assert truncate_text("kurz", 100) == "kurz"

    def test_long_text(self):
        result = truncate_text("a" * 200, 50)
        assert len(result) == 50
        assert result.endswith("... [truncated]")

    def test_none_input(self):
        assert truncate_text(None, 100) is None

    def test_empty(self):
        assert truncate_text("", 100) == ""


# ─── escape_js_string ───────────────────────────────────────────

class TestEscapeJsString:
    def test_quotes(self):
        assert escape_js_string('say "hello"') == 'say \\"hello\\"'

    def test_backtick(self):
        assert escape_js_string("test `code`") == "test \\`code\\`"

    def test_template_literal(self):
        assert escape_js_string("${var}") == "\\${var}"

    def test_script_tag(self):
        assert escape_js_string("</script>") == "<\\/script>"
