"""Authentifizierung per Bearer-Token (API-Key pro Projekt) und Admin-Key."""

import secrets as _secrets

from fastapi import Depends, Header, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.project import Project

# Bearer-Token-Schema für Swagger-UI
security = HTTPBearer()


def verify_admin_key(x_admin_key: str = Header(...)):
    """
    Prüft den Admin-Key für Projekt-Verwaltungsendpunkte.
    Nutzt timing-safe Vergleich um Timing-Angriffe zu verhindern.
    """
    if not _secrets.compare_digest(x_admin_key, settings.dreamline_secret_key):
        raise HTTPException(status_code=403, detail="Ungültiger Admin-Key")
    return True


async def get_current_project(
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: AsyncSession = Depends(get_db),
) -> Project:
    """
    Extrahiert das Projekt aus dem Bearer-Token.
    Jeder API-Key gehört zu genau einem Projekt.
    """
    api_key = credentials.credentials

    stmt = select(Project).where(Project.api_key == api_key, Project.is_active == True)
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=401,
            detail="Ungültiger oder deaktivierter API-Key",
        )

    return project
