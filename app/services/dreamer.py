"""
Dream-Engine – Orchestriert den Konsolidierungslauf.

Nutzt die aufgeteilten Module:
- dream_locks.py: Dual-Lock-Strategie (DB + Dateisystem)
- dream_prompts.py: Prompt-Building und Memory-Taxonomie
- dream_sync.py: File-to-DB Synchronisation

Quellen: Claude Code autoDream.ts, consolidationPrompt.ts
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.dream import Dream
from app.models.memory import Memory
from app.models.project import Project
from app.models.session import Session
from app.services import ai_client
from app.services.dream_locks import (
    acquire_dual_locks,
    check_consolidate_lock,
    find_memory_dir,
    make_skipped_dream,
    release_consolidate_lock,
    release_lock,
    rollback_consolidate_lock,
    snapshot_memory_dir,
    validate_agent_writes,
)
from app.services.dream_prompts import CONSOLIDATION_SYSTEM_PROMPT, build_user_prompt
from app.services.dream_sync import sync_files_to_db

logger = logging.getLogger(__name__)

# Re-Export für Abwärtskompatibilität (link.py importiert _sync_files_to_db)
_sync_files_to_db = sync_files_to_db


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
    """
    start_time = time.monotonic()

    # Dual-Lock erwerben
    success, memory_dir, prior_mtime = await acquire_dual_locks(db, project_id)
    if not success:
        if memory_dir and not check_consolidate_lock(memory_dir):
            summary = "Übersprungen: Claude Code Dream-Lock aktiv."
        else:
            summary = "Übersprungen: Dream-Lock bereits aktiv oder Race verloren."
        dream = make_skipped_dream(project_id, summary)
        db.add(dream)
        await db.flush()
        return dream

    pre_snapshot = snapshot_memory_dir(memory_dir) if memory_dir else {}

    try:
        result = await _execute_dream(db, project_id, ai_provider, ai_model, start_time)
        await _post_dream(db, project_id, result, memory_dir, pre_snapshot, ai_provider, ai_model)

        if memory_dir:
            release_consolidate_lock(memory_dir)
        return result

    except Exception as e:
        logger.error("Projekt %s: Unerwarteter Dream-Fehler: %s", project_id, str(e), exc_info=True)
        if memory_dir:
            rollback_consolidate_lock(memory_dir, prior_mtime)
        duration_ms = int((time.monotonic() - start_time) * 1000)
        dream = Dream(
            project_id=project_id,
            sessions_reviewed=0,
            summary="Interner Fehler bei der Konsolidierung.",
            error_detail=str(e)[:2000],
            ai_provider_used=ai_provider,
            status="failed",
            duration_ms=duration_ms,
        )
        db.add(dream)
        await db.flush()
        return dream

    finally:
        await release_lock(db, project_id)


async def _post_dream(
    db: AsyncSession,
    project_id: UUID,
    dream_result: Dream,
    memory_dir: Path | None,
    pre_snapshot: dict[str, float],
    ai_provider: str,
    ai_model: str,
) -> None:
    """Nacharbeiten nach einem erfolgreichen Dream."""
    # Post-Dream Validierung
    if memory_dir and dream_result.status == "completed":
        valid_files, violations = validate_agent_writes(memory_dir, pre_snapshot)
        if valid_files:
            logger.info("Projekt %s: %d Dateien im Memory-Dir geändert", project_id, len(valid_files))
        if violations:
            logger.warning("Projekt %s: TOOL-ENFORCEMENT-VERSTOSS! %d Dateien ausserhalb", project_id, len(violations))

    # Memories als Markdown ins Projekt schreiben
    if dream_result.status == "completed" and (dream_result.memories_created + dream_result.memories_updated) > 0:
        try:
            from app.services.memory_writer import write_memories_to_project
            write_result = await write_memories_to_project(db, project_id)
            logger.info("Projekt %s: %d Memories geschrieben (%s)", project_id, write_result["written"], write_result["path"])
        except (ImportError, OSError, ValueError, RuntimeError) as write_err:
            logger.warning("Memory-Write fehlgeschlagen: %s", str(write_err))

    # Ollama Modelfile-Sync
    if ai_provider == "ollama" and settings.ollama_modelfile_sync:
        try:
            from app.services.ollama_modelfile import sync_ollama_modelfile
            sync_result = await sync_ollama_modelfile(db, project_id, ai_model)
            if sync_result.get("status") == "success":
                logger.info("Projekt %s: Ollama-Modell '%s' aktualisiert", project_id, sync_result["model_name"])
            else:
                logger.warning("Projekt %s: Ollama-Sync fehlgeschlagen: %s", project_id, sync_result.get("error"))
        except (ImportError, OSError, RuntimeError) as ollama_err:
            logger.warning("Ollama Modelfile-Sync Fehler: %s", str(ollama_err))


def _parse_dream_operations(response_text: str) -> tuple[list[dict], str]:
    """Extrahiert Dream-Operationen und Summary aus der KI-Antwort (JSON-Modus).

    Robust gegen verschiedene Antwort-Formate:
    1. Reines JSON (Claude mit --output-format json, OpenAI mit response_format)
    2. JSON in Markdown-Codeblock (```json ... ```)
    3. JSON eingebettet in Freitext (z.B. Codex ohne JSON-Enforcement)
    """
    clean_text = response_text.strip()

    # Strategie 1: JSON aus Markdown-Codeblöcken extrahieren
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

    # Strategie 2: Direktes JSON-Parsing (schneller Pfad)
    try:
        result_data = json.loads(clean_text)
        return result_data.get("operations", []), result_data.get("summary", "")
    except json.JSONDecodeError:
        pass

    # Strategie 3: JSON-Objekt im Freitext finden (fuer Provider ohne JSON-Mode)
    # Sucht alle Top-Level '{...}' Blöcke und prüft ob einer "operations" enthält
    search_start = 0
    max_search_len = min(len(clean_text), 500_000)  # Schutz gegen extrem große Inputs
    while search_start < max_search_len:
        brace_start = clean_text.find("{", search_start)
        if brace_start < 0:
            break
        depth = 0
        for i in range(brace_start, max_search_len):
            if clean_text[i] == "{":
                depth += 1
            elif clean_text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = clean_text[brace_start:i + 1]
                    try:
                        result_data = json.loads(candidate)
                        if "operations" in result_data:
                            return result_data.get("operations", []), result_data.get("summary", "")
                    except json.JSONDecodeError:
                        pass
                    search_start = i + 1
                    break
        else:
            break  # Kein schließendes '}' gefunden

    # Alle Strategien fehlgeschlagen
    raise json.JSONDecodeError(
        "Kein gueltiges JSON mit 'operations' in der KI-Antwort gefunden",
        clean_text[:200], 0,
    )


async def _execute_dream(
    db: AsyncSession,
    project_id: UUID,
    ai_provider: str,
    ai_model: str,
    start_time: float,
) -> Dream:
    """
    Interne Dream-Ausführung (nach Lock-Erwerb).

    Phasen: Sessions laden → Memories laden → Prompt bauen → KI aufrufen → Ergebnis verarbeiten
    """

    # Phase 1: Unverarbeitete Sessions laden
    stmt = (
        select(Session)
        .where(Session.project_id == project_id)
        .where(Session.is_consolidated == False)  # noqa: E712
        .order_by(Session.created_at.asc())
    )
    result = await db.execute(stmt)
    new_sessions = list(result.scalars().all())

    # Session-Exclusion: Neueste Session wenn < 60s alt (noch im Schreibprozess)
    if len(new_sessions) > 1:
        now = datetime.now(timezone.utc)
        latest = new_sessions[-1]
        if latest.created_at:
            created = latest.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if (now - created).total_seconds() < settings.session_exclusion_seconds:
                new_sessions = new_sessions[:-1]

    if not new_sessions:
        logger.info("Projekt %s: Keine neuen Sessions.", project_id)
        dream = Dream(
            project_id=project_id, sessions_reviewed=0,
            summary="Keine neuen Sessions vorhanden.", status="completed", duration_ms=0,
        )
        db.add(dream)
        await db.flush()
        return dream

    # Phase 2: Bestehende Memories laden
    mem_result = await db.execute(
        select(Memory).where(Memory.project_id == project_id).order_by(Memory.key)
    )
    existing_memories = list(mem_result.scalars().all())

    # Phase 3: Prompt zusammenbauen
    project_name = (await db.execute(select(Project.name).where(Project.id == project_id))).scalar() or ""
    agent_memory_dir = find_memory_dir(project_name)
    transcript_dir = str(agent_memory_dir.parent) if agent_memory_dir else None

    # JSON-Modus für alle Provider (Agent-Modus im Container deaktiviert)
    use_agent_mode = False

    user_prompt = build_user_prompt(
        existing_memories, new_sessions,
        memory_dir=str(agent_memory_dir) if agent_memory_dir else None,
        transcript_dir=transcript_dir,
        use_agent_mode=use_agent_mode,
    )

    logger.info(
        "Projekt %s: Dream gestartet – %d Sessions, %d Erinnerungen",
        project_id, len(new_sessions), len(existing_memories),
    )

    # Phase 4: KI-API aufrufen
    tokens_used = 0
    try:
        response_text, tokens_used = await _call_ai(
            db, project_id, ai_provider, ai_model,
            user_prompt, existing_memories, use_agent_mode, agent_memory_dir,
        )
    except Exception as e:
        logger.error("KI-API-Fehler für Projekt %s (%s): %s", project_id, ai_provider, str(e))
        duration_ms = int((time.monotonic() - start_time) * 1000)
        dream = Dream(
            project_id=project_id, sessions_reviewed=len(new_sessions),
            summary=f"KI-API-Fehler ({ai_provider}): {str(e)[:500]}",
            error_detail=str(e)[:2000],
            ai_provider_used=ai_provider,
            status="failed", duration_ms=duration_ms,
        )
        db.add(dream)
        await db.flush()
        return dream

    # Phase 5: Ergebnis verarbeiten
    try:
        created, updated, deleted, summary = await _process_result(
            db, project_id, response_text, use_agent_mode, agent_memory_dir,
            existing_memories, new_sessions, start_time, tokens_used,
        )
    except (json.JSONDecodeError, ValueError, KeyError, RuntimeError) as e:
        logger.error("Projekt %s: Fehler beim Verarbeiten der KI-Antwort: %s", project_id, str(e)[:200])
        duration_ms = int((time.monotonic() - start_time) * 1000)
        dream = Dream(
            project_id=project_id, sessions_reviewed=len(new_sessions),
            summary=f"Fehler beim Verarbeiten der KI-Antwort: {str(e)[:300]}",
            error_detail=f"Provider: {ai_provider}\nAntwort (Auszug): {response_text[:500]}\n\nFehler: {str(e)[:1000]}",
            ai_provider_used=ai_provider, tokens_used=tokens_used,
            status="failed", duration_ms=duration_ms,
        )
        db.add(dream)
        await db.flush()
        return dream

    # Phase 6: Sessions markieren und Dream-Protokoll
    for session in new_sessions:
        session.is_consolidated = True

    duration_ms = int((time.monotonic() - start_time) * 1000)
    dream = Dream(
        project_id=project_id,
        sessions_reviewed=len(new_sessions),
        memories_created=created, memories_updated=updated, memories_deleted=deleted,
        summary=summary, tokens_used=tokens_used,
        duration_ms=duration_ms, status="completed",
        ai_provider_used=ai_provider,
    )
    db.add(dream)
    await db.flush()

    logger.info(
        "Projekt %s: Dream abgeschlossen – %d erstellt, %d aktualisiert, %d gelöscht (%d ms)",
        project_id, created, updated, deleted, duration_ms,
    )
    return dream


async def _call_ai(
    db: AsyncSession,
    project_id: UUID,
    ai_provider: str,
    ai_model: str,
    user_prompt: str,
    existing_memories: list[Memory],
    use_agent_mode: bool,
    agent_memory_dir: Path | None,
) -> tuple[str, int]:
    """Ruft die KI-API auf (Agent-Modus oder JSON-Modus)."""
    if use_agent_mode:
        agent_prompt = CONSOLIDATION_SYSTEM_PROMPT + "\n\n---\n\n" + user_prompt
        resume_sid_result = await db.execute(
            select(Project.dream_cli_session_id).where(Project.id == project_id)
        )
        resume_session_id = resume_sid_result.scalar()

        response_text, tokens_used, new_session_id = await ai_client.dream_with_tools(
            provider=ai_provider, model=ai_model,
            prompt=agent_prompt, memory_dir=str(agent_memory_dir),
            resume_session_id=resume_session_id,
        )

        if new_session_id:
            await db.execute(
                sa_update(Project).where(Project.id == project_id)
                .values(dream_cli_session_id=new_session_id)
            )
        return response_text, tokens_used

    elif ai_provider == "anthropic":
        memories_context = ""
        if existing_memories:
            mem_parts = []
            for mem in existing_memories:
                mem_parts.append(f"### {mem.key} [{mem.memory_type}] (confidence: {mem.confidence})")
                mem_parts.append(mem.content)
                mem_parts.append("")
            memories_context = "\n".join(mem_parts)

        return await ai_client.complete_with_cache(
            model=ai_model, system_prompt=CONSOLIDATION_SYSTEM_PROMPT,
            user_prompt=user_prompt, existing_memories_context=memories_context,
        )

    else:
        return await ai_client.complete(
            provider=ai_provider, model=ai_model,
            system_prompt=CONSOLIDATION_SYSTEM_PROMPT, user_prompt=user_prompt,
        )


async def _process_result(
    db: AsyncSession,
    project_id: UUID,
    response_text: str,
    use_agent_mode: bool,
    agent_memory_dir: Path | None,
    existing_memories: list[Memory],
    new_sessions: list[Session],
    start_time: float,
    tokens_used: int,
) -> tuple[int, int, int, str]:
    """
    Verarbeitet die KI-Antwort: Entweder File-Sync (Agent) oder JSON-Operationen.
    Rückgabe: (created, updated, deleted, summary)
    Wirft Exception bei JSON-Parse-Fehlern.
    """
    created, updated, deleted = 0, 0, 0

    if use_agent_mode and agent_memory_dir:
        summary = response_text[:500] if response_text else "Agent-Dream abgeschlossen."
        try:
            created, updated, deleted = await sync_files_to_db(
                db, project_id, agent_memory_dir, existing_memories,
            )
        except (OSError, ValueError, RuntimeError) as sync_err:
            logger.warning("File-Sync nach Agent-Dream fehlgeschlagen: %s", sync_err)
        return created, updated, deleted, summary

    # JSON-Modus: Antwort parsen und Operationen anwenden
    operations, summary = _parse_dream_operations(response_text)
    memory_index = {mem.key: mem for mem in existing_memories}
    valid_types = {"user", "feedback", "project", "reference"}

    for op in operations:
        action = op.get("action")
        key = op.get("key", "").strip()
        if not key:
            continue

        raw_type = op.get("type", "project")
        mem_type = raw_type if raw_type in valid_types else "project"

        if action == "create":
            db.add(Memory(
                project_id=project_id, key=key,
                content=op.get("content", ""), memory_type=mem_type,
                confidence=min(max(op.get("confidence", 0.5), 0.0), 1.0),
                source_count=len(new_sessions),
            ))
            created += 1

        elif action == "update":
            existing = memory_index.get(key)
            if existing:
                # Alte Version speichern bevor Update
                from app.models.memory_version import MemoryVersion
                db.add(MemoryVersion(
                    memory_id=existing.id,
                    content=existing.content,
                    confidence=existing.confidence,
                    changed_by="dream",
                ))
                existing.content = op.get("content", existing.content)
                existing.confidence = min(max(op.get("confidence", existing.confidence), 0.0), 1.0)
                existing.source_count += len(new_sessions)
                if raw_type in valid_types:
                    existing.memory_type = mem_type
                updated += 1
            else:
                db.add(Memory(
                    project_id=project_id, key=key,
                    content=op.get("content", ""), memory_type=mem_type,
                    confidence=min(max(op.get("confidence", 0.5), 0.0), 1.0),
                    source_count=len(new_sessions),
                ))
                created += 1

        elif action == "delete":
            existing = memory_index.get(key)
            if existing:
                await db.delete(existing)
                deleted += 1

    return created, updated, deleted, summary
