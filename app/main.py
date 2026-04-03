"""Dreamline – Selbstevolvierender KI-Gedächtniskonsolidierungs-Service.

FastAPI-Anwendung mit automatischer Tabellenerstellung und Hintergrund-Worker.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import create_tables

async def _run_migrations():
    """
    Führt Alembic-Migrationen aus. Fallback auf create_all bei frischer Installation.
    """
    try:
        from alembic.config import Config
        from alembic import command
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
        logger.info("Alembic-Migrationen erfolgreich.")
    except Exception as e:
        logger.warning("Alembic-Migration fehlgeschlagen (%s), Fallback auf create_all.", e)
        await create_tables()
from app.routers import auth, dashboard, dreams, health, link, memories, projects, recall, sessions, stats
from app.worker.scheduler import start_scheduler, stop_scheduler

# Logging konfigurieren
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lebenszyklus-Management: Tabellen erstellen und Worker starten/stoppen."""
    # Startup
    logger.info("Dreamline startet...")
    await _run_migrations()
    logger.info("Datenbank-Migrationen geprüft.")
    start_scheduler()
    logger.info("Dreamline bereit.")

    yield

    # Shutdown
    logger.info("Dreamline fährt herunter...")
    stop_scheduler()
    logger.info("Dreamline gestoppt.")


app = FastAPI(
    title="Dreamline",
    description="Selbstevolvierender KI-Gedächtniskonsolidierungs-Service",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS-Middleware (Dashboard + externe Frontends)
_default_origins = ["http://localhost:8100", "http://127.0.0.1:8100"]
_custom_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()] if settings.cors_origins else []
app.add_middleware(
    CORSMiddleware,
    allow_origins=_custom_origins or _default_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Statische Dateien (dashboard.js)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Router registrieren
app.include_router(dashboard.router)
app.include_router(auth.router)
app.include_router(health.router)
app.include_router(projects.router)
app.include_router(sessions.router)
app.include_router(recall.router)
app.include_router(memories.router)
app.include_router(dreams.router)
app.include_router(stats.router)
app.include_router(link.router)
