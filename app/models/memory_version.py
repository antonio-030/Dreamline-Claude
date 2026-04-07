"""Memory-Versionierung – Änderungshistorie für Erinnerungen."""

from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import mapped_column

from app.database import Base


class MemoryVersion(Base):
    """Speichert den vorherigen Zustand einer Memory vor jeder Änderung."""
    __tablename__ = "memory_versions"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    memory_id = mapped_column(
        UUID(as_uuid=True), ForeignKey("memories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content = mapped_column(Text, nullable=False)
    confidence = mapped_column(Float, default=0.5)
    changed_by = mapped_column(String(50), nullable=False)  # "dream", "quick-extract", "manual"
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
