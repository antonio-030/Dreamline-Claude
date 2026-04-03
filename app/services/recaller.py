"""
Recall-Service – findet relevante Erinnerungen per Stichwortsuche oder KI-gestützt.

Unterstützt zwei Modi:
- fast: ILIKE-Stichwortsuche (Standard, schnell, kein KI-Aufruf)
- smart: KI-gestützte Relevanzprüfung (wie Claude Codes findRelevantMemories)
"""

import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import Memory
from app.services import ai_client


# ─── Memory-Staleness (1:1 wie memoryAge.ts) ─────────────────────

def _memory_staleness_note(updated_at: datetime | None) -> str | None:
    """
    Gibt eine Staleness-Warnung zurück für Memories > 1 Tag alt.
    1:1 wie memoryAge.ts memoryFreshnessText().
    """
    if not updated_at:
        return None

    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    days = (now - updated_at).days

    if days == 0:
        return None  # Heute aktualisiert — keine Warnung
    elif days == 1:
        return "Updated yesterday — verify against current code before asserting as fact."
    else:
        return f"Updated {days} days ago — verify against current code before asserting as fact."

logger = logging.getLogger(__name__)

# Prompt für KI-gestützte Relevanzprüfung (1:1 wie findRelevantMemories.ts)
SMART_RECALL_PROMPT = """You are selecting memories that will be useful to Claude Code as it processes a user's query. Return a list of filenames for up to 5 helpful memories. Be selective and discerning — only include memories that are genuinely relevant to the query.

Return ONLY a JSON array of memory keys: ["key1", "key2"]

Query: {query}

Available memories:
{memories}"""


async def recall_memories(
    db: AsyncSession,
    project_id: UUID,
    query: str,
    limit: int = 5,
    mode: str = "fast",
    ai_provider: str = "claude-abo",
    ai_model: str = "claude-sonnet-4-5-20250514",
) -> list[dict]:
    """
    Sucht relevante Erinnerungen. Unterstützt zwei Modi:

    - fast: ILIKE-Stichwortsuche (Standard)
    - smart: KI wählt die relevantesten Erinnerungen aus
    """
    if mode == "smart":
        return await _recall_smart(
            db, project_id, query, limit, ai_provider, ai_model
        )
    return await _recall_fast(db, project_id, query, limit)


async def _recall_fast(
    db: AsyncSession,
    project_id: UUID,
    query: str,
    limit: int = 5,
) -> list[dict]:
    """
    Sucht Erinnerungen per ILIKE-Stichwortsuche in Key und Content.

    Sortiert nach Konfidenz * Relevanz (Treffer in Key zählt mehr).
    """
    # Suchbegriffe aufteilen für breitere Suche
    terms = query.strip().split()
    if not terms:
        return []

    # ILIKE-Sonderzeichen escapen (verhindert Wildcard-Injection)
    def _escape_ilike(t: str) -> str:
        return t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    # Alle Erinnerungen des Projekts laden, die mindestens einen Suchbegriff enthalten
    conditions = []
    for term in terms:
        safe_term = _escape_ilike(term)
        pattern = f"%{safe_term}%"
        conditions.append(Memory.key.ilike(pattern))
        conditions.append(Memory.content.ilike(pattern))

    stmt = (
        select(Memory)
        .where(Memory.project_id == project_id)
        .where(or_(*conditions))
    )
    result = await db.execute(stmt)
    memories = result.scalars().all()

    # Relevanz berechnen: Treffer im Key zählen doppelt
    scored = []
    for mem in memories:
        key_lower = mem.key.lower()
        content_lower = mem.content.lower()
        relevance = 0.0

        for term in terms:
            term_lower = term.lower()
            if term_lower in key_lower:
                relevance += 2.0  # Key-Treffer zählt doppelt
            if term_lower in content_lower:
                relevance += 1.0

        # Normalisieren auf 0-1 Bereich
        max_possible = len(terms) * 3.0  # 2 für Key + 1 für Content pro Term
        relevance_score = min(relevance / max_possible, 1.0) if max_possible > 0 else 0.0

        # Gesamtscore: Konfidenz * Relevanz
        final_score = mem.confidence * relevance_score

        entry = {
            "id": mem.id,
            "key": mem.key,
            "content": mem.content,
            "memory_type": mem.memory_type,
            "confidence": mem.confidence,
            "relevance_score": round(final_score, 4),
        }
        # Staleness-Warnung (1:1 wie memoryAge.ts)
        staleness = _memory_staleness_note(mem.updated_at)
        if staleness:
            entry["staleness_warning"] = staleness
        scored.append(entry)

    # Nach Score absteigend sortieren und limitieren
    scored.sort(key=lambda x: x["relevance_score"], reverse=True)

    logger.info(
        "Recall (fast) für Projekt %s: %d Treffer für '%s'",
        project_id, len(scored[:limit]), query,
    )
    return scored[:limit]


async def _recall_smart(
    db: AsyncSession,
    project_id: UUID,
    query: str,
    limit: int = 5,
    ai_provider: str = "claude-abo",
    ai_model: str = "claude-sonnet-4-5-20250514",
) -> list[dict]:
    """
    KI-gestützte Recall-Suche. Lädt alle Memories und lässt die KI
    die relevantesten auswählen (wie Claude Codes findRelevantMemories).

    Fallback auf fast-Modus bei KI-Fehlern.
    """
    # Alle Erinnerungen des Projekts laden
    stmt = (
        select(Memory)
        .where(Memory.project_id == project_id)
        .order_by(Memory.key)
    )
    result = await db.execute(stmt)
    all_memories = list(result.scalars().all())

    if not all_memories:
        return []

    # Memory-Liste für den Prompt aufbauen
    memory_lines = []
    memory_index = {}
    for mem in all_memories:
        memory_lines.append(f"- [{mem.key}] ({mem.memory_type}): {mem.content[:200]}")
        memory_index[mem.key] = mem

    prompt = SMART_RECALL_PROMPT.format(
        query=query,
        memories="\n".join(memory_lines),
    )

    try:
        response_text, _tokens = await ai_client.complete(
            provider=ai_provider,
            model=ai_model,
            system_prompt="Du bist ein Relevanz-Filter. Antworte nur mit JSON.",
            user_prompt=prompt,
        )

        # JSON-Array parsen
        clean_text = response_text.strip()
        # JSON aus Markdown-Codeblöcken extrahieren
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

        selected_keys = json.loads(clean_text)

        if not isinstance(selected_keys, list):
            raise ValueError("KI-Antwort ist kein JSON-Array")

        # Ausgewählte Memories zurückgeben
        results = []
        for i, key in enumerate(selected_keys[:limit]):
            mem = memory_index.get(key)
            if mem:
                # Relevanz-Score basierend auf KI-Reihenfolge (erste = relevanteste)
                relevance = round(1.0 - (i * 0.15), 4)
                entry = {
                    "id": mem.id,
                    "key": mem.key,
                    "content": mem.content,
                    "memory_type": mem.memory_type,
                    "confidence": mem.confidence,
                    "relevance_score": max(relevance, 0.1),
                }
                staleness = _memory_staleness_note(mem.updated_at)
                if staleness:
                    entry["staleness_warning"] = staleness
                results.append(entry)

        logger.info(
            "Recall (smart) für Projekt %s: %d Treffer für '%s'",
            project_id, len(results), query,
        )
        return results

    except Exception as e:
        logger.warning(
            "Smart-Recall fehlgeschlagen für Projekt %s: %s. Fallback auf fast-Modus.",
            project_id, str(e),
        )
        # Fallback auf ILIKE-Suche
        return await _recall_fast(db, project_id, query, limit)
