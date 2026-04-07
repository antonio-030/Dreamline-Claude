"""
Memory-Writer – Schreibt konsolidierte Memories als Markdown-Dateien
ins Projekt-Memory-Verzeichnis (gleicher Ort wie Claude Code autoDream).

Pfad: ~/.claude/projects/{projekt-key}/memory/

So kann Claude Code die Memories beim nächsten Start direkt lesen
und der Kontext ist sofort verfügbar.
"""

import json
import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import CLAUDE_PROJECTS_DIR
from app.models.memory import Memory
from app.models.project import Project

logger = logging.getLogger(__name__)

# Codex Memory-Verzeichnis (relativ zum Projekt-Root)
CODEX_MEMORY_SUBDIR = ".codex/memory"

# AGENTS.md Marker für Dreamline-verwalteten Bereich
AGENTS_MD_START = "<!-- dreamline:start -->"
AGENTS_MD_END = "<!-- dreamline:end -->"

# CLAUDE.md Marker für Dreamline-Hinweis
CLAUDE_MD_START = "<!-- dreamline-start -->"
CLAUDE_MD_END = "<!-- dreamline-end -->"

# Memory-Typ zu Datei-Präfix Mapping
TYPE_PREFIXES = {
    "user": "user",
    "feedback": "feedback",
    "project": "project",
    "reference": "reference",
}

def _yaml_escape(value: str) -> str:
    """Escaped einen Wert für sichere Einbettung in YAML-Frontmatter."""
    # Zeilenumbrüche entfernen und Anführungszeichen escapen
    safe = value.replace("\n", " ").replace("\r", "").replace('"', '\\"')
    # Immer in Anführungszeichen setzen wenn Sonderzeichen oder YAML-Delimiter enthalten
    if "---" in safe or any(c in safe for c in (':', '#', '{', '}', '[', ']', "'", '"')):
        return f'"{safe}"'
    return safe


ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200


def _sanitize_path(path_str: str) -> str:
    """Konvertiert einen Projektpfad in das Claude Code Format.

    Claude Code speichert projektspezifische Daten unter
    ~/.claude/projects/{sanitized-path}/. Dabei wird der absolute
    Dateipfad wie folgt umgewandelt:

    1. ":" wird entfernt  – Windows-Laufwerksbuchstabe (z.B. "C:" → "C")
    2. "/" und "\\" werden durch "-" ersetzt – Pfad-Trennzeichen
       werden zu Bindestrichen, da Ordnernamen keine Slashes enthalten dürfen.

    Beispiel:
        "C:\\Users\\max\\Desktop\\MeinProjekt"
        → "C--Users-max--Desktop-MeinProjekt"

    Die Funktion versucht zuerst, den echten Ordnernamen im
    ~/.claude/projects/-Verzeichnis per case-insensitive Suche
    zu finden, da Claude Code den Namen bereits angelegt haben könnte.
    Nur als Fallback wird manuell konvertiert.
    """
    # Zuerst: Prüfe ob ein passender Ordner existiert (case-insensitive)
    projects_dir = CLAUDE_PROJECTS_DIR
    if projects_dir.exists():
        # Normalisierter Suchstring
        search = path_str.lower().replace(":", "").replace("/", "-").replace("\\", "-").strip("-")
        for entry in projects_dir.iterdir():
            if entry.is_dir() and entry.name.lower().replace("--", "-") == search.replace("--", "-"):
                return entry.name
            # Auch exakte Übereinstimmung prüfen
            if entry.is_dir() and search in entry.name.lower():
                return entry.name

    # Fallback: Manuell konvertieren
    return path_str.replace(":", "").replace("/", "-").replace("\\", "-").strip("-")


def _key_to_filename(key: str) -> str:
    """Konvertiert einen Memory-Key in einen Dateinamen."""
    # Sonderzeichen entfernen, Leerzeichen zu Unterstrichen
    clean = key.replace(" ", "_").replace("/", "_").replace("\\", "_")
    # Nur alphanumerisch + Unterstrich + Bindestrich
    clean = "".join(c for c in clean if c.isalnum() or c in "_-")
    return f"{clean}.md"


def _find_project_dir(project_name: str) -> Path | None:
    """Findet das Claude-Projektverzeichnis anhand des Projektnamens.

    Claude Code benennt seine Projektordner nach dem sanitisierten
    absoluten Pfad des Projekts (siehe _sanitize_path()). Da Dreamline
    nur den Projektnamen kennt, nicht den vollen Pfad, wird hier
    per Substring-Suche (case-insensitive) in ~/.claude/projects/
    nach einem passenden Ordner gesucht.

    Beispiel: Projektname "MeinProjekt" findet den Ordner
    "C--Users-max--Desktop-MeinProjekt", weil das letzte Segment
    nach "--" exakt "MeinProjekt" ist.

    Gibt None zurück, wenn kein passender Ordner existiert.
    """
    if not CLAUDE_PROJECTS_DIR.exists():
        return None

    name_lower = project_name.lower()

    # Exakter Match auf letztes Pfad-Segment (nach letztem "--")
    for entry in CLAUDE_PROJECTS_DIR.iterdir():
        if not entry.is_dir():
            continue
        segments = entry.name.split("--")
        last_segment = segments[-1].lower() if segments else ""
        if last_segment == name_lower:
            return entry

    return None


def _write_claude_md_hint(project_dir: Path, project: "Project", memory_count: int) -> None:
    """Schreibt den Dreamline-Hinweis ins echte Projekt ODER ins Claude-Projects-Dir.

    Priorität: <local_path>/.claude/CLAUDE.md (dort liest Claude Code)
    Fallback: ~/.claude/projects/<encoded>/CLAUDE.md (für Container ohne Zugriff)
    """
    # Echtes Projektverzeichnis bevorzugen (Host-Zugriff)
    real_project = Path(project.local_path) if project.local_path else None
    if real_project and real_project.exists():
        claude_dir = real_project / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        claude_md_path = claude_dir / "CLAUDE.md"
    else:
        # Fallback: ins Claude-Projects-Verzeichnis (Container)
        claude_md_path = project_dir / "CLAUDE.md"

    interval = project.dream_interval_hours or 12
    min_sess = project.min_sessions_for_dream or 3

    hint = f"""{CLAUDE_MD_START}
## Dreamline (automatische Memory-Konsolidierung)

Dieses Projekt nutzt Dreamline. Sessions werden automatisch gesammelt und per KI konsolidiert.

- **Memories**: `memory/` Verzeichnis ({memory_count} Erinnerungen), Index in `memory/MEMORY.md`
- **Dream-Rhythmus**: alle {interval}h bei mindestens {min_sess} neuen Sessions
- **Du musst keine Memories manuell schreiben** — Dreamline übernimmt das automatisch
{CLAUDE_MD_END}"""

    existing = ""
    if claude_md_path.exists():
        existing = claude_md_path.read_text(encoding="utf-8")

    if CLAUDE_MD_START in existing:
        before = existing.split(CLAUDE_MD_START)[0]
        after = existing.split(CLAUDE_MD_END)[1] if CLAUDE_MD_END in existing else ""
        new_content = f"{before}{hint}{after}"
    else:
        new_content = existing.rstrip()
        if new_content:
            new_content += "\n\n"
        new_content += hint + "\n"

    claude_md_path.write_text(new_content, encoding="utf-8")


def _cleanup_orphaned_files(memory_dir: Path, valid_filenames: set[str]) -> int:
    """Löscht .md-Dateien im Memory-Verzeichnis die keiner aktuellen Memory entsprechen."""
    removed = 0
    protected = {ENTRYPOINT_NAME, "CLAUDE.md"}
    for f in memory_dir.glob("*.md"):
        if f.name in protected or f.name in valid_filenames:
            continue
        try:
            f.unlink()
            removed += 1
            logger.info("Verwaiste Memory-Datei gelöscht: %s", f.name)
        except OSError as e:
            logger.warning("Konnte %s nicht löschen: %s", f.name, str(e))
    return removed


async def write_memories_to_project(
    db: AsyncSession,
    project_id: UUID,
) -> dict:
    """
    Schreibt alle Memories eines Projekts als Markdown-Dateien
    ins Claude Code Memory-Verzeichnis.

    Gibt zurück: {"written": N, "path": "...", "errors": [...]}
    """
    # Projekt laden
    stmt = select(Project).where(Project.id == project_id)
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()

    if not project:
        return {"written": 0, "path": None, "errors": ["Projekt nicht gefunden"]}

    # Memory-Verzeichnis bestimmen – zuerst über local_path, dann über Projektname
    project_dir = None
    if project.local_path:
        sanitized = _sanitize_path(project.local_path)
        candidate = CLAUDE_PROJECTS_DIR / sanitized
        if candidate.exists():
            project_dir = candidate

    if not project_dir:
        project_dir = _find_project_dir(project.name)

    if not project_dir:
        return {"written": 0, "path": None, "errors": [
            f"Kein Claude-Projektverzeichnis für '{project.name}' gefunden"
        ]}

    memory_dir = project_dir / "memory"

    # Verzeichnis erstellen
    memory_dir.mkdir(parents=True, exist_ok=True)

    # Alle Memories laden
    mem_stmt = select(Memory).where(Memory.project_id == project_id).order_by(Memory.key)
    mem_result = await db.execute(mem_stmt)
    memories = list(mem_result.scalars().all())

    if not memories:
        return {"written": 0, "path": str(memory_dir), "errors": []}

    written = 0
    errors = []
    index_entries = []

    for mem in memories:
        try:
            # Dateiname generieren
            prefix = TYPE_PREFIXES.get(mem.memory_type, "project")
            filename = _key_to_filename(f"{prefix}_{mem.key}")
            filepath = memory_dir / filename

            # Markdown mit Frontmatter schreiben (gleichen Format wie Claude Code)
            # YAML-Werte in Anführungszeichen setzen um Injection zu verhindern
            safe_name = _yaml_escape(mem.key)
            safe_desc = _yaml_escape(mem.content[:100] + ('...' if len(mem.content) > 100 else ''))
            content = f"""---
name: {safe_name}
description: {safe_desc}
type: {mem.memory_type}
confidence: {mem.confidence}
source_count: {mem.source_count}
---

{mem.content}
"""
            filepath.write_text(content, encoding="utf-8")
            written += 1

            # Index-Eintrag
            short_desc = mem.content[:120].replace("\n", " ")
            index_entries.append(f"- [{mem.key}]({filename}) — {short_desc}")

        except Exception as e:
            errors.append(f"{mem.key}: {str(e)}")
            logger.error("Fehler beim Schreiben von Memory %s: %s", mem.key, str(e))

    # MEMORY.md Index schreiben
    try:
        index_content = "\n".join(index_entries[:MAX_ENTRYPOINT_LINES])
        index_path = memory_dir / ENTRYPOINT_NAME

        # Bestehenden MEMORY.md lesen und Dreamline-Bereich aktualisieren
        existing_content = ""
        dreamline_marker = "<!-- dreamline-managed-start -->"
        dreamline_end = "<!-- dreamline-managed-end -->"

        if index_path.exists():
            existing_content = index_path.read_text(encoding="utf-8")

        if dreamline_marker in existing_content:
            # Nur den Dreamline-Bereich aktualisieren
            before = existing_content.split(dreamline_marker)[0]
            after = existing_content.split(dreamline_end)[1] if dreamline_end in existing_content else ""
            new_content = f"{before}{dreamline_marker}\n{index_content}\n{dreamline_end}{after}"
        else:
            # Dreamline-Bereich am Ende anhängen
            new_content = existing_content.rstrip()
            if new_content:
                new_content += "\n\n"
            new_content += f"{dreamline_marker}\n{index_content}\n{dreamline_end}\n"

        index_path.write_text(new_content, encoding="utf-8")

    except Exception as e:
        errors.append(f"MEMORY.md: {str(e)}")

    # CLAUDE.md Dreamline-Hinweis aktualisieren
    try:
        _write_claude_md_hint(project_dir, project, len(memories))
    except Exception as e:
        errors.append(f"CLAUDE.md: {str(e)}")

    # Verwaiste Memory-Dateien aufräumen (z.B. nach Deduplizierung)
    valid_filenames = {_key_to_filename(f"{TYPE_PREFIXES.get(m.memory_type, 'project')}_{m.key}") for m in memories}
    try:
        removed = _cleanup_orphaned_files(memory_dir, valid_filenames)
        if removed:
            logger.info("Projekt %s: %d verwaiste Memory-Dateien gelöscht", project.name, removed)
    except Exception as e:
        errors.append(f"Cleanup: {str(e)}")

    logger.info(
        "Projekt %s: %d Memories geschrieben nach %s",
        project.name, written, memory_dir,
    )

    # Codex-Support: Memories auch ins Codex-Memory-Verzeichnis + AGENTS.md schreiben
    source_tool = getattr(project, "source_tool", "claude")
    if source_tool in ("codex", "both") and project.local_path:
        codex_errors = _write_memories_for_codex(
            project_local_path=project.local_path,
            memories=memories,
            index_entries=index_entries,
        )
        errors.extend(codex_errors)

    return {
        "written": written,
        "path": str(memory_dir),
        "errors": errors,
    }


def _is_safe_project_path(path: Path) -> bool:
    """
    Prüft ob ein Projektpfad sicher beschrieben werden darf.
    Verhindert Path-Traversal-Angriffe über manipulierte local_path Werte.
    """
    resolved = path.resolve()
    # Nicht in System-Verzeichnisse schreiben
    forbidden = ("/etc", "/usr", "/bin", "/sbin", "/var", "/boot", "/proc", "/sys",
                 "C:/Windows", "C:/Program Files")
    path_str = str(resolved).replace("\\", "/")
    for f in forbidden:
        if path_str.lower().startswith(f.lower()):
            return False
    # Muss ein existierendes Verzeichnis sein
    return resolved.is_dir()


def _write_memories_for_codex(
    project_local_path: str,
    memories: list,
    index_entries: list[str],
) -> list[str]:
    """
    Schreibt Memories ins Codex-Memory-Verzeichnis und aktualisiert AGENTS.md.

    Codex hat kein ~/.codex/projects/ wie Claude. Stattdessen:
    1. Memories nach {projekt}/.codex/memory/ schreiben
    2. AGENTS.md im Projekt-Root mit Memory-Index aktualisieren
    """
    errors = []
    project_root = Path(project_local_path)

    if not _is_safe_project_path(project_root):
        errors.append(f"Codex: Unsicherer Pfad abgelehnt: {project_local_path}")
        return errors

    if not project_root.exists():
        errors.append(f"Codex: Projektverzeichnis existiert nicht: {project_local_path}")
        return errors

    # 1. Memory-Dateien schreiben
    codex_memory_dir = project_root / CODEX_MEMORY_SUBDIR
    try:
        codex_memory_dir.mkdir(parents=True, exist_ok=True)

        for mem in memories:
            try:
                prefix = TYPE_PREFIXES.get(mem.memory_type, "project")
                filename = _key_to_filename(f"{prefix}_{mem.key}")
                filepath = codex_memory_dir / filename

                safe_name = _yaml_escape(mem.key)
                safe_desc = _yaml_escape(mem.content[:100] + ('...' if len(mem.content) > 100 else ''))
                content = f"""---
name: {safe_name}
description: {safe_desc}
type: {mem.memory_type}
confidence: {mem.confidence}
source_count: {mem.source_count}
---

{mem.content}
"""
                filepath.write_text(content, encoding="utf-8")
            except Exception as e:
                errors.append(f"Codex {mem.key}: {str(e)}")

        # MEMORY.md Index im Codex-Memory-Verzeichnis
        codex_index = codex_memory_dir / ENTRYPOINT_NAME
        codex_index.write_text(
            "\n".join(index_entries[:MAX_ENTRYPOINT_LINES]) + "\n",
            encoding="utf-8",
        )

    except Exception as e:
        errors.append(f"Codex Memory-Dir: {str(e)}")

    # 2. AGENTS.md im Projekt-Root aktualisieren
    try:
        agents_md_path = project_root / "AGENTS.md"
        memory_section = (
            f"\n## Dreamline Memories\n\n"
            f"Automatisch konsolidierte Projekt-Memories aus vergangenen Sessions.\n"
            f"Dateien: `.codex/memory/`\n\n"
            + "\n".join(index_entries[:50])
        )

        if agents_md_path.exists():
            existing = agents_md_path.read_text(encoding="utf-8")
            if AGENTS_MD_START in existing:
                # Bestehenden Bereich aktualisieren
                before = existing.split(AGENTS_MD_START)[0]
                after = existing.split(AGENTS_MD_END)[1] if AGENTS_MD_END in existing else ""
                new_content = f"{before}{AGENTS_MD_START}\n{memory_section}\n{AGENTS_MD_END}{after}"
            else:
                # Bereich am Ende anfügen
                new_content = existing.rstrip() + f"\n\n{AGENTS_MD_START}\n{memory_section}\n{AGENTS_MD_END}\n"
        else:
            # Neue AGENTS.md erstellen
            new_content = f"# {project_root.name}\n\n{AGENTS_MD_START}\n{memory_section}\n{AGENTS_MD_END}\n"

        agents_md_path.write_text(new_content, encoding="utf-8")
        logger.info("AGENTS.md aktualisiert: %s", agents_md_path)

    except Exception as e:
        errors.append(f"AGENTS.md: {str(e)}")

    return errors
