"""
Schnell-Extraktion – 1:1 wie Claude Code extractMemories.

Läuft nach jeder Session und extrahiert offensichtliche Fakten sofort.
Features aus Claude Code:
- Cursor-Tracking (lastExtractedSessionId) – nur neue Sessions verarbeiten
- Overlap-Prevention (is_extracting) – kein paralleles Extrahieren
- Trailing Runs – nach Abschluss prüfen ob neue Sessions dazukamen
- Mutual Exclusion – überspringt wenn Hauptagent selbst Memories geschrieben hat
- Duplikat-Check – bestehende Keys nicht überschreiben
- maxTurns=5 Equivalent – nur hochkonfidente Fakten (>0.8)

Referenz: claude-code-study/services/extractMemories/extractMemories.ts
"""

import json
import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import Memory
from app.models.project import Project
from app.models.session import Session
from app.services import ai_client

logger = logging.getLogger(__name__)

# Quick-Extract Prompt (wie extractMemories buildExtractAutoOnlyPrompt)
QUICK_EXTRACT_PROMPT = """You are performing a quick memory extraction from a single chat session.
Extract ONLY high-confidence facts (>0.8) that should be saved immediately.

Save:
- Explicit information (URLs, IPs, credentials hints, names, roles)
- Clear user preferences ("always do X", "never do Y")
- Confirmed solutions that worked
- Project decisions and constraints

Do NOT save:
- Guesses or uncertain information
- One-time context details
- Things that change frequently
- Code patterns derivable from the codebase
- Debugging solutions (the fix is in the code)

Response as JSON:
{
  "operations": [
    {"action": "create", "key": "topic-name", "type": "reference|feedback|user|project", "content": "...", "confidence": 0.9}
  ],
  "extract_summary": "Brief summary of what was extracted, or 'Nothing new' if empty"
}"""


# Pro-Projekt State (wie extractMemories closure-scoped state)
class _ProjectExtractorState:
    """Closure-scoped State pro Projekt (wie extractMemories.ts)."""

    def __init__(self):
        self.is_extracting: bool = False
        self.pending_session: Session | None = None
        self.pending_project_id: UUID | None = None
        self.pending_ai_provider: str | None = None
        self.pending_ai_model: str | None = None
        # Turn-Throttle (1:1 wie tengu_bramble_lintel)
        self.sessions_since_last_extract: int = 0


# Pro-Projekt State Registry
_project_states: dict[str, _ProjectExtractorState] = {}


def _get_state(project_id: UUID) -> _ProjectExtractorState:
    key = str(project_id)
    if key not in _project_states:
        _project_states[key] = _ProjectExtractorState()
    return _project_states[key]


def _build_session_prompt(session: Session) -> str:
    """Erstellt den User-Prompt aus einer einzelnen Session."""
    messages = json.loads(session.messages_json)
    parts = ["## Chat Session"]

    if session.outcome:
        parts.append(f"Outcome: {session.outcome}")

    if session.metadata_json:
        try:
            meta = json.loads(session.metadata_json)
            # Projektkontext mitgeben wenn vorhanden
            ctx = meta.get("project_context")
            if ctx:
                parts.append(f"\n## Project context\n{ctx[:2000]}")
            meta_clean = {k: v for k, v in meta.items() if k != "project_context"}
            if meta_clean:
                parts.append(f"Metadata: {json.dumps(meta_clean, ensure_ascii=False)}")
        except json.JSONDecodeError:
            pass

    parts.append("")
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if len(content) > 2000:
            content = content[:2000] + "\n... [truncated]"
        parts.append(f"**{role}**: {content}")

    return "\n".join(parts)


def _check_memory_writes_since(project_id: UUID, memory_dir: Path | None) -> bool:
    """
    Mutual Exclusion: Prüft ob der Hauptagent seit dem letzten Dream
    selbst Memories geschrieben hat.
    1:1 wie Claude Code hasMemoryWritesSince() in extractMemories.ts:122-148.

    Wenn der Hauptagent gerade Dateien im Memory-Dir geändert hat,
    überspringen wir die Extraktion (der Agent hat es selbst gemacht).
    """
    if not memory_dir or not memory_dir.exists():
        return False

    # Prüfe ob kürzlich (< 30 Sekunden) Dateien im Memory-Dir geändert wurden
    import time
    now = time.time()
    threshold = 30  # Sekunden

    try:
        for filepath in memory_dir.glob("*.md"):
            if filepath.name == "MEMORY.md":
                continue
            if filepath.name == ".consolidate-lock":
                continue
            mtime = filepath.stat().st_mtime
            if now - mtime < threshold:
                logger.info(
                    "Projekt %s: Mutual Exclusion – %s wurde vor %ds geändert, überspringe Quick-Extract",
                    project_id, filepath.name, int(now - mtime),
                )
                return True
    except OSError:
        pass

    return False


async def quick_extract(
    db: AsyncSession,
    session: Session,
    project_id: UUID,
    ai_provider: str,
    ai_model: str,
) -> str | None:
    """
    Schnell-Extraktion mit Overlap-Prevention, Trailing Runs und Mutual Exclusion.
    1:1 wie Claude Code extractMemories.
    """
    state = _get_state(project_id)

    # Turn-Throttle (1:1 wie tengu_bramble_lintel):
    # Nur alle N Sessions extrahieren statt nach jeder (spart API-Kosten)
    from app.config import settings
    state.sessions_since_last_extract += 1
    if state.sessions_since_last_extract < settings.extract_every_n_sessions:
        logger.debug(
            "Projekt %s: Extract-Throttle – %d/%d Sessions",
            project_id, state.sessions_since_last_extract, settings.extract_every_n_sessions,
        )
        return None
    state.sessions_since_last_extract = 0

    # Mutual Exclusion: Prüfe ob der Hauptagent gerade selbst Memories schreibt
    # (1:1 wie hasMemoryWritesSince in extractMemories.ts)
    from app.services.dream_locks import find_memory_dir
    project_name_result = await db.execute(
        select(Project.name).where(Project.id == project_id)
    )
    project_name = project_name_result.scalar() or ""
    memory_dir = find_memory_dir(project_name)
    if _check_memory_writes_since(project_id, memory_dir):
        # Cursor trotzdem vorwärts bewegen (wie Claude Code)
        await db.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(last_extracted_session_id=str(session.id))
        )
        return "Übersprungen: Hauptagent hat selbst Memories geschrieben."

    # Overlap-Prevention: Wenn schon eine Extraktion läuft, stashen (Coalescing)
    if state.is_extracting:
        logger.info(
            "Projekt %s: Quick-Extract läuft bereits – stashe Session %s für Trailing Run",
            project_id, session.id,
        )
        state.pending_session = session
        state.pending_project_id = project_id
        state.pending_ai_provider = ai_provider
        state.pending_ai_model = ai_model
        return None

    state.is_extracting = True
    try:
        result = await _run_extraction(db, session, project_id, ai_provider, ai_model)

        # Persistenten Cursor aktualisieren (1:1 wie lastMemoryMessageUuid)
        await db.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(last_extracted_session_id=str(session.id))
        )

        # Trailing Run: Prüfe ob während der Extraktion eine neue Session kam
        while state.pending_session is not None:
            trailing = state.pending_session
            trailing_pid = state.pending_project_id
            trailing_provider = state.pending_ai_provider
            trailing_model = state.pending_ai_model
            state.pending_session = None

            logger.info("Projekt %s: Trailing Quick-Extract für gestashte Session", project_id)
            await _run_extraction(
                db, trailing, trailing_pid, trailing_provider, trailing_model,
            )

            # Cursor nach Trailing Run aktualisieren
            await db.execute(
                update(Project)
                .where(Project.id == project_id)
                .values(last_extracted_session_id=str(trailing.id))
            )

        return result
    finally:
        state.is_extracting = False


async def _run_extraction(
    db: AsyncSession,
    session: Session,
    project_id: UUID,
    ai_provider: str,
    ai_model: str,
) -> str | None:
    """Interne Extraktions-Logik für eine einzelne Session."""
    user_prompt = _build_session_prompt(session)

    try:
        response_text, _tokens = await ai_client.complete(
            provider=ai_provider,
            model=ai_model,
            system_prompt=QUICK_EXTRACT_PROMPT,
            user_prompt=user_prompt,
        )
    except Exception as e:
        logger.warning(
            "Quick-Extract fehlgeschlagen für Session %s: %s",
            session.id, str(e),
        )
        return None

    # JSON parsen
    try:
        clean_text = response_text.strip()
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
        extract_summary = result_data.get("extract_summary", "Nothing new")
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Quick-Extract JSON-Fehler für Session %s: %s", session.id, str(e))
        return None

    if not operations:
        logger.info("Session %s: Quick-Extract – keine neuen Fakten.", session.id)
        return extract_summary

    # Bestehende Keys laden (Duplikat-Check)
    mem_stmt = select(Memory.key).where(Memory.project_id == project_id)
    mem_result = await db.execute(mem_stmt)
    existing_keys = {row[0] for row in mem_result.all()}

    created = 0
    for op in operations:
        action = op.get("action")
        key = op.get("key", "").strip()
        if not key or action != "create":
            continue

        if key in existing_keys:
            continue

        confidence = op.get("confidence", 0.8)
        if confidence < 0.8:
            continue

        # Memory-Typ validieren (Ollama gibt manchmal ungültige Werte zurück)
        valid_types = {"user", "feedback", "project", "reference"}
        raw_type = op.get("type", "reference")
        mem_type = raw_type if raw_type in valid_types else "reference"

        new_mem = Memory(
            project_id=project_id,
            key=key,
            content=op.get("content", ""),
            memory_type=mem_type,
            confidence=min(max(confidence, 0.0), 1.0),
            source_count=1,
        )
        db.add(new_mem)
        existing_keys.add(key)
        created += 1

    if created > 0:
        await db.flush()
        logger.info("Session %s: Quick-Extract – %d neue Erinnerungen.", session.id, created)

    return extract_summary
