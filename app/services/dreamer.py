"""
Dream-Engine – das Herzstück der Gedächtniskonsolidierung.

1:1 Nachbau von Claude Code's autoDream (aus services/autoDream/).
4-Typen Memory-System (user, feedback, project, reference).
4-Phasen Dream-Zyklus (Orient → Gather → Consolidate → Prune).
Respektiert Claude Code's .consolidate-lock Datei.

Quellen:
- consolidationPrompt.ts → CONSOLIDATION_SYSTEM_PROMPT
- consolidationLock.ts → .consolidate-lock Mechanismus
- autoDream.ts → Gate-System (Time, Sessions, Lock)
"""

import json
import logging
import os
import platform
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, func, delete, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dream import Dream, DreamLock
from app.models.memory import Memory
from app.models.project import Project
from app.models.session import Session
from app.services import ai_client

logger = logging.getLogger(__name__)

# ─── Dual-Lock-Strategie ────────────────────────────────────────────────
#
# Dreamline verwendet ZWEI unabhängige Lock-Mechanismen um parallele
# Memory-Konsolidierung zu verhindern. Beide müssen frei sein damit
# ein Dream starten kann.
#
# 1. DreamLock (Datenbank-Tabelle)
#    - Verhindert dass zwei Dream-Worker parallel für dasselbe Projekt laufen.
#    - Lebt in der DB (Tabelle dream_locks), projektbezogen.
#    - Wird am Anfang von run_dream() erworben und im finally-Block freigegeben.
#    - Veraltete Locks (>1 Stunde) werden automatisch übernommen, falls ein
#      Worker abgestürzt ist.
#
# 2. .consolidate-lock (Datei im Memory-Verzeichnis)
#    - Respektiert Claude Code's eigenes Lock-System (consolidationLock.ts).
#    - Claude Code setzt diese Datei wenn es selbst einen Dream durchführt.
#    - Dreamline LIEST dieses Lock zuerst (gibt Claude Code Vorrang) und
#      schreibt es dann selbst, um Claude Code zu signalisieren dass wir aktiv sind.
#    - Enthält die PID des Holders — bei frischem Lock und lebender PID wird
#      der Dream übersprungen.
#    - Nach erfolgreichem Dream bleibt die mtime stehen als "lastConsolidatedAt"
#      Marker, den Claude Code beim nächsten autoDream-Gate prüft.
#
# Was passiert wenn nur eines der beiden existiert?
# - Nur DreamLock (DB): Ein anderer Dreamline-Worker ist aktiv → Dream wird
#   übersprungen. Claude Code weiß davon nichts (kein Datei-Lock).
# - Nur .consolidate-lock (Datei): Claude Code selbst dreamt gerade →
#   Dreamline wartet. Der DB-Lock existiert nicht, also würde ein zweiter
#   Dreamline-Worker ebenfalls die Datei sehen und warten.
# - Keines: Frei, Dream kann starten (beide Locks werden sofort erworben).
# - Beide: Dreamline ist aktiv — korrekter Normalzustand während eines Dreams.
#
# ────────────────────────────────────────────────────────────────────────

# Maximale Lock-Dauer bevor ein Lock als veraltet gilt (1 Stunde)
LOCK_STALE_THRESHOLD = timedelta(hours=1)

# Claude Code Konstanten (aus consolidationLock.ts)
CONSOLIDATE_LOCK_FILE = ".consolidate-lock"

# 1 Stunde in Millisekunden — identisch zu Claude Code consolidationLock.ts:19
# Locks die älter sind gelten als veraltet (Worker abgestürzt)
HOLDER_STALE_MS = 60 * 60 * 1000

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def _find_memory_dir(project_name: str) -> Path | None:
    """Findet das Memory-Verzeichnis eines Projekts im Claude-Projektordner."""
    if not CLAUDE_PROJECTS_DIR.exists():
        return None
    name_lower = project_name.lower()
    for entry in CLAUDE_PROJECTS_DIR.iterdir():
        if entry.is_dir() and name_lower in entry.name.lower():
            return entry / "memory"
    return None


def _check_consolidate_lock(memory_dir: Path) -> bool:
    """
    Prüft den .consolidate-lock von Claude Code (1:1 aus consolidationLock.ts).
    Gibt True zurück wenn KEIN aktiver Lock besteht (wir dürfen dreamen).
    Gibt False zurück wenn Claude Code gerade selbst dreamt.
    """
    lock_path = memory_dir / CONSOLIDATE_LOCK_FILE
    if not lock_path.exists():
        return True  # Kein Lock → frei

    try:
        stat = lock_path.stat()
        age_ms = (time.time() - stat.st_mtime) * 1000

        # Lock älter als 1 Stunde → veraltet, wir können übernehmen
        if age_ms > HOLDER_STALE_MS:
            return True

        # Lock ist frisch – prüfe ob der Prozess noch lebt
        try:
            pid_str = lock_path.read_text().strip()
            pid = int(pid_str)
            # Prüfe ob PID noch läuft
            os.kill(pid, 0)  # Signal 0 = nur prüfen, nicht killen
            logger.info("Claude Code Dream-Lock aktiv (PID %d, %ds alt). Überspringe.", pid, int(age_ms / 1000))
            return False  # Prozess lebt → Lock aktiv
        except (ValueError, ProcessLookupError, PermissionError):
            # PID nicht parsebar oder Prozess tot → Lock veraltet
            return True

    except OSError:
        return True  # Fehler beim Lesen → ignorieren


def _write_consolidate_lock(memory_dir: Path) -> float | None:
    """
    Schreibt den .consolidate-lock wie Claude Code (mtime = lastConsolidatedAt).
    Gibt die vorherige mtime zurück (für Rollback bei Fehler).
    Gibt None zurück wenn ein anderer Prozess die Race-Condition gewonnen hat.
    1:1 aus consolidationLock.ts:tryAcquireConsolidationLock()
    """
    lock_path = memory_dir / CONSOLIDATE_LOCK_FILE
    prior_mtime = 0.0
    try:
        if lock_path.exists():
            prior_mtime = lock_path.stat().st_mtime
        memory_dir.mkdir(parents=True, exist_ok=True)
        my_pid = f"dreamline-{os.getpid()}"
        lock_path.write_text(my_pid)

        # Race-Condition-Schutz: Zurücklesen und prüfen ob wir gewonnen haben
        # (1:1 aus consolidationLock.ts:74-81)
        try:
            verify = lock_path.read_text().strip()
            if verify != my_pid:
                logger.info(
                    "Consolidate-Lock Race verloren: erwartet '%s', gelesen '%s'",
                    my_pid, verify,
                )
                return None
        except OSError:
            return None

        logger.info("Consolidate-Lock erworben: %s (prior_mtime: %s)", lock_path, prior_mtime)
    except OSError as e:
        logger.warning("Consolidate-Lock schreiben fehlgeschlagen: %s", e)
        return None
    return prior_mtime


def _snapshot_memory_dir(memory_dir: Path) -> dict[str, float]:
    """
    Erstellt einen Snapshot des Memory-Verzeichnisses (Dateiname → mtime).
    Wird VOR dem Agent-Dream aufgenommen für Post-Dream Validierung.
    1:1 wie Claude Code createAutoMemCanUseTool() + DreamTask.filesTouched
    """
    snapshot = {}
    if not memory_dir.exists():
        return snapshot
    try:
        for filepath in memory_dir.iterdir():
            if filepath.is_file():
                snapshot[filepath.name] = filepath.stat().st_mtime
    except OSError:
        pass
    return snapshot


def _validate_agent_writes(
    memory_dir: Path,
    pre_snapshot: dict[str, float],
) -> tuple[list[str], list[str]]:
    """
    Post-Dream Validierung: Prüft welche Dateien der Agent geändert/erstellt hat.
    Gibt zurück: (valid_files, violation_files)

    1:1 wie Claude Code isAutoMemPath() Enforcement:
    - Dateien innerhalb memory_dir → erlaubt
    - Dateien außerhalb → Verstoß (wird geloggt)
    """
    valid_files = []
    violation_files = []

    if not memory_dir.exists():
        return valid_files, violation_files

    try:
        # Prüfe alle Dateien im Memory-Dir auf Änderungen
        for filepath in memory_dir.iterdir():
            if not filepath.is_file():
                continue
            current_mtime = filepath.stat().st_mtime
            prev_mtime = pre_snapshot.get(filepath.name)

            if prev_mtime is None:
                # Neue Datei — erlaubt
                valid_files.append(filepath.name)
            elif current_mtime != prev_mtime:
                # Geänderte Datei — erlaubt
                valid_files.append(filepath.name)

        # Prüfe ob Dateien im Eltern-Verzeichnis geändert wurden (Verstoß!)
        parent = memory_dir.parent
        if parent.exists():
            for filepath in parent.iterdir():
                if filepath == memory_dir:
                    continue
                if filepath.is_file() and filepath.suffix in (".md", ".txt", ".json", ".py", ".ts", ".js"):
                    # Prüfe ob die Datei kürzlich geändert wurde (< 60 Sek)
                    if time.time() - filepath.stat().st_mtime < 60:
                        violation_files.append(str(filepath))

    except OSError as e:
        logger.warning("Post-Dream Validierung fehlgeschlagen: %s", e)

    return valid_files, violation_files


def _release_consolidate_lock(memory_dir: Path) -> None:
    """
    Gibt den .consolidate-lock frei nach erfolgreichem Dream.
    Body leeren, mtime bleibt als lastConsolidatedAt stehen.
    """
    lock_path = memory_dir / CONSOLIDATE_LOCK_FILE
    try:
        lock_path.write_text("")
    except OSError:
        pass


def _rollback_consolidate_lock(memory_dir: Path, prior_mtime: float) -> None:
    """
    Rollback bei Dream-Fehler: mtime zurücksetzen auf vorherigen Wert.
    1:1 aus consolidationLock.ts:rollbackConsolidationLock()
    """
    lock_path = memory_dir / CONSOLIDATE_LOCK_FILE
    try:
        if prior_mtime == 0:
            lock_path.unlink(missing_ok=True)
            return
        lock_path.write_text("")
        os.utime(lock_path, (prior_mtime, prior_mtime))
        logger.info("Consolidate-Lock Rollback: mtime zurück auf %s", prior_mtime)
    except OSError as e:
        logger.warning(
            "Consolidate-Lock Rollback fehlgeschlagen: %s — nächster Trigger verzögert um minHours", e
        )


async def _sync_files_to_db(
    db: AsyncSession,
    project_id: UUID,
    memory_dir: Path,
    existing_memories: list[Memory],
) -> tuple[int, int, int]:
    """
    Synchronisiert Memory-Dateien vom Dateisystem zurück in die Datenbank.
    Wird nach dem Agent-Modus aufgerufen, wenn die KI direkt Dateien geschrieben hat.

    Liest alle .md Dateien im Memory-Verzeichnis (außer MEMORY.md) und
    aktualisiert die DB entsprechend.

    Rückgabe: (created, updated, deleted)
    """
    import re

    if not memory_dir.exists():
        return 0, 0, 0

    created, updated, deleted = 0, 0, 0
    memory_index = {mem.key: mem for mem in existing_memories}
    seen_keys = set()

    # Memory-Dateien sortiert nach mtime laden, Cap bei MAX_MEMORY_FILES (200)
    # (1:1 wie memoryScan.ts MAX_MEMORY_FILES)
    md_files = []
    for fp in memory_dir.glob("*.md"):
        if fp.name == ENTRYPOINT_NAME or fp.name == CONSOLIDATE_LOCK_FILE:
            continue
        try:
            md_files.append((fp.stat().st_mtime, fp))
        except OSError:
            continue
    md_files.sort(key=lambda x: x[0], reverse=True)
    md_files = md_files[:MAX_MEMORY_FILES]

    for _, filepath in md_files:
        try:
            content = filepath.read_text(encoding="utf-8")

            # Frontmatter parsen
            name = filepath.stem
            mem_type = "project"
            description = ""
            confidence = 0.7
            body = content

            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = parts[1]
                    body = parts[2].strip()

                    # Frontmatter-Felder extrahieren
                    for line in frontmatter.strip().split("\n"):
                        if line.startswith("name:"):
                            name = line.split(":", 1)[1].strip()
                        elif line.startswith("type:"):
                            mem_type = line.split(":", 1)[1].strip()
                        elif line.startswith("description:"):
                            description = line.split(":", 1)[1].strip()
                        elif line.startswith("confidence:"):
                            try:
                                confidence = float(line.split(":", 1)[1].strip())
                            except ValueError:
                                pass

            if not body:
                continue

            seen_keys.add(name)

            # In DB aktualisieren oder erstellen
            existing = memory_index.get(name)
            if existing:
                # Aktualisieren wenn Inhalt sich geändert hat
                if existing.content != body or existing.memory_type != mem_type:
                    existing.content = body
                    existing.memory_type = mem_type
                    existing.confidence = min(max(confidence, 0.0), 1.0)
                    existing.source_count += 1
                    updated += 1
            else:
                # Neuen Eintrag erstellen
                new_mem = Memory(
                    project_id=project_id,
                    key=name,
                    content=body,
                    memory_type=mem_type,
                    confidence=min(max(confidence, 0.0), 1.0),
                    source_count=1,
                )
                db.add(new_mem)
                created += 1

        except Exception as e:
            logger.warning("Fehler beim Synchronisieren von %s: %s", filepath.name, e)

    # Gelöschte Memories erkennen (in DB aber nicht mehr als Datei)
    for key, mem in memory_index.items():
        if key not in seen_keys:
            # Prüfe ob die Datei wirklich gelöscht wurde (nicht nur umbenannt)
            # Wir löschen nur wenn es vorher eine Datei gab
            await db.delete(mem)
            deleted += 1

    return created, updated, deleted

# ─── 4-Typen Memory-Taxonomie (wie Claude Code autoDream) ──────────

MEMORY_TYPES = """
## Memory-Typen

Es gibt 4 Typen von Erinnerungen:

### user
Informationen über den Endnutzer/Kunden: Rolle, Ziele, Vorlieben, Wissenstand.
Hilft dem Chatbot, Antworten auf die Person zuzuschneiden.
Beispiel: "Kunde bevorzugt kurze, technische Antworten ohne Smalltalk"

### feedback
Feedback das zeigt was funktioniert und was nicht. Sowohl Korrekturen ("das war falsch")
als auch Bestätigungen ("genau richtig"). Enthält immer ein **Warum** und **Wann anwenden**.
Beispiel: "Bei Retoure-Fragen sofort Gutschein anbieten. Warum: 80% Erfolgsrate. Anwenden: Wenn Kunde frustriert klingt."

### project
Fakten über das Projekt/Produkt die nicht aus dem Code ableitbar sind.
Geschäftslogik, Deadlines, Entscheidungen, aktuelle Initiativen.
Beispiel: "Ab 01.04.2026 neue Versandkosten: kostenlos ab 30€ statt 50€"

### reference
Verweise auf externe Ressourcen und wo man Informationen findet.
Beispiel: "Retoure-Formular unter /retoure, Frist 14 Tage, Kontakt: retoure@firma.de"
"""

# ─── Konsolidierungs-Prompt (1:1 aus Claude Code autoDream) ────────

# Konstanten identisch zu Claude Code memdir.ts
ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_KB = 25

CONSOLIDATION_SYSTEM_PROMPT = f"""# Dream: Memory Consolidation

You are performing a dream — a reflective pass over your memory files.
Synthesize what you've learned recently into durable, well-organized memories
so that future sessions can orient quickly.

{MEMORY_TYPES}

## What NOT to save in memory

- Code patterns, conventions, architecture, file paths, or project structure — these can be derived by reading the current project state.
- Git history, recent changes, or who-changed-what — git log / git blame are authoritative.
- Debugging solutions or fix recipes — the fix is in the code; the commit message has the context.
- Anything already documented in CLAUDE.md files.
- Ephemeral task details: in-progress work, temporary state, current conversation context.

## Phase 1 — Orient

- `ls` the memory directory to see what already exists
- Read `{ENTRYPOINT_NAME}` to understand the current index
- Skim existing topic files so you improve them rather than creating duplicates

## Phase 2 — Gather recent signal

Look for new information worth persisting. Sources in rough priority order:

1. **Transcript search** — if you need specific context, grep the JSONL transcripts for narrow terms:
   `grep -rn "<narrow term>" <transcript_dir>/ --include="*.jsonl" | tail -50`
   Don't exhaustively read transcripts. Look only for things you already suspect matter.
2. **Existing memories that drifted** — facts that contradict something you see in the codebase now
3. **Project context** — if provided, use CLAUDE.md and file structure to understand the project better

Don't save everything. Look only for things that are durable and non-obvious.

## Phase 3 — Consolidate

For each thing worth remembering:
- **Merge** new signal into existing topic memories rather than creating near-duplicates
- **Convert** relative dates ("yesterday", "last week") to absolute dates so they remain interpretable after time passes
- **Delete** contradicted facts — if today's investigation disproves an old memory, fix it at the source
- **Create** new memories only for genuinely new topics

Memory file format for each memory:
```
---
name: {{memory name}}
description: {{one-line description — used to decide relevance, so be specific}}
type: {{user, feedback, project, reference}}
---

{{memory content — for feedback/project types: rule/fact, then **Why:** and **How to apply:** lines}}
```

## Phase 4 — Prune and index

The {ENTRYPOINT_NAME} index must stay under {MAX_ENTRYPOINT_LINES} lines AND under ~{MAX_ENTRYPOINT_KB}KB.
It's an **index**, not a dump — each entry should be one line under ~150 characters:
`- [Title](file.md) — one-line hook`. Never write memory content directly into it.

- Remove pointers to memories that are now stale, wrong, or superseded
- Demote verbose entries: if an index line is over ~200 chars, shorten it
- Add pointers to newly important memories
- Resolve contradictions — if two memories disagree, fix the wrong one

## Confidence scoring
- 0.3-0.5: Single observation, not yet confirmed
- 0.5-0.7: Observed multiple times, likely correct
- 0.7-0.9: Frequently confirmed, very reliable
- 0.9-1.0: Factually certain (e.g. URL, company name)

## Response format
Respond EXCLUSIVELY with valid JSON:
{{
  "operations": [
    {{"action": "create", "key": "topic-name", "type": "feedback", "content": "...", "confidence": 0.85}},
    {{"action": "update", "key": "existing-key", "content": "Updated...", "confidence": 0.9}},
    {{"action": "delete", "key": "outdated-key"}}
  ],
  "summary": "Brief summary of what was consolidated, updated, or pruned."
}}

If nothing changed (memories are already tight), return empty operations array."""


# ─── Memory-Manifest (1:1 wie memoryScan.ts) ────────────────────

# Maximale Anzahl Memory-Dateien die gescannt werden (identisch zu memoryScan.ts).
# Verhindert Memory-Bloat bei Projekten mit sehr vielen Konsolidierungszyklen.
MAX_MEMORY_FILES = 200


def _scan_memory_manifest(memory_dir: Path) -> str:
    """
    Scannt alle .md-Dateien im Memory-Dir und erstellt ein Manifest.
    1:1 wie memoryScan.ts scanMemoryFiles() + formatMemoryManifest().

    Das Manifest wird in den Prompt injiziert → Agent spart einen ls-Turn.
    Sortiert nach mtime (neueste zuerst), max 200 Dateien.
    """
    if not memory_dir or not memory_dir.exists():
        return ""

    entries = []
    try:
        for filepath in memory_dir.glob("*.md"):
            if filepath.name == ENTRYPOINT_NAME:
                continue
            if filepath.name == CONSOLIDATE_LOCK_FILE:
                continue

            try:
                stat = filepath.stat()
                mtime_ms = stat.st_mtime * 1000

                # Frontmatter parsen (erste 30 Zeilen, wie memoryScan.ts)
                content = filepath.read_text(encoding="utf-8")
                description = ""
                mem_type = ""
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        for line in parts[1].strip().split("\n"):
                            if line.startswith("description:"):
                                description = line.split(":", 1)[1].strip()[:100]
                            elif line.startswith("type:"):
                                mem_type = line.split(":", 1)[1].strip()

                # ISO-Timestamp aus mtime
                mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")

                entry = f"- [{mem_type}] {filepath.name} ({mtime_iso})"
                if description:
                    entry += f": {description}"
                entries.append((stat.st_mtime, entry))

            except OSError:
                continue

    except OSError:
        return ""

    # Sortieren nach mtime (neueste zuerst), Cap bei MAX_MEMORY_FILES
    entries.sort(key=lambda x: x[0], reverse=True)
    entries = entries[:MAX_MEMORY_FILES]

    if not entries:
        return ""

    lines = [e[1] for e in entries]
    return "\n".join(lines)


def _build_user_prompt(
    existing_memories: list[Memory],
    new_sessions: list[Session],
    memory_dir: str | None = None,
    transcript_dir: str | None = None,
    use_agent_mode: bool = False,
) -> str:
    """
    Erstellt den User-Prompt – 1:1 wie Claude Code buildConsolidationPrompt().

    Agent-Modus: Agent bekommt Pfade und greped selbst.
    JSON-Modus: Session-Daten werden als Text mitgegeben.
    """
    parts = []

    # ── Pfade für Agent-Modus ──
    if use_agent_mode and memory_dir:
        parts.append(f"Memory directory: `{memory_dir}`")
        parts.append("This directory already exists — write to it directly with the Write tool (do not run mkdir or check for its existence).\n")

        # Memory-Manifest Pre-Injection (1:1 wie memoryScan.ts)
        # Spart dem Agent einen ls-Turn
        manifest = _scan_memory_manifest(Path(memory_dir))
        if manifest:
            parts.append("## Existing memory files\n")
            parts.append(manifest)
            parts.append("\nCheck this list before writing — update an existing file rather than creating a duplicate.\n")

    if use_agent_mode and transcript_dir:
        parts.append(f"Session transcripts: `{transcript_dir}` (large JSONL files — grep narrowly, don't read whole files)\n")

    # ── Session-IDs für Agent-Modus ──
    session_ids = []
    for s in new_sessions:
        if s.metadata_json:
            try:
                meta = json.loads(s.metadata_json)
                sid = meta.get("session_id")
                if sid:
                    session_ids.append(sid)
            except json.JSONDecodeError:
                pass

    # ── Projektkontext aus Session-Metadaten ──
    project_context = None
    for session in new_sessions:
        if session.metadata_json:
            try:
                meta = json.loads(session.metadata_json)
                if not project_context:
                    ctx = meta.get("project_context")
                    if ctx and len(ctx) > 50:
                        project_context = ctx
            except json.JSONDecodeError:
                pass

    if project_context:
        parts.append("## Additional context\n")
        if len(project_context) > 5000:
            project_context = project_context[:5000] + "\n... [truncated]"
        parts.append(project_context)
        parts.append("")

    # ── Agent-Modus: Session-IDs + Tool-Constraints ──
    if use_agent_mode:
        parts.append(f"Sessions since last consolidation ({len(new_sessions)}):")
        for sid in session_ids:
            parts.append(f"- {sid}")
        if not session_ids:
            for i, s in enumerate(new_sessions, 1):
                parts.append(f"- session-{i} ({s.created_at})")
        parts.append("")

        # Session-Daten als Kontext mitgeben (Agent kann zusätzlich grepen)
        parts.append("## Session summaries (from hook)")
        for i, session in enumerate(new_sessions, 1):
            messages = json.loads(session.messages_json)
            outcome_text = f" → outcome: {session.outcome}" if session.outcome else ""
            parts.append(f"### Session {i}{outcome_text}")
            for msg in messages:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                if len(content) > 2000:
                    content = content[:2000] + "\n... [truncated]"
                parts.append(f"**{role}**: {content}")
            parts.append("")

    else:
        # ── JSON-Modus: Alles im Prompt ──
        if existing_memories:
            parts.append(f"## Existing memories ({len(existing_memories)} entries)\n")
            for mem in existing_memories:
                mem_type = getattr(mem, "memory_type", "unknown")
                parts.append(
                    f"### {mem.key} [{mem_type}] (confidence: {mem.confidence}, "
                    f"sources: {mem.source_count})"
                )
                parts.append(mem.content)
                parts.append("")
        else:
            parts.append("## Existing memories\nNone yet (first consolidation).\n")

        parts.append(f"## Sessions since last consolidation ({len(new_sessions)})\n")
        for i, session in enumerate(new_sessions, 1):
            messages = json.loads(session.messages_json)
            outcome_text = f" → outcome: {session.outcome}" if session.outcome else ""
            parts.append(f"### Session {i}{outcome_text}")
            for msg in messages:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                if len(content) > 2000:
                    content = content[:2000] + "\n... [truncated]"
                parts.append(f"**{role}**: {content}")
            parts.append("")

    # ── Anweisung ──
    parts.append("## Task")
    if use_agent_mode:
        parts.append(
            "Follow the 4-phase process: Orient → Gather → Consolidate → Prune. "
            "Write or update memory files directly in the memory directory. "
            "Update MEMORY.md to stay under 200 lines. "
            "Return a brief summary of what you consolidated, updated, or pruned."
        )
    else:
        parts.append(
            "Follow the 4-phase process: Orient → Gather → Consolidate → Prune. "
            "Respond only with the JSON format specified in the system prompt."
        )

    return "\n".join(parts)


# ─── Dream-Lock Verwaltung ────────────────────────────────────────────

async def _acquire_lock(db: AsyncSession, project_id: UUID) -> bool:
    """
    Versucht einen Dream-Lock für das Projekt zu erwerben.

    Gibt True zurück wenn Lock erfolgreich, False wenn bereits gesperrt.
    Veraltete Locks (>1 Stunde) werden übernommen.
    """
    worker_id = platform.node() or "unknown-worker"
    now = datetime.now(timezone.utc)
    stale_threshold = now - LOCK_STALE_THRESHOLD

    # Prüfen ob ein existierender Lock vorhanden ist
    stmt = select(DreamLock).where(DreamLock.project_id == project_id)
    result = await db.execute(stmt)
    existing_lock = result.scalar_one_or_none()

    if existing_lock:
        lock_time = existing_lock.locked_at
        if lock_time and lock_time.tzinfo is None:
            lock_time = lock_time.replace(tzinfo=timezone.utc)

        if lock_time and lock_time > stale_threshold:
            # Lock ist noch frisch – Dream überspringen
            logger.info(
                "Projekt %s: Dream-Lock aktiv (von %s seit %s). Überspringe.",
                project_id, existing_lock.locked_by, existing_lock.locked_at,
            )
            return False

        # Lock ist veraltet – übernehmen
        logger.warning(
            "Projekt %s: Veralteter Lock von %s (%s). Wird übernommen.",
            project_id, existing_lock.locked_by, existing_lock.locked_at,
        )
        existing_lock.locked_by = worker_id
        existing_lock.locked_at = now
        await db.flush()
        return True

    # Kein Lock vorhanden – neuen erstellen
    new_lock = DreamLock(
        project_id=project_id,
        locked_by=worker_id,
    )
    db.add(new_lock)
    await db.flush()
    logger.info("Projekt %s: Dream-Lock erworben von %s.", project_id, worker_id)
    return True


async def _release_lock(db: AsyncSession, project_id: UUID) -> None:
    """Gibt den Dream-Lock für ein Projekt frei."""
    stmt = delete(DreamLock).where(DreamLock.project_id == project_id)
    await db.execute(stmt)
    await db.flush()
    logger.info("Projekt %s: Dream-Lock freigegeben.", project_id)


def _make_skipped_dream(project_id: UUID, summary: str) -> Dream:
    """Erstellt ein übersprungenes Dream-Objekt (Lock-Konflikte, Race-Conditions)."""
    return Dream(
        project_id=project_id,
        sessions_reviewed=0,
        summary=summary,
        status="skipped",
        duration_ms=0,
    )


async def _acquire_dual_locks(
    db: AsyncSession,
    project_id: UUID,
) -> tuple[bool, Path | None, float]:
    """
    Erwirbt beide Locks der Dual-Lock-Strategie (siehe Modul-Docstring).

    Rückgabe: (success, memory_dir, prior_mtime)
    - success=True: Beide Locks erworben, Dream darf starten.
    - success=False: Mindestens ein Lock blockiert. Caller muss das
      zurückgegebene Dream-Objekt aus dem Attribut .skip_dream lesen.
    - memory_dir: Pfad zum Memory-Verzeichnis (oder None wenn nicht vorhanden).
    - prior_mtime: Vorherige mtime des .consolidate-lock (für Rollback).
    """
    # 1. DreamLock (DB) erwerben
    lock_acquired = await _acquire_lock(db, project_id)
    if not lock_acquired:
        return False, None, 0.0

    # 2. Claude Code .consolidate-lock prüfen
    project_name = (
        await db.execute(select(Project.name).where(Project.id == project_id))
    ).scalar() or ""
    memory_dir = _find_memory_dir(project_name)

    if memory_dir and not _check_consolidate_lock(memory_dir):
        await _release_lock(db, project_id)
        return False, memory_dir, 0.0

    # 3. Eigenen .consolidate-lock setzen
    prior_mtime = 0.0
    if memory_dir:
        lock_result = _write_consolidate_lock(memory_dir)
        if lock_result is None:
            # Race-Condition verloren — ein anderer Prozess hat den Lock
            await _release_lock(db, project_id)
            return False, memory_dir, 0.0
        prior_mtime = lock_result

    return True, memory_dir, prior_mtime


async def _post_dream_memory_write(
    db: AsyncSession,
    project_id: UUID,
    dream_result: Dream,
    memory_dir: Path | None,
    pre_snapshot: dict[str, float],
) -> None:
    """
    Nacharbeiten nach einem erfolgreichen Dream:
    - Validiert dass der Agent nur im erlaubten Verzeichnis geschrieben hat.
    - Schreibt Memories als Markdown ins Projekt (nur im JSON-Modus nötig,
      im Agent-Modus hat der Agent die Dateien direkt geschrieben).
    """
    # Post-Dream Validierung: Prüfe dass Agent nur im memory_dir geschrieben hat
    # (1:1 wie createAutoMemCanUseTool + isAutoMemPath Enforcement)
    if memory_dir and dream_result.status == "completed":
        valid_files, violations = _validate_agent_writes(memory_dir, pre_snapshot)
        if valid_files:
            logger.info(
                "Projekt %s: Agent hat %d Dateien im Memory-Dir geändert: %s",
                project_id, len(valid_files), ", ".join(valid_files[:10]),
            )
        if violations:
            logger.warning(
                "Projekt %s: TOOL-ENFORCEMENT-VERSTOSS! Agent hat %d Dateien AUSSERHALB des Memory-Dir geändert: %s",
                project_id, len(violations), ", ".join(violations[:5]),
            )

    # Memories als Markdown ins Projekt schreiben (nur im JSON-Modus)
    if dream_result.status != "completed":
        return
    if dream_result.memories_created + dream_result.memories_updated == 0:
        return

    project_name = (
        await db.execute(select(Project.name).where(Project.id == project_id))
    ).scalar() or ""
    agent_mem_dir = _find_memory_dir(project_name)
    was_agent_mode = (
        (await db.execute(select(Project.ai_provider).where(Project.id == project_id))).scalar() == "claude-abo"
        and agent_mem_dir is not None
    )
    if was_agent_mode:
        return  # Agent hat direkt geschrieben, kein Writeback nötig

    try:
        from app.services.memory_writer import write_memories_to_project
        write_result = await write_memories_to_project(db, project_id)
        logger.info(
            "Projekt %s: %d Memories ins Projekt geschrieben (%s)",
            project_id, write_result["written"], write_result["path"],
        )
    except Exception as write_err:
        logger.warning("Memory-Write fehlgeschlagen: %s", str(write_err))


async def run_dream(
    db: AsyncSession,
    project_id: UUID,
    ai_provider: str,
    ai_model: str,
) -> Dream:
    """
    Führt einen Konsolidierungslauf (Dream) für ein Projekt durch.

    Ablauf:
    1. Dual-Lock erwerben (DB-Lock + .consolidate-lock)
    2. Dream ausführen (_execute_dream)
    3. Post-Dream Validierung und Memory-Writeback
    4. Locks freigeben (finally-Block)

    Siehe Modul-Docstring für Details zur Dual-Lock-Strategie.
    """
    start_time = time.monotonic()

    # ── Dual-Lock erwerben ──
    success, memory_dir, prior_mtime = await _acquire_dual_locks(db, project_id)
    if not success:
        # Grund ermitteln für aussagekräftige Summary
        if memory_dir and not _check_consolidate_lock(memory_dir):
            summary = "Übersprungen: Claude Code Dream-Lock aktiv."
        else:
            summary = "Übersprungen: Dream-Lock bereits aktiv oder Race verloren."
        dream = _make_skipped_dream(project_id, summary)
        db.add(dream)
        await db.flush()
        return dream

    # ── Pre-Dream Snapshot für Post-Validation ──
    pre_snapshot = _snapshot_memory_dir(memory_dir) if memory_dir else {}

    try:
        # ── Dream ausführen ──
        result = await _execute_dream(db, project_id, ai_provider, ai_model, start_time)

        # ── Post-Dream Nacharbeiten ──
        await _post_dream_memory_write(db, project_id, result, memory_dir, pre_snapshot)

        # Erfolg: .consolidate-lock freigeben (mtime = jetzt = lastConsolidatedAt)
        if memory_dir:
            _release_consolidate_lock(memory_dir)
        return result

    except Exception as e:
        # Bei Fehler: Rollback mtime auf vorherigen Wert (1:1 wie Claude Code)
        logger.error("Projekt %s: Unerwarteter Dream-Fehler: %s", project_id, str(e))
        if memory_dir:
            _rollback_consolidate_lock(memory_dir, prior_mtime)
        duration_ms = int((time.monotonic() - start_time) * 1000)
        dream = Dream(
            project_id=project_id,
            sessions_reviewed=0,
            summary=f"Unerwarteter Fehler: {str(e)[:500]}",
            status="failed",
            duration_ms=duration_ms,
        )
        db.add(dream)
        await db.flush()
        return dream

    finally:
        await _release_lock(db, project_id)


def _parse_dream_operations(response_text: str) -> tuple[list[dict], str]:
    """
    Extrahiert Dream-Operationen und Summary aus der KI-Antwort (JSON-Modus).

    Die KI antwortet manchmal mit reinem JSON, manchmal in Markdown-Codeblöcken.
    Diese Funktion behandelt beide Fälle.

    Rückgabe: (operations, summary)
    Wirft json.JSONDecodeError wenn das Parsen fehlschlägt.
    """
    clean_text = response_text.strip()

    # JSON aus Markdown-Codeblöcken extrahieren (```json ... ```)
    if "```" in clean_text:
        lines = clean_text.split("\n")
        in_block = False
        json_lines = []
        for line in lines:
            if line.strip().startswith("```"):
                in_block = not in_block
                continue
            if in_block:
                json_lines.append(line)
        if json_lines:
            clean_text = "\n".join(json_lines)

    result_data = json.loads(clean_text)
    operations = result_data.get("operations", [])
    summary = result_data.get("summary", "")
    return operations, summary


async def _execute_dream(
    db: AsyncSession,
    project_id: UUID,
    ai_provider: str,
    ai_model: str,
    start_time: float,
) -> Dream:
    """
    Interne Dream-Ausführung (nach Lock-Erwerb).

    Phasen:
    1. Sessions laden und filtern
    2. Bestehende Memories laden
    3. Prompt zusammenbauen
    4. KI-API aufrufen
    5. Ergebnis verarbeiten (Agent-Modus oder JSON-Modus)
    6. Dream-Protokoll erstellen
    """

    # ── Phase 1: Unverarbeitete Sessions laden ──────────────────────
    stmt = (
        select(Session)
        .where(Session.project_id == project_id)
        .where(Session.is_consolidated == False)
        .order_by(Session.created_at.asc())
    )
    result = await db.execute(stmt)
    new_sessions = list(result.scalars().all())

    # Session-Exclusion: Die neueste Session wird ausgeschlossen wenn sie
    # jünger als 60 Sekunden ist. Grund: Die Session die den Dream triggert
    # ist möglicherweise noch im Schreibprozess — der Nutzer tippt noch, oder
    # der Hook hat die Session noch nicht vollständig persistiert. Würden wir
    # sie jetzt konsolidieren, gingen die letzten Nachrichten verloren.
    # (1:1 wie autoDream.ts:165 — "current session always has a recent mtime")
    if len(new_sessions) > 1:
        now = datetime.now(timezone.utc)
        latest = new_sessions[-1]
        if latest.created_at:
            created = latest.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if (now - created).total_seconds() < 60:
                new_sessions = new_sessions[:-1]
                logger.debug(
                    "Projekt %s: Neueste Session ausgeschlossen (< 60s alt)",
                    project_id,
                )

    if not new_sessions:
        logger.info("Projekt %s: Keine neuen Sessions.", project_id)
        dream = Dream(
            project_id=project_id,
            sessions_reviewed=0,
            summary="Keine neuen Sessions vorhanden.",
            status="completed",
            duration_ms=0,
        )
        db.add(dream)
        await db.flush()
        return dream

    # ── Phase 2: Bestehende Erinnerungen laden ─────────────────────
    mem_stmt = (
        select(Memory)
        .where(Memory.project_id == project_id)
        .order_by(Memory.key)
    )
    mem_result = await db.execute(mem_stmt)
    existing_memories = list(mem_result.scalars().all())

    # ── Phase 3: Prompt zusammenbauen ──────────────────────────────
    # Memory-Verzeichnis und Transcript-Verzeichnis ermitteln
    project_name = (await db.execute(select(Project.name).where(Project.id == project_id))).scalar() or ""
    agent_memory_dir = _find_memory_dir(project_name)

    # Transcript-Verzeichnis: der Eltern-Ordner des Memory-Dirs
    transcript_dir = None
    if agent_memory_dir:
        transcript_dir = str(agent_memory_dir.parent)

    # Agent-Modus für claude-abo (CLI hat Tool-Zugriff, schreibt Dateien direkt)
    # Andere Provider (anthropic, openai) nutzen JSON-Modus
    # 1:1 wie Claude Code: runForkedAgent() mit createAutoMemCanUseTool()
    use_agent_mode = ai_provider == "claude-abo" and agent_memory_dir is not None

    user_prompt = _build_user_prompt(
        existing_memories,
        new_sessions,
        memory_dir=str(agent_memory_dir) if agent_memory_dir else None,
        transcript_dir=transcript_dir,
        use_agent_mode=use_agent_mode,
    )

    logger.info(
        "Projekt %s: Dream gestartet – %d Sessions, %d Erinnerungen (Modus: %s)",
        project_id, len(new_sessions), len(existing_memories),
        "agent" if use_agent_mode else "json",
    )

    # ── Phase 4: KI-API aufrufen ───────────────────────────────────
    try:
        if use_agent_mode:
            # Agent-Modus: Claude CLI mit Tool-Zugriff (1:1 wie runForkedAgent)
            agent_prompt = CONSOLIDATION_SYSTEM_PROMPT + "\n\n---\n\n" + user_prompt

            # Resume-Session-ID laden für Cache-Sharing zwischen Dreams
            resume_sid_result = await db.execute(
                select(Project.dream_cli_session_id).where(Project.id == project_id)
            )
            resume_session_id = resume_sid_result.scalar()

            response_text, tokens_used, new_session_id = await ai_client.dream_with_tools(
                provider=ai_provider,
                model=ai_model,
                prompt=agent_prompt,
                memory_dir=str(agent_memory_dir),
                resume_session_id=resume_session_id,
            )

            # Session-ID für nächsten Dream persistieren (Cache-Sharing)
            if new_session_id:
                await db.execute(
                    sa_update(Project)
                    .where(Project.id == project_id)
                    .values(dream_cli_session_id=new_session_id)
                )

        elif ai_provider == "anthropic":
            # Anthropic API mit Prompt-Caching (1:1 wie CacheSafeParams)
            # System-Prompt + bestehende Memories werden gecached
            memories_context = ""
            if existing_memories:
                mem_parts = []
                for mem in existing_memories:
                    mem_parts.append(f"### {mem.key} [{mem.memory_type}] (confidence: {mem.confidence})")
                    mem_parts.append(mem.content)
                    mem_parts.append("")
                memories_context = "\n".join(mem_parts)

            response_text, tokens_used = await ai_client.complete_with_cache(
                model=ai_model,
                system_prompt=CONSOLIDATION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                existing_memories_context=memories_context,
            )

        else:
            # JSON-Modus für andere Provider (OpenAI etc.)
            response_text, tokens_used = await ai_client.complete(
                provider=ai_provider,
                model=ai_model,
                system_prompt=CONSOLIDATION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )

    except Exception as e:
        logger.error("KI-API-Fehler für Projekt %s: %s", project_id, str(e))
        duration_ms = int((time.monotonic() - start_time) * 1000)
        dream = Dream(
            project_id=project_id,
            sessions_reviewed=len(new_sessions),
            summary=f"Fehler: {str(e)[:500]}",
            status="failed",
            duration_ms=duration_ms,
        )
        db.add(dream)
        await db.flush()
        return dream

    # ── Phase 5: Ergebnis verarbeiten ──────────────────────────────
    created, updated, deleted = 0, 0, 0

    if use_agent_mode and agent_memory_dir:
        # Agent-Modus: Der Agent hat direkt ins Dateisystem geschrieben.
        # Jetzt synchronisieren wir die Dateien zurück in die DB.
        # (1:1 wie Claude Code: Agent schreibt → DreamTask trackt filesTouched)
        summary = response_text[:500] if response_text else "Agent-Dream abgeschlossen."
        try:
            created, updated, deleted = await _sync_files_to_db(
                db, project_id, agent_memory_dir, existing_memories,
            )
            logger.info(
                "Projekt %s: File-Sync nach Agent-Dream – %d erstellt, %d aktualisiert, %d gelöscht",
                project_id, created, updated, deleted,
            )
        except Exception as sync_err:
            logger.warning("File-Sync nach Agent-Dream fehlgeschlagen: %s", sync_err)

    else:
        # JSON-Modus: Antwort parsen und Operationen auf DB anwenden
        try:
            operations, summary = _parse_dream_operations(response_text)
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(
                "JSON-Parse-Fehler für Projekt %s: %s\nAntwort: %s",
                project_id, str(e), response_text[:300],
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)
            dream = Dream(
                project_id=project_id,
                sessions_reviewed=len(new_sessions),
                tokens_used=tokens_used,
                summary=f"JSON-Parse-Fehler: {str(e)[:200]}",
                status="failed",
                duration_ms=duration_ms,
            )
            db.add(dream)
            await db.flush()
            return dream

        # Operationen (create/update/delete) auf die DB anwenden
        memory_index = {mem.key: mem for mem in existing_memories}

        for op in operations:
            action = op.get("action")
            key = op.get("key", "").strip()
            if not key:
                continue

            if action == "create":
                new_mem = Memory(
                    project_id=project_id,
                    key=key,
                    content=op.get("content", ""),
                    memory_type=op.get("type", "project"),
                    confidence=min(max(op.get("confidence", 0.5), 0.0), 1.0),
                    source_count=len(new_sessions),
                )
                db.add(new_mem)
                created += 1

            elif action == "update":
                existing = memory_index.get(key)
                if existing:
                    existing.content = op.get("content", existing.content)
                    existing.confidence = min(max(op.get("confidence", existing.confidence), 0.0), 1.0)
                    existing.source_count += len(new_sessions)
                    if op.get("type"):
                        existing.memory_type = op["type"]
                    updated += 1
                else:
                    # Key existiert nicht in DB → als Create behandeln
                    new_mem = Memory(
                        project_id=project_id,
                        key=key,
                        content=op.get("content", ""),
                        memory_type=op.get("type", "project"),
                        confidence=min(max(op.get("confidence", 0.5), 0.0), 1.0),
                        source_count=len(new_sessions),
                    )
                    db.add(new_mem)
                    created += 1

            elif action == "delete":
                existing = memory_index.get(key)
                if existing:
                    await db.delete(existing)
                    deleted += 1

    # ── Phase 6: Abschluss — Sessions markieren und Dream-Protokoll ──
    for session in new_sessions:
        session.is_consolidated = True

    duration_ms = int((time.monotonic() - start_time) * 1000)

    dream = Dream(
        project_id=project_id,
        sessions_reviewed=len(new_sessions),
        memories_created=created,
        memories_updated=updated,
        memories_deleted=deleted,
        summary=summary,
        tokens_used=tokens_used,
        duration_ms=duration_ms,
        status="completed",
    )
    db.add(dream)
    await db.flush()

    logger.info(
        "Projekt %s: Dream abgeschlossen – %d erstellt, %d aktualisiert, %d gelöscht (%d ms)",
        project_id, created, updated, deleted, duration_ms,
    )
    return dream
