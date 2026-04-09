# Dreamline – Detail-Referenz

Ergaenzende Details zu den Regeln in CLAUDE.md. Nicht als Regelwerk, sondern als Nachschlagewerk.

## Projektstruktur

```
app/
  config.py, database.py, auth.py, main.py
  models/    — project, session, memory, dream, memory_version, runtime_settings
  schemas/   — Pydantic-Schemas
  routers/   — dashboard, projects, sessions, memories, dreams, recall, stats, link, settings, health, auth
  services/  — ai_client (Fassade), ai_common, ai_cli_provider, ai_api_provider
               dreamer, extractor, recaller, memory_writer, session_parser
               hook_installer, session_importer, dream_locks, dream_prompts, dream_sync
               codex_watcher, ollama_modelfile, utils
  worker/    — scheduler (APScheduler)
  static/    — dashboard-core.js, dashboard-*.js, dashboard.css
  templates/ — dashboard.html
alembic/     — DB-Migrationen
tests/       — pytest (162 Tests)
```

## Bestehende Hilfsfunktionen (hier zuerst schauen!)

| Funktion | Datei | Zweck |
|---|---|---|
| `truncate_text()` | `utils.py` | Text kuerzen mit Suffix |
| `escape_js_string()` | `utils.py` | JS-sichere String-Einbettung |
| `decode_claude_dir_name()` | `utils.py` | Claude-Projektname → Dateipfad |
| `guess_display_name()` | `utils.py` | Projektname aus Ordnername |
| `_invoke_cli()` | `ai_common.py` | CLI-Subprocess mit Timeout + stderr-Filter |
| `_with_retry()` | `ai_common.py` | Exponential-Backoff Retry |
| `_estimate_tokens_from_word_count()` | `ai_common.py` | Grobe Token-Schaetzung |
| `_parse_cli_json_output()` | `ai_common.py` | Claude CLI JSON-Output parsen |
| `_strip_cli_warnings()` | `ai_common.py` | Harmlose stderr-Warnungen entfernen |
| `import_claude_sessions()` | `session_importer.py` | .jsonl Sessions importieren |
| `import_codex_sessions()` | `session_importer.py` | Codex Sessions nach cwd importieren |
| `install_hook()` | `hook_installer.py` | Stop-Hook + settings.json registrieren |
| `parse_session_file()` | `session_parser.py` | JSONL parsen (Claude + Codex) |

## CLI-Provider Flags

**Claude:** `claude --print --output-format json --max-turns 5`
**Codex:** `codex exec --full-auto --skip-git-repo-check --ephemeral -m MODEL -`

- NICHT `--quiet` bei Codex (existiert nicht)
- Codex gibt auf stderr aus, nicht stdout — immer beide Streams kombiniert lesen
- Harmlose Warnungen ("Read-only file system", "could not update PATH") filtern

## Defaults (konsistent halten!)

| Parameter | Default | Wo definiert |
|---|---|---|
| ai_provider | `claude-abo` | config.py, projects.py, link.py |
| ai_model | `claude-sonnet-4-5-20250514` | config.py, projects.py, link.py |
| dream_provider | `null` (= ai_provider) | config.py, projects.py, link.py |
| dream_model | `null` (= ai_model) | config.py, projects.py, link.py |
| dream_interval_hours | `12` | config.py, projects.py, link.py |
| min_sessions_for_dream | `3` | config.py, projects.py, link.py |

Wenn Defaults geaendert werden → an ALLEN Stellen gleichzeitig aendern!

## Frontend-Konventionen

- API-Aufrufe: `apiFetch()` Wrapper (Error-Handling + Toast)
- HTML-Escaping: `esc()` bei allen dynamischen Inhalten
- Hint-Boxes: Max 2 Zeilen, erste fett, zweite grau
- Leere Zustaende: immer Handlungsanweisung
- Sprache konsistent: Nav-Button = Tab-Titel (z.B. "Sitzungen" nicht "Sessions")
- Settings-UI: blaue Uppercase-Gruppenheader, 2-Spalten-Grid
- Auth-Status: ALLE Provider anzeigen (Claude + Codex)

## Rate-Limit-Schema

| Typ | Limit | Beispiele |
|---|---|---|
| Reads | `120/min` | Listen, Details, Status |
| Writes | `30/min` | Update, Delete |
| Scans/Imports | `10/min` | Scan, Quick-Add, Provider-Status |
| Schwere Ops | `2-5/min` | Dreams, Ollama-Sync, Settings-Reset |

## Dream-Pipeline (6 Phasen)

Lock → Sessions → Memories → Prompt → AI → Result

- Dual-Lock: DB (DreamLock) + Dateisystem (.consolidate-lock)
- Jede Phase eigenes try/except mit Dream-Protokoll bei Fehler
- Lock immer releasen (finally-Block)
- Memory-Updates: alte Version in `memory_versions` BEVOR Update
- JSON-Parsing: 3 Strategien (direkt → Codeblock → Brace-Matching)
