"""Memory-Modell – konsolidierte Erinnerungen aus vergangenen Sessions."""

from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import mapped_column, relationship

from app.database import Base


class Memory(Base):
    __tablename__ = "memories"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False, index=True
    )
    key = mapped_column(String(200), nullable=False)  # Thema/Titel
    content = mapped_column(Text, nullable=False)  # Der eigentliche Inhalt
    memory_type = mapped_column(String(20), default="project")  # user, feedback, project, reference
    confidence = mapped_column(Float, default=0.5)  # 0-1 Konfidenz
    source_count = mapped_column(Integer, default=1)  # Anzahl beitragender Sessions
    last_consolidated_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Beziehung zum Projekt
    project = relationship("Project", back_populates="memories")
