"""Health-Check-Endpunkt mit Datenbank-Validierung."""

import logging

from fastapi import APIRouter
from sqlalchemy import text

from app.database import async_session

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health_check():
    """
    Gibt den Status der Anwendung zurück.
    Prüft ob die Datenbank erreichbar ist.
    """
    db_ok = False
    try:
        async with async_session() as db:
            await db.execute(text("SELECT 1"))
            db_ok = True
    except Exception as e:
        logger.warning("Health-Check DB fehlgeschlagen: %s", e)

    if not db_ok:
        return {"status": "degraded", "service": "dreamline", "database": "unreachable"}

    return {"status": "healthy", "service": "dreamline", "database": "connected"}
