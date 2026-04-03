"""
Dream-Prompts – Memory-Taxonomie, Konsolidierungs-Prompt und Prompt-Building.

Enthält den 1:1-Nachbau von Claude Code's consolidationPrompt.ts
und die Logik zum Zusammenbauen der User-Prompts.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.models.memory import Memory
from app.models.session import Session

logger = logging.getLogger(__name__)

# Konstanten identisch zu Claude Code memdir.ts
ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_KB = 25
MAX_MEMORY_FILES = 200

# Lock-Dateiname (wird beim Scannen ignoriert)
CONSOLIDATE_LOCK_FILE = ".consolidate-lock"


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
name: {{{{memory name}}}}
description: {{{{one-line description — used to decide relevance, so be specific}}}}
type: {{{{user, feedback, project, reference}}}}
---

{{{{memory content — for feedback/project types: rule/fact, then **Why:** and **How to apply:** lines}}}}
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
{{{{
  "operations": [
    {{{{"action": "create", "key": "topic-name", "type": "feedback", "content": "...", "confidence": 0.85}}}},
    {{{{"action": "update", "key": "existing-key", "content": "Updated...", "confidence": 0.9}}}},
    {{{{"action": "delete", "key": "outdated-key"}}}}
  ],
  "summary": "Brief summary of what was consolidated, updated, or pruned."
}}}}

If nothing changed (memories are already tight), return empty operations array."""


def scan_memory_manifest(memory_dir: Path) -> str:
    """
    Scannt alle .md-Dateien im Memory-Dir und erstellt ein Manifest.
    Sortiert nach mtime (neueste zuerst), max 200 Dateien.
    """
    if not memory_dir or not memory_dir.exists():
        return ""

    entries = []
    try:
        for filepath in memory_dir.glob("*.md"):
            if filepath.name in (ENTRYPOINT_NAME, CONSOLIDATE_LOCK_FILE):
                continue

            try:
                stat = filepath.stat()
                description = ""
                mem_type = ""

                content = filepath.read_text(encoding="utf-8")
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        for line in parts[1].strip().split("\n"):
                            if line.startswith("description:"):
                                description = line.split(":", 1)[1].strip()[:100]
                            elif line.startswith("type:"):
                                mem_type = line.split(":", 1)[1].strip()

                mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
                entry = f"- [{mem_type}] {filepath.name} ({mtime_iso})"
                if description:
                    entry += f": {description}"
                entries.append((stat.st_mtime, entry))

            except OSError:
                continue

    except OSError:
        return ""

    entries.sort(key=lambda x: x[0], reverse=True)
    entries = entries[:MAX_MEMORY_FILES]

    if not entries:
        return ""

    return "\n".join(e[1] for e in entries)


def build_user_prompt(
    existing_memories: list[Memory],
    new_sessions: list[Session],
    memory_dir: str | None = None,
    transcript_dir: str | None = None,
    use_agent_mode: bool = False,
) -> str:
    """
    Erstellt den User-Prompt für die Dream-Engine.

    Agent-Modus: Agent bekommt Pfade und greped selbst.
    JSON-Modus: Session-Daten werden als Text mitgegeben.
    """
    parts = []

    # Pfade für Agent-Modus
    if use_agent_mode and memory_dir:
        parts.append(f"Memory directory: `{memory_dir}`")
        parts.append("This directory already exists — write to it directly with the Write tool (do not run mkdir or check for its existence).\n")

        manifest = scan_memory_manifest(Path(memory_dir))
        if manifest:
            parts.append("## Existing memory files\n")
            parts.append(manifest)
            parts.append("\nCheck this list before writing — update an existing file rather than creating a duplicate.\n")

    if use_agent_mode and transcript_dir:
        parts.append(f"Session transcripts: `{transcript_dir}` (large JSONL files — grep narrowly, don't read whole files)\n")

    # Session-IDs extrahieren
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

    # Projektkontext aus Session-Metadaten
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

    # Session-Daten einfügen
    if use_agent_mode:
        parts.extend(_build_agent_mode_sessions(new_sessions, session_ids))
    else:
        parts.extend(_build_json_mode_sessions(existing_memories, new_sessions))

    # Anweisung
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


def _build_agent_mode_sessions(
    new_sessions: list[Session],
    session_ids: list[str],
) -> list[str]:
    """Baut den Session-Teil für den Agent-Modus."""
    parts = []
    parts.append(f"Sessions since last consolidation ({len(new_sessions)}):")
    for sid in session_ids:
        parts.append(f"- {sid}")
    if not session_ids:
        for i, s in enumerate(new_sessions, 1):
            parts.append(f"- session-{i} ({s.created_at})")
    parts.append("")

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

    return parts


def _build_json_mode_sessions(
    existing_memories: list[Memory],
    new_sessions: list[Session],
) -> list[str]:
    """Baut den Session-Teil für den JSON-Modus."""
    parts = []

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

    return parts
