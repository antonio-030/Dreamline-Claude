# Dreamline – Changelog

Interne Entwicklungsdokumentation. Wird bei jeder Aenderung gepflegt, damit KI-Agenten
und Entwickler sofort den Kontext haben: Was wurde geaendert, warum, und was ist noch offen.

---

## [2026-04-09] Qualitaets-Offensive + Codex-Integration

### Codex-CLI repariert und robust gemacht
- **Problem**: Codex-Dreams schlugen fehl — `--quiet` existiert nicht bei `codex exec`,
  fehlender `--skip-git-repo-check` fuer Docker, kein Model-Durchreichen.
- **Fix**: `--full-auto --skip-git-repo-check --ephemeral -m MODEL -` als korrekte Args.
- **Stderr**: Codex gibt alles auf stderr aus (auch Login-Status). `_invoke_cli()` kombiniert
  jetzt stdout+stderr und filtert harmlose Docker-Warnungen raus.
- **Zombie-Schutz**: `await process.wait()` nach `process.kill()` bei Timeout.
- **Leere Antworten**: Werfen jetzt `RuntimeError` statt still weitergegeben zu werden.

### Tests von 0 auf 162
- **Blocker**: `import anthropic` und `import openai` auf Modul-Ebene in `ai_client.py`
  blockierten alle Tests wenn die Pakete nicht installiert waren.
- **Fix**: Lazy Imports (in-function) fuer `anthropic` und `openai` in den 5 Funktionen
  die sie brauchen. Tests importieren nur die Hilfsfunktionen.
- **Neue Testdateien**: `test_extractor.py` (11 Tests), `test_sessions_router.py` (5 Tests).
- **pyproject.toml** erstellt mit pytest-Konfiguration (`asyncio_mode = "auto"`).
- Tests laufen im Docker-Container (`pip install pytest pytest-asyncio`).

### ai_client.py aufgeteilt (706 -> 4 Module)
- `ai_common.py` (249 Z.) — Retry, Token-Schaetzung, CLI-JSON-Parsing, stderr-Filter, `_invoke_cli()`
- `ai_cli_provider.py` (187 Z.) — Claude-Abo + Codex-Sub CLI-Aufrufe
- `ai_api_provider.py` (179 Z.) — Anthropic + OpenAI API + Ollama
- `ai_client.py` (175 Z.) — Public API Fassade mit Re-Exports fuer Rueckwaertskompatibilitaet

### except Exception eingegrenzt (37 -> 9)
- 28 Stellen durch spezifische Typen ersetzt: `json.JSONDecodeError`, `OSError`,
  `ValueError`, `RuntimeError`, `UnicodeError`, `PermissionError`, etc.
- 9 verbliebene sind alle legitimierte Top-Level Catch-Alls (Scheduler, Health, Background-Tasks).

### link.py Service-Extraktion (823 -> 569 Zeilen)
- `hook_installer.py` (neu) — Hook-Installation + settings.json-Registrierung
- `session_importer.py` (neu) — Claude + Codex Session-Import (waren dupliziert)
- `utils.py` erweitert — `decode_claude_dir_name()`, `guess_display_name()`, `escape_js_string()`

### Rate Limits auf alle 32 Endpoints
- Schema: Reads `120/min`, Writes `30/min`, Scans/Imports `10/min`, schwere Ops `2-5/min`
- Vorher nur 4 Endpoints geschuetzt (dreams, sessions, recall, memory-import).

### Import-Validierung
- Memory-Import: `content max_length=50_000`, Liste max 500 Items.
- Codex Session-Importer: `max_messages=50` konsistent mit Claude-Import.

### start.sh Migrationsfehler sichtbar
- Vorher: `alembic upgrade head 2>/dev/null || echo "uebersprungen"` (schluckt Fehler).
- Jetzt: stderr sichtbar, Warnung bei Fehler, kein `exit 1` (create_tables als Fallback).

### Dream-Pipeline robuster
- Phase 5 (`_process_result`) hat jetzt eigenes try/except mit Dream-Protokoll.
- `_parse_dream_operations`: Sucht jetzt ALLE `{...}` Bloecke, nicht nur den ersten.
  Input-Limit 500KB gegen extrem grosse Antworten.

### UI/UX: Codex-Onboarding
- Setup-Wizard: "KI-Provider anmelden" statt nur "Claude-Abo anmelden".
- Einstellungen: Zwei-Spalten Auth-Karte (Claude + Codex Status nebeneinander).
- Auth-Modal: Tabs fuer Claude (Token-Flow) und Codex (Terminal-Login mit kopierbaren Befehlen).
- Codex-Auth-Endpoint: `_check_codex_cli_auth()` liest `codex login status`, parallel via `asyncio.gather()`.

### Dream-Provider separat konfigurierbar
- Neue DB-Spalten: `dream_provider`, `dream_model` auf Project (nullable).
- UI: Checkbox "Separaten Dream-Provider verwenden" im Projekt-Editor und Scan-Popup.
- Scheduler + Dream-Trigger nutzen `project.dream_provider or project.ai_provider`.

### CI-Pipeline
- `.github/workflows/ci.yml` — 4 Jobs: lint (ruff), tests (pytest), migration check, docker build.

### CLAUDE.md erweitert
- Neue Abschnitte: Tests, Router-Architektur, CLI-Provider, Docker.
- Session-Learnings als verbindliche Regeln aufgenommen.

---

## Offene Punkte / Naechste Schritte

- **Security**: Dashboard-Auth von sessionStorage auf serverseitige Session/HttpOnly Cookie umstellen (P2)
- **Observability**: request_id/project_id Korrelation ueber alle Logs (P1)
- **Contract Tests**: Session-Ingest -> Quick-Extract -> Dream-Trigger -> Memory-Sync End-to-End
- **Load Tests**: Session-Volumen, Scheduler-Last, Provider-Timeout Recovery
- **Audit-Report**: `docs/repo-audit-report.md` ist veraltet (Stand vor Qualitaets-Offensive) — aktualisieren
