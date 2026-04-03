# Dreamline autoDream 1:1 Angleichung – Design-Spec

**Datum:** 2026-04-02
**Ziel:** Dreamline's Dream-Engine exakt wie Claude Code's autoDream implementieren
**Referenz:** claude-code-study/services/autoDream/, services/extractMemories/, tasks/DreamTask/

---

## Status Quo

Dreamline hat bereits:
- Memory-Pfad `~/.claude/projects/<name>/memory/` ✓
- MEMORY.md Index (200 Zeilen, ~25KB) ✓
- 4-Phasen Prompt (Orient → Gather → Consolidate → Prune) ✓
- `.consolidate-lock` Respektierung ✓
- 4 Memory-Typen + Frontmatter ✓
- Claude CLI Agent-Modus mit Tool-Zugriff ✓
- PostgreSQL DB für Sessions/Memories/Dreams ✓
- Dashboard auf localhost:8100 ✓

---

## Lücke 1: Tool-Einschränkung (createAutoMemCanUseTool)

### Problem
Der Dream-Agent hat uneingeschränkten Zugriff auf alle Tools. Er könnte Dateien außerhalb des Memory-Verzeichnisses ändern.

### Claude Code Referenz
`extractMemories.ts:171-222` – `createAutoMemCanUseTool(memoryDir)`:
- Read/Grep/Glob: unbeschränkt
- Bash: nur read-only (ls, grep, cat, head, tail, stat, wc, find)
- Write/Edit: nur innerhalb `memoryDir` (`isAutoMemPath()` Check)
- Alles andere: DENIED

### Implementierung
Datei: `app/services/ai_client.py` → `_dream_claude_abo_agent()`

Claude CLI unterstützt `--allowedTools` nicht granular genug für Pfad-Einschränkungen.
Stattdessen: **System-Prompt Einschränkung** + **Post-Dream Validierung**.

```python
# Im System-Prompt an den Agent:
TOOL_CONSTRAINTS = f"""
**Tool constraints for this run:**
- Bash is restricted to read-only commands (ls, find, grep, cat, stat, wc, head, tail).
  Anything that writes, redirects to a file, or modifies state will be denied.
- You may ONLY write/edit files inside: {memory_dir}
- Do NOT modify any files outside the memory directory.
- Do NOT use MCP tools, WebSearch, or Agent tools.
"""
```

Post-Dream Validierung in `_sync_files_to_db()`:
- Prüfe ob der Agent nur Dateien im Memory-Verzeichnis geändert hat
- Logge Warnung wenn Dateien außerhalb gefunden werden

### Aufwand: Klein (Prompt + Validierung)

---

## Lücke 2: extractMemories (Quick-Extract nach jedem Query)

### Problem
Claude Code extrahiert nach JEDEM Query sofort offensichtliche Fakten. Dreamline macht das nur beim Dream-Zyklus (alle paar Stunden).

### Claude Code Referenz
`extractMemories.ts` – Komplettes System:
- Cursor-basiert (`lastMemoryMessageUuid`)
- Coalescing (stasht wenn schon running)
- Trailing runs
- `maxTurns: 5`
- Mutual Exclusion mit Hauptagent

### Implementierung
Das Hook-System von Dreamline sendet bereits nach jeder Session Daten.
Der Hook IST der extractMemories-Equivalent.

**Verbesserung:** Der Hook soll nicht nur Session-Daten senden, sondern der Backend-Quick-Extract soll diese sofort verarbeiten.

Datei: `app/services/extractor.py` (existiert bereits als `quick_extract`)

Aktueller Flow:
```
Hook sendet Session → POST /api/v1/sessions → quick_extract() im Hintergrund
```

Was fehlt:
1. **Cursor-Tracking**: `last_extracted_message_id` pro Projekt in DB
2. **Overlap-Prevention**: Flag `is_extracting` pro Projekt
3. **Trailing Run**: Nach Abschluss prüfen ob neue Session reinkam
4. **maxTurns: 5**: Limit für den Quick-Extract Agent

Neue Datei: `app/services/quick_extractor.py`

```python
class QuickExtractor:
    """1:1 wie Claude Code extractMemories – Cursor-basiert, coalesced."""

    def __init__(self, project_id: UUID):
        self.project_id = project_id
        self.is_extracting = False
        self.pending_session_id: UUID | None = None
        self.last_cursor: datetime | None = None

    async def extract(self, session: Session, db: AsyncSession):
        if self.is_extracting:
            # Coalescing: stash für trailing run
            self.pending_session_id = session.id
            return

        self.is_extracting = True
        try:
            await self._run_extraction(session, db)

            # Trailing run
            while self.pending_session_id:
                next_id = self.pending_session_id
                self.pending_session_id = None
                next_session = await db.get(Session, next_id)
                if next_session:
                    await self._run_extraction(next_session, db)
        finally:
            self.is_extracting = False

    async def _run_extraction(self, session: Session, db: AsyncSession):
        # Mutual Exclusion: Prüfe ob Hauptagent seit Cursor geschrieben hat
        # ... KI-Call mit maxTurns=5 ...
        # Cursor aktualisieren
        self.last_cursor = session.created_at
```

### Aufwand: Mittel (neuer Service + DB-Schema-Erweiterung)

---

## Lücke 3: Session-Discovery (listSessionsTouchedSince)

### Problem
Dreamline lädt alle unconsolidated Sessions. Claude Code nutzt mtime-basierte Discovery.

### Claude Code Referenz
`consolidationLock.ts:118-124`:
```typescript
const candidates = await listCandidates(dir, true)  // stat jede .jsonl
return candidates.filter(c => c.mtime > sinceMs).map(c => c.sessionId)
```

### Implementierung
Dreamline nutzt PostgreSQL statt Dateisystem. Der äquivalente Ansatz:

```python
# Statt: SELECT * FROM sessions WHERE is_consolidated = false
# Besser: SELECT * FROM sessions WHERE created_at > last_dream_at
stmt = (
    select(Session)
    .where(Session.project_id == project_id)
    .where(Session.created_at > last_consolidated_at)
    .order_by(Session.created_at.asc())
)
```

Plus **Scan-Throttle**: Nicht bei jedem Scheduler-Tick alle Projekte prüfen.

```python
# In worker/scheduler.py:
last_scan_at: dict[UUID, float] = {}  # pro Projekt
SCAN_THROTTLE_MS = 10 * 60 * 1000  # 10 Minuten

async def check_project(project_id):
    now = time.time() * 1000
    if now - last_scan_at.get(project_id, 0) < SCAN_THROTTLE_MS:
        return  # Throttled
    last_scan_at[project_id] = now
    # ... Session-Gate prüfen ...
```

### Aufwand: Klein (Query-Optimierung + Throttle)

---

## Lücke 4: Forked Agent Architecture

### Problem
Claude Code nutzt `runForkedAgent()` mit Cache-Sharing und isoliertem Context. Dreamline startet `claude --print` als separaten Prozess.

### Claude Code Referenz
`forkedAgent.ts:489-626`:
- Cache-Sharing via `cacheSafeParams`
- Isolierter Context via `createSubagentContext`
- `onMessage` Callback für Progress
- `skipTranscript: true`

### Implementierung
Da Dreamline die Claude CLI extern aufruft (nicht als Node.js Fork), ist echtes Cache-Sharing nicht möglich. Aber wir können:

1. **`--resume` Flag**: Claude CLI kann eine bestehende Session fortsetzen → gleicher Cache
2. **`--output-format json`**: Strukturierte Antworten mit Token-Usage
3. **Streaming**: `--stream` für Progress-Updates (statt `onMessage`)

```python
async def _dream_claude_abo_agent(prompt, memory_dir):
    process = await asyncio.create_subprocess_exec(
        claude_path,
        "--print",
        "--output-format", "json",
        "--permission-mode", "bypassPermissions",
        "--max-turns", "20",  # Limit wie Claude Code
        "--append-system-prompt", tool_constraints,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=memory_dir,
    )
```

**Realistisch:** Cache-Sharing ist ein Claude-internes Feature. Wir können es nicht von außen nutzen. Das ist OK – der Container läuft als eigenständiger Service, nicht als Fork.

### Aufwand: Klein (CLI-Parameter-Optimierung)

---

## Lücke 5: DreamTask UI (Progress-Tracking im Dashboard)

### Problem
Das Dashboard zeigt keinen Dream-Fortschritt. Claude Code hat einen vollständigen Task-Tracker.

### Claude Code Referenz
`DreamTask.ts`:
- Phase: `starting` → `updating`
- `filesTouched[]`
- `turns[]` (max 30)
- Kill-Handler

### Implementierung
Neues DB-Model: `DreamProgress`

```python
class DreamProgress(Base):
    __tablename__ = "dream_progress"

    id = mapped_column(UUID, primary_key=True, default=uuid4)
    dream_id = mapped_column(UUID, ForeignKey("dreams.id"), nullable=False)
    phase = mapped_column(String(20), default="starting")  # starting, updating, completed, failed
    files_touched = mapped_column(Text, default="[]")  # JSON Array
    turns = mapped_column(Text, default="[]")  # JSON Array [{text, tool_count}]
    created_at = mapped_column(DateTime, server_default=func.now())
    updated_at = mapped_column(DateTime, onupdate=func.now())
```

Dashboard-Erweiterung:
- SSE (Server-Sent Events) Endpoint: `GET /api/v1/dreams/progress/{dream_id}`
- Oder Polling: Dashboard fragt alle 5 Sekunden nach

Frontend:
```javascript
// Im Dreams-Tab: Live-Progress anzeigen wenn ein Dream läuft
async function watchDreamProgress(dreamId) {
    const interval = setInterval(async () => {
        const progress = await apiFetch(`/api/v1/dreams/progress/${dreamId}`, ...);
        updateDreamUI(progress);
        if (progress.phase === 'completed' || progress.phase === 'failed') {
            clearInterval(interval);
        }
    }, 5000);
}
```

### Aufwand: Mittel (DB-Model + API + Frontend)

---

## Lücke 6: Prompt-Angleichung (Transcript-Zugriff)

### Problem
Claude Code's Dream-Agent greped die JSONL-Transcripts direkt. Dreamline gibt dem Agent nur Session-Daten im Prompt.

### Claude Code Referenz
`consolidationPrompt.ts:39-41`:
```
grep -rn "<narrow term>" ${transcriptDir}/ --include="*.jsonl" | tail -50
```

### Implementierung
Da der Container `~/.claude/projects/` gemountet hat, kann der Agent die Transcripts direkt grepen!

Prompt-Änderung in `dreamer.py`:
```python
# Statt: Session-Daten im Prompt
# Besser: Transcript-Pfad angeben + Agent grepen lassen

transcript_dir = str(CLAUDE_PROJECTS_DIR / project_dir_name)

prompt = f"""# Dream: Memory Consolidation

Memory directory: `{memory_dir}`
This directory already exists — write to it directly.

Session transcripts: `{transcript_dir}` (large JSONL files — grep narrowly, don't read whole files)

Sessions since last consolidation ({len(session_ids)}):
{chr(10).join(f'- {sid}' for sid in session_ids)}

...4 Phasen...
"""
```

Der Agent kann dann selbst:
```bash
grep -rn "deployment" /home/dreamline/.claude/projects/C--Users-user--Desktop-mein-projekt/ --include="*.jsonl" | tail -50
```

### Aufwand: Klein (Prompt-Umbau)

---

## Lücke 7: Rollback-Mechanismus

### Problem
Bei Dream-Fehler wird die `.consolidate-lock` mtime nicht zurückgesetzt.

### Claude Code Referenz
`consolidationLock.ts:91-108`:
```typescript
if (priorMtime === 0) {
    await unlink(path)  // Restore no-file state
} else {
    await writeFile(path, '')
    await utimes(path, priorMtime/1000, priorMtime/1000)
}
```

### Implementierung
```python
def _rollback_consolidate_lock(memory_dir: Path, prior_mtime: float) -> None:
    lock_path = memory_dir / CONSOLIDATE_LOCK_FILE
    try:
        if prior_mtime == 0:
            lock_path.unlink(missing_ok=True)
        else:
            lock_path.write_text("")
            os.utime(lock_path, (prior_mtime, prior_mtime))
    except OSError as e:
        logger.warning("Consolidate-Lock Rollback fehlgeschlagen: %s", e)
```

In `run_dream()`:
```python
# Vor Lock-Erwerb: prior_mtime speichern
prior_mtime = lock_path.stat().st_mtime if lock_path.exists() else 0

# Bei Fehler: Rollback
except Exception:
    _rollback_consolidate_lock(memory_dir, prior_mtime)
```

### Aufwand: Klein (eine Funktion)

---

## Lücke 8: Gate-System (4-stufig statt 2-stufig)

### Problem
Dreamline hat nur Time+Sessions Gates. Claude Code hat 4 Gates (billigste zuerst).

### Claude Code Referenz
`autoDream.ts:128-190`:
1. Time-Gate: `hoursSince >= minHours` (1 stat)
2. Scan-Throttle: `sinceScanMs < 10min` (closure-Variable)
3. Session-Gate: `sessionIds.length >= minSessions` (stat alle .jsonl)
4. Lock-Gate: `tryAcquireConsolidationLock()` (PID-Check)

### Implementierung
Datei: `app/worker/scheduler.py` – Refactoring des Schedulers:

```python
class DreamGateSystem:
    """4-stufiges Gate-System wie Claude Code autoDream."""

    SCAN_THROTTLE_SECONDS = 600  # 10 Minuten

    def __init__(self):
        self.last_scan_at: dict[str, float] = {}

    async def should_dream(self, project_id, db) -> bool:
        project = await db.get(Project, project_id)
        if not project:
            return False

        # Gate 1: Time-Gate (billigste Prüfung)
        last_dream = await self._get_last_dream_time(project_id, db)
        hours_since = (time.time() - last_dream) / 3600
        if hours_since < project.dream_interval_hours:
            return False

        # Gate 2: Scan-Throttle
        now = time.time()
        pid_key = str(project_id)
        if now - self.last_scan_at.get(pid_key, 0) < self.SCAN_THROTTLE_SECONDS:
            return False
        self.last_scan_at[pid_key] = now

        # Gate 3: Session-Gate
        count = await self._count_sessions_since(project_id, last_dream, db)
        if count < project.min_sessions_for_dream:
            return False

        # Gate 4: Lock-Gate (.consolidate-lock + DB DreamLock)
        memory_dir = _find_memory_dir(project.name)
        if memory_dir and not _check_consolidate_lock(memory_dir):
            return False

        return True
```

### Aufwand: Klein (Refactoring bestehender Scheduler)

---

## Lücke 9: Transcript-Zugriff im Container

### Problem
Der Dream-Agent bekommt Session-Daten als Text im Prompt, kann aber nicht selbst in den Transcripts suchen.

### Lösung
Ist identisch mit Lücke 6. Durch das Volume-Mount `~/.claude/projects` hat der Agent bereits Zugriff. Wir müssen nur den Prompt ändern.

Zusätzlich: Der Agent soll die Session-IDs bekommen und kann dann gezielt grepen:
```
grep -rn "deployment" /home/dreamline/.claude/projects/C--Users-user--Desktop-mein-projekt/abc123.jsonl | tail -50
```

### Aufwand: Bereits in Lücke 6 enthalten

---

## Lücke 10: Config-System

### Problem
Kein globaler Kill-Switch, keine Feature-Flag-Äquivalente.

### Claude Code Referenz
- `autoDreamEnabled` in settings.json
- GrowthBook Feature `tengu_onyx_plover` mit `minHours`, `minSessions`

### Implementierung
Dreamline Config-Erweiterung in `.env`:

```env
# autoDream Konfiguration
AUTODREAM_ENABLED=true
AUTODREAM_MIN_HOURS=24
AUTODREAM_MIN_SESSIONS=5
AUTODREAM_SCAN_THROTTLE_MINUTES=10
```

Plus Dashboard-Einstellungen:
- Toggle: autoDream an/aus
- Slider: Dream-Intervall (Stunden)
- Slider: Min. Sessions

In `app/config.py`:
```python
class Settings(BaseSettings):
    # ... bestehend ...
    autodream_enabled: bool = True
    autodream_min_hours: int = 24
    autodream_min_sessions: int = 5
    autodream_scan_throttle_minutes: int = 10
```

### Aufwand: Klein (Config + Dashboard UI)

---

## Implementierungsreihenfolge

| Phase | Lücke | Aufwand | Priorität |
|-------|-------|---------|-----------|
| **Phase 1** | Lücke 1: Tool-Einschränkung | Klein | Sicherheit |
| **Phase 1** | Lücke 7: Rollback | Klein | Korrektheit |
| **Phase 1** | Lücke 10: Config | Klein | Grundlage |
| **Phase 2** | Lücke 6+9: Transcript-Zugriff | Klein | Qualität |
| **Phase 2** | Lücke 3: Session-Discovery | Klein | Performance |
| **Phase 2** | Lücke 8: 4-stufiges Gate | Klein | Effizienz |
| **Phase 3** | Lücke 2: Quick-Extract | Mittel | Feature |
| **Phase 3** | Lücke 5: DreamTask UI | Mittel | UX |
| **Phase 4** | Lücke 4: Forked Agent | Klein | Optimierung |

**Geschätzte Gesamtdauer:** 3 Phasen á 1-2 Sessions

---

## Architektur-Überblick nach Angleichung

```
┌─────────────────────────────────────────────────┐
│                  Dashboard UI                      │
│  (Projekte, Sessions, Memories, Dreams, Progress)  │
├─────────────────────────────────────────────────┤
│                  FastAPI Backend                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
│  │ Sessions │  │ Dreams   │  │ Quick-Extract │   │
│  │ Router   │  │ Router   │  │ Service       │   │
│  └────┬─────┘  └────┬─────┘  └──────┬───────┘   │
│       │              │                │            │
│  ┌────┴──────────────┴────────────────┴───────┐   │
│  │           Gate-System (4 Stufen)            │   │
│  │  Time → Throttle → Sessions → Lock          │   │
│  └─────────────────────┬───────────────────────┘   │
│                        │                            │
│  ┌─────────────────────┴───────────────────────┐   │
│  │         Dream-Engine (dreamer.py)            │   │
│  │  ┌─────────────┐  ┌──────────────────────┐  │   │
│  │  │ claude-abo  │  │ anthropic / openai    │  │   │
│  │  │ (Agent-Mode)│  │ (JSON-Mode)           │  │   │
│  │  │ Tool-Access │  │ Prompt-Only           │  │   │
│  │  └──────┬──────┘  └──────────┬────────────┘  │   │
│  │         │                     │               │   │
│  │  ┌──────┴─────────────────────┴────────────┐  │   │
│  │  │    Memory-Writer + DB-Sync               │  │   │
│  │  └──────────────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────┤
│  Volume: ~/.claude/projects/<name>/memory/          │
│  Lock:   .consolidate-lock (PID + mtime)            │
│  Index:  MEMORY.md (max 200 lines, ~25KB)           │
│  Files:  *.md (Frontmatter + Content)               │
├─────────────────────────────────────────────────┤
│  PostgreSQL: sessions, memories, dreams, projects   │
└─────────────────────────────────────────────────┘
```
