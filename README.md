# Dreamline

**Selbstevolvierender KI-Gedächtnis-Konsolidierungsservice** -- Langzeitgedächtnis für KI-Agenten als eigenständiger Docker-Service.

Dreamline sammelt Chat-Sessions, "träumt" periodisch (konsolidiert Wissen per LLM), und pflegt eine kompakte, sich weiterentwickelnde Wissensbasis. Sessions rein, konsolidierte Memories raus -- automatisch gepflegt und dedupliziert.

## Was Dreamline macht

```
Session rein  -->  Dreamline sammelt  -->  Dream-Engine konsolidiert  -->  Memories raus
(Hook/API)          (PostgreSQL)            (LLM)                         (Markdown-Dateien)
```

**Kernfeatures:**
- **4-Phasen Dream-Zyklus**: Orient -> Gather -> Consolidate -> Prune
- **4-Typen Memory-System**: user, feedback, project, reference (mit Frontmatter)
- **5-stufiges Gate-System**: Enabled -> Time -> Throttle -> Sessions -> Lock
- **Quick-Extract**: Sofortige Fakten-Extraktion nach jeder Session
- **Smart Recall**: KI-gestützte Relevanzsuche über alle Memories
- **Dual-Lock**: Dateisystem-Lock + DB-Lock verhindert parallele Dreams
- **Prompt-Caching**: CLI nutzt internen Cache (11.000+ Cache-Read-Tokens beobachtet)

## Schnellstart

```bash
# 1. Klonen und konfigurieren
git clone https://github.com/antonio-030/Dreamline-Claude.git
cd Dreamline-Claude
cp .env.example .env
# .env anpassen (Passwort ändern!)

# 2. Starten (Docker Desktop muss laufen)
docker compose up -d

# 3. Projekt erstellen (gibt API-Key zurück)
curl -X POST http://localhost:8100/api/v1/projects \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: change-me-in-production" \
  -d '{"name": "Mein Projekt", "ai_provider": "claude-abo"}'

# 4. Session aufzeichnen
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

# 5. Dream manuell auslösen (oder auf den Scheduler warten)
curl -X POST http://localhost:8100/api/v1/dreams \
  -H "Authorization: Bearer dl_DEIN_API_KEY"

# 6. Memories abrufen
curl "http://localhost:8100/api/v1/recall?query=deployment" \
  -H "Authorization: Bearer dl_DEIN_API_KEY"
```

## Architektur

```
┌──────────────────────────────────────────────────────┐
│                   Dashboard (localhost:8100)          │
├──────────────────────────────────────────────────────┤
│                   FastAPI Backend                     │
│                                                      │
│  ┌────────────┐  ┌────────────┐  ┌───────────────┐  │
│  │  Sessions   │  │   Dreams   │  │ Quick-Extract │  │
│  │  Router     │  │   Router   │  │   Service     │  │
│  └─────┬──────┘  └─────┬──────┘  └───────┬───────┘  │
│        │               │                  │          │
│  ┌─────┴───────────────┴──────────────────┴───────┐  │
│  │          Gate-System (5 Stufen)                 │  │
│  │  Enabled -> Time -> Throttle -> Sessions -> Lock│  │
│  └──────────────────────┬─────────────────────────┘  │
│                         │                            │
│  ┌──────────────────────┴─────────────────────────┐  │
│  │          Dream-Engine (dreamer.py)              │  │
│  │                                                 │  │
│  │  Agent-Modus (CLI)           JSON-Modus (API)   │  │
│  │  - CLI mit Tool-Zugriff      - Prompt -> JSON   │  │
│  │  - Schreibt direkt Dateien   - Dreamline wendet │  │
│  │  - --resume Cache-Sharing      Operationen an   │  │
│  │                                                 │  │
│  │  Memory-Writer: Markdown + MEMORY.md Index      │  │
│  └─────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────┤
│  Volume: ~/.claude/projects/<name>/memory/            │
│  Lock:   .consolidate-lock (PID + mtime)              │
│  Index:  MEMORY.md (max 200 Zeilen, ~25KB)            │
│  Files:  *.md (Frontmatter + Content)                 │
├──────────────────────────────────────────────────────┤
│  PostgreSQL: sessions, memories, dreams, projects     │
└──────────────────────────────────────────────────────┘
```

## KI-Provider

| Provider | Beschreibung | API-Key nötig? |
|----------|-------------|-----------------|
| `claude-abo` | Claude CLI mit bestehendem Abo (Standard) | Nein |
| `anthropic` | Anthropic API direkt mit Prompt-Caching | Ja |
| `openai` | OpenAI API mit JSON-Mode | Ja |

**Empfehlung:** `claude-abo` nutzt das bestehende Abo ohne zusätzliche Kosten. Die CLI authentifiziert sich automatisch über `~/.claude/.credentials.json`.

## API-Referenz

Alle Endpoints (außer `/health`) benötigen einen Bearer-Token im `Authorization`-Header.

### Kern-Endpoints

| Methode | Endpoint | Beschreibung |
|---------|----------|-------------|
| GET | `/health` | Health-Check |
| POST | `/api/v1/projects` | Projekt erstellen (Admin-Key) |
| GET | `/api/v1/projects` | Projekte auflisten (Admin-Key) |
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
| GET | `/api/v1/link/scan` | Lokale Projekte scannen |
| POST | `/api/v1/link/quick-add` | One-Click Projekt-Setup |
| POST | `/api/v1/link` | Projekt mit lokalem Pfad verknüpfen |

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

### autoDream-Parameter

| Variable | Default | Beschreibung |
|----------|---------|-------------|
| `AUTODREAM_ENABLED` | `true` | Automatische Dreams an/aus |
| `AUTODREAM_MIN_HOURS` | `12` | Mindestabstand zwischen Dreams |
| `AUTODREAM_MIN_SESSIONS` | `3` | Mindestanzahl neuer Sessions für Dream |
| `AUTODREAM_SCAN_THROTTLE_MINUTES` | `10` | Pause zwischen Gate-Checks |
| `DREAM_CHECK_INTERVAL_MINUTES` | `60` | Scheduler-Intervall |
| `EXTRACT_EVERY_N_SESSIONS` | `1` | Quick-Extract alle N Sessions |

## Hook einrichten

Dreamline wird automatisch mit Sessions gefüttert über einen Hook in der KI-Agenten-Konfiguration:

```bash
# Hook-Script vom Server abrufen (nach Projekt-Erstellung)
curl "http://localhost:8100/api/v1/link/hook/PROJEKT_ID" \
  -H "Authorization: Bearer dl_DEIN_API_KEY" \
  -o .claude/helpers/dreamline-sync.cjs

# Oder: One-Click Setup über das Dashboard
curl -X POST "http://localhost:8100/api/v1/link/quick-add" \
  -H "X-Admin-Key: change-me-in-production" \
  -H "Content-Type: application/json" \
  -d '{"project_path": "/pfad/zu/deinem/projekt"}'
```

Der Hook wird als `Stop`-Event registriert und sendet nach jeder Session die Konversation an Dreamline.

## Memory-Format

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

**Memory-Typen:**
- `user` -- Infos über den Nutzer (Rolle, Vorlieben, Wissen)
- `feedback` -- Was funktioniert / was nicht (mit Warum + Wann anwenden)
- `project` -- Projekt-Fakten die nicht aus dem Code ableitbar sind
- `reference` -- Verweise auf externe Systeme und Ressourcen

## So funktioniert der Dream-Zyklus

1. **Record** -- Deine Anwendung sendet Chat-Sessions an Dreamline via `POST /api/v1/sessions`.

2. **Accumulate** -- Sessions werden gespeichert und als "unconsolidated" markiert.

3. **Dream** -- Wenn genug Sessions da sind (konfigurierbar), startet die Dream-Engine:
   - Lädt alle unverarbeiteten Sessions
   - Lädt bestehende Memories
   - Baut einen Konsolidierungs-Prompt
   - Ruft das konfigurierte LLM auf
   - Wendet die Operationen an (create, update, delete)
   - Markiert Sessions als konsolidiert
   - Schreibt Memories als Markdown ins Projekt-Verzeichnis

4. **Recall** -- Deine Anwendung fragt Memories ab via `GET /api/v1/recall?query=...`.

Dreams können manuell (`POST /api/v1/dreams`) oder automatisch per Scheduler ausgelöst werden.

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
│   │   ├── project.py        # Projekt + API-Key
│   │   └── session.py        # Chat-Session
│   ├── routers/
│   │   ├── sessions.py       # POST/GET/DELETE Sessions
│   │   ├── dreams.py         # Dream-Trigger + Status
│   │   ├── memories.py       # Memory-Verwaltung
│   │   ├── recall.py         # Relevanz-Suche
│   │   ├── projects.py       # Projekt-Verwaltung
│   │   ├── link.py           # Hook-Setup + Scanning
│   │   ├── stats.py          # Statistiken
│   │   ├── auth.py           # Login/Status
│   │   ├── dashboard.py      # Web-Dashboard
│   │   └── health.py         # Health-Check
│   ├── services/
│   │   ├── dreamer.py        # Dream-Engine (Kern)
│   │   ├── extractor.py      # Quick-Extract
│   │   ├── ai_client.py      # CLI/API Wrapper
│   │   ├── recaller.py       # Relevanz-Suche
│   │   └── memory_writer.py  # Markdown-Export
│   ├── worker/
│   │   └── scheduler.py      # APScheduler + Gates
│   └── schemas/              # Pydantic Schemas
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

## Lizenz

MIT
