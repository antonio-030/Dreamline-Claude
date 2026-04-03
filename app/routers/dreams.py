"""Dream-Endpunkte – manuelles Auslösen, Verlauf und Löschen der Konsolidierungen."""

from uuid import UUID as PyUUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_project
from app.database import get_db
from app.models.dream import Dream
from app.models.project import Project
from app.schemas.dream import DreamResponse, DreamTriggerResponse
from app.services.dreamer import run_dream

router = APIRouter(prefix="/api/v1/dreams", tags=["dreams"])
limiter = Limiter(key_func=get_remote_address)


@router.post("", response_model=DreamTriggerResponse)
@limiter.limit("2/minute")
async def trigger_dream(
    request: Request,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Löst manuell einen Konsolidierungslauf (Dream) aus. Max 2/Minute."""
    dream = await run_dream(
        db=db,
        project_id=project.id,
        ai_provider=project.ai_provider,
        ai_model=project.ai_model,
    )

    if dream.sessions_reviewed == 0:
        return DreamTriggerResponse(
            message="Keine neuen Sessions zum Konsolidieren.",
            dream=DreamResponse.model_validate(dream),
        )

    return DreamTriggerResponse(
        message=f"Dream abgeschlossen: {dream.memories_created} erstellt, "
                f"{dream.memories_updated} aktualisiert, {dream.memories_deleted} gelöscht.",
        dream=DreamResponse.model_validate(dream),
    )


@router.get("", response_model=list[DreamResponse])
async def list_dreams(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Listet den Verlauf aller Konsolidierungsläufe auf."""
    stmt = (
        select(Dream)
        .where(Dream.project_id == project.id)
        .order_by(Dream.created_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/status")
async def dream_status(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """
    Gibt den aktuellen Dream-Status zurück.
    Wie Claude Code DreamTask: phase, filesTouched, sessionsReviewing.
    """
    from app.models.dream import DreamLock

    # Prüfe ob ein Dream gerade läuft (DreamLock aktiv)
    lock_stmt = select(DreamLock).where(DreamLock.project_id == project.id)
    lock_result = await db.execute(lock_stmt)
    lock = lock_result.scalar_one_or_none()

    is_running = False
    if lock:
        from datetime import datetime, timedelta, timezone
        lock_time = lock.locked_at
        if lock_time and lock_time.tzinfo is None:
            lock_time = lock_time.replace(tzinfo=timezone.utc)
        stale = datetime.now(timezone.utc) - timedelta(hours=1)
        is_running = lock_time is not None and lock_time > stale

    # Letzter Dream
    last_stmt = (
        select(Dream)
        .where(Dream.project_id == project.id)
        .order_by(Dream.created_at.desc())
        .limit(1)
    )
    last_result = await db.execute(last_stmt)
    last_dream = last_result.scalar_one_or_none()

    # Unkonsolidierte Sessions zählen
    from sqlalchemy import func
    from app.models.session import Session
    count_stmt = (
        select(func.count())
        .select_from(Session)
        .where(Session.project_id == project.id)
        .where(Session.is_consolidated == False)
    )
    pending = (await db.execute(count_stmt)).scalar() or 0

    return {
        "is_running": is_running,
        "phase": "updating" if is_running else ("idle" if not last_dream else "completed"),
        "pending_sessions": pending,
        "last_dream": {
            "id": str(last_dream.id),
            "status": last_dream.status,
            "sessions_reviewed": last_dream.sessions_reviewed,
            "memories_created": last_dream.memories_created,
            "memories_updated": last_dream.memories_updated,
            "memories_deleted": last_dream.memories_deleted,
            "duration_ms": last_dream.duration_ms,
            "created_at": last_dream.created_at.isoformat() if last_dream.created_at else None,
            "summary": last_dream.summary[:300] if last_dream.summary else None,
        } if last_dream else None,
    }


@router.delete("/{dream_id}")
async def delete_dream(
    dream_id: PyUUID,
    reset_sessions: bool = Query(False, description="Sessions zurücksetzen für erneute Verarbeitung"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """
    Löscht einen Dream-Eintrag.
    Mit reset_sessions=true werden die zugehörigen Sessions auf
    'nicht konsolidiert' zurückgesetzt — sie werden beim nächsten
    Dream erneut verarbeitet.
    """
    stmt = (
        select(Dream)
        .where(Dream.id == dream_id)
        .where(Dream.project_id == project.id)
    )
    result = await db.execute(stmt)
    dream = result.scalar_one_or_none()

    if not dream:
        raise HTTPException(status_code=404, detail="Dream nicht gefunden")

    # Sessions zurücksetzen wenn gewünscht
    sessions_reset = 0
    if reset_sessions:
        from sqlalchemy import update
        from app.models.session import Session
        reset_result = await db.execute(
            update(Session)
            .where(Session.project_id == project.id)
            .where(Session.is_consolidated == True)
            .values(is_consolidated=False)
        )
        sessions_reset = reset_result.rowcount

    await db.delete(dream)
    return {
        "message": f"Dream gelöscht{f', {sessions_reset} Sessions zurückgesetzt' if sessions_reset else ''}.",
        "sessions_reset": sessions_reset,
    }
