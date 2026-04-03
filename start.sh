#!/bin/bash
# Dreamline Startup: Alembic-Migrationen + Uvicorn
set -e

echo "Dreamline: Führe Alembic-Migrationen aus..."
alembic upgrade head 2>/dev/null || echo "Alembic übersprungen (erste Installation oder kein Schema-Change)"

echo "Dreamline: Starte Uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
