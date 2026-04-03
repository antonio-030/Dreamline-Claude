"""Asynchrones SQLAlchemy-Setup für PostgreSQL."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# Engine erstellen mit Connection-Pool
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
)

# Session-Factory
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """Basisklasse für alle SQLAlchemy-Modelle."""
    pass


async def get_db() -> AsyncSession:
    """Dependency für FastAPI – liefert eine Datenbank-Session."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def create_tables():
    """Erstellt alle Tabellen beim Start der Anwendung."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
