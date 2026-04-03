"""Pydantic-Schemas für Memory-Endpunkte."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class MemoryResponse(BaseModel):
    """Einzelne Erinnerung in der Antwort."""
    id: UUID
    key: str
    content: str
    memory_type: str = "project"
    confidence: float
    source_count: int
    last_consolidated_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RecallResponse(BaseModel):
    """Erinnerung mit Relevanz-Score für Recall-Abfragen."""
    id: UUID
    key: str
    content: str
    memory_type: str = "project"
    confidence: float
    relevance_score: float = Field(..., description="Berechneter Relevanz-Score")

    model_config = {"from_attributes": True}
