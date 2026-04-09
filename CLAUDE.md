# Dreamline – Projektregeln

Self-Hosted KI-Gedaechtnis-Service. Sammelt Chat-Sessions (Claude Code + OpenAI Codex), konsolidiert per KI ("Dreaming"), schreibt Memory-Dateien zurueck ins Projekt.

**Stack:** FastAPI async, SQLAlchemy 2.0 async, PostgreSQL, Alembic, Docker Compose, Vanilla JS Dashboard

## Kommandos

- **Tests:** `docker exec dreamline-claude-dreamline-1 python -m pytest tests/ -q`
- **Build:** `docker compose up -d --build dreamline`
- **Logs:** `docker logs dreamline-claude-dreamline-1 --tail 50`
- **Health:** `curl http://localhost:8100/health`

## Harte Regeln (IMMER einhalten)

### Sprache & Stil
- Code: Englisch. Kommentare/Docstrings/UI: Deutsch. Commits: Deutsch oder Englisch.

### Async & Ressourcen
- Alle async Aufrufe MUESSEN `await` haben — fehlende `await` sind der haeufigste Async-Bug
- Ressourcen (DB-Sessions, Dateien, Subprocess) IMMER mit Context-Manager (`async with`, `with`) oder `try/finally`
- `process.kill()` IMMER gefolgt von `await process.wait()` — verhindert Zombie-Prozesse
- Niemals mutable Defaults: `def fn(items=[])` verboten → `def fn(items=None):`

### Sicherheit
- Input-Validation auf ALLEN Pydantic-Modellen: `max_length`, `ge`/`le`, `pattern`
- SQL: Nur SQLAlchemy 2.x ORM (`select()` nicht `.query()`), KEINE Raw-SQL-Strings
- Rate Limits auf ALLEN Endpoints (slowapi): Reads `120/min`, Writes `30/min`, schwere Ops `2-5/min`
- CLI: Immer `subprocess` mit Liste, nie `shell=True`
- Secrets NIEMALS loggen

### Error Handling
- `except Exception` NUR als Top-Level Catch-All (Scheduler, Background-Tasks, Health)
- Ueberall sonst: spezifische Typen (`json.JSONDecodeError`, `OSError`, `ValueError`, `RuntimeError`)
- Fehler nie still schlucken — immer `logger.warning(...)` mit Kontext + `project_id`
- Dream-Fehler: Echte Meldung in `Dream.error_detail`, NICHT generischen Text

### Code-Qualitaet
- Max ~300 Zeilen pro Datei — groessere Module aufteilen (Fassade-Pattern)
- **Vor dem Schreiben pruefen**: `grep` ob Funktion schon existiert → siehe `docs/reference.md`
- Doppelter Code in >1 Datei → extrahieren in `app/services/`
- Funktion >20 Zeilen Business-Logik in Routern → in Service verschieben
- Return-Type-Hints auf allen Funktionen, Docstrings (Deutsch) auf oeffentlichen

### Edge Cases (bei jeder neuen Funktion pruefen)
- Was passiert bei leerem Input? Bei `None`? Bei extrem grossem Input? Bei Netzwerk-Timeout?
- Leere KI-Antworten → sofort `RuntimeError`, nicht still weitergeben
- JSON-Parsing: 3 Strategien (direkt → Markdown-Codeblock → Brace-Matching im Freitext)

### Tests
- Neue Codepfade: Mindestens Happy-Path + Error-Case testen
- `pytest tests/ -q` MUSS vor Push gruen sein
- Externe SDKs (`anthropic`, `openai`) IMMER lazy importieren (in-function)
- Mocks fuer DB (`AsyncMock`), keine echte Datenbank in Unit-Tests

### Verifikation (nach jeder Aenderung)
- Tests ausfuehren und Ergebnis zeigen — niemals Aenderung ohne Verifikation abschliessen
- Bei UI-Aenderungen: Screenshot oder Playwright-Test
- Bei API-Aenderungen: `curl`-Aufruf mit Ergebnis

### Rueckwaertskompatibilitaet
- Neue DB-Spalten: `nullable=True` oder `server_default`
- Neue Config-Werte: sinnvoller Default in `app/config.py`
- API-Responses: nur erweitern, nie Felder entfernen/umbenennen
- Neue Tabellen: Alembic-Migration UND `alembic/env.py` Import

### Docker & Deployment
- Non-root User `dreamline`, Volumes fuer `.claude/` und `.codex/`
- `start.sh`: stderr NICHT unterdruecken (`2>/dev/null` verboten)
- JS/CSS-Aenderungen: Version-Query-String hochzaehlen (`?v=N`) in `dashboard.html`
- CI: `.github/workflows/ci.yml` (lint + tests + migration + docker)

### Dokumentation
- **CHANGELOG.md**: kompakt halten (max ~80 Zeilen), aeltere Eintraege loeschen — `git log` reicht
- Bei groesseren Aenderungen VOR dem Commit aktualisieren

## Skills nutzen

Bei passenden Aufgaben diese Skills/Plugins aktiv einsetzen:
- `/frontend-design` — fuer Dashboard-UI (Vanilla JS + CSS), hochwertige Designs
- `/simplify` — nach Code-Aenderungen: prueft Wiederverwendbarkeit, Qualitaet, Effizienz
- Context7 (MCP) — aktuelle Doku fuer FastAPI, SQLAlchemy, Pydantic nachschlagen statt raten

## Neue Features – Checkliste

1. Model + Migration (nullable/default)
2. Pydantic-Schema erweitern (nie Felder entfernen)
3. Config in `app/config.py` + ggf. `SETTING_DEFINITIONS`
4. Service implementieren (Business-Logik NICHT im Router)
5. Router (duenn: Request → Service → Response)
6. Frontend (dashboard.js + dashboard.html, Version-Bump)
7. Tests schreiben (Happy-Path + Error-Case)
8. CHANGELOG.md aktualisieren
9. Verifizieren: Tests gruen + Docker rebuild + manueller Test

## Referenz

Detail-Dokumentation (CLI-Flags, Hilfsfunktionen-Tabelle, Defaults, Frontend-Konventionen): siehe `docs/reference.md`
