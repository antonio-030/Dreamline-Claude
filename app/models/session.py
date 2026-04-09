"""Session-Modell – speichert Chat-Verläufe zur späteren Konsolidierung."""

from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import mapped_column, relationship

from app.database import Base


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_project_consolidated", "project_id", "is_consolidated"),
    )

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False, index=True
    )
    messages_json = mapped_column(Text, nullable=False)  # JSON-Array der Nachrichten
    outcome = mapped_column(String(50), nullable=True)  # positive, negative, neutral
    metadata_json = mapped_column(Text, nullable=True)  # Beliebige Metadaten als JSON
    is_consolidated = mapped_column(Boolean, default=False, index=True)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Beziehung zum Projekt
    project = relationship("Project", back_populates="sessions")
