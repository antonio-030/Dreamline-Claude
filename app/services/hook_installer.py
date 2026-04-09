"""Hook-Installer – Installiert den Dreamline Stop-Hook in Claude Code Projekten."""

import json
import logging
from pathlib import Path

from app.config import settings
from app.services.utils import escape_js_string

logger = logging.getLogger(__name__)

# Hook-Template aus externer Datei
_HOOK_TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "dreamline-sync.cjs.tpl"


def load_hook_template() -> str:
    """Laedt das Hook-Template aus der Template-Datei."""
    return _HOOK_TEMPLATE_PATH.read_text(encoding="utf-8")


def install_hook(
    local_path: Path,
    api_key: str,
    project_name: str,
    dreamline_url: str,
) -> bool:
    """
    Installiert den Dreamline Stop-Hook in einem Claude Code Projekt.
    Erstellt die Hook-Datei und registriert sie in settings.json.
    Gibt True zurueck bei Erfolg, False bei Fehler.
    """
    try:
        claude_dir = local_path / ".claude"
        helpers_dir = claude_dir / "helpers"
        settings_path = claude_dir / "settings.json"

        # Helpers-Verzeichnis erstellen falls noetig
        helpers_dir.mkdir(parents=True, exist_ok=True)

        # Hook-Datei schreiben
        hook_content = load_hook_template().format(
            dreamline_url=dreamline_url,
            api_key=api_key,
            project_name=escape_js_string(project_name),
        )
        hook_path = helpers_dir / "dreamline-sync.cjs"
        hook_path.write_text(hook_content)
        logger.info("Hook-Datei geschrieben: %s", hook_path)

        # settings.json aktualisieren
        _register_hook_in_settings(settings_path)

        return True

    except Exception as e:
        logger.error("Hook-Installation fehlgeschlagen: %s", str(e))
        return False


def _register_hook_in_settings(settings_path: Path) -> None:
    """Registriert den Hook-Befehl in der Claude settings.json."""
    if settings_path.exists():
        config = json.loads(settings_path.read_text())
    else:
        config = {}

    hooks = config.setdefault("hooks", {})
    stop_hooks = hooks.setdefault("Stop", [{"hooks": []}])

    hook_cmd = "node %CLAUDE_PROJECT_DIR%/.claude/helpers/dreamline-sync.cjs"
    existing_hooks = stop_hooks[0].get("hooks", [])
    already_exists = any(h.get("command") == hook_cmd for h in existing_hooks)

    if not already_exists:
        existing_hooks.append({
            "type": "command",
            "command": hook_cmd,
            "timeout": settings.hook_timeout_ms,
        })
        stop_hooks[0]["hooks"] = existing_hooks
        settings_path.write_text(json.dumps(config, indent=2, ensure_ascii=False))
        logger.info("Hook in settings.json registriert: %s", settings_path)
