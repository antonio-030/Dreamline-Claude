"""Zentrale Konfiguration aus Umgebungsvariablen und Pfadkonstanten."""

from pathlib import Path

from pydantic_settings import BaseSettings

# Gemeinsame Pfadkonstanten (nicht in den Settings, da sie vom Home-Dir abhängen)
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"


class Settings(BaseSettings):
    """Anwendungseinstellungen – werden aus .env oder Umgebungsvariablen geladen."""

    database_url: str = "postgresql+asyncpg://dreamline:dreamline_secret@db:5432/dreamline"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    dreamline_secret_key: str = "change-me-in-production"

    # Standard-Provider: "claude-abo", "anthropic", "openai", "ollama"
    default_ai_provider: str = "claude-abo"
    default_ai_model: str = "claude-sonnet-4-5-20250514"

    # Ollama-Konfiguration (lokale LLMs)
    # host.docker.internal zeigt auf den Docker-Host (wo Ollama läuft)
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_timeout: float = 120.0  # Ollama kann langsamer sein als Cloud-APIs
    ollama_modelfile_sync: bool = True  # Nach jedem Dream Custom-Modell mit Memories erstellen

    # autoDream Konfiguration (1:1 wie Claude Code tengu_onyx_plover)
    autodream_enabled: bool = True
    autodream_min_hours: int = 12  # Time-Gate: Mindestabstand zwischen Dreams
    autodream_min_sessions: int = 3  # Session-Gate: Mindestanzahl neuer Sessions
    autodream_scan_throttle_minutes: int = 10  # Scan-Throttle zwischen Checks

    # Quick-Extract Throttle (1:1 wie tengu_bramble_lintel)
    # Extraktion nur alle N Sessions statt nach jeder (spart API-Kosten)
    extract_every_n_sessions: int = 1  # 1 = jede Session, 2 = jede zweite, etc.

    # Codex-Watcher: Pollt ~/.codex/sessions/ auf neue Sessions
    codex_watcher_enabled: bool = False
    codex_watcher_interval_seconds: int = 120
    codex_sessions_dir: str = ""  # Leer = auto-detect (~/.codex/sessions/)

    # CORS (kommaseparierte Origins, leer = nur localhost)
    cors_origins: str = ""

    # Worker-Einstellungen
    dream_check_interval_minutes: int = 60

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

# Sicherheitsprüfung: Default Admin-Key durch zufälligen ersetzen
if settings.dreamline_secret_key == "change-me-in-production":
    import secrets as _secrets
    import logging as _logging
    _generated_key = _secrets.token_hex(32)
    settings.dreamline_secret_key = _generated_key
    _logging.getLogger(__name__).warning(
        "DREAMLINE_SECRET_KEY nicht gesetzt! "
        "Generierter Einmal-Key (geht bei Neustart verloren): %s",
        _generated_key,
    )
