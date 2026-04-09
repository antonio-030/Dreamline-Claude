#!/bin/bash
# Dreamline Startup: Alembic-Migrationen + Uvicorn
set -e

echo "Dreamline: Führe Alembic-Migrationen aus..."
if ! alembic upgrade head 2>&1; then
    echo "WARNUNG: Alembic-Migration fehlgeschlagen (erste Installation oder Schema-Fehler)"
    echo "Pruefe ob die Datenbank erreichbar ist."
fi

echo "Dreamline: Starte Uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
