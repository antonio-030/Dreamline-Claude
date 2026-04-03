"""Authentifizierung per Bearer-Token (API-Key pro Projekt)."""

from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.project import Project

# Bearer-Token-Schema für Swagger-UI
security = HTTPBearer()


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
