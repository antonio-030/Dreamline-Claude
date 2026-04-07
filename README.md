# Dreamline v2.0

**Selbstevolvierender KI-Gedächtnis-Konsolidierungsservice** -- Langzeitgedächtnis für KI-Agenten als eigenständiger Docker-Service. Kompatibel mit **Claude Code**, **OpenAI Codex** und **Ollama**.

Dreamline sammelt Chat-Sessions aus verschiedenen KI-Tools, "träumt" periodisch (konsolidiert Wissen per LLM), und pflegt eine kompakte, sich weiterentwickelnde Wissensbasis. Sessions rein, konsolidierte Memories raus -- automatisch gepflegt und dedupliziert.

## Was Dreamline macht

```
Claude Code ─┐
              ├─→  Dreamline sammelt  →  Dream-Engine konsolidiert  →  Memories raus
OpenAI Codex ─┘    (PostgreSQL)          (Claude/Codex/Ollama/API)     (Markdown + AGENTS.md)
```

**Kernfeatures:**
- **Multi-Tool Support**: Claude Code (Hook), OpenAI Codex (Watcher), oder beide gleichzeitig
- **5 Dream-Provider**: Claude (Abo), Codex (Abo), Ollama (lokal), Anthropic (API), OpenAI (API)
- **4-Phasen Dream-Zyklus**: Orient -> Gather -> Consolidate -> Prune
- **4-Typen Memory-System**: user, feedback, project, reference (mit Frontmatter)
- **5-stufiges Gate-System**: Enabled -> Time -> Throttle -> Sessions -> Lock
- **Quick-Extract**: Sofortige Fakten-Extraktion nach jeder Session
- **Smart Recall**: KI-gestützte Relevanzsuche über alle Memories
- **Dual-Lock**: Dateisystem-Lock + DB-Lock verhindert parallele Dreams
- **Cross-Tool Memories**: Memories sind sowohl in Claude Code als auch in Codex verfügbar
- **Web-Dashboard**: Projekte verwalten, Provider wählen, Dreams auslösen

## Schnellstart

```bash
# 1. Klonen und konfigurieren
git clone https://github.com/antonio-030/Dreamline-Claude.git
cd Dreamline-Claude
cp .env.example .env
# DREAMLINE_SECRET_KEY in .env ändern (das ist dein Admin-Passwort!)

# 2. Starten (Docker Desktop muss laufen)
docker compose up -d

# 3. Dashboard öffnen → http://localhost:8100
#    Der Setup-Wizard führt dich durch die Einrichtung:
#    a) Admin-Key eingeben (= DREAMLINE_SECRET_KEY aus .env)
#    b) Einstellungen → "Anmelden" → claude setup-token im Terminal
#    c) Projekte → "Neues Projekt" → Projekt anklicken → fertig!
```

### Manuell per CLI

```bash
# Projekt erstellen (gibt API-Key zurück)
curl -X POST http://localhost:8100/api/v1/projects \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: change-me-in-production" \
  -d '{"name": "Mein Projekt", "ai_provider": "claude-abo", "source_tool": "both"}'

# Session aufzeichnen
curl -X POST http://localhost:8100/api/v1/sessions \
  -H "Authorization: Bearer dl_DEIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "Wie funktioniert das Deployment?"},
      {"role": "assistant", "content": "Deployment via docker compose auf dem Server."}
    ],
    "outcome": "positive"
  }'

# Dream manuell auslösen (oder auf den Scheduler warten)
curl -X POST http://localhost:8100/api/v1/dreams \
  -H "Authorization: Bearer dl_DEIN_API_KEY"

# Memories abrufen
curl "http://localhost:8100/api/v1/recall?query=deployment" \
  -H "Authorization: Bearer dl_DEIN_API_KEY"
```

## Architektur

```
┌──────────────────────────────────────────────────────────┐
│              Dashboard (localhost:8100)                    │
│  Provider-Auswahl · Projekt-Tabs · Dream-Trigger          │
├──────────────────────────────────────────────────────────┤
│                   FastAPI Backend                          │
│                                                            │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐   │
│  │  Sessions     │  │    Dreams    │  │ Quick-Extract │   │
│  │  Router       │  │    Router    │  │   Service     │   │
│  └──────┬───────┘  └──────┬───────┘  └───────┬───────┘   │
│         │                 │                   │            │
│  ┌──────┴─────────────────┴───────────────────┴────────┐  │
│  │           Gate-System (5 Stufen)                     │  │
│  │  Enabled -> Time -> Throttle -> Sessions -> Lock     │  │
│  └─────────────────────┬───────────────────────────────┘  │
│                        │                                   │
│  ┌─────────────────────┴───────────────────────────────┐  │
│  │           Dream-Engine (dreamer.py)                  │  │
│  │                                                      │  │
│  │  claude-abo    codex-sub     ollama     API-Provider  │  │
│  │  (CLI Agent)   (CLI Exec)    (lokal)    (anthropic/   │  │
│  │  + --resume    + gpt-5.2     + Custom     openai)     │  │
│  │  + Tool-Zugriff  -codex       Modelle                 │  │
│  └──────────────────────────────────────────────────────┘  │
├────────────────────────────────────────────────────────────┤
│  Session-Quellen:                                          │
│  ┌─────────────────┐  ┌──────────────────────────┐        │
│  │  Claude Code     │  │  OpenAI Codex             │       │
│  │  Stop-Hook       │  │  Filesystem-Watcher       │       │
│  │  (settings.json) │  │  (pollt ~/.codex/sessions) │      │
│  └─────────────────┘  └──────────────────────────┘        │
├────────────────────────────────────────────────────────────┤
│  Memory-Output:                                            │
│  Claude: ~/.claude/projects/<key>/memory/ + MEMORY.md      │
│  Codex:  <projekt>/.codex/memory/ + AGENTS.md              │
│  Lock:   .consolidate-lock (PID + mtime)                   │
├────────────────────────────────────────────────────────────┤
│  PostgreSQL: sessions, memories, dreams, projects          │
└────────────────────────────────────────────────────────────┘
```

## KI-Provider

### Dream-Provider (wer konsolidiert)

| Provider | Beschreibung | API-Key nötig? | Modell |
|----------|-------------|-----------------|--------|
| `claude-abo` | Claude CLI mit bestehendem Abo (Standard) | Nein | claude-sonnet-4-5 |
| `codex-sub` | Codex CLI mit OpenAI Plus/Pro Abo | Nein | gpt-5.2-codex |
| `ollama` | Lokale LLMs, Custom-Modelle mit Memories | Nein | z.B. llama3.2:latest |
| `anthropic` | Anthropic API mit Prompt-Caching | Ja | claude-sonnet-4-5 |
| `openai` | OpenAI API mit JSON-Mode | Ja | gpt-4o |

### Session-Quellen (woher kommen Sessions)

| Source-Tool | Mechanismus | Beschreibung |
|-------------|------------|-------------|
| `claude` | Stop-Hook in `settings.json` | Sendet nach jeder Claude Code Session |
| `codex` | Filesystem-Watcher (alle 120s) | Pollt `~/.codex/sessions/` auf neue Dateien |
| `both` | Hook + Watcher gleichzeitig | Sessions aus beiden Tools |

**Empfehlung:** `claude-abo` als Dream-Provider nutzt das bestehende Abo ohne zusätzliche Kosten. Für Codex-Nutzer bietet `codex-sub` dasselbe. Ollama ist komplett kostenlos und offline.

## Dashboard

Das Web-Dashboard unter `http://localhost:8100` bietet:

- **Projekte verwalten**: Erstellen, bearbeiten, löschen
- **Provider wählen**: Dream-Provider und Session-Quelle pro Projekt konfigurierbar
- **Zwei Projekt-Tabs**: Claude Code Projekte und OpenAI Codex Projekte getrennt scannen
- **Dreams auslösen**: Manuell oder automatisch per Scheduler
- **Session-Import**: Vorhandene Sessions aus Claude und Codex importieren
- **Memory-Übersicht**: Alle konsolidierten Memories einsehen
- **Statistiken**: Token-Verbrauch, Dream-Verlauf, Session-Anzahl
- **Ollama-Sync**: Custom-Modelle mit Memories als System-Prompt erstellen

## API-Referenz

Alle Endpoints (außer `/health`) benötigen einen Bearer-Token im `Authorization`-Header.

### Kern-Endpoints

| Methode | Endpoint | Beschreibung |
|---------|----------|-------------|
| GET | `/health` | Health-Check |
| POST | `/api/v1/projects` | Projekt erstellen (Admin-Key) |
| GET | `/api/v1/projects` | Projekte auflisten (Admin-Key) |
| PATCH | `/api/v1/projects/{id}` | Projekt bearbeiten (Provider, Source-Tool, etc.) |
| DELETE | `/api/v1/projects/{id}` | Projekt löschen |
| POST | `/api/v1/sessions` | Chat-Session aufzeichnen |
| GET | `/api/v1/sessions` | Sessions auflisten |
| POST | `/api/v1/dreams` | Dream manuell auslösen |
| GET | `/api/v1/dreams` | Dream-Verlauf |
| GET | `/api/v1/dreams/status` | Aktueller Dream-Status |
| GET | `/api/v1/memories` | Alle Memories auflisten |
| GET | `/api/v1/recall?query=...` | Relevante Memories suchen |
| GET | `/api/v1/stats` | Aggregierte Statistiken |

### Link-Endpoints (Projekt-Verknüpfung)

| Methode | Endpoint | Beschreibung |
|---------|----------|-------------|
| GET | `/api/v1/link/scan` | Lokale Claude-Projekte scannen |
| GET | `/api/v1/link/scan-codex` | Lokale Codex-Projekte scannen |
| POST | `/api/v1/link/quick-add` | One-Click Setup (Claude) |
| POST | `/api/v1/link/quick-add-codex` | One-Click Setup (Codex) |
| POST | `/api/v1/link` | Projekt mit lokalem Pfad verknüpfen |
| POST | `/api/v1/link/import-sessions/{id}` | Lokale Sessions importieren |
| POST | `/api/v1/link/sync/{id}` | Memories ins Projekt schreiben |

### Ollama-Endpoints

| Methode | Endpoint | Beschreibung |
|---------|----------|-------------|
| POST | `/api/v1/projects/{id}/ollama/sync` | Custom-Modell mit Memories erstellen |
| GET | `/api/v1/projects/{id}/ollama/status` | Ollama-Modell Status |

## Konfiguration

### Umgebungsvariablen (.env)

| Variable | Default | Beschreibung |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://...` | PostgreSQL-Verbindung |
| `DB_PASSWORD` | `dreamline_secret` | PostgreSQL-Passwort |
| `DREAMLINE_SECRET_KEY` | `change-me-in-production` | Admin-Key für Projektverwaltung |
| `DEFAULT_AI_PROVIDER` | `claude-abo` | Standard-Provider |
| `ANTHROPIC_API_KEY` | (leer) | Nur für `anthropic`-Provider |
| `OPENAI_API_KEY` | (leer) | Nur für `openai`-Provider |

### Codex-Watcher

| Variable | Default | Beschreibung |
|----------|---------|-------------|
| `CODEX_WATCHER_ENABLED` | `false` | Codex-Session-Watcher aktivieren |
| `CODEX_WATCHER_INTERVAL_SECONDS` | `120` | Polling-Intervall in Sekunden |
| `CODEX_SESSIONS_DIR` | (auto) | Pfad zu `~/.codex/sessions/` (auto-detect) |

### Ollama

| Variable | Default | Beschreibung |
|----------|---------|-------------|
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Ollama-Server URL |
| `OLLAMA_TIMEOUT` | `120.0` | Timeout in Sekunden |
| `OLLAMA_MODELFILE_SYNC` | `true` | Custom-Modell nach Dream aktualisieren |

### autoDream-Parameter

| Variable | Default | Beschreibung |
|----------|---------|-------------|
| `AUTODREAM_ENABLED` | `true` | Automatische Dreams an/aus |
| `AUTODREAM_MIN_HOURS` | `12` | Mindestabstand zwischen Dreams |
| `AUTODREAM_MIN_SESSIONS` | `3` | Mindestanzahl neuer Sessions für Dream |
| `AUTODREAM_SCAN_THROTTLE_MINUTES` | `10` | Pause zwischen Gate-Checks |
| `DREAM_CHECK_INTERVAL_MINUTES` | `60` | Scheduler-Intervall |
| `EXTRACT_EVERY_N_SESSIONS` | `1` | Quick-Extract alle N Sessions |

## Session-Erfassung

### Claude Code (Hook-basiert)

Dreamline registriert automatisch einen Stop-Hook in Claude Code. Nach jeder Session wird ein Node.js-Script ausgeführt das die Session an Dreamline sendet.

```
Claude Code Session endet
  -> Stop-Hook feuert
  -> dreamline-sync.cjs liest Session-Daten
  -> POST /api/v1/sessions
  -> Quick-Extract (optional)
  -> autoDream Gate-Check
```

### OpenAI Codex (Watcher-basiert)

Codex hat kein Hook-System. Dreamline nutzt einen Background-Worker der `~/.codex/sessions/` pollt:

```
Codex Session endet
  -> Session-Datei in ~/.codex/sessions/YYYY/MM/DD/ geschrieben
  -> Codex-Watcher erkennt neue Datei (alle 120s)
  -> Session-Parser extrahiert Messages + CWD
  -> CWD wird auf Dreamline-Projekt gemappt
  -> Session in DB importiert
  -> Quick-Extract + autoDream
```

Der Codex-Parser erkennt automatisch das Codex-JSONL-Format und filtert System-Messages (AGENTS.md-Instruktionen, Permissions) heraus.

## Memory-Output

Dreamline schreibt Memories als Markdown-Dateien mit YAML-Frontmatter:

```markdown
---
name: deployment-workflow
description: Standard-Deployment-Prozess für Produktion
type: reference
confidence: 0.95
---

Deployment erfolgt via docker compose auf dem Produktionsserver.
Schritte: git push, SSH auf Server, docker compose up -d --build.
```

### Wo landen die Memories?

| Tool | Memory-Pfad | Index-Datei |
|------|------------|-------------|
| Claude Code | `~/.claude/projects/<key>/memory/` | `MEMORY.md` |
| OpenAI Codex | `<projekt>/.codex/memory/` | `AGENTS.md` (Dreamline-Section) |

Bei `source_tool: "both"` werden Memories an beide Orte geschrieben.

**Memory-Typen:**
- `user` -- Infos über den Nutzer (Rolle, Vorlieben, Wissen)
- `feedback` -- Was funktioniert / was nicht (mit Warum + Wann anwenden)
- `project` -- Projekt-Fakten die nicht aus dem Code ableitbar sind
- `reference` -- Verweise auf externe Systeme und Ressourcen

## Projektstruktur

```
Dreamline/
├── app/
│   ├── main.py              # FastAPI App + Lifespan
│   ├── config.py             # Konfiguration aus .env
│   ├── auth.py               # API-Key Authentifizierung
│   ├── database.py           # PostgreSQL AsyncSession
│   ├── models/
│   │   ├── dream.py          # Dream + DreamLock
│   │   ├── memory.py         # Memory (4 Typen)
│   │   ├── project.py        # Projekt + API-Key + source_tool
│   │   └── session.py        # Chat-Session
│   ├── routers/
│   │   ├── sessions.py       # POST/GET/DELETE Sessions
│   │   ├── dreams.py         # Dream-Trigger + Status
│   │   ├── memories.py       # Memory-Verwaltung
│   │   ├── recall.py         # Relevanz-Suche
│   │   ├── projects.py       # Projekt-Verwaltung
│   │   ├── link.py           # Hook-Setup + Codex-Scan
│   │   ├── stats.py          # Statistiken
│   │   ├── auth.py           # Login/Status
│   │   ├── dashboard.py      # Web-Dashboard
│   │   └── health.py         # Health-Check
│   ├── services/
│   │   ├── dreamer.py        # Dream-Engine (Kern)
│   │   ├── extractor.py      # Quick-Extract
│   │   ├── ai_client.py      # Multi-Provider (Claude/Codex/Ollama/API)
│   │   ├── session_parser.py # Unified Parser (Claude + Codex JSONL)
│   │   ├── codex_watcher.py  # Codex Session-Watcher
│   │   ├── recaller.py       # Relevanz-Suche
│   │   ├── memory_writer.py  # Markdown + AGENTS.md Export
│   │   └── ollama_modelfile.py # Ollama Custom-Modelle
│   ├── worker/
│   │   └── scheduler.py      # APScheduler + Gates + Codex-Watcher
│   └── templates/
│       └── dashboard.html    # Web-Dashboard UI
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env
```

## Changelog

### v2.0 -- Multi-Tool Support (2026-04-03)

- **OpenAI Codex Integration**: Session-Erfassung per Filesystem-Watcher, Codex-JSONL-Parser, `scan-codex` und `quick-add-codex` Endpoints
- **Codex als Dream-Provider**: `codex-sub` nutzt die Codex CLI mit OpenAI Plus/Pro Abo
- **Unified Session-Parser**: Erkennt automatisch Claude- und Codex-JSONL-Formate
- **AGENTS.md Support**: Memories werden auch in Codex' AGENTS.md geschrieben
- **Dashboard Provider-Auswahl**: Dream-Provider (Claude/Codex/Ollama/API) und Session-Quelle (Claude/Codex/beide) im Dashboard wählbar
- **`source_tool` Feld**: Projekte können auf `claude`, `codex` oder `both` konfiguriert werden
- **Docker**: Codex CLI im Container installiert, `~/.codex` Volume-Mount

### v1.0 -- Initiales Release

- Dream-Engine mit 4-Phasen-Zyklus
- Claude Code Hook-Integration
- 4 Provider: claude-abo, anthropic, openai, ollama
- Web-Dashboard mit Projektverwaltung
- Quick-Extract, Smart Recall, Dual-Lock
- Ollama Custom-Modelle mit Memories als System-Prompt

## Lizenz

MIT
