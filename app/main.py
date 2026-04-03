"""Dreamline – Selbstevolvierender KI-Gedächtniskonsolidierungs-Service.

FastAPI-Anwendung mit Alembic-Migrationen und Hintergrund-Worker.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import create_tables
from app.routers import auth, dashboard, dreams, health, link, memories, projects, recall, sessions, stats
from app.worker.scheduler import start_scheduler, stop_scheduler

# Logging konfigurieren (vor allen logger-Zugriffen)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _run_migrations():
    """
    Erstellt/aktualisiert die Datenbanktabellen.
    Nutzt create_all (sicher für neue + bestehende Installationen).
    Alembic-Migrationen werden separat per CLI aufgerufen:
      docker exec dreamline-dreamline-1 alembic upgrade head
    """
    await create_tables()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lebenszyklus-Management: Migrationen, Worker starten/stoppen."""
    logger.info("Dreamline startet...")
    await _run_migrations()
    logger.info("Datenbank-Migrationen geprüft.")
    start_scheduler()
    logger.info("Dreamline bereit.")

    yield

    logger.info("Dreamline fährt herunter...")
    stop_scheduler()
    logger.info("Dreamline gestoppt.")


app = FastAPI(
    title="Dreamline",
    description="Selbstevolvierender KI-Gedächtniskonsolidierungs-Service",
    version="2.0.0",
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
