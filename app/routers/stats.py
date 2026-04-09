"""Statistik-Endpunkt – aggregierte Kennzahlen über das Projekt."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_project
from app.database import get_db
from app.models.dream import Dream
from app.models.memory import Memory
from app.models.project import Project
from app.models.session import Session

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])
limiter = Limiter(key_func=get_remote_address)


class MemoryTypeCount(BaseModel):
    """Anzahl Erinnerungen pro Typ."""
    memory_type: str
    count: int


class StatsResponse(BaseModel):
    """Aggregierte Projektstatistiken."""
    total_sessions: int
    sessions_consolidated: int
    sessions_unconsolidated: int
    total_memories: int
    memories_by_type: list[MemoryTypeCount]
    total_dreams: int
    dreams_completed: int
    dreams_failed: int
    average_confidence: float | None
    last_dream_at: datetime | None
    next_dream_estimated: datetime | None


@router.get("", response_model=StatsResponse)
@limiter.limit("60/minute")
async def get_stats(
    request: Request,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Gibt aggregierte Statistiken für das aktuelle Projekt zurück."""
    project_id = project.id

    # Sessions zählen (gesamt, konsolidiert, unkonsolidiert)
    session_total_stmt = (
        select(func.count()).select_from(Session).where(Session.project_id == project_id)
    )
    session_consolidated_stmt = (
        select(func.count())
        .select_from(Session)
        .where(Session.project_id == project_id)
        .where(Session.is_consolidated == True)
    )

    # Memories zählen (gesamt + nach Typ)
    memory_total_stmt = (
        select(func.count()).select_from(Memory).where(Memory.project_id == project_id)
    )
    memory_by_type_stmt = (
        select(Memory.memory_type, func.count())
        .where(Memory.project_id == project_id)
        .group_by(Memory.memory_type)
    )

    # Durchschnittliche Konfidenz
    avg_confidence_stmt = (
        select(func.avg(Memory.confidence)).where(Memory.project_id == project_id)
    )

    # Dreams zählen (gesamt, completed, failed)
    dream_total_stmt = (
        select(func.count()).select_from(Dream).where(Dream.project_id == project_id)
    )
    dream_completed_stmt = (
        select(func.count())
        .select_from(Dream)
        .where(Dream.project_id == project_id)
        .where(Dream.status == "completed")
    )
    dream_failed_stmt = (
        select(func.count())
        .select_from(Dream)
        .where(Dream.project_id == project_id)
        .where(Dream.status == "failed")
    )

    # Letzter Dream
    last_dream_stmt = (
        select(Dream.created_at)
        .where(Dream.project_id == project_id)
        .where(Dream.status == "completed")
        .order_by(Dream.created_at.desc())
        .limit(1)
    )

    # Alle Abfragen ausführen
    total_sessions = (await db.execute(session_total_stmt)).scalar() or 0
    consolidated = (await db.execute(session_consolidated_stmt)).scalar() or 0
    total_memories = (await db.execute(memory_total_stmt)).scalar() or 0
    avg_confidence = (await db.execute(avg_confidence_stmt)).scalar()
    total_dreams = (await db.execute(dream_total_stmt)).scalar() or 0
    dreams_completed = (await db.execute(dream_completed_stmt)).scalar() or 0
    dreams_failed = (await db.execute(dream_failed_stmt)).scalar() or 0
    last_dream_at = (await db.execute(last_dream_stmt)).scalar()

    # Memory-Typen aufschlüsseln
    type_result = await db.execute(memory_by_type_stmt)
    memories_by_type = [
        MemoryTypeCount(memory_type=row[0], count=row[1])
        for row in type_result.all()
    ]

    # Nächsten Dream schätzen
    next_dream_estimated = None
    if last_dream_at:
        last_dream_utc = last_dream_at
        if last_dream_utc.tzinfo is None:
            last_dream_utc = last_dream_utc.replace(tzinfo=timezone.utc)
        next_dream_estimated = last_dream_utc + timedelta(hours=project.dream_interval_hours)
    elif total_sessions > 0:
        # Noch kein Dream gelaufen – schätzen basierend auf fehlenden Sessions
        unconsolidated = total_sessions - consolidated
        if unconsolidated >= project.min_sessions_for_dream:
            # Bedingungen schon erfüllt, nächster Check-Zyklus
            next_dream_estimated = datetime.now(timezone.utc) + timedelta(minutes=5)

    return StatsResponse(
        total_sessions=total_sessions,
        sessions_consolidated=consolidated,
        sessions_unconsolidated=total_sessions - consolidated,
        total_memories=total_memories,
        memories_by_type=memories_by_type,
        total_dreams=total_dreams,
        dreams_completed=dreams_completed,
        dreams_failed=dreams_failed,
        average_confidence=round(avg_confidence, 4) if avg_confidence is not None else None,
        last_dream_at=last_dream_at,
        next_dream_estimated=next_dream_estimated,
    )
