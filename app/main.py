"""Dreamline – Selbstevolvierender KI-Gedächtniskonsolidierungs-Service.

FastAPI-Anwendung mit automatischer Tabellenerstellung und Hintergrund-Worker.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import create_tables
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
    await create_tables()
    logger.info("Datenbanktabellen erstellt/geprüft.")
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
