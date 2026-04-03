"""Session-Endpunkt – zeichnet Chat-Verläufe auf, optional mit Schnell-Extraktion."""

import json
import logging
from uuid import UUID as PyUUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_project
from app.database import get_db, async_session
from app.models.project import Project
from app.models.session import Session
from app.schemas.session import SessionCreate, SessionListItem, SessionResponse
from app.services.extractor import quick_extract
from app.worker.scheduler import check_project_dream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])
limiter = Limiter(key_func=get_remote_address)


def _parse_messages(json_str: str | None) -> list[dict]:
    """Parst einen JSON-String sicher in eine Liste von Nachrichten-Dicts.

    Wird an mehreren Stellen benötigt (Session-Liste, Session-Detail, etc.).
    Gibt bei None, leerem String oder ungültigem JSON eine leere Liste zurück.
    """
    if not json_str:
        return []
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_json(json_str: str | None, fallback=None):
    """Parst einen beliebigen JSON-String sicher.

    Im Gegensatz zu _parse_messages() gibt diese Funktion den
    JSON-Wert unverändert zurück (dict, list, etc.).
    Bei Fehler wird der Fallback-Wert zurückgegeben.
    """
    if not json_str:
        return fallback
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return fallback


async def _run_quick_extract(
    session_id: str,
    project_id: str,
    messages_json: str,
    outcome: str | None,
    metadata_json: str | None,
    ai_provider: str,
    ai_model: str,
):
    """Hintergrund-Task für Schnell-Extraktion nach Session-Erstellung."""
    async with async_session() as db:
        try:
            # Session-Objekt für den Extractor rekonstruieren
            session_obj = Session(
                id=session_id,
                project_id=project_id,
                messages_json=messages_json,
                outcome=outcome,
                metadata_json=metadata_json,
            )

            summary = await quick_extract(
                db=db,
                session=session_obj,
                project_id=project_id,
                ai_provider=ai_provider,
                ai_model=ai_model,
            )
            await db.commit()

            if summary:
                logger.info(
                    "Schnell-Extraktion für Session %s abgeschlossen: %s",
                    session_id, summary,
                )
        except Exception as e:
            logger.error(
                "Schnell-Extraktion Hintergrund-Fehler für Session %s: %s",
                session_id, str(e),
            )
            await db.rollback()


@router.get("", response_model=list[SessionListItem])
async def list_sessions(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200, description="Maximale Anzahl Sessions"),
    offset: int = Query(0, ge=0, description="Offset für Paginierung"),
):
    """Listet alle Sessions eines Projekts mit Vorschau und Nachrichtenanzahl."""
    stmt = (
        select(Session)
        .where(Session.project_id == project.id)
        .order_by(Session.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    sessions = result.scalars().all()

    items = []
    for s in sessions:
        # Nachrichten parsen für Vorschau und Anzahl
        messages = _parse_messages(s.messages_json)

        # Erste User-Nachricht als Vorschau
        preview = ""
        for msg in messages:
            if msg.get("role") == "user" and msg.get("content"):
                preview = msg["content"][:150]
                break
        if not preview and messages:
            preview = messages[0].get("content", "")[:150]

        items.append(SessionListItem(
            id=s.id,
            outcome=s.outcome,
            is_consolidated=s.is_consolidated,
            created_at=s.created_at,
            message_count=len(messages),
            preview=preview,
        ))

    return items


@router.get("/{session_id}")
async def get_session(
    session_id: PyUUID,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Gibt eine einzelne Session mit allen Nachrichten zurück."""
    stmt = (
        select(Session)
        .where(Session.id == session_id)
        .where(Session.project_id == project.id)
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if not session:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Session nicht gefunden")

    messages = _parse_messages(session.messages_json)

    # Metadaten sind ein einzelnes Dict, keine Nachrichten-Liste
    metadata = _parse_json(session.metadata_json)

    return {
        "id": str(session.id),
        "project_id": str(session.project_id),
        "outcome": session.outcome,
        "is_consolidated": session.is_consolidated,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "message_count": len(messages),
        "messages": messages,
        "metadata": metadata,
    }


@router.delete("/{session_id}")
async def delete_session(
    session_id: PyUUID,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Löscht eine einzelne Session."""
    stmt = (
        select(Session)
        .where(Session.id == session_id)
        .where(Session.project_id == project.id)
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(status_code=404, detail="Session nicht gefunden")

    await db.delete(session)
    await db.flush()
    return {"message": "Session gelöscht."}


@router.post("", response_model=SessionResponse)
@limiter.limit("60/hour")
async def create_session(
    request: Request,
    data: SessionCreate,
    background_tasks: BackgroundTasks,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """
    Speichert einen Chat-Verlauf zur späteren Konsolidierung. Max 60/Stunde.

    Falls quick_extract für das Projekt aktiviert ist, wird im Hintergrund
    eine Schnell-Extraktion gestartet (offensichtliche Fakten sofort speichern).
    """
    # Nachrichten und Metadaten als JSON serialisieren
    messages_json = json.dumps(
        [msg.model_dump() for msg in data.messages],
        ensure_ascii=False,
    )
    metadata_json = json.dumps(data.metadata, ensure_ascii=False) if data.metadata else None

    session = Session(
        project_id=project.id,
        messages_json=messages_json,
        outcome=data.outcome,
        metadata_json=metadata_json,
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)

    # Schnell-Extraktion als Hintergrund-Task starten (falls aktiviert)
    quick_extract_summary = None
    if project.quick_extract:
        background_tasks.add_task(
            _run_quick_extract,
            session_id=str(session.id),
            project_id=str(project.id),
            messages_json=messages_json,
            outcome=data.outcome,
            metadata_json=metadata_json,
            ai_provider=project.ai_provider,
            ai_model=project.ai_model,
        )
        quick_extract_summary = "Schnell-Extraktion gestartet (läuft im Hintergrund)"

    # Per-Session Dream-Trigger: Gate-Check nach jeder eingehenden Session
    # (1:1 wie Claude Code: executeAutoDream() nach jedem Turn in postSamplingHooks)
    background_tasks.add_task(
        check_project_dream,
        project_id=str(project.id),
    )

    # SessionResponse mit optionalem quick_extract_summary
    return SessionResponse(
        id=session.id,
        project_id=session.project_id,
        outcome=session.outcome,
        is_consolidated=session.is_consolidated,
        created_at=session.created_at,
        quick_extract_summary=quick_extract_summary,
    )
