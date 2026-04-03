"""
Ollama Modelfile-Service — macht Ollama nach jedem Dream "schlauer".

Nach einem erfolgreichen Dream erstellt dieser Service ein Custom-Ollama-Modell
mit den konsolidierten Memories als SYSTEM-Prompt. Das Modell heißt
"dreamline-{projektname}" und wird bei jedem Dream aktualisiert.

So kennt das lokale LLM permanent das Projektwissen — ohne dass bei jedem
Call der gesamte Memory-Kontext mitgesendet werden muss.

Ollama-Endpoints:
- POST /api/create — Erstellt/aktualisiert ein Modell aus einem Modelfile
- GET /api/tags — Listet verfügbare Modelle (für Health-Check)
- DELETE /api/delete — Löscht ein Custom-Modell
"""

import logging
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.memory import Memory
from app.models.project import Project

logger = logging.getLogger(__name__)

# Maximale Länge des SYSTEM-Prompts im Modelfile (in Zeichen, nicht Tokens).
# 4000 Tokens ≈ 16.000 Zeichen. Wir begrenzen konservativ auf 12.000 Zeichen
# damit auch bei kleineren Modellen (8B) genug Platz für den User-Prompt bleibt.
MAX_SYSTEM_CHARS = 12_000

# Prioritätsreihenfolge der Memory-Typen im System-Prompt.
# User-Präferenzen und Feedback sind am wichtigsten (beeinflussen Verhalten direkt),
# Projekt-Wissen und Referenzen sind ergänzend.
TYPE_PRIORITY = {"user": 0, "feedback": 1, "project": 2, "reference": 3}

TYPE_LABELS_DE = {
    "user": "Nutzer-Präferenzen",
    "feedback": "Verhaltensregeln",
    "project": "Projekt-Wissen",
    "reference": "Referenzen & Links",
}


def _slugify(name: str) -> str:
    """Erzeugt einen URL-sicheren Namen für das Ollama-Modell."""
    return "dreamline-" + name.lower().replace(" ", "-").replace("_", "-")[:40]


def build_modelfile_content(
    base_model: str,
    project_name: str,
    memories: list[Memory],
) -> str:
    """
    Baut den Modelfile-String für ein Custom-Ollama-Modell.

    Das Modelfile enthält:
    - FROM: Das Base-Modell (z.B. llama3.1:8b)
    - SYSTEM: Alle konsolidierten Memories als Projekt-Kontext
    - PARAMETER temperature 0.3: Niedrig für konsistente Antworten

    Memories werden nach Typ gruppiert und nach Konfidenz sortiert.
    Wenn der Gesamt-Text das Zeichenlimit überschreitet, werden die
    ältesten/niedrigsten Memories abgeschnitten.
    """
    if not memories:
        return f'FROM {base_model}\nPARAMETER temperature 0.3\n'

    # Memories nach Typ gruppieren und nach Konfidenz sortieren
    grouped: dict[str, list[Memory]] = {}
    for mem in sorted(memories, key=lambda m: (-TYPE_PRIORITY.get(m.memory_type, 99), -m.confidence)):
        grouped.setdefault(mem.memory_type, []).append(mem)

    # System-Prompt aufbauen
    parts = [
        f'Du bist ein Assistent für das Projekt "{project_name}".',
        "Nutze folgendes Projektwissen für deine Antworten:\n",
    ]

    total_chars = sum(len(p) for p in parts)

    for mem_type in ["user", "feedback", "project", "reference"]:
        mems = grouped.get(mem_type, [])
        if not mems:
            continue

        label = TYPE_LABELS_DE.get(mem_type, mem_type)
        section_header = f"\n## {label}\n"
        section_chars = len(section_header)

        entries = []
        for mem in mems:
            entry = f"- **{mem.key}**: {mem.content}\n"
            if total_chars + section_chars + len(entry) > MAX_SYSTEM_CHARS:
                break  # Limit erreicht — Rest abschneiden
            entries.append(entry)
            section_chars += len(entry)

        if entries:
            parts.append(section_header)
            parts.extend(entries)
            total_chars += section_chars

    system_text = "".join(parts)

    # Modelfile zusammenbauen
    # Anführungszeichen im System-Text escapen
    escaped = system_text.replace('"', '\\"')

    modelfile = f"""FROM {base_model}
SYSTEM "{escaped}"
PARAMETER temperature 0.3
PARAMETER num_ctx 8192
"""
    return modelfile


async def sync_ollama_modelfile(
    db: AsyncSession,
    project_id: UUID,
    base_model: str,
) -> dict:
    """
    Erstellt oder aktualisiert ein Custom-Ollama-Modell mit den aktuellen Memories.

    Ablauf:
    1. Alle Memories des Projekts aus der DB laden
    2. Modelfile-Content bauen (Memories als SYSTEM-Prompt)
    3. POST /api/create an Ollama → Modell wird erstellt/überschrieben
    4. Custom-Modellname im Projekt speichern

    Rückgabe: {"model_name": "dreamline-...", "memories_included": N, "status": "success"}
    """
    # Projekt und Memories laden
    project = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if not project:
        return {"status": "error", "error": "Projekt nicht gefunden"}

    mem_result = await db.execute(
        select(Memory).where(Memory.project_id == project_id).order_by(Memory.confidence.desc())
    )
    memories = list(mem_result.scalars().all())

    model_name = _slugify(project.name)
    modelfile = build_modelfile_content(base_model, project.name, memories)

    # Ollama API: Modell erstellen/aktualisieren
    url = f"{settings.ollama_base_url}/api/create"
    try:
        async with httpx.AsyncClient(timeout=settings.ollama_timeout) as client:
            response = await client.post(url, json={
                "name": model_name,
                "modelfile": modelfile,
            })
            response.raise_for_status()
    except httpx.ConnectError:
        logger.warning("Ollama nicht erreichbar unter %s", settings.ollama_base_url)
        return {"status": "error", "error": "Ollama nicht erreichbar"}
    except httpx.HTTPStatusError as e:
        logger.warning("Ollama Modelfile-Erstellung fehlgeschlagen: %s", e)
        return {"status": "error", "error": str(e)}

    # Custom-Modellname im Projekt speichern
    project.ollama_custom_model_name = model_name
    await db.flush()

    logger.info(
        "Ollama Modelfile-Sync: '%s' erstellt mit %d Memories (Basis: %s)",
        model_name, len(memories), base_model,
    )

    return {
        "model_name": model_name,
        "base_model": base_model,
        "memories_included": len(memories),
        "modelfile_chars": len(modelfile),
        "status": "success",
    }


async def check_ollama_health() -> dict:
    """
    Prüft ob Ollama läuft und welche Modelle verfügbar sind.

    GET /api/tags → Liste aller lokalen Modelle.
    """
    url = f"{settings.ollama_base_url}/api/tags"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            response.raise_for_status()

        data = response.json()
        models = [m.get("name", "") for m in data.get("models", [])]
        dreamline_models = [m for m in models if m.startswith("dreamline-")]

        return {
            "available": True,
            "models": models,
            "dreamline_models": dreamline_models,
            "base_url": settings.ollama_base_url,
        }
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return {
            "available": False,
            "error": str(e),
            "base_url": settings.ollama_base_url,
        }


async def delete_ollama_modelfile(project_name: str) -> bool:
    """Löscht ein Custom-Ollama-Modell."""
    model_name = _slugify(project_name)
    url = f"{settings.ollama_base_url}/api/delete"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.delete(url, json={"name": model_name})
            return response.status_code == 200
    except (httpx.ConnectError, httpx.HTTPStatusError):
        return False
