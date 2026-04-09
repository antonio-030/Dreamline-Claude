"""
Codex Session-Watcher – Pollt ~/.codex/sessions/ auf neue JSONL-Dateien
und importiert sie in die Dreamline-DB.

Da Codex kein Hook-System wie Claude Code hat, übernimmt dieser
Background-Worker die Session-Erfassung per Dateisystem-Polling.

Projekt-Zuordnung: Jede Codex-Session enthält in der ersten Zeile
(session_meta) das Arbeitsverzeichnis (cwd). Dieses wird gegen
Project.local_path in der DB gematcht.
"""

import json
import logging
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models.project import Project
from app.models.session import Session as DreamlineSession
from app.services.session_parser import parse_session_file

logger = logging.getLogger(__name__)

# Mindest-Dateialter in Sekunden bevor sie gelesen wird
# (verhindert Lesen während Codex noch schreibt)
from app.config import settings as _settings
MIN_FILE_AGE_SECONDS = _settings.codex_min_file_age_seconds

# Tracker-Datei: Liste bereits gesyncter Session-Dateien
SYNCED_TRACKER_FILENAME = ".dreamline-synced"


def _get_codex_sessions_dir() -> Path:
    """Gibt das Codex-Sessions-Verzeichnis zurück."""
    if settings.codex_sessions_dir:
        return Path(settings.codex_sessions_dir)
    return Path.home() / ".codex" / "sessions"


def _load_synced_set(tracker_path: Path) -> set[str]:
    """Lädt die Liste bereits gesyncter Dateien."""
    if not tracker_path.exists():
        return set()
    try:
        return set(tracker_path.read_text(encoding="utf-8").strip().split("\n"))
    except (OSError, IOError):
        return set()


def _save_synced(tracker_path: Path, filename: str):
    """Fügt eine Datei zur Synced-Liste hinzu."""
    try:
        tracker_path.parent.mkdir(parents=True, exist_ok=True)
        with tracker_path.open("a", encoding="utf-8") as f:
            f.write(filename + "\n")
    except (OSError, IOError) as e:
        logger.warning("Kann Tracker nicht aktualisieren: %s", e)


def _normalize_path(p: str) -> str:
    """Normalisiert einen Pfad für Vergleiche (Kleinbuchstaben, Forward-Slashes)."""
    return p.replace("\\", "/").rstrip("/").lower()


async def sync_codex_sessions():
    """
    Hauptfunktion: Scannt Codex-Sessions und importiert neue in die DB.
    Wird vom Scheduler periodisch aufgerufen.
    """
    sessions_dir = _get_codex_sessions_dir()
    if not sessions_dir.exists():
        logger.debug("Codex-Sessions-Verzeichnis existiert nicht: %s", sessions_dir)
        return

    codex_home = Path.home() / ".codex"
    tracker_path = codex_home / SYNCED_TRACKER_FILENAME
    synced = _load_synced_set(tracker_path)

    # Alle JSONL-Dateien rekursiv finden
    jsonl_files = sorted(sessions_dir.rglob("*.jsonl"))
    if not jsonl_files:
        return

    now = time.time()
    new_files = []

    for f in jsonl_files:
        # Bereits gesynct?
        if f.name in synced:
            continue
        # Noch zu jung? (Codex schreibt vielleicht noch)
        file_age = now - f.stat().st_mtime
        if file_age < MIN_FILE_AGE_SECONDS:
            continue
        new_files.append(f)

    if not new_files:
        return

    logger.info("Codex-Watcher: %d neue Session-Dateien gefunden", len(new_files))

    async with async_session() as db:
        try:
            # Alle aktiven Projekte mit source_tool codex oder both laden
            stmt = select(Project).where(
                Project.is_active == True,
                Project.source_tool.in_(["codex", "both"]),
            )
            result = await db.execute(stmt)
            projects = list(result.scalars().all())

            if not projects:
                logger.debug("Codex-Watcher: Keine Projekte mit source_tool=codex/both")
                return

            # Projekt-Lookup nach normalisiertem local_path
            project_map: dict[str, Project] = {}
            for p in projects:
                if p.local_path:
                    project_map[_normalize_path(p.local_path)] = p

            imported = 0
            for f in new_files:
                try:
                    parsed = parse_session_file(f, source_tool="codex")
                    if not parsed:
                        # Datei ist klar ungültig (zu wenig Messages) -- als synced markieren
                        _save_synced(tracker_path, f.name)
                        continue

                    if not parsed.cwd:
                        # Kein CWD extrahiert -- ungültige Session
                        _save_synced(tracker_path, f.name)
                        continue

                    # Projekt über cwd matchen
                    normalized_cwd = _normalize_path(parsed.cwd)
                    project = project_map.get(normalized_cwd)

                    if not project:
                        # Auch Subpfade prüfen (z.B. cwd ist ein Unterordner)
                        for path_key, proj in project_map.items():
                            if normalized_cwd.startswith(path_key):
                                project = proj
                                break

                    if not project:
                        # Kein passendes Projekt -- NICHT als synced markieren,
                        # damit ein späterer Retry möglich ist wenn das Projekt
                        # nachträglich angelegt wird
                        logger.debug(
                            "Codex-Watcher: Kein Projekt für cwd=%s gefunden (wird erneut geprüft)", parsed.cwd,
                        )
                        continue

                    # Session in DB erstellen
                    session = DreamlineSession(
                        project_id=project.id,
                        messages_json=json.dumps(parsed.messages, ensure_ascii=False),
                        outcome="neutral",
                        metadata_json=json.dumps({
                            "source": "codex-watcher",
                            "source_file": parsed.source_file,
                            "session_id": parsed.session_id,
                            "source_tool": "codex",
                            "cwd": parsed.cwd,
                            "total_tokens": parsed.total_tokens,
                        }, ensure_ascii=False),
                    )
                    db.add(session)
                    imported += 1
                    _save_synced(tracker_path, f.name)

                except (json.JSONDecodeError, ValueError, OSError) as e:
                    # Transiente Fehler: NICHT als synced markieren (Retry beim nächsten Scan)
                    logger.warning("Codex-Watcher: Fehler bei %s: %s", f.name, e)

            if imported > 0:
                await db.commit()
                logger.info("Codex-Watcher: %d Sessions importiert", imported)

                # Quick-Extract triggern für neue Sessions
                await _trigger_quick_extract(db, projects)

        except Exception as e:
            logger.error("Codex-Watcher Fehler: %s", e)
            await db.rollback()


async def _trigger_quick_extract(db: AsyncSession, projects: list[Project]):
    """Triggert Quick-Extract für Projekte mit neuen Codex-Sessions."""
    try:
        from app.services.extractor import quick_extract
        from app.models.session import Session as DLSession

        for project in projects:
            if not project.quick_extract:
                continue

            # Neueste unkonsolidierte Session des Projekts laden
            stmt = (
                select(DLSession)
                .where(DLSession.project_id == project.id)
                .where(DLSession.is_consolidated == False)  # noqa: E712
                .order_by(DLSession.created_at.desc())
                .limit(1)
            )
            result = await db.execute(stmt)
            latest_session = result.scalar_one_or_none()
            if latest_session:
                await quick_extract(
                    db, latest_session, project.id,
                    project.ai_provider, project.ai_model,
                )
    except (ImportError, RuntimeError) as e:
        logger.warning("Quick-Extract Trigger fehlgeschlagen: %s", e)
