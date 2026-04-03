"""Pydantic-Schemas für Dream-Endpunkte."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class DreamResponse(BaseModel):
    """Antwort nach einem Dream-Lauf."""
    id: UUID
    project_id: UUID
    sessions_reviewed: int
    memories_created: int
    memories_updated: int
    memories_deleted: int
    summary: str | None
    tokens_used: int
    duration_ms: int
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DreamTriggerResponse(BaseModel):
    """Antwort beim manuellen Auslösen eines Dreams."""
    message: str
    dream: DreamResponse | None = None
