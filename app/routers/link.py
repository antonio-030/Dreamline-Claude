"""
Projekt-Verknüpfung – Verbindet ein Dreamline-Projekt mit einem lokalen Ordner.
Installiert automatisch den Claude Code Hook der Sessions an Dreamline sendet.
"""

import json
import logging
import os
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_admin_key
from app.config import settings
from app.database import get_db
from app.models.project import Project

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/link", tags=["link"])

# Hook-Template aus externer Datei laden (nicht mehr inline)
_HOOK_TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "dreamline-sync.cjs.tpl"


def _load_hook_template() -> str:
    """Lädt das Hook-Template aus der Template-Datei."""
    return _HOOK_TEMPLATE_PATH.read_text(encoding="utf-8")


def _escape_js_string(s: str) -> str:
    """Escaped einen String für die sichere Einbettung in JavaScript-Quellcode."""
    return s.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")


class QuickAddRequest(BaseModel):
    """Request zum schnellen Hinzufügen eines Projekts – ein Klick."""
    dir_name: str = Field(..., description="Claude-Projektordner-Name (z.B. C--Users-max--Desktop-MeinProjekt)")
    dream_interval_hours: int = Field(12, ge=1)
    min_sessions_for_dream: int = Field(3, ge=1)
    quick_extract: bool = Field(True)
    source_tool: str = Field("claude", description="Quell-Tool: claude, codex oder both")
    ai_provider: str = Field("claude-abo", description="Dream-Provider: claude-abo, codex-sub, ollama, anthropic, openai")
    ai_model: str = Field("claude-sonnet-4-5-20250514", description="KI-Modell")


class QuickAddCodexRequest(BaseModel):
    """Request zum Hinzufügen eines Codex-Projekts über den lokalen Pfad."""
    local_path: str = Field(..., description="Absoluter Pfad zum lokalen Projekt (aus scan-codex)")
    dream_interval_hours: int = Field(12, ge=1)
    min_sessions_for_dream: int = Field(3, ge=1)
    quick_extract: bool = Field(True)
    source_tool: str = Field("codex", description="Quell-Tool: codex oder both")
    ai_provider: str = Field("claude-abo", description="Dream-Provider: claude-abo, codex-sub, ollama, anthropic, openai")
    ai_model: str = Field("claude-sonnet-4-5-20250514", description="KI-Modell")


class LinkRequest(BaseModel):
    """Request zum Verknüpfen eines Projekts mit einem lokalen Ordner."""
    project_id: UUID
    local_path: str = Field(..., description="Absoluter Pfad zum lokalen Projekt")
    dreamline_url: str = Field("http://localhost:8100", description="Dreamline API URL")


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




def _guess_display_name(dir_name: str) -> str:
    """
    Erzeugt einen lesbaren Anzeigenamen aus dem Claude-Projektnamen.
    Nimmt das letzte Segment als wahrscheinlichsten Projektnamen.
    Beispiel: C--Users-max--Desktop-MeinProjekt → MeinProjekt
    """
    # Letztes Segment nach dem letzten -- ist meist der Projektname
    parts = dir_name.split("--")
    last = parts[-1] if parts else dir_name
    # Falls noch - drin sind, ist der letzte Teil nach - der Name
    # z.B. "Desktop-MeinProjekt" → "MeinProjekt"
    sub = last.split("-")
    return sub[-1] if sub else last


def _decode_claude_dir_name(dir_name: str) -> str:
    """
    Dekodiert einen Claude-Projektnamen zurück in einen Dateipfad.

    Claude Code encodiert Pfade so: ":" entfernt, "/" und "\\" zu "-".
    Das ergibt "--" für Pfadtrenner und "-" für echte Bindestriche.

    Beispiele:
    - C--Users-acea--Desktop-Techlogia → C:/Users/acea/Desktop/Techlogia
    - home--user--projects--myapp → /home/user/projects/myapp
    """
    # "--" durch "/" ersetzen (Pfadtrenner)
    path = dir_name.replace("--", "/")

    if len(path) > 1 and path[1] == "/":
        # Windows: Erster Buchstabe ist Laufwerk (C/Users → C:/Users)
        path = path[0] + ":/" + path[2:]
    elif not path.startswith("/"):
        # Linux: Pfad beginnt nicht mit "/" → fehlendes "/" ergänzen
        # z.B. "home/user/projects" → "/home/user/projects"
        known_linux_roots = ("home", "root", "opt", "var", "tmp", "usr", "srv")
        first_segment = path.split("/")[0].lower()
        if first_segment in known_linux_roots:
            path = "/" + path

    return path


@router.get("/scan", response_model=ScanResponse)
async def scan_local_projects(_: bool = Depends(verify_admin_key)):
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
        display_name = _guess_display_name(dir_name)

        # Session-Dateien zählen
        session_files = list(entry.glob("*.jsonl"))
        session_count = len([f for f in session_files if not f.name.startswith("agent-")])

        # Pfad-Hinweis: Lesbarer machen für die Anzeige
        path_hint = _decode_claude_dir_name(dir_name)

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
async def quick_add_project(
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
    project_dir = home / ".claude" / "projects" / data.dir_name

    if not project_dir.exists():
        raise HTTPException(status_code=404, detail=f"Claude-Projekt '{data.dir_name}' nicht gefunden")

    display_name = _guess_display_name(data.dir_name)

    # Lokalen Pfad aus dem Claude-Ordnernamen rekonstruieren.
    # Claude Code encodiert: ":" entfernt, "/" und "\" zu "-".
    # "--" = Pfadtrenner (war / oder \), einzelnes "-" = normaler Bindestrich.
    # Beispiel: C--Users-acea--Desktop-Techlogia → C:/Users/acea/Desktop/Techlogia
    local_path = _decode_claude_dir_name(data.dir_name)

    # 1. Projekt in DB erstellen
    api_key = f"dl_{_secrets.token_hex(28)}"
    project = Project(
        name=display_name,
        api_key=api_key,
        ai_provider=data.ai_provider,
        ai_model=data.ai_model,
        dream_interval_hours=data.dream_interval_hours,
        min_sessions_for_dream=data.min_sessions_for_dream,
        quick_extract=data.quick_extract,
        local_path=local_path,
        source_tool=data.source_tool,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)

    # 2. Hook-Skript in das Claude-Projektverzeichnis schreiben
    helpers_dir = project_dir / "helpers"
    helpers_dir.mkdir(parents=True, exist_ok=True)

    hook_content = _load_hook_template().format(
        dreamline_url="http://localhost:8100",
        api_key=api_key,
        project_name=_escape_js_string(display_name),
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
                "timeout": 8000,
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

    # 5. Vorhandene lokale Sessions automatisch importieren
    imported_count = 0
    try:
        imported_count = await _import_sessions_for_project(db, project.id, project_dir)
        if imported_count > 0:
            logger.info("Auto-Import: %d Sessions für '%s' importiert", imported_count, display_name)
    except Exception as e:
        logger.warning("Auto-Import fehlgeschlagen für '%s': %s", display_name, str(e))

    await db.commit()

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
async def scan_codex_projects(_: bool = Depends(verify_admin_key)):
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
async def quick_add_codex_project(
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
        imported_count = await _import_codex_sessions_for_project(
            db, project.id, local_path,
        )
    except Exception as e:
        logger.warning("Codex Session-Import fehlgeschlagen: %s", str(e))

    await db.commit()

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


async def _import_codex_sessions_for_project(
    db: AsyncSession,
    project_id,
    local_path: str,
) -> int:
    """Importiert vorhandene Codex-Sessions für ein Projekt basierend auf dem cwd."""

    from app.models.session import Session as DreamlineSession
    from app.services.session_parser import parse_session_file

    codex_sessions_dir = Path.home() / ".codex" / "sessions"
    if not codex_sessions_dir.exists():
        return 0

    normalized_path = local_path.replace("\\", "/").rstrip("/").lower()
    imported = 0

    for jsonl_file in sorted(codex_sessions_dir.rglob("*.jsonl")):
        try:
            parsed = parse_session_file(jsonl_file, source_tool="codex")
            if not parsed or not parsed.cwd:
                continue

            # Prüfe ob cwd zum Projekt passt
            if parsed.cwd.replace("\\", "/").rstrip("/").lower() != normalized_path:
                continue

            session = DreamlineSession(
                project_id=project_id,
                messages_json=json.dumps(parsed.messages, ensure_ascii=False),
                outcome="neutral",
                metadata_json=json.dumps({
                    "source": "codex-import",
                    "source_file": parsed.source_file,
                    "session_id": parsed.session_id,
                    "source_tool": "codex",
                    "cwd": parsed.cwd,
                }, ensure_ascii=False),
            )
            db.add(session)
            imported += 1
        except Exception:
            continue

    if imported > 0:
        await db.flush()
    return imported


@router.post("", response_model=LinkResponse)
async def link_project(
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
    hook_content = _load_hook_template().format(
        dreamline_url=data.dreamline_url,
        api_key=project.api_key,
        project_name=_escape_js_string(project.name),
    )

    # Versuche Hook zu installieren (funktioniert wenn projects-Volume gemountet)
    hook_installed = False
    try:
        hook_installed = _install_hook(
            local_path=local_path,
            api_key=project.api_key,
            project_name=_escape_js_string(project.name),
            dreamline_url=data.dreamline_url,
        )
    except Exception as e:
        logger.warning("Auto-Hook-Installation nicht möglich: %s", str(e))

    return LinkResponse(
        success=True,
        project_name=_escape_js_string(project.name),
        local_path=str(local_path),
        hook_installed=hook_installed,
        message="Verknüpfung gespeichert!" + (
            " Hook automatisch installiert." if hook_installed
            else " Hook muss manuell installiert werden – nutze den /api/v1/link/hook Endpoint."
        ),
    )


@router.get("/hook/{project_id}")
async def get_hook_script(
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

    hook_content = _load_hook_template().format(
        dreamline_url="http://localhost:8100",
        api_key=project.api_key,
        project_name=_escape_js_string(project.name),
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
                "timeout": 8000,
            },
        },
    }


async def _import_sessions_for_project(
    db: AsyncSession,
    project_id: UUID,
    project_dir: Path,
) -> int:
    """
    Interne Import-Funktion: Liest .jsonl-Dateien und erstellt Dreamline-Sessions.
    Nutzt den Unified Session-Parser (session_parser.py) für Claude- und Codex-Formate.
    Gibt die Anzahl importierter Sessions zurück.
    """
    from app.models.session import Session as DreamlineSession
    from app.services.session_parser import parse_session_file

    jsonl_files = sorted(
        [f for f in project_dir.glob("*.jsonl") if not f.name.startswith("agent-")],
        key=lambda f: f.stat().st_mtime,
    )
    if not jsonl_files:
        return 0

    # Bereits importierte Dateien prüfen
    existing_stmt = select(DreamlineSession.metadata_json).where(
        DreamlineSession.project_id == project_id
    )
    existing_result = await db.execute(existing_stmt)
    existing_files = set()
    for row in existing_result.scalars().all():
        if row:
            try:
                meta = json.loads(row)
                src = meta.get("source_file")
                if src:
                    existing_files.add(src)
            except (json.JSONDecodeError, TypeError):
                pass

    imported = 0
    for jsonl_file in jsonl_files:
        if jsonl_file.name in existing_files:
            continue

        try:
            parsed = parse_session_file(jsonl_file)
            if not parsed:
                continue

            session = DreamlineSession(
                project_id=project_id,
                messages_json=json.dumps(parsed.messages[-10:], ensure_ascii=False),
                outcome="neutral",
                metadata_json=json.dumps({
                    "source": "jsonl-import",
                    "source_file": jsonl_file.name,
                    "session_id": parsed.session_id,
                    "source_tool": parsed.source_tool,
                }, ensure_ascii=False),
            )
            db.add(session)
            imported += 1
        except Exception:
            continue

    if imported > 0:
        await db.flush()
    return imported


@router.post("/import-sessions/{project_id}")
async def import_local_sessions(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """
    Importiert vorhandene Claude-Session-Transkripte (.jsonl) in die Dreamline-DB.
    Nutzt die Shared-Funktion _import_sessions_for_project().
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
    imported = await _import_sessions_for_project(db, project_id, project_dir)
    skipped = total_files - imported

    return {
        "imported": imported,
        "skipped": skipped,
        "errors": 0,
        "total_files": total_files,
        "project_dir": str(project_dir),
    }


@router.post("/sync/{project_id}")
async def sync_memories_to_project(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_admin_key),
):
    """Schreibt alle Memories als Markdown-Dateien ins lokale Projekt-Memory-Verzeichnis."""
    from app.services.memory_writer import write_memories_to_project
    result = await write_memories_to_project(db, project_id)
    return result


def _install_hook(local_path: Path, api_key: str, project_name: str, dreamline_url: str) -> bool:
    """
    Installiert den Dreamline Stop-Hook in einem Claude Code Projekt.
    Erstellt die Hook-Datei und registriert sie in settings.json.
    """
    try:
        claude_dir = local_path / ".claude"
        helpers_dir = claude_dir / "helpers"
        settings_path = claude_dir / "settings.json"

        # Helpers-Verzeichnis erstellen falls nötig
        helpers_dir.mkdir(parents=True, exist_ok=True)

        # Hook-Datei schreiben
        hook_content = _load_hook_template().format(
            dreamline_url=dreamline_url,
            api_key=api_key,
            project_name=project_name,
        )
        hook_path = helpers_dir / "dreamline-sync.cjs"
        hook_path.write_text(hook_content)
        logger.info("Hook-Datei geschrieben: %s", hook_path)

        # settings.json aktualisieren
        if settings_path.exists():
            config = json.loads(settings_path.read_text())
        else:
            config = {}

        hooks = config.setdefault("hooks", {})
        stop_hooks = hooks.setdefault("Stop", [{"hooks": []}])

        # Prüfe ob Dreamline-Hook schon existiert
        hook_cmd = "node %CLAUDE_PROJECT_DIR%/.claude/helpers/dreamline-sync.cjs"
        existing_hooks = stop_hooks[0].get("hooks", [])
        already_exists = any(h.get("command") == hook_cmd for h in existing_hooks)

        if not already_exists:
            existing_hooks.append({
                "type": "command",
                "command": hook_cmd,
                "timeout": 8000,
            })
            stop_hooks[0]["hooks"] = existing_hooks
            settings_path.write_text(json.dumps(config, indent=2, ensure_ascii=False))
            logger.info("Hook in settings.json registriert: %s", settings_path)

        return True

    except Exception as e:
        logger.error("Hook-Installation fehlgeschlagen: %s", str(e))
        return False
