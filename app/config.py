"""Zentrale Konfiguration aus Umgebungsvariablen."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Anwendungseinstellungen – werden aus .env oder Umgebungsvariablen geladen."""

    database_url: str = "postgresql+asyncpg://dreamline:dreamline_secret@db:5432/dreamline"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    dreamline_secret_key: str = "change-me-in-production"

    # Standard-Provider: "claude-abo" (nutzt Abo), "anthropic" (API-Key), "openai" (API-Key)
    default_ai_provider: str = "claude-abo"
    default_ai_model: str = "claude-sonnet-4-5-20250514"

    # autoDream Konfiguration (1:1 wie Claude Code tengu_onyx_plover)
    autodream_enabled: bool = True
    autodream_min_hours: int = 12  # Time-Gate: Mindestabstand zwischen Dreams
    autodream_min_sessions: int = 3  # Session-Gate: Mindestanzahl neuer Sessions
    autodream_scan_throttle_minutes: int = 10  # Scan-Throttle zwischen Checks

    # Quick-Extract Throttle (1:1 wie tengu_bramble_lintel)
    # Extraktion nur alle N Sessions statt nach jeder (spart API-Kosten)
    extract_every_n_sessions: int = 1  # 1 = jede Session, 2 = jede zweite, etc.

    # Worker-Einstellungen
    dream_check_interval_minutes: int = 60

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
