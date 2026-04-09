"""
Dream-Sync – Synchronisiert Memory-Dateien vom Dateisystem zurück in die DB.

Wird nach dem Agent-Modus aufgerufen wenn die KI direkt Dateien geschrieben hat.
Parst Markdown-Dateien mit YAML-Frontmatter und aktualisiert die DB entsprechend.
"""

import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import Memory
from app.services.dream_prompts import CONSOLIDATE_LOCK_FILE, ENTRYPOINT_NAME, MAX_MEMORY_FILES

logger = logging.getLogger(__name__)


async def sync_files_to_db(
    db: AsyncSession,
    project_id: UUID,
    memory_dir: Path,
    existing_memories: list[Memory],
) -> tuple[int, int, int]:
    """
    Synchronisiert Memory-Dateien vom Dateisystem zurück in die Datenbank.

    Liest alle .md Dateien im Memory-Verzeichnis (außer MEMORY.md) und
    aktualisiert die DB entsprechend.

    Rückgabe: (created, updated, deleted)
    """
    if not memory_dir.exists():
        return 0, 0, 0

    created, updated, deleted = 0, 0, 0
    memory_index = {mem.key: mem for mem in existing_memories}
    seen_keys = set()

    # Memory-Dateien sortiert nach mtime laden, Cap bei MAX_MEMORY_FILES
    md_files = []
    for fp in memory_dir.glob("*.md"):
        if fp.name in (ENTRYPOINT_NAME, CONSOLIDATE_LOCK_FILE):
            continue
        try:
            md_files.append((fp.stat().st_mtime, fp))
        except OSError:
            continue
    md_files.sort(key=lambda x: x[0], reverse=True)
    md_files = md_files[:MAX_MEMORY_FILES]

    for _, filepath in md_files:
        try:
            content = filepath.read_text(encoding="utf-8")
            name, mem_type, confidence, body = _parse_frontmatter(filepath.stem, content)

            if not body:
                continue

            seen_keys.add(name)

            existing = memory_index.get(name)
            if existing:
                if existing.content != body or existing.memory_type != mem_type:
                    existing.content = body
                    existing.memory_type = mem_type
                    existing.confidence = min(max(confidence, 0.0), 1.0)
                    existing.source_count += 1
                    updated += 1
            else:
                new_mem = Memory(
                    project_id=project_id,
                    key=name,
                    content=body,
                    memory_type=mem_type,
                    confidence=min(max(confidence, 0.0), 1.0),
                    source_count=1,
                )
                db.add(new_mem)
                created += 1

        except (OSError, ValueError, UnicodeDecodeError) as e:
            logger.warning("Fehler beim Synchronisieren von %s: %s", filepath.name, e)

    # Gelöschte Memories erkennen
    for key, mem in memory_index.items():
        if key not in seen_keys:
            await db.delete(mem)
            deleted += 1

    return created, updated, deleted


def _parse_frontmatter(
    fallback_name: str,
    content: str,
) -> tuple[str, str, float, str]:
    """
    Parst YAML-Frontmatter aus einer Markdown-Datei.
    Rückgabe: (name, mem_type, confidence, body)
    """
    name = fallback_name
    mem_type = "project"
    confidence = 0.7
    body = content

    if content.startswith("---"):
        # Zeilenbasiertes Parsing: Suche das schließende "---" (nur am Zeilenanfang)
        lines = content.split("\n")
        end_idx = None
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                end_idx = i
                break
        if end_idx is not None:
            frontmatter = "\n".join(lines[1:end_idx])
            body = "\n".join(lines[end_idx + 1:]).strip()

            for line in frontmatter.strip().split("\n"):
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("type:"):
                    mem_type = line.split(":", 1)[1].strip()
                elif line.startswith("confidence:"):
                    try:
                        confidence = float(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass

    return name, mem_type, confidence, body
