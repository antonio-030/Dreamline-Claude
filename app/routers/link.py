"""
Projekt-Verknüpfung – Verbindet ein Dreamline-Projekt mit einem lokalen Ordner.
Installiert automatisch den Claude Code Hook der Sessions an Dreamline sendet.
"""

import json
import logging
import os
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_admin_key
from app.config import settings
from app.database import get_db
from app.models.project import Project
from app.services.hook_installer import install_hook, load_hook_template
from app.services.session_importer import import_claude_sessions, import_codex_sessions
from app.services.utils import decode_claude_dir_name, escape_js_string, guess_display_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/link", tags=["link"])
limiter = Limiter(key_func=get_remote_address)


VALID_PROVIDERS = ("claude-abo", "codex-sub", "ollama", "anthropic", "openai")
VALID_SOURCE_TOOLS = ("claude", "codex", "both")


class QuickAddRequest(BaseModel):
    """Request zum schnellen Hinzufügen eines Projekts – ein Klick."""
    dir_name: str = Field(..., max_length=500, pattern=r"^[a-zA-Z0-9._\- ]+$", description="Claude-Projektordner-Name")
    dream_interval_hours: int = Field(12, ge=1, le=720)
    min_sessions_for_dream: int = Field(3, ge=1, le=1000)
    quick_extract: bool = Field(True)
    source_tool: str = Field("claude", pattern=r"^(claude|codex|both)$")
    ai_provider: str = Field("claude-abo", pattern=r"^(claude-abo|codex-sub|ollama|anthropic|openai)$")
    ai_model: str = Field("claude-sonnet-4-5-20250514", max_length=100)
    dream_provider: str | None = Field(None, pattern=r"^(claude-abo|codex-sub|ollama|anthropic|openai)$")
    dream_model: str | None = Field(None, max_length=100)


class QuickAddCodexRequest(BaseModel):
    """Request zum Hinzufügen eines Codex-Projekts über den lokalen Pfad."""
    local_path: str = Field(..., max_length=1000, description="Absoluter Pfad zum lokalen Projekt")
    dream_interval_hours: int = Field(12, ge=1, le=720)
    min_sessions_for_dream: int = Field(3, ge=1, le=1000)
    quick_extract: bool = Field(True)
    source_tool: str = Field("codex", pattern=r"^(codex|both)$")
    ai_provider: str = Field("claude-abo", pattern=r"^(claude-abo|codex-sub|ollama|anthropic|openai)$")
    ai_model: str = Field("claude-sonnet-4-5-20250514", max_length=100)
    dream_provider: str | None = Field(None, pattern=r"^(claude-abo|codex-sub|ollama|anthropic|openai)$")
    dream_model: str | None = Field(None, max_length=100)


class LinkRequest(BaseModel):
    """Request zum Verknüpfen eines Projekts mit einem lokalen Ordner."""
    project_id: UUID
    local_path: str = Field(..., max_length=1000, description="Absoluter Pfad zum lokalen Projekt")
    dreamline_url: str = Field(default_factory=lambda: settings.dreamline_base_url, max_length=200, pattern=r"^https?://")


class LinkResponse(BaseModel):
    """Antwort nach erfolgreicher Verknüpfung."""
    success: bool
    project_name: str
    local_path: str
    hook_installed: bool
    message: str


class ScanResponse(BaseModel):
    """Lokale Projekte die Claude Code kennt."""
    projects: list[dict]




@router.get("/scan", response_model=ScanResponse)
@limiter.limit("10/minute")
async def scan_local_projects(request: Request, _: bool = Depends(verify_admin_key)):
    """
    Scannt die gemounteten Claude Code Projekte.
    Liest ~/.claude/projects/ (vom Host durchgereicht per Volume-Mount).
    Gibt den Claude-Ordnernamen, einen geschätzten Anzeigenamen und
    die Session-Anzahl zurück. Der echte lokale Pfad muss vom User
    bestätigt werden (da die Kodierung verlustbehaftet ist).
    """
    home = Path.home()
    projects_dir = home / ".claude" / "projects"

    if not projects_dir.exists():
        return ScanResponse(projects=[])

    found = []
    for entry in sorted(projects_dir.iterdir()):
        if not entry.is_dir():
            continue

        dir_name = entry.name
        display_name = guess_display_name(dir_name)

        # Session-Dateien zählen
        session_files = list(entry.glob("*.jsonl"))
        session_count = len([f for f in session_files if not f.name.startswith("agent-")])

        # Pfad-Hinweis: Lesbarer machen für die Anzeige
        path_hint = decode_claude_dir_name(dir_name)

        found.append({
            "dir_name": dir_name,
            "display_name": display_name,
            "path_hint": path_hint,
            "session_count": session_count,
            "last_modified": max((f.stat().st_mtime for f in session_files), default=0),
        })

    # Nach letzter Aktivität sortieren
    found.sort(key=lambda x: x["last_modified"], reverse=True)

    return ScanResponse(projects=found)


@router.post("/quick-add")
@limiter.limit("10/minute")
async def quick_add_project(
    request: Request,
    data: QuickAddRequest,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """
    Ein-Klick Projekt-Einrichtung: Erstellt Projekt, installiert Hook,
    alles über das gemountete ~/.claude/ Volume – kein lokaler Pfad nötig.
    """
    import secrets as _secrets

    home = Path.home()
    projects_root = (home / ".claude" / "projects").resolve()
    project_dir = home / ".claude" / "projects" / data.dir_name

    # Path-Traversal-Schutz: resolved Pfad muss unter projects_root bleiben
    if not str(project_dir.resolve()).startswith(str(projects_root) + "/"):
        raise HTTPException(status_code=400, detail="Ungültiger Projektname")

    if not project_dir.exists():
        raise HTTPException(status_code=404, detail=f"Claude-Projekt '{data.dir_name}' nicht gefunden")

    display_name = guess_display_name(data.dir_name)

    # Lokalen Pfad aus dem Claude-Ordnernamen rekonstruieren.
    # Claude Code encodiert: ":" entfernt, "/" und "\" zu "-".
    # "--" = Pfadtrenner (war / oder \), einzelnes "-" = normaler Bindestrich.
    # Beispiel: C--Users-acea--Desktop-Techlogia → C:/Users/acea/Desktop/Techlogia
    local_path = decode_claude_dir_name(data.dir_name)

    # 1. Projekt in DB erstellen
    api_key = f"dl_{_secrets.token_hex(28)}"
    project = Project(
        name=display_name,
        api_key=api_key,
        ai_provider=data.ai_provider,
        ai_model=data.ai_model,
        dream_provider=data.dream_provider,
        dream_model=data.dream_model,
        dream_interval_hours=data.dream_interval_hours,
        min_sessions_for_dream=data.min_sessions_for_dream,
        quick_extract=data.quick_extract,
        local_path=local_path,
        source_tool=data.source_tool,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)

    # 2. Hook-Skript ins echte Projektverzeichnis schreiben
    #    %CLAUDE_PROJECT_DIR% zeigt auf local_path, nicht auf ~/.claude/projects/
    real_project_dir = Path(local_path) if local_path else None
    if real_project_dir and real_project_dir.exists():
        helpers_dir = real_project_dir / ".claude" / "helpers"
    else:
        # Fallback: ins Claude-Projektverzeichnis (weniger ideal)
        helpers_dir = project_dir / "helpers"
        logger.warning("Echtes Projektverzeichnis '%s' nicht erreichbar, schreibe Hook in %s", local_path, helpers_dir)
    helpers_dir.mkdir(parents=True, exist_ok=True)

    hook_content = load_hook_template().format(
        dreamline_url=settings.dreamline_base_url,
        api_key=api_key,
        project_name=escape_js_string(display_name),
    )
    hook_path = helpers_dir / "dreamline-sync.cjs"
    hook_path.write_text(hook_content)
    logger.info("Hook-Skript geschrieben: %s", hook_path)

    # 3. settings.json im Claude-Projektverzeichnis anlegen/aktualisieren
    settings_path = project_dir / "settings.json"
    if settings_path.exists():
        try:
            config = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            config = {}
    else:
        config = {}

    hooks = config.setdefault("hooks", {})

    # Stop-Hook hinzufügen (plattform-unabhängig mit node)
    hook_cmd = "node %CLAUDE_PROJECT_DIR%/.claude/helpers/dreamline-sync.cjs"
    stop_hooks = hooks.setdefault("Stop", [])

    # Prüfe ob Hook schon existiert
    already_exists = False
    for entry in stop_hooks:
        for h in entry.get("hooks", []):
            if "dreamline-sync" in h.get("command", ""):
                already_exists = True
                break

    if not already_exists:
        stop_hooks.append({
            "hooks": [{
                "type": "command",
                "command": hook_cmd,
                "timeout": settings.hook_timeout_ms,
            }]
        })
        settings_path.write_text(json.dumps(config, indent=2, ensure_ascii=False))
        logger.info("Hook in settings.json registriert: %s", settings_path)

    # 4. Vorhandene Memory-Dateien in die DB synchronisieren
    # (Damit der Dream die bestehenden Memories kennt und keine Duplikate erstellt)
    memories_synced = 0
    memory_dir = project_dir / "memory"
    if memory_dir.exists():
        try:
            from app.services.dreamer import _sync_files_to_db
            from app.models.memory import Memory
            created, updated, _ = await _sync_files_to_db(db, project.id, memory_dir, [])
            memories_synced = created + updated
            if memories_synced > 0:
                logger.info("Memory-Sync: %d Memories aus %s in DB importiert", memories_synced, memory_dir)
        except Exception as e:
            logger.warning("Memory-Sync fehlgeschlagen: %s", str(e))

    # 5. CLAUDE.md Dreamline-Hinweis schreiben
    try:
        from app.services.memory_writer import _write_claude_md_hint
        _write_claude_md_hint(project_dir, project, memories_synced)
        logger.info("CLAUDE.md Hint geschrieben: %s", project_dir / "CLAUDE.md")
    except Exception as e:
        logger.warning("CLAUDE.md Hint fehlgeschlagen: %s", str(e))

    # 6. Vorhandene lokale Sessions automatisch importieren
    imported_count = 0
    try:
        imported_count = await import_claude_sessions(db, project.id, project_dir)
        if imported_count > 0:
            logger.info("Auto-Import: %d Sessions für '%s' importiert", imported_count, display_name)
    except Exception as e:
        logger.warning("Auto-Import fehlgeschlagen für '%s': %s", display_name, str(e))

    return {
        "success": True,
        "project_id": str(project.id),
        "project_name": display_name,
        "api_key": api_key,
        "hook_installed": True,
        "sessions_imported": imported_count,
        "memories_synced": memories_synced,
        "message": f"Projekt '{display_name}' erstellt, Hook installiert, {imported_count} Sessions + {memories_synced} Memories importiert.",
    }


@router.get("/scan-codex")
@limiter.limit("10/minute")
async def scan_codex_projects(request: Request, _: bool = Depends(verify_admin_key)):
    """
    Scannt ~/.codex/sessions/ und gruppiert Sessions nach Arbeitsverzeichnis (cwd).
    Gibt eine Liste von Projekten zurück die mit Codex bearbeitet wurden.
    """
    codex_sessions_dir = Path.home() / ".codex" / "sessions"
    if not codex_sessions_dir.exists():
        return {"projects": []}



    # Alle JSONL-Dateien finden und cwd extrahieren
    cwd_sessions: dict[str, list[dict]] = {}

    for jsonl_file in sorted(codex_sessions_dir.rglob("*.jsonl")):
        try:
            # Nur erste Zeile lesen (effizient, liest nicht die ganze Datei)
            with open(jsonl_file, encoding="utf-8", errors="replace") as fh:
                first_line = fh.readline()

            if not first_line.strip():
                continue

            entry = json.loads(first_line)

            if entry.get("type") != "session_meta":
                continue

            payload = entry.get("payload", {})
            cwd = payload.get("cwd", "")
            if not cwd:
                continue

            if cwd not in cwd_sessions:
                cwd_sessions[cwd] = []

            cwd_sessions[cwd].append({
                "file": jsonl_file.name,
                "timestamp": payload.get("timestamp", ""),
                "mtime": jsonl_file.stat().st_mtime,
            })

        except (json.JSONDecodeError, OSError, IndexError):
            continue

    # Ergebnis aufbereiten
    found = []
    for cwd, sessions in cwd_sessions.items():
        # Anzeigename: Letztes Verzeichnis-Segment
        display_name = Path(cwd).name or cwd
        found.append({
            "cwd": cwd,
            "display_name": display_name,
            "session_count": len(sessions),
            "last_modified": max(s["mtime"] for s in sessions),
        })

    found.sort(key=lambda x: x["last_modified"], reverse=True)
    return {"projects": found}


@router.post("/quick-add-codex")
@limiter.limit("10/minute")
async def quick_add_codex_project(
    request: Request,
    data: QuickAddCodexRequest,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """
    Ein-Klick Projekt-Einrichtung für Codex-Projekte.
    Erstellt Projekt mit source_tool=codex und importiert vorhandene Sessions.
    Kein Hook nötig – der Codex-Watcher übernimmt die Session-Erfassung.
    """
    import secrets as _secrets

    local_path = data.local_path
    display_name = Path(local_path).name or "Codex-Projekt"

    # Prüfe ob Codex-Sessions für diesen Pfad existieren
    codex_sessions_dir = Path.home() / ".codex" / "sessions"
    session_count = 0
    if codex_sessions_dir.exists():
    
        for jsonl_file in codex_sessions_dir.rglob("*.jsonl"):
            try:
                with open(jsonl_file, encoding="utf-8", errors="replace") as fh:
                    first_line = fh.readline()
                if not first_line.strip():
                    continue
                entry = json.loads(first_line)
                if entry.get("type") == "session_meta":
                    cwd = entry.get("payload", {}).get("cwd", "")
                    if cwd.replace("\\", "/").rstrip("/").lower() == local_path.replace("\\", "/").rstrip("/").lower():
                        session_count += 1
            except (json.JSONDecodeError, OSError):
                continue

    # 1. Projekt in DB erstellen
    api_key = f"dl_{_secrets.token_hex(28)}"
    project = Project(
        name=display_name,
        api_key=api_key,
        ai_provider=data.ai_provider,
        ai_model=data.ai_model,
        dream_provider=data.dream_provider,
        dream_model=data.dream_model,
        dream_interval_hours=data.dream_interval_hours,
        min_sessions_for_dream=data.min_sessions_for_dream,
        quick_extract=data.quick_extract,
        local_path=local_path,
        source_tool=data.source_tool,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)

    # 2. Vorhandene Codex-Sessions importieren
    imported_count = 0
    try:
        imported_count = await import_codex_sessions(
            db, project.id, local_path,
        )
    except Exception as e:
        logger.warning("Codex Session-Import fehlgeschlagen: %s", str(e))

    return {
        "success": True,
        "project_id": str(project.id),
        "project_name": display_name,
        "api_key": api_key,
        "source_tool": data.source_tool,
        "hook_installed": False,  # Kein Hook nötig – Watcher übernimmt
        "sessions_found": session_count,
        "sessions_imported": imported_count,
        "message": (
            f"Codex-Projekt '{display_name}' erstellt, "
            f"{imported_count} Sessions importiert. "
            f"Codex-Watcher muss in .env aktiviert werden (CODEX_WATCHER_ENABLED=true)."
        ),
    }


@router.post("", response_model=LinkResponse)
@limiter.limit("10/minute")
async def link_project(
    request: Request,
    data: LinkRequest,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """
    Verknüpft ein Dreamline-Projekt mit einem lokalen Ordner.
    Installiert automatisch den Claude Code Stop-Hook.
    """
    # Projekt laden
    stmt = select(Project).where(Project.id == data.project_id)
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")

    local_path = Path(data.local_path)

    # Projektpfad speichern
    project.local_path = str(local_path)
    await db.flush()

    # Hook-Skript generieren
    hook_content = load_hook_template().format(
        dreamline_url=data.dreamline_url,
        api_key=project.api_key,
        project_name=escape_js_string(project.name),
    )

    # Versuche Hook zu installieren (funktioniert wenn projects-Volume gemountet)
    hook_installed = False
    try:
        hook_installed = install_hook(
            local_path=local_path,
            api_key=project.api_key,
            project_name=escape_js_string(project.name),
            dreamline_url=data.dreamline_url,
        )
    except Exception as e:
        logger.warning("Auto-Hook-Installation nicht möglich: %s", str(e))

    return LinkResponse(
        success=True,
        project_name=escape_js_string(project.name),
        local_path=str(local_path),
        hook_installed=hook_installed,
        message="Verknüpfung gespeichert!" + (
            " Hook automatisch installiert." if hook_installed
            else " Hook muss manuell installiert werden – nutze den /api/v1/link/hook Endpoint."
        ),
    )


@router.get("/hook/{project_id}")
@limiter.limit("30/minute")
async def get_hook_script(
    request: Request,
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """Gibt das Hook-Skript für ein Projekt zurück (zum manuellen Installieren)."""
    stmt = select(Project).where(Project.id == project_id)
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")

    hook_content = load_hook_template().format(
        dreamline_url=settings.dreamline_base_url,
        api_key=project.api_key,
        project_name=escape_js_string(project.name),
    )

    return {
        "project": project.name,
        "hook_script": hook_content,
        "install_instructions": {
            "1": f"Speichere das Skript als: {project.local_path or '<dein-projekt>'}/.claude/helpers/dreamline-sync.cjs",
            "2": "Füge in .claude/settings.json unter hooks.Stop hinzu:",
            "hook_config": {
                "type": "command",
                "command": "node %CLAUDE_PROJECT_DIR%/.claude/helpers/dreamline-sync.cjs",
                "timeout": settings.hook_timeout_ms,
            },
        },
    }


@router.post("/import-sessions/{project_id}")
@limiter.limit("5/minute")
async def import_local_sessions(
    request: Request,
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """
    Importiert vorhandene Claude-Session-Transkripte (.jsonl) in die Dreamline-DB.
    Nutzt die Shared-Funktion import_claude_sessions().
    """
    from app.models.project import Project

    # Projekt laden
    stmt = select(Project).where(Project.id == project_id)
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")

    # Claude-Projektverzeichnis finden
    claude_projects_dir = Path.home() / ".claude" / "projects"
    project_dir = None

    # Über Projektname suchen
    name_lower = project.name.lower()
    for entry in claude_projects_dir.iterdir():
        if entry.is_dir() and name_lower in entry.name.lower():
            project_dir = entry
            break

    if not project_dir:
        raise HTTPException(
            status_code=404,
            detail=f"Kein Claude-Projektverzeichnis für '{project.name}' gefunden"
        )

    # Anzahl Dateien vor Import zählen
    total_files = len([f for f in project_dir.glob("*.jsonl") if not f.name.startswith("agent-")])

    # Import ausführen
    imported = await import_claude_sessions(db, project_id, project_dir)
    skipped = total_files - imported

    return {
        "imported": imported,
        "skipped": skipped,
        "errors": 0,
        "total_files": total_files,
        "project_dir": str(project_dir),
    }


@router.post("/sync/{project_id}")
@limiter.limit("5/minute")
async def sync_memories_to_project(
    request: Request,
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """Schreibt alle Memories als Markdown-Dateien ins lokale Projekt-Memory-Verzeichnis."""
    from app.services.memory_writer import write_memories_to_project
    result = await write_memories_to_project(db, project_id)
    return result


