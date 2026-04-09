---
paths: ["app/models/**", "alembic/**", "app/database.py"]
description: Datenbank- und Migrations-Regeln
---

# Datenbank-Regeln

- SQLAlchemy 2.x async: `select()` statt `.query()`, `AsyncSession` als Context-Manager
- Neue Spalten: IMMER `nullable=True` oder `server_default` (Rueckwaertskompatibilitaet)
- Neue Indexes: In Alembic-Migration erstellen, NICHT nur im Model
- Migrationen: Idempotent (IF NOT EXISTS), Model-Import in `alembic/env.py`
- Connection Pool: `pool_pre_ping=True`, `pool_recycle=3600`
- Kein `session.query()`, kein `.add_all()` ohne `await flush()`
