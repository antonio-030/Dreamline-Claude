"""Memories-Endpunkt – listet und löscht konsolidierte Erinnerungen eines Projekts."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_project
from app.database import get_db
from app.models.memory import Memory
from app.models.project import Project
from app.schemas.memory import MemoryResponse

router = APIRouter(prefix="/api/v1/memories", tags=["memories"])


@router.get("", response_model=list[MemoryResponse])
async def list_memories(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Gibt alle Erinnerungen des aktuellen Projekts zurück."""
    stmt = (
        select(Memory)
        .where(Memory.project_id == project.id)
        .order_by(Memory.key)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Löscht eine einzelne Erinnerung."""
    from uuid import UUID as PyUUID
    stmt = (
        select(Memory)
        .where(Memory.id == PyUUID(memory_id))
        .where(Memory.project_id == project.id)
    )
    result = await db.execute(stmt)
    memory = result.scalar_one_or_none()

    if not memory:
        raise HTTPException(status_code=404, detail="Erinnerung nicht gefunden")

    await db.delete(memory)
    await db.flush()
    return {"message": f"Erinnerung '{memory.key}' gelöscht."}
