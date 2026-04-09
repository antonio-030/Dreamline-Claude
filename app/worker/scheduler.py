"""
Hintergrund-Worker mit 4-stufigem Gate-System (1:1 wie Claude Code autoDream).

Gate-Reihenfolge (billigste Prüfung zuerst):
1. Enabled-Gate: Ist autoDream global aktiviert?
2. Time-Gate: Stunden seit letztem Dream >= minHours (1 DB-Query)
3. Scan-Throttle: Letzter Scan >= 10 Minuten her (closure-Variable)
4. Session-Gate: Genügend neue Sessions seit letztem Dream
5. Lock-Gate: .consolidate-lock + DB DreamLock

Referenz: claude-code-study/services/autoDream/autoDream.ts
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select

from app.config import settings
from app.database import async_session
from app.models.dream import Dream
from app.models.project import Project
from app.models.session import Session
from app.models.memory import Memory
from app.services.dreamer import run_dream

logger = logging.getLogger(__name__)

# Globaler Scheduler
scheduler = AsyncIOScheduler()

# Scan-Throttle: Pro Projekt letzte Scan-Zeit (wie autoDream.ts lastSessionScanAt)
_last_scan_at: dict[str, float] = {}


async def check_and_run_dreams():
    """
    4-stufiges Gate-System für jeden aktiven Projekt.
    Billigste Prüfung zuerst – die meisten Projekte steigen früh aus.
    """
    # Gate 0: Global Kill-Switch
    if not settings.autodream_enabled:
        return

    async with async_session() as db:
        try:
            stmt = select(Project).where(Project.is_active == True)
            result = await db.execute(stmt)
            projects = result.scalars().all()

            for project in projects:
                try:
                    await _check_project_gates(db, project)
                except Exception as e:
                    logger.error(
                        "Fehler beim Dream-Check für Projekt %s: %s",
                        project.name, str(e),
                    )

            await db.commit()
        except Exception as e:
            logger.error("Fehler im Dream-Check-Zyklus: %s", str(e))
            await db.rollback()


async def _check_project_gates(db, project: Project):
    """
    4-stufiges Gate-System pro Projekt (1:1 wie autoDream.ts:125-190).
    """
    pid = str(project.id)

    # ── Gate 1: Time-Gate (billigste Prüfung: 1 DB-Query) ──
    last_dream_stmt = (
        select(Dream.created_at)
        .where(Dream.project_id == project.id)
        .where(Dream.status == "completed")
        .order_by(Dream.created_at.desc())
        .limit(1)
    )
    last_dream_result = await db.execute(last_dream_stmt)
    last_dream_at = last_dream_result.scalar()

    if last_dream_at:
        last_at = last_dream_at.replace(tzinfo=timezone.utc) if last_dream_at.tzinfo is None else last_dream_at
        hours_since = (datetime.now(timezone.utc) - last_at).total_seconds() / 3600
    else:
        hours_since = float("inf")  # Noch nie geträumt

    min_hours = project.dream_interval_hours or settings.autodream_min_hours
    if hours_since < min_hours:
        return  # Noch nicht genug Zeit vergangen

    # ── Gate 2: Scan-Throttle (10 Minuten zwischen Scans) ──
    now = time.time()
    throttle_seconds = settings.autodream_scan_throttle_minutes * 60
    last_scan = _last_scan_at.get(pid, 0)
    if now - last_scan < throttle_seconds:
        logger.debug(
            "Projekt %s: Scan-Throttle – letzter Scan vor %ds",
            project.name, int(now - last_scan),
        )
        return
    _last_scan_at[pid] = now

    # ── Gate 3: Session-Gate (zähle neue Sessions seit letztem Dream) ──
    since_time = last_dream_at if last_dream_at else datetime.min.replace(tzinfo=timezone.utc)
    if since_time.tzinfo is None:
        since_time = since_time.replace(tzinfo=timezone.utc)

    count_stmt = (
        select(func.count())
        .select_from(Session)
        .where(Session.project_id == project.id)
        .where(Session.is_consolidated == False)
        .where(Session.created_at > since_time)
    )
    count_result = await db.execute(count_stmt)
    session_count = count_result.scalar() or 0

    min_sessions = project.min_sessions_for_dream or settings.autodream_min_sessions
    if session_count < min_sessions:
        logger.debug(
            "Projekt %s: Session-Gate – %d Sessions, brauche %d",
            project.name, session_count, min_sessions,
        )
        return

    # ── Gate 4: Lock-Gate (wird innerhalb run_dream geprüft) ──
    # run_dream() prüft DreamLock (DB) + .consolidate-lock (Dateisystem)

    # Alle Gates bestanden – Dream starten
    logger.info(
        "Projekt %s: Alle Gates bestanden (%.1fh seit letztem Dream, %d Sessions). Starte Dream...",
        project.name, hours_since, session_count,
    )

    dream_provider = project.dream_provider or project.ai_provider
    dream_model = project.dream_model or project.ai_model
    await run_dream(
        db=db,
        project_id=project.id,
        ai_provider=dream_provider,
        ai_model=dream_model,
    )


async def check_project_dream(project_id: str):
    """
    Per-Session Dream-Trigger: Wird nach jeder eingehenden Session aufgerufen.
    1:1 wie Claude Code executeAutoDream() in postSamplingHooks.

    Prüft alle Gates für das spezifische Projekt und startet ggf. einen Dream.
    """
    from uuid import UUID as PyUUID

    if not settings.autodream_enabled:
        return

    async with async_session() as db:
        try:
            stmt = select(Project).where(
                Project.id == PyUUID(project_id),
                Project.is_active == True,
            )
            result = await db.execute(stmt)
            project = result.scalar_one_or_none()

            if not project:
                return

            await _check_project_gates(db, project)
            await db.commit()
        except Exception as e:
            logger.error(
                "Per-Session Dream-Check Fehler für Projekt %s: %s",
                project_id, str(e),
            )
            await db.rollback()


def start_scheduler():
    """Startet den Hintergrund-Scheduler."""
    scheduler.add_job(
        check_and_run_dreams,
        "interval",
        minutes=settings.dream_check_interval_minutes,
        id="dream_checker",
        replace_existing=True,
    )

    # Codex-Watcher: Pollt ~/.codex/sessions/ auf neue Session-Dateien
    if settings.codex_watcher_enabled:
        from app.services.codex_watcher import sync_codex_sessions
        scheduler.add_job(
            sync_codex_sessions,
            "interval",
            seconds=settings.codex_watcher_interval_seconds,
            id="codex_watcher",
            replace_existing=True,
        )
        logger.info(
            "Codex-Watcher aktiviert (Intervall: %ds)",
            settings.codex_watcher_interval_seconds,
        )

    # Memory-TTL-Cleanup: Abgelaufene Memories täglich entfernen
    scheduler.add_job(
        _cleanup_expired_memories,
        "interval",
        hours=24,
        id="memory_ttl_cleanup",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Dream-Scheduler gestartet (Check-Intervall: %d Min, "
        "autoDream: %s, minHours: %d, minSessions: %d, Scan-Throttle: %d Min)",
        settings.dream_check_interval_minutes,
        settings.autodream_enabled,
        settings.autodream_min_hours,
        settings.autodream_min_sessions,
        settings.autodream_scan_throttle_minutes,
    )


async def _cleanup_expired_memories():
    """Entfernt abgelaufene Memories (TTL/Expiration)."""
    from sqlalchemy import delete
    async with async_session() as db:
        now = datetime.now(timezone.utc)
        stmt = delete(Memory).where(
            Memory.expires_at.isnot(None),
            Memory.expires_at < now,
        )
        result = await db.execute(stmt)
        await db.commit()
        if result.rowcount > 0:
            logger.info("TTL-Cleanup: %d abgelaufene Memories entfernt", result.rowcount)


def stop_scheduler():
    """Stoppt den Hintergrund-Scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Dream-Scheduler gestoppt.")
