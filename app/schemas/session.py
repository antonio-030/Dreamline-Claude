"""Pydantic-Schemas für Session-Endpunkte."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class MessageItem(BaseModel):
    """Einzelne Nachricht in einem Chat-Verlauf."""
    role: str = Field(..., description="Rolle: user oder assistant")
    content: str = Field(..., description="Nachrichteninhalt")


class SessionCreate(BaseModel):
    """Request-Body zum Erstellen einer Session."""
    messages: list[MessageItem] = Field(..., min_length=1, description="Chat-Nachrichten")
    outcome: str | None = Field(None, description="Ergebnis: positive, negative, neutral")
    metadata: dict[str, Any] | None = Field(None, description="Beliebige Metadaten")


class SessionResponse(BaseModel):
    """Antwort nach dem Erstellen einer Session."""
    id: UUID
    project_id: UUID
    outcome: str | None
    is_consolidated: bool
    created_at: datetime
    quick_extract_summary: str | None = Field(
        None, description="Zusammenfassung der Schnell-Extraktion (falls aktiviert)"
    )

    model_config = {"from_attributes": True}


class SessionListItem(BaseModel):
    """Einzelne Session in der Listenansicht."""
    id: UUID
    outcome: str | None
    is_consolidated: bool
    created_at: datetime
    message_count: int = Field(description="Anzahl Nachrichten in der Session")
    preview: str = Field(description="Vorschau der ersten User-Nachricht")

    model_config = {"from_attributes": True}
