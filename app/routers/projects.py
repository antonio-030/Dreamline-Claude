"""Projekt-Verwaltung – erstellt Projekte und API-Keys, inkl. Löschen."""

import secrets
from uuid import UUID

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_admin_key
from app.database import get_db
from app.models.dream import Dream, DreamLock
from app.models.memory import Memory
from app.models.project import Project
from app.models.session import Session

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    """Request zum Erstellen eines neuen Projekts."""
    name: str = Field(..., min_length=1, max_length=200, description="Projektname")
    ai_provider: Literal["claude-abo", "codex-sub", "anthropic", "openai", "ollama"] = Field("claude-abo", description="KI-Anbieter")
    ai_model: str = Field("claude-sonnet-4-5-20250514", max_length=100, description="KI-Modell")
    dream_provider: Literal["claude-abo", "codex-sub", "anthropic", "openai", "ollama"] | None = Field(None, description="Separater Dream-Provider (null = ai_provider)")
    dream_model: str | None = Field(None, max_length=100, description="Separates Dream-Modell (null = ai_model)")
    dream_interval_hours: int = Field(12, ge=1, le=720, description="Dream-Intervall in Stunden")
    min_sessions_for_dream: int = Field(3, ge=1, le=1000, description="Mindestanzahl Sessions für Dream")
    quick_extract: bool = Field(True, description="Schnell-Extraktion nach jeder Session")
    source_tool: Literal["claude", "codex", "both"] = Field("claude", description="Quell-Tool")


class ProjectResponse(BaseModel):
    """Antwort für Projektdaten. API-Key wird maskiert in Listen-Responses."""
    id: UUID
    name: str
    api_key: str
    ai_provider: str
    ai_model: str
    dream_provider: str | None = None
    dream_model: str | None = None
    dream_interval_hours: int
    min_sessions_for_dream: int
    quick_extract: bool
    source_tool: str = "claude"
    local_path: str | None = None
    is_active: bool

    model_config = {"from_attributes": True}


def _mask_key(key: str) -> str:
    """Maskiert einen API-Key für sichere Anzeige."""
    if not key or len(key) < 12:
        return "***"
    return key[:6] + "..." + key[-4:]


def _generate_api_key() -> str:
    """Generiert einen sicheren API-Key mit dl_-Präfix."""
    return f"dl_{secrets.token_hex(28)}"


@router.post("", response_model=ProjectResponse)
async def create_project(
    data: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """Erstellt ein neues Projekt mit API-Key."""
    project = Project(
        name=data.name,
        api_key=_generate_api_key(),
        ai_provider=data.ai_provider,
        ai_model=data.ai_model,
        dream_provider=data.dream_provider,
        dream_model=data.dream_model,
        dream_interval_hours=data.dream_interval_hours,
        min_sessions_for_dream=data.min_sessions_for_dream,
        quick_extract=data.quick_extract,
        source_tool=data.source_tool,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)
    return project


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """Listet alle Projekte auf."""
    stmt = select(Project).order_by(Project.created_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


class ProjectUpdate(BaseModel):
    """Request zum Bearbeiten eines Projekts. Nur übergebene Felder werden geändert."""
    name: str | None = Field(None, min_length=1, max_length=200)
    ai_provider: Literal["claude-abo", "codex-sub", "anthropic", "openai", "ollama"] | None = None
    ai_model: str | None = None
    dream_provider: Literal["claude-abo", "codex-sub", "anthropic", "openai", "ollama", ""] | None = None
    dream_model: str | None = None
    dream_interval_hours: int | None = Field(None, ge=1)
    min_sessions_for_dream: int | None = Field(None, ge=1)
    quick_extract: bool | None = None
    source_tool: Literal["claude", "codex", "both"] | None = None
    local_path: str | None = None
    is_active: bool | None = None


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    data: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """Bearbeitet ein Projekt. Nur übergebene Felder werden aktualisiert."""
    stmt = select(Project).where(Project.id == project_id)
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")

    # Nur Felder aktualisieren die explizit übergeben wurden
    update_data = data.model_dump(exclude_unset=True)
    # Leerer String bei dream_provider/dream_model = zurücksetzen auf NULL
    for key in ("dream_provider", "dream_model"):
        if key in update_data and update_data[key] == "":
            update_data[key] = None
    for field, value in update_data.items():
        setattr(project, field, value)

    await db.flush()
    await db.refresh(project)
    return project


@router.delete("/{project_id}")
async def delete_project(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """Löscht ein Projekt mit allen zugehörigen Daten (Sessions, Memories, Dreams)."""
    # Projekt prüfen
    stmt = select(Project).where(Project.id == project_id)
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")

    # Alle zugehörigen Daten löschen (Reihenfolge wegen Fremdschlüssel)
    await db.execute(delete(DreamLock).where(DreamLock.project_id == project_id))
    await db.execute(delete(Dream).where(Dream.project_id == project_id))
    await db.execute(delete(Memory).where(Memory.project_id == project_id))
    await db.execute(delete(Session).where(Session.project_id == project_id))
    await db.execute(delete(Project).where(Project.id == project_id))

    return {"message": f"Projekt '{project.name}' und alle zugehörigen Daten gelöscht."}


# ─── Ollama Endpoints ────────────────────────────────────────────

@router.post("/{project_id}/ollama/sync")
async def sync_ollama_model(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """
    Erstellt oder aktualisiert ein Custom-Ollama-Modell mit den aktuellen Memories.
    Das Modell enthält alle Memories als SYSTEM-Prompt und wird bei Ollama registriert.
    """
    from app.services.ollama_modelfile import sync_ollama_modelfile

    stmt = select(Project).where(Project.id == project_id)
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")

    sync_result = await sync_ollama_modelfile(db, project_id, project.ai_model)
    return sync_result


@router.get("/{project_id}/ollama/status")
async def ollama_model_status(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """Zeigt den Status des Custom-Ollama-Modells für dieses Projekt."""
    from app.services.ollama_modelfile import check_ollama_health

    stmt = select(Project).where(Project.id == project_id)
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")

    health = await check_ollama_health()

    return {
        "project": project.name,
        "ai_provider": project.ai_provider,
        "ai_model": project.ai_model,
        "custom_model_name": project.ollama_custom_model_name,
        "has_custom_model": (
            project.ollama_custom_model_name in health.get("models", [])
            if health.get("available") else None
        ),
        "ollama": health,
    }


# ─── Provider Health Check ──────────────────────────────────────

@router.get("/provider-status")
async def provider_status(
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """Prüft den KI-Provider-Status aller aktiven Projekte."""
    from app.services.ai_client import check_provider_health

    stmt = select(Project).where(Project.is_active == True)
    result = await db.execute(stmt)
    projects = result.scalars().all()

    # Einzigartige Provider/Model-Kombinationen sammeln und prüfen
    checked: dict[str, dict] = {}
    results = []

    for p in projects:
        cache_key = f"{p.ai_provider}:{p.ai_model}"
        if cache_key not in checked:
            checked[cache_key] = await check_provider_health(p.ai_provider, p.ai_model)

        health = checked[cache_key]
        entry = {
            "project_id": str(p.id),
            "project_name": p.name,
            **health,
        }

        # Dream-Provider prüfen wenn verschieden
        dp = p.dream_provider or p.ai_provider
        dm = p.dream_model or p.ai_model
        dream_key = f"{dp}:{dm}"
        if dream_key != cache_key:
            if dream_key not in checked:
                checked[dream_key] = await check_provider_health(dp, dm)
            entry["dream_provider_health"] = checked[dream_key]

        results.append(entry)

    return results
