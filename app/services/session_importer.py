"""Session-Importer – Importiert .jsonl Sessions (Claude + Codex) in die DB."""

import json
import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.session_parser import parse_session_file

logger = logging.getLogger(__name__)


async def import_claude_sessions(
    db: AsyncSession,
    project_id: UUID,
    project_dir: Path,
    max_messages: int = 50,
) -> int:
    """
    Importiert Claude .jsonl-Sessions aus einem Projektverzeichnis.
    Ueberspringt bereits importierte Dateien (Dedup via source_file in Metadaten).
    Gibt die Anzahl importierter Sessions zurueck.
    """
    from app.models.session import Session as DreamlineSession

    jsonl_files = sorted(
        [f for f in project_dir.glob("*.jsonl") if not f.name.startswith("agent-")],
        key=lambda f: f.stat().st_mtime,
    )
    if not jsonl_files:
        return 0

    # Bereits importierte Dateien pruefen
    existing_files = await _get_imported_source_files(db, project_id)

    imported = 0
    for jsonl_file in jsonl_files:
        if jsonl_file.name in existing_files:
            continue

        try:
            parsed = parse_session_file(jsonl_file)
            if not parsed:
                continue

            session = DreamlineSession(
                project_id=project_id,
                messages_json=json.dumps(parsed.messages[-max_messages:], ensure_ascii=False),
                outcome="neutral",
                metadata_json=json.dumps({
                    "source": "jsonl-import",
                    "source_file": jsonl_file.name,
                    "session_id": parsed.session_id,
                    "source_tool": parsed.source_tool,
                }, ensure_ascii=False),
            )
            db.add(session)
            imported += 1
        except Exception as e:
            logger.warning("Session-Import fehlgeschlagen fuer %s: %s", jsonl_file.name, str(e)[:200])
            continue

    if imported > 0:
        await db.flush()
    return imported


async def import_codex_sessions(
    db: AsyncSession,
    project_id: UUID,
    local_path: str,
) -> int:
    """
    Importiert Codex-Sessions basierend auf dem cwd (Arbeitsverzeichnis).
    Scannt ~/.codex/sessions/ und filtert nach passendem Pfad.
    Gibt die Anzahl importierter Sessions zurueck.
    """
    from app.models.session import Session as DreamlineSession

    codex_sessions_dir = Path.home() / ".codex" / "sessions"
    if not codex_sessions_dir.exists():
        return 0

    normalized_path = local_path.replace("\\", "/").rstrip("/").lower()
    imported = 0

    for jsonl_file in sorted(codex_sessions_dir.rglob("*.jsonl")):
        try:
            parsed = parse_session_file(jsonl_file, source_tool="codex")
            if not parsed or not parsed.cwd:
                continue

            # Pruefen ob cwd zum Projekt passt
            if parsed.cwd.replace("\\", "/").rstrip("/").lower() != normalized_path:
                continue

            session = DreamlineSession(
                project_id=project_id,
                messages_json=json.dumps(parsed.messages, ensure_ascii=False),
                outcome="neutral",
                metadata_json=json.dumps({
                    "source": "codex-import",
                    "source_file": parsed.source_file,
                    "session_id": parsed.session_id,
                    "source_tool": "codex",
                    "cwd": parsed.cwd,
                }, ensure_ascii=False),
            )
            db.add(session)
            imported += 1
        except Exception as e:
            logger.warning("Codex-Session-Import fehlgeschlagen fuer %s: %s", jsonl_file.name, str(e)[:200])
            continue

    if imported > 0:
        await db.flush()
    return imported


async def _get_imported_source_files(db: AsyncSession, project_id: UUID) -> set[str]:
    """Laedt die bereits importierten Dateinamen aus den Session-Metadaten."""
    from app.models.session import Session as DreamlineSession

    result = await db.execute(
        select(DreamlineSession.metadata_json).where(
            DreamlineSession.project_id == project_id
        )
    )
    files = set()
    for row in result.scalars().all():
        if row:
            try:
                meta = json.loads(row)
                src = meta.get("source_file")
                if src:
                    files.add(src)
            except (json.JSONDecodeError, TypeError):
                pass
    return files
