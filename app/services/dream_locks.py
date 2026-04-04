"""
Dual-Lock-Strategie für parallele Dream-Verhinderung.

Dreamline verwendet ZWEI unabhängige Lock-Mechanismen:

1. DreamLock (Datenbank-Tabelle)
   - Verhindert parallele Dream-Worker für dasselbe Projekt.
   - Veraltete Locks (>1 Stunde) werden automatisch übernommen.

2. .consolidate-lock (Datei im Memory-Verzeichnis)
   - Respektiert Claude Code's eigenes Lock-System (consolidationLock.ts).
   - Enthält die PID des Holders und mtime als lastConsolidatedAt Marker.

Quellen: consolidationLock.ts, autoDream.ts
"""

import logging
import os
import platform
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import CLAUDE_PROJECTS_DIR
from app.models.dream import Dream, DreamLock
from app.models.project import Project

logger = logging.getLogger(__name__)

# Maximale Lock-Dauer bevor ein Lock als veraltet gilt (1 Stunde)
LOCK_STALE_THRESHOLD = timedelta(hours=1)

# Claude Code Konstanten (aus consolidationLock.ts)
CONSOLIDATE_LOCK_FILE = ".consolidate-lock"

# 1 Stunde in Millisekunden — identisch zu Claude Code consolidationLock.ts:19
HOLDER_STALE_MS = 60 * 60 * 1000


def find_memory_dir(project_name: str) -> Path | None:
    """
    Findet das Memory-Verzeichnis eines Projekts im Claude-Projektordner.
    Nutzt exakten Match auf das letzte Pfad-Segment um Cross-Project-Zugriff
    zu verhindern (z.B. "App" darf nicht "MyApp" matchen).
    """
    if not CLAUDE_PROJECTS_DIR.exists():
        return None
    name_lower = project_name.lower()

    # 1. Exakter Match auf letztes Segment (z.B. "C--Users--Desktop-Techlogia" → "Techlogia")
    for entry in CLAUDE_PROJECTS_DIR.iterdir():
        if not entry.is_dir():
            continue
        # Letztes Segment nach "--" ist der Projektname
        segments = entry.name.split("--")
        last_segment = segments[-1].lower() if segments else ""
        # Bindestriche im letzten Segment sind Teil des Namens
        if last_segment == name_lower:
            return entry / "memory"

    # 2. Fallback: Suffix-Match (für Sonderfälle wie Bindestriche im Projektnamen)
    for entry in CLAUDE_PROJECTS_DIR.iterdir():
        if entry.is_dir() and entry.name.lower().endswith(name_lower):
            return entry / "memory"

    return None


def check_consolidate_lock(memory_dir: Path) -> bool:
    """
    Prüft den .consolidate-lock von Claude Code.
    Gibt True zurück wenn KEIN aktiver Lock besteht (wir dürfen dreamen).
    Gibt False zurück wenn Claude Code gerade selbst dreamt.
    """
    lock_path = memory_dir / CONSOLIDATE_LOCK_FILE
    if not lock_path.exists():
        return True

    try:
        stat = lock_path.stat()
        age_ms = (time.time() - stat.st_mtime) * 1000

        if age_ms > HOLDER_STALE_MS:
            return True

        try:
            pid_str = lock_path.read_text().strip()
            pid = int(pid_str)
            os.kill(pid, 0)
            logger.info("Claude Code Dream-Lock aktiv (PID %d, %ds alt). Überspringe.", pid, int(age_ms / 1000))
            return False
        except (ValueError, ProcessLookupError, PermissionError):
            return True

    except OSError:
        return True


def write_consolidate_lock(memory_dir: Path) -> float | None:
    """
    Schreibt den .consolidate-lock (mtime = lastConsolidatedAt).
    Gibt die vorherige mtime zurück (für Rollback bei Fehler).
    Gibt None zurück wenn die Race-Condition verloren wurde.
    """
    lock_path = memory_dir / CONSOLIDATE_LOCK_FILE
    prior_mtime = 0.0
    try:
        if lock_path.exists():
            prior_mtime = lock_path.stat().st_mtime
        memory_dir.mkdir(parents=True, exist_ok=True)
        my_pid = f"dreamline-{os.getpid()}"
        lock_path.write_text(my_pid)

        try:
            verify = lock_path.read_text().strip()
            if verify != my_pid:
                logger.info("Consolidate-Lock Race verloren: erwartet '%s', gelesen '%s'", my_pid, verify)
                return None
        except OSError:
            return None

        logger.info("Consolidate-Lock erworben: %s (prior_mtime: %s)", lock_path, prior_mtime)
    except OSError as e:
        logger.warning("Consolidate-Lock schreiben fehlgeschlagen: %s", e)
        return None
    return prior_mtime


def release_consolidate_lock(memory_dir: Path) -> None:
    """Gibt den .consolidate-lock frei (Body leeren, mtime bleibt)."""
    lock_path = memory_dir / CONSOLIDATE_LOCK_FILE
    try:
        lock_path.write_text("")
    except OSError:
        pass


def rollback_consolidate_lock(memory_dir: Path, prior_mtime: float) -> None:
    """Rollback bei Dream-Fehler: mtime zurücksetzen auf vorherigen Wert."""
    lock_path = memory_dir / CONSOLIDATE_LOCK_FILE
    try:
        if prior_mtime == 0:
            lock_path.unlink(missing_ok=True)
            return
        lock_path.write_text("")
        os.utime(lock_path, (prior_mtime, prior_mtime))
        logger.info("Consolidate-Lock Rollback: mtime zurück auf %s", prior_mtime)
    except OSError as e:
        logger.warning("Consolidate-Lock Rollback fehlgeschlagen: %s", e)


def snapshot_memory_dir(memory_dir: Path) -> dict[str, float]:
    """Erstellt einen Snapshot des Memory-Verzeichnisses (Dateiname → mtime)."""
    snapshot = {}
    if not memory_dir.exists():
        return snapshot
    try:
        for filepath in memory_dir.iterdir():
            if filepath.is_file():
                snapshot[filepath.name] = filepath.stat().st_mtime
    except OSError:
        pass
    return snapshot


def validate_agent_writes(
    memory_dir: Path,
    pre_snapshot: dict[str, float],
) -> tuple[list[str], list[str]]:
    """
    Post-Dream Validierung: Prüft welche Dateien der Agent geändert/erstellt hat.
    Gibt zurück: (valid_files, violation_files)
    """
    valid_files = []
    violation_files = []

    if not memory_dir.exists():
        return valid_files, violation_files

    try:
        for filepath in memory_dir.iterdir():
            if not filepath.is_file():
                continue
            current_mtime = filepath.stat().st_mtime
            prev_mtime = pre_snapshot.get(filepath.name)

            if prev_mtime is None:
                valid_files.append(filepath.name)
            elif current_mtime != prev_mtime:
                valid_files.append(filepath.name)

        parent = memory_dir.parent
        if parent.exists():
            for filepath in parent.iterdir():
                if filepath == memory_dir:
                    continue
                if filepath.is_file() and filepath.suffix in (".md", ".txt", ".json", ".py", ".ts", ".js"):
                    if time.time() - filepath.stat().st_mtime < 60:
                        violation_files.append(str(filepath))

    except OSError as e:
        logger.warning("Post-Dream Validierung fehlgeschlagen: %s", e)

    return valid_files, violation_files


# ─── Datenbank-Lock Verwaltung ────────────────────────────────────────

async def acquire_lock(db: AsyncSession, project_id: UUID) -> bool:
    """
    Versucht einen Dream-Lock (DB) für das Projekt zu erwerben.
    Veraltete Locks (>1 Stunde) werden übernommen.
    """
    worker_id = platform.node() or "unknown-worker"
    now = datetime.now(timezone.utc)
    stale_threshold = now - LOCK_STALE_THRESHOLD

    stmt = select(DreamLock).where(DreamLock.project_id == project_id)
    result = await db.execute(stmt)
    existing_lock = result.scalar_one_or_none()

    if existing_lock:
        lock_time = existing_lock.locked_at
        if lock_time and lock_time.tzinfo is None:
            lock_time = lock_time.replace(tzinfo=timezone.utc)

        if lock_time and lock_time > stale_threshold:
            logger.info(
                "Projekt %s: Dream-Lock aktiv (von %s seit %s). Überspringe.",
                project_id, existing_lock.locked_by, existing_lock.locked_at,
            )
            return False

        logger.warning(
            "Projekt %s: Veralteter Lock von %s (%s). Wird übernommen.",
            project_id, existing_lock.locked_by, existing_lock.locked_at,
        )
        existing_lock.locked_by = worker_id
        existing_lock.locked_at = now
        await db.flush()
        return True

    new_lock = DreamLock(project_id=project_id, locked_by=worker_id)
    db.add(new_lock)
    await db.flush()
    logger.info("Projekt %s: Dream-Lock erworben von %s.", project_id, worker_id)
    return True


async def release_lock(db: AsyncSession, project_id: UUID) -> None:
    """Gibt den Dream-Lock für ein Projekt frei."""
    stmt = delete(DreamLock).where(DreamLock.project_id == project_id)
    await db.execute(stmt)
    await db.flush()
    logger.info("Projekt %s: Dream-Lock freigegeben.", project_id)


def make_skipped_dream(project_id: UUID, summary: str) -> Dream:
    """Erstellt ein übersprungenes Dream-Objekt."""
    return Dream(
        project_id=project_id,
        sessions_reviewed=0,
        summary=summary,
        status="skipped",
        duration_ms=0,
    )


async def acquire_dual_locks(
    db: AsyncSession,
    project_id: UUID,
) -> tuple[bool, Path | None, float]:
    """
    Erwirbt beide Locks der Dual-Lock-Strategie.

    Rückgabe: (success, memory_dir, prior_mtime)
    """
    lock_acquired = await acquire_lock(db, project_id)
    if not lock_acquired:
        return False, None, 0.0

    project_name = (
        await db.execute(select(Project.name).where(Project.id == project_id))
    ).scalar() or ""
    memory_dir = find_memory_dir(project_name)

    if memory_dir and not check_consolidate_lock(memory_dir):
        await release_lock(db, project_id)
        return False, memory_dir, 0.0

    prior_mtime = 0.0
    if memory_dir:
        lock_result = write_consolidate_lock(memory_dir)
        if lock_result is None:
            await release_lock(db, project_id)
            return False, memory_dir, 0.0
        prior_mtime = lock_result

    return True, memory_dir, prior_mtime
