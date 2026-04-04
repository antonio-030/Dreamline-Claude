"""Pydantic-Schemas für Session-Endpunkte."""

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# Maximale Größe für Metadaten (serialisiert)
MAX_METADATA_BYTES = 10_240


class MessageItem(BaseModel):
    """Einzelne Nachricht in einem Chat-Verlauf."""
    role: str = Field(..., max_length=20, description="Rolle: user oder assistant")
    content: str = Field(..., max_length=50_000, description="Nachrichteninhalt (max 50KB)")


class SessionCreate(BaseModel):
    """Request-Body zum Erstellen einer Session."""
    messages: list[MessageItem] = Field(..., min_length=1, max_length=100, description="Chat-Nachrichten (max 100)")
    outcome: Literal["positive", "negative", "neutral"] | None = Field(None, description="Ergebnis")
    metadata: dict[str, Any] | None = Field(None, description="Beliebige Metadaten (max 10KB)")

    @field_validator("metadata")
    @classmethod
    def validate_metadata_size(cls, v: dict | None) -> dict | None:
        """Begrenzt die Größe und Tiefe der Metadaten."""
        if v is None:
            return v
        serialized = json.dumps(v)
        if len(serialized) > MAX_METADATA_BYTES:
            raise ValueError(f"Metadaten überschreiten {MAX_METADATA_BYTES} Bytes")
        return v


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
