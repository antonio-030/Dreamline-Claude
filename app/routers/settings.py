"""Settings-Endpunkt – zur Laufzeit änderbare Konfiguration über die Web-UI."""

import json
import logging
import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_admin_key
from app.config import settings
from app.database import get_db
from app.models.runtime_settings import RuntimeSetting

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

# Env-Variablen die bei Änderung sofort gesetzt werden müssen
_ENV_SYNC_KEYS = {
    "claude_oauth_token": "CLAUDE_CODE_OAUTH_TOKEN",
}

# Editierbare Settings mit Typ, Beschreibung und Grenzen
SETTING_DEFINITIONS = {
    # autoDream
    "autodream_enabled": {"type": "bool", "label": "autoDream aktiviert", "group": "dream"},
    "autodream_min_hours": {"type": "int", "label": "Min. Stunden zwischen Dreams", "group": "dream", "min": 1, "max": 720},
    "autodream_min_sessions": {"type": "int", "label": "Min. Sessions für Dream", "group": "dream", "min": 1, "max": 1000},
    "autodream_scan_throttle_minutes": {"type": "int", "label": "Scan-Throttle (Minuten)", "group": "dream", "min": 1, "max": 60},
    "dream_check_interval_minutes": {"type": "int", "label": "Dream-Check-Intervall (Minuten)", "group": "dream", "min": 5, "max": 1440},
    "default_dream_provider": {"type": "str", "label": "Standard Dream-Provider", "group": "dream"},
    "default_dream_model": {"type": "str", "label": "Standard Dream-Modell", "group": "dream"},

    # Quick-Extract
    "extract_every_n_sessions": {"type": "int", "label": "Extrahiere alle N Sessions", "group": "extract", "min": 1, "max": 100},
    "extract_min_confidence": {"type": "float", "label": "Min. Konfidenz für Extract", "group": "extract", "min": 0.1, "max": 1.0},
    "extract_mutual_exclusion_seconds": {"type": "int", "label": "Mutual-Exclusion (Sekunden)", "group": "extract", "min": 5, "max": 300},

    # Ollama
    "ollama_base_url": {"type": "str", "label": "Ollama URL", "group": "ollama"},
    "ollama_timeout": {"type": "float", "label": "Ollama Timeout (Sekunden)", "group": "ollama", "min": 10, "max": 600},
    "ollama_modelfile_sync": {"type": "bool", "label": "Ollama Auto-Sync", "group": "ollama"},

    # Codex-Watcher
    "codex_watcher_enabled": {"type": "bool", "label": "Codex-Watcher aktiviert", "group": "codex"},
    "codex_watcher_interval_seconds": {"type": "int", "label": "Codex Check-Intervall (Sekunden)", "group": "codex", "min": 30, "max": 3600},

    # Claude-Abo Auth
    "claude_oauth_token": {"type": "secret", "label": "Claude OAuth-Token", "group": "auth"},
    "claude_oauth_token_saved_at": {"type": "str", "label": "Token gespeichert am", "group": "auth", "hidden": True},

    # KI-Client
    "ai_max_retries": {"type": "int", "label": "Max. API-Retries", "group": "ai", "min": 0, "max": 10},
    "ai_cli_timeout_seconds": {"type": "int", "label": "CLI Timeout (Sekunden)", "group": "ai", "min": 30, "max": 600},
    "ai_max_output_tokens": {"type": "int", "label": "Max. Output-Tokens", "group": "ai", "min": 256, "max": 16384},
    "ai_agent_max_turns": {"type": "int", "label": "Max. Agent-Turns", "group": "ai", "min": 1, "max": 100},

    # Tuning
    "smart_recall_limit": {"type": "int", "label": "Smart-Recall Limit", "group": "tuning", "min": 10, "max": 1000},
    "session_exclusion_seconds": {"type": "int", "label": "Session-Exclusion (Sekunden)", "group": "tuning", "min": 0, "max": 300},
    "session_import_max_messages": {"type": "int", "label": "Max. Messages pro Import", "group": "tuning", "min": 5, "max": 200},
    "prompt_truncation_chars": {"type": "int", "label": "Prompt-Trunkierung (Zeichen)", "group": "tuning", "min": 500, "max": 10000},
    "lock_stale_hours": {"type": "int", "label": "Lock-Stale-Schwelle (Stunden)", "group": "tuning", "min": 1, "max": 24},

    # Dreamline
    "dreamline_base_url": {"type": "str", "label": "Dreamline URL", "group": "system"},
    "hook_timeout_ms": {"type": "int", "label": "Hook-Timeout (ms)", "group": "system", "min": 1000, "max": 30000},
}


def _get_current_value(key: str) -> str:
    """Liest den aktuellen Wert aus der app.config.settings oder os.environ."""
    # Secrets zuerst aus os.environ lesen (z.B. CLAUDE_CODE_OAUTH_TOKEN)
    env_name = _ENV_SYNC_KEYS.get(key)
    if env_name:
        return os.environ.get(env_name, "")
    val = getattr(settings, key, None)
    if val is None:
        return ""
    return str(val).lower() if isinstance(val, bool) else str(val)


def _mask_secret(value: str) -> str:
    """Maskiert einen Secret-Wert für die API-Antwort."""
    if not value or len(value) < 16:
        return "***" if value else ""
    return value[:12] + "..." + value[-4:]


def _apply_value(key: str, value: str) -> None:
    """Setzt einen Wert in der laufenden settings-Instanz + ggf. os.environ."""
    definition = SETTING_DEFINITIONS.get(key)
    if not definition:
        return

    # Secrets nur in os.environ setzen, nicht in der Settings-Instanz
    env_name = _ENV_SYNC_KEYS.get(key)
    if env_name:
        if value:
            os.environ[env_name] = value
        return

    typ = definition["type"]
    if typ == "bool":
        setattr(settings, key, value.lower() in ("true", "1", "yes"))
    elif typ == "int":
        setattr(settings, key, int(value))
    elif typ == "float":
        setattr(settings, key, float(value))
    else:
        setattr(settings, key, value)


@router.get("")
async def get_settings(
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """Gibt alle konfigurierbaren Einstellungen mit aktuellem Wert zurück."""
    # DB-Overrides laden
    result = await db.execute(select(RuntimeSetting))
    db_overrides = {r.key: r.value for r in result.scalars().all()}

    output = []
    for key, definition in SETTING_DEFINITIONS.items():
        if definition.get("hidden"):
            continue
        current = db_overrides.get(key, _get_current_value(key))
        display_value = _mask_secret(current) if definition["type"] == "secret" else current
        entry = {
            "key": key,
            "value": display_value,
            "has_value": bool(current),
            "default": "" if definition["type"] == "secret" else _get_current_value(key),
            "has_override": key in db_overrides,
            **{k: v for k, v in definition.items() if k != "hidden"},
        }
        # Token-Ablaufdatum anhängen
        if key == "claude_oauth_token":
            entry["saved_at"] = db_overrides.get("claude_oauth_token_saved_at", "")
        output.append(entry)
    return output


class SettingsUpdate(BaseModel):
    """Request zum Aktualisieren von Einstellungen."""
    settings: dict[str, str] = Field(..., description="Key-Value-Paare der zu ändernden Settings")


@router.patch("")
async def update_settings(
    data: SettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """Aktualisiert Einstellungen zur Laufzeit (ohne Neustart)."""
    updated = []
    errors = []

    for key, value in data.settings.items():
        if key not in SETTING_DEFINITIONS:
            errors.append(f"Unbekannte Einstellung: {key}")
            continue

        definition = SETTING_DEFINITIONS[key]
        typ = definition["type"]

        # Validierung
        try:
            if typ == "int":
                int_val = int(value)
                if "min" in definition and int_val < definition["min"]:
                    errors.append(f"{key}: Wert {int_val} unter Minimum {definition['min']}")
                    continue
                if "max" in definition and int_val > definition["max"]:
                    errors.append(f"{key}: Wert {int_val} über Maximum {definition['max']}")
                    continue
            elif typ == "float":
                float_val = float(value)
                if "min" in definition and float_val < definition["min"]:
                    errors.append(f"{key}: Wert {float_val} unter Minimum {definition['min']}")
                    continue
                if "max" in definition and float_val > definition["max"]:
                    errors.append(f"{key}: Wert {float_val} über Maximum {definition['max']}")
                    continue
        except ValueError:
            errors.append(f"{key}: Ungültiger Wert '{value}' für Typ {typ}")
            continue

        # In DB speichern
        existing = await db.execute(
            select(RuntimeSetting).where(RuntimeSetting.key == key)
        )
        row = existing.scalar_one_or_none()
        if row:
            row.value = value
        else:
            db.add(RuntimeSetting(key=key, value=value))

        # Sofort in laufende Instanz übernehmen
        _apply_value(key, value)
        updated.append(key)
        is_sensitive = definition.get("type") == "secret" or any(w in key.lower() for w in ("key", "token", "secret", "password"))
        logger.info("Einstellung geändert: %s = %s", key, "***" if is_sensitive else value)

        # Bei Token-Speicherung automatisch Timestamp setzen
        if key == "claude_oauth_token" and value:
            from datetime import datetime, timezone
            ts = datetime.now(timezone.utc).isoformat()
            ts_existing = await db.execute(
                select(RuntimeSetting).where(RuntimeSetting.key == "claude_oauth_token_saved_at")
            )
            ts_row = ts_existing.scalar_one_or_none()
            if ts_row:
                ts_row.value = ts
            else:
                db.add(RuntimeSetting(key="claude_oauth_token_saved_at", value=ts))

    await db.flush()
    return {"updated": updated, "errors": errors}


@router.delete("/reset")
async def reset_settings(
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """Setzt alle Runtime-Overrides zurück auf .env-/Default-Werte."""
    from sqlalchemy import delete
    await db.execute(delete(RuntimeSetting))
    await db.flush()

    # Settings-Instanz neu laden wäre ideal, aber Pydantic-Settings
    # liest nur beim Start aus .env. Neustart nötig für vollständigen Reset.
    return {"message": "Alle Overrides gelöscht. Neustart empfohlen für vollständigen Reset."}
