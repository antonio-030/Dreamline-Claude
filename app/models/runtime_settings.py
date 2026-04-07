"""Runtime-Einstellungen – zur Laufzeit änderbare Konfiguration (ohne Neustart)."""

from sqlalchemy import DateTime, Float, Integer, String, Text, Boolean, func
from sqlalchemy.orm import mapped_column

from app.database import Base


class RuntimeSetting(Base):
    """Key-Value-Tabelle für zur Laufzeit änderbare Einstellungen."""
    __tablename__ = "runtime_settings"

    key = mapped_column(String(100), primary_key=True)
    value = mapped_column(Text, nullable=False)
    updated_at = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
