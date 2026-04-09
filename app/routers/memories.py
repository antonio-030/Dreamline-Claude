"""Memories-Endpunkt – listet, löscht, exportiert und importiert konsolidierte Erinnerungen."""

from uuid import UUID as PyUUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_project
from app.database import get_db
from app.models.memory import Memory
from app.models.project import Project
from app.schemas.memory import MemoryResponse

router = APIRouter(prefix="/api/v1/memories", tags=["memories"])
limiter = Limiter(key_func=get_remote_address)


@router.get("", response_model=list[MemoryResponse])
@limiter.limit("120/minute")
async def list_memories(
    request: Request,
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
@limiter.limit("30/minute")
async def delete_memory(
    request: Request,
    memory_id: PyUUID,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Löscht eine einzelne Erinnerung."""
    stmt = (
        select(Memory)
        .where(Memory.id == memory_id)
        .where(Memory.project_id == project.id)
    )
    result = await db.execute(stmt)
    memory = result.scalar_one_or_none()

    if not memory:
        raise HTTPException(status_code=404, detail="Erinnerung nicht gefunden")

    await db.delete(memory)
    await db.flush()
    return {"message": f"Erinnerung '{memory.key}' gelöscht."}


# ─── Bulk Export/Import ─────────────────────────────────────────

@router.get("/export")
@limiter.limit("10/minute")
async def export_memories(
    request: Request,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Exportiert alle Erinnerungen als JSON-Array."""
    stmt = select(Memory).where(Memory.project_id == project.id).order_by(Memory.key)
    result = await db.execute(stmt)
    memories = result.scalars().all()

    export_data = [
        {
            "key": m.key,
            "content": m.content,
            "memory_type": m.memory_type,
            "confidence": m.confidence,
            "source_count": m.source_count,
        }
        for m in memories
    ]
    return JSONResponse(
        content=export_data,
        headers={"Content-Disposition": f"attachment; filename={project.name}_memories.json"},
    )


class MemoryImportItem(BaseModel):
    key: str = Field(..., max_length=200)
    content: str = Field(..., max_length=50_000)
    memory_type: str = Field("project", pattern=r"^(user|feedback|project|reference)$")
    confidence: float = Field(0.7, ge=0.0, le=1.0)


@router.post("/import")
@limiter.limit("10/minute")
async def import_memories(
    request: Request,
    items: list[MemoryImportItem],
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Importiert Erinnerungen aus JSON. Max 500 Items, existierende Keys werden aktualisiert."""
    if len(items) > 500:
        raise HTTPException(status_code=400, detail="Maximal 500 Erinnerungen pro Import")
    # Existierende Keys laden
    stmt = select(Memory).where(Memory.project_id == project.id)
    result = await db.execute(stmt)
    existing = {m.key: m for m in result.scalars().all()}

    created = 0
    updated = 0
    for item in items:
        if item.key in existing:
            mem = existing[item.key]
            mem.content = item.content
            mem.memory_type = item.memory_type
            mem.confidence = item.confidence
            updated += 1
        else:
            db.add(Memory(
                project_id=project.id,
                key=item.key,
                content=item.content,
                memory_type=item.memory_type,
                confidence=item.confidence,
            ))
            created += 1

    await db.flush()
    return {"created": created, "updated": updated, "total": len(items)}
