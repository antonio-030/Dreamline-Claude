# Dreamline – Projektregeln

## Was ist Dreamline?

Selbstevolvierender KI-Gedächtniskonsolidierungs-Service. Sammelt Chat-Sessions von Claude Code und OpenAI Codex, konsolidiert Wissen per KI ("Dreaming") und schreibt die Ergebnisse als Memory-Dateien zurück ins Projekt. Beim nächsten Start hat der KI-Agent sofort den vollen Kontext.

## Tech-Stack

- **Backend:** FastAPI (async), SQLAlchemy 2.0 (async), PostgreSQL 16
- **Frontend:** Vanilla JS + Jinja2 Templates (Single-Page Dashboard)
- **Deployment:** Docker Compose (app + postgres)
- **Migrationen:** Alembic
- **KI-Provider:** Claude-Abo (CLI), Codex-Sub (CLI), Anthropic API, OpenAI API, Ollama (lokal)

## Projektstruktur

```
app/
  config.py          # Zentrale Konfiguration (Settings-Klasse, alle Parameter)
  database.py        # SQLAlchemy Engine + Session-Factory
  auth.py            # Admin-Key + Bearer-Token Auth
  main.py            # FastAPI-App, Middleware, Router-Registrierung
  models/            # SQLAlchemy-Modelle (project, session, memory, dream, memory_version, runtime_settings)
  schemas/           # Pydantic-Schemas für API-Responses
  routers/           # FastAPI-Router (dashboard, projects, sessions, memories, dreams, recall, stats, link, settings, health, auth)
  services/          # Business-Logik (dreamer, extractor, recaller, ai_client, memory_writer, dream_locks, dream_prompts, dream_sync, codex_watcher, session_parser, ollama_modelfile, utils)
  worker/            # Hintergrund-Scheduler (APScheduler)
  static/            # dashboard.js (gesamte Frontend-Logik)
  templates/         # dashboard.html (Jinja2-Template)
alembic/             # DB-Migrationen
```

## Verbindliche Regeln

### Sprache
- **Code:** Englisch (Variablennamen, Funktionsnamen, Klassen)
- **Kommentare & Docstrings:** Deutsch
- **UI-Texte:** Deutsch
- **Git-Commits:** Deutsch oder Englisch

### Kein Hardcoding
- **ALLE konfigurierbaren Werte** stehen in `app/config.py` (Settings-Klasse)
- Neue Werte → in `app/config.py` mit Default hinzufügen, NICHT als Konstante in der Datei
- Werte die der User ändern können soll → zusätzlich in `app/routers/settings.py` SETTING_DEFINITIONS registrieren
- Die Settings-UI (`/api/v1/settings`) erlaubt Änderungen zur Laufzeit ohne Neustart
- Reihenfolge: `.env` → `app/config.py` Default → DB-Override (runtime_settings Tabelle)

### Rückwärtskompatibilität
- **Neue DB-Spalten** immer `nullable=True` oder mit `server_default` → bestehende Daten bleiben gültig
- **Neue Config-Werte** immer mit sinnvollem Default → bestehende .env-Dateien funktionieren weiter
- **API-Responses** nur erweitern, nie Felder umbenennen oder entfernen
- **Alembic-Migrationen** immer idempotent (IF NOT EXISTS wo möglich)
- **Neue Tabellen** in Alembic UND in `alembic/env.py` (Model-Import) registrieren

### Sicherheit
- Input-Validation auf ALLEN Pydantic-Modellen: `max_length`, `ge=`/`le=` Bounds, `pattern=` wo sinnvoll
- Admin-Key-Vergleich: Immer `secrets.compare_digest()` (timing-safe)
- SQL: Nur SQLAlchemy ORM, KEINE Raw-SQL-Strings oder f-String-Interpolation in Queries
- Pfade: `_is_safe_project_path()` prüfen bevor auf Dateisystem geschrieben wird
- CLI-Aufrufe: Immer `subprocess` mit Liste (nicht `shell=True`)
- Rate Limits auf alle öffentlichen Endpunkte (slowapi)
- Secrets NIEMALS loggen (API-Keys, Admin-Keys, Credentials)

### Error Handling
- Kein `except Exception: continue` ohne Logging → immer `logger.warning(...)` mit Kontext
- Kein `except:` (bare except) → immer `except Exception as e:`
- Provider-Fehler: Echte Fehlermeldung in `Dream.error_detail` speichern, NICHT generischen Text
- Kein stiller Fallback auf anderen Provider → Fehler dem User anzeigen

### Code-Qualität
- Max ~300 Zeilen pro Datei (Services/Router die größer werden → aufteilen)
- Kein toter Code (unbenutzte Imports, auskommentierte Blöcke)
- Docstrings auf allen öffentlichen Funktionen (Deutsch)
- Return-Type-Hints auf allen Funktionen
- Doppelter Code → in `app/services/utils.py` extrahieren

### Tests
- Tests in `tests/` für neue Services und Business-Logik
- `pytest tests/ -q` muss vor jedem Push grün sein (im Docker: `docker exec dreamline-claude-dreamline-1 python -m pytest tests/ -q`)
- Externe SDK-Imports (`anthropic`, `openai`) IMMER lazy (in-function `import`) in Dateien die auch testbare Hilfsfunktionen enthalten
- Test-Dependencies (`pytest`, `pytest-asyncio`) werden im Container installiert, NICHT in requirements.txt
- Mocks für DB-Operationen (`AsyncMock`), keine echte Datenbank in Unit-Tests
- Neue Codepfade: Mindestens Happy-Path + Error-Case testen

### Router-Architektur
- Router enthalten NUR HTTP-Handling: Request parsen → Service aufrufen → Response bauen
- Private Hilfsfunktionen mit >20 Zeilen Business-Logik → in `app/services/` extrahieren
- Keine Dateisystem-Operationen (lesen/schreiben) direkt in Routern → Services nutzen
- Projektstruktur: `app/services/hook_installer.py`, `app/services/session_importer.py` für spezialisierte Logik

### Frontend (dashboard.js)
- Alle API-Aufrufe über `apiFetch()` Wrapper (einheitliches Error-Handling + Toast)
- `Promise.allSettled()` statt `Promise.all()` wenn ein Fehler nicht alles brechen soll
- `setInterval`/`setTimeout` in Tracking-Variablen speichern und bei Tab-Wechsel clearen
- HTML-Escaping über `esc()` Funktion bei allen dynamischen Inhalten (XSS-Schutz)
- Neue UI-Elemente: Deutsche Labels, Dark-Theme CSS-Variablen nutzen
- Hint-Boxes: Max 2 Zeilen, erste Zeile fett = Was ist das, zweite Zeile grau/klein = Aktionen/Details
- Leere Zustände: Nie nur "Keine Daten" — immer Handlungsanweisung hinzufügen (z.B. "Starte einen Dream im Projekte-Tab")
- Stat-Cards: Keine GROSSBUCHSTABEN-Labels, stattdessen `font-weight:500`. Sub-Label für Kontext nutzen
- Sprache konsistent: Nav-Button und Tab-Titel müssen übereinstimmen (z.B. "Sitzungen" nicht "Sessions")
- Settings-UI: Gruppenüberschriften als blaue Uppercase-Labels, Inputs im 2-Spalten-Grid, Toggles über volle Breite

### Datenbank
- Connection Pool: `pool_pre_ping=True`, `pool_recycle=3600` (in database.py)
- Neue Indexes → in Alembic-Migration, nicht nur im Model
- Composite-Index `(project_id, is_consolidated)` auf Sessions für häufigste Query
- Unique-Constraint `(project_id, key)` auf Memories → verhindert Duplikate

### Dream-Pipeline
- 6-Phasen: Lock → Sessions → Memories → Prompt → AI → Result
- Dual-Lock: DB (DreamLock Tabelle) + Dateisystem (.consolidate-lock)
- Bei Fehler: Lock immer releasen (finally-Block), Dream mit status="failed" + error_detail speichern
- Memory-Updates: Alte Version in `memory_versions` speichern BEVOR Update
- Kein Fallback-Provider: Wenn der konfigurierte Provider fehlschlägt → Fehler anzeigen

### Docker
- Non-root User `dreamline` (Claude CLI verweigert Root)
- Volumes: `.claude/` für Auth + Projekte, `.codex/` für Sessions (read-only)
- Alembic-Migrationen in `start.sh` vor Uvicorn
- Startskripte dürfen stderr NICHT unterdrücken (`2>/dev/null` verboten) → Fehler müssen sichtbar sein

### API-Design
- Prefix: Alle Endpunkte unter `/api/v1/`
- Auth: Admin-Endpunkte → `X-Admin-Key` Header, Projekt-Endpunkte → `Bearer` Token
- Rate Limits: Auf allen öffentlichen Endpunkten (slowapi), kritische Endpunkte strenger (z.B. Dream: 2/min)
- Responses: Nur erweitern, nie Felder entfernen oder umbenennen (Rückwärtskompatibilität)
- Fehler: HTTPException mit deutschem `detail`-Text, passender Status-Code (400/401/403/404/500)
- Paginierung: `limit`/`offset` Parameter mit sinnvollen Defaults und Obergrenzen (`le=`)

### Logging
- Level: `INFO` für normale Operationen, `WARNING` für recoverable Fehler, `ERROR` für fatale Fehler
- Kontext: Immer `project_id` mitloggen wo verfügbar
- Secrets: NIEMALS API-Keys, Admin-Keys oder Credentials loggen
- Exception: Bei `except Exception as e:` immer `str(e)[:200]` loggen (Länge begrenzen)

## Defaults (konsistent halten!)

| Parameter | Default | Wo definiert |
|-----------|---------|--------------|
| ai_provider | `claude-abo` | config.py, projects.py, link.py |
| ai_model | `claude-sonnet-4-5-20250514` | config.py, projects.py, link.py |
| dream_provider | `null` (= ai_provider) | config.py, projects.py, link.py |
| dream_model | `null` (= ai_model) | config.py, projects.py, link.py |
| dream_interval_hours | `12` | config.py, projects.py, link.py |
| min_sessions_for_dream | `3` | config.py, projects.py, link.py |

Wenn Defaults geändert werden → an ALLEN Stellen gleichzeitig ändern!

## Neue Features hinzufügen – Checkliste

1. **Model:** Neue Spalte/Tabelle in `app/models/` → `nullable=True` oder Default
2. **Migration:** `alembic/versions/` → neue Revision, Model in `alembic/env.py` importieren
3. **Schema:** Pydantic-Schema in `app/schemas/` erweitern → Response-Felder nur hinzufügen, nie entfernen
4. **Config:** Neue Parameter in `app/config.py` → Default setzen
5. **Settings-UI:** In `app/routers/settings.py` SETTING_DEFINITIONS registrieren (falls UI-konfigurierbar)
6. **Router/Service:** Business-Logik implementieren
7. **Frontend:** `dashboard.js` + `dashboard.html` erweitern
8. **Tests:** Unit-Tests in `tests/` für neue Service-Funktionen schreiben
9. **Verify:** `pytest tests/ -q` im Container grün + Docker rebuild + manueller Test aller betroffenen Tabs
