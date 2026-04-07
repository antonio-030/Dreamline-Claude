"""Projekt-Modell – verwaltet API-Keys und Konfiguration pro Projekt."""

from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import mapped_column, relationship

from app.database import Base


class Project(Base):
    __tablename__ = "projects"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = mapped_column(String(200), nullable=False)
    api_key = mapped_column(String(64), unique=True, nullable=False, index=True)
    ai_provider = mapped_column(String(20), default="anthropic")  # anthropic oder openai
    ai_model = mapped_column(String(100), default="claude-sonnet-4-5-20250514")
    dream_interval_hours = mapped_column(Integer, default=24)
    min_sessions_for_dream = mapped_column(Integer, default=5)
    quick_extract = mapped_column(Boolean, default=True)  # Schnell-Extraktion nach jeder Session
    local_path = mapped_column(String(500), nullable=True)  # Lokaler Projektpfad (z.B. /home/user/mein-projekt)
    # Persistenter Cursor für Quick-Extract (1:1 wie extractMemories.ts lastMemoryMessageUuid)
    last_extracted_session_id = mapped_column(String(36), nullable=True)
    # Claude CLI Session-ID für Dream-Agent Cache-Sharing (--resume)
    dream_cli_session_id = mapped_column(String(100), nullable=True)
    # Quell-Tool: "claude", "codex" oder "both"
    source_tool = mapped_column(String(20), default="claude")
    # Ollama Custom-Modellname (z.B. "dreamline-meinprojekt")
    # Wird nach jedem Dream mit Memories als SYSTEM-Prompt aktualisiert
    ollama_custom_model_name = mapped_column(String(200), nullable=True)
    is_active = mapped_column(Boolean, default=True)
    # Zeitpunkt der letzten Quick-Extraction (persistiert statt in-memory)
    last_extract_at = mapped_column(DateTime(timezone=True), nullable=True)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Beziehungen -- lazy="noload" verhindert dass bei jedem Projekt-Query
    # automatisch ALLE Sessions/Memories/Dreams mitgeladen werden.
    # Explizit mit selectinload()/joinedload() laden wo nötig.
    sessions = relationship("Session", back_populates="project", lazy="noload")
    memories = relationship("Memory", back_populates="project", lazy="noload")
    dreams = relationship("Dream", back_populates="project", lazy="noload")
