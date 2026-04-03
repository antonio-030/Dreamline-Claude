"""Dream-Modell + DreamLock – Protokoll der Konsolidierungsläufe mit Sperrmechanismus."""

from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import mapped_column, relationship

from app.database import Base


class Dream(Base):
    __tablename__ = "dreams"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False, index=True
    )
    sessions_reviewed = mapped_column(Integer, nullable=False)
    memories_created = mapped_column(Integer, default=0)
    memories_updated = mapped_column(Integer, default=0)
    memories_deleted = mapped_column(Integer, default=0)
    summary = mapped_column(Text, nullable=True)
    tokens_used = mapped_column(Integer, default=0)
    duration_ms = mapped_column(Integer, default=0)
    status = mapped_column(String(20), default="completed")  # completed oder failed
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Beziehung zum Projekt
    project = relationship("Project", back_populates="dreams")


class DreamLock(Base):
    """Sperrmechanismus – verhindert parallele Dreams pro Projekt."""
    __tablename__ = "dream_locks"

    project_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), primary_key=True
    )
    locked_by = mapped_column(String(100), nullable=False)  # Hostname oder Worker-ID
    locked_at = mapped_column(DateTime(timezone=True), server_default=func.now())
