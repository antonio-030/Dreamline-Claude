"""Recall-Endpunkt – findet relevante Erinnerungen per Stichwortsuche oder KI-gestützt."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_project
from app.database import get_db
from app.models.project import Project
from app.schemas.memory import RecallResponse
from app.services.recaller import recall_memories

router = APIRouter(prefix="/api/v1/recall", tags=["recall"])


@router.get("", response_model=list[RecallResponse])
async def recall(
    query: str = Query(..., min_length=1, description="Suchbegriff"),
    limit: int = Query(5, ge=1, le=50, description="Maximale Anzahl Ergebnisse"),
    mode: str = Query(
        "fast",
        description="Suchmodus: 'fast' (ILIKE, Standard) oder 'smart' (KI-gestützt)",
        pattern="^(fast|smart)$",
    ),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """
    Sucht relevante Erinnerungen für den angegebenen Suchbegriff.

    Modi:
    - fast: Schnelle ILIKE-Stichwortsuche (Standard, kein KI-Aufruf)
    - smart: KI wählt die relevantesten Erinnerungen aus (langsamer, genauer)
    """
    results = await recall_memories(
        db=db,
        project_id=project.id,
        query=query,
        limit=limit,
        mode=mode,
        ai_provider=project.ai_provider,
        ai_model=project.ai_model,
    )
    return results
