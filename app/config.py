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

    # Tuning-Parameter (vorher hardcoded, jetzt konfigurierbar)
    smart_recall_limit: int = 200  # Max Memories für Smart-Recall
    session_exclusion_seconds: int = 60  # Frische Sessions vom Dream ausschließen
    extract_mutual_exclusion_seconds: int = 30  # Wartezeit nach Agent-Writes
    session_import_max_messages: int = 50  # Max Messages pro Session-Import
    codex_min_file_age_seconds: int = 30  # Mindest-Dateialter für Codex-Import
    codex_unmatched_expiry_days: int = 7  # Ungematchte Codex-Sessions nach N Tagen ignorieren

    # KI-Client Einstellungen (vorher hardcoded in ai_client.py)
    ai_max_retries: int = 3  # Retry-Versuche bei API-Fehlern
    ai_backoff_base_seconds: float = 2.0  # Exponentieller Backoff-Basis
    ai_cli_timeout_seconds: int = 300  # Timeout für CLI-Aufrufe (Claude/Codex)
    ai_max_output_tokens: int = 4096  # Max Output-Tokens für API-Aufrufe
    ai_agent_max_turns: int = 20  # Max Turns für Dream-Agent
    ai_ollama_temperature: float = 0.3  # Temperatur für Ollama-Modelle

    # Dream-Prompts (vorher hardcoded in dream_prompts.py)
    max_memory_files: int = 200  # Max Memory-Dateien pro Projekt
    max_entrypoint_lines: int = 200  # Max Zeilen in MEMORY.md
    max_entrypoint_kb: int = 25  # Max Größe von MEMORY.md in KB
    prompt_truncation_chars: int = 2000  # Max Zeichen pro Session/Content im Prompt
    project_context_max_chars: int = 5000  # Max Zeichen für Projekt-Kontext

    # Lock-Konfiguration (vorher hardcoded in dream_locks.py)
    lock_stale_hours: int = 1  # Stale-Lock-Schwelle in Stunden

    # Quick-Extract (vorher hardcoded in extractor.py)
    extract_min_confidence: float = 0.8  # Minimale Konfidenz für Quick-Extract

    # Session-Parser (vorher hardcoded in session_parser.py)
    max_message_length: int = 3000  # Max Zeichen pro Nachricht
    max_messages_per_session: int = 30  # Max Nachrichten pro Session

    # Dreamline-URL für Hooks
    dreamline_base_url: str = "http://localhost:8100"
    hook_timeout_ms: int = 8000  # Timeout für Hook-Aufrufe in Millisekunden

    # Session-Preview
    session_preview_length: int = 150  # Vorschau-Länge in Zeichen

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

# Sicherheitsprüfung: Default Admin-Key durch zufälligen ersetzen
if settings.dreamline_secret_key == "change-me-in-production":
    import secrets as _secrets
    import logging as _logging
    _generated_key = _secrets.token_hex(32)
    settings.dreamline_secret_key = _generated_key
    _logging.getLogger(__name__).warning(
        "DREAMLINE_SECRET_KEY nicht gesetzt! Generierter Einmal-Key: %s...%s "
        "(geht bei Neustart verloren – bitte in .env setzen)",
        _generated_key[:8], _generated_key[-4:],
    )
