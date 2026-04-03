"""
Projekt-Verknüpfung – Verbindet ein Dreamline-Projekt mit einem lokalen Ordner.
Installiert automatisch den Claude Code Hook der Sessions an Dreamline sendet.
"""

import json
import logging
import os
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.project import Project

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/link", tags=["link"])

# ─── Hook-Template ───────────────────────────────────────────────────

HOOK_TEMPLATE = '''#!/usr/bin/env node
/**
 * Dreamline Auto-Sync Hook v2 (automatisch installiert)
 * Sendet Claude Code Sessions + Projektkontext an Dreamline.
 */
const http = require('http');
const fs = require('fs');
const path = require('path');

const DREAMLINE_URL = '{dreamline_url}';
const DREAMLINE_API_KEY = '{api_key}';
const PROJECT_NAME = '{project_name}';

const sessionId = process.env.CLAUDE_SESSION_ID || 'unknown';
const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();
const trackerPath = path.join(projectDir, '.claude', '.dreamline-synced');

function loadSynced() {{
  try {{ return new Set(fs.existsSync(trackerPath) ? fs.readFileSync(trackerPath,'utf-8').split('\\n').filter(Boolean) : []); }} catch {{ return new Set(); }}
}}

function readStdin() {{
  return new Promise(r => {{
    let d=''; process.stdin.setEncoding('utf-8');
    process.stdin.on('data', c => d+=c);
    process.stdin.on('end', () => r(d));
    setTimeout(() => r(d), 2000);
  }});
}}

// Projektkontext sammeln: CLAUDE.md + Dateistruktur
function gatherProjectContext() {{
  const ctx = [];

  // CLAUDE.md lesen (Projektregeln)
  const claudeMdPaths = [
    path.join(projectDir, 'CLAUDE.md'),
    path.join(projectDir, '.claude', 'CLAUDE.md'),
  ];
  for (const p of claudeMdPaths) {{
    try {{
      if (fs.existsSync(p)) {{
        const content = fs.readFileSync(p, 'utf-8');
        ctx.push('[CLAUDE.md]\\n' + content.substring(0, 3000));
        break;
      }}
    }} catch {{}}
  }}

  // Dateistruktur (nur Top-Level + wichtige Unterordner)
  try {{
    const ignore = new Set(['.git', 'node_modules', '.next', '__pycache__', '.venv', 'venv', '.claude-flow', '.swarm', '.hive-mind', 'dist', 'build']);
    const tree = [];

    function walk(dir, prefix, depth) {{
      if (depth > 2) return;
      try {{
        const entries = fs.readdirSync(dir, {{ withFileTypes: true }});
        for (const e of entries) {{
          if (ignore.has(e.name) || e.name.startsWith('.')) continue;
          const rel = prefix + e.name;
          if (e.isDirectory()) {{
            tree.push(rel + '/');
            if (depth < 2) walk(path.join(dir, e.name), rel + '/', depth + 1);
          }} else {{
            tree.push(rel);
          }}
          if (tree.length > 100) return;
        }}
      }} catch {{}}
    }}
    walk(projectDir, '', 0);
    if (tree.length) ctx.push('[Dateistruktur]\\n' + tree.join('\\n'));
  }} catch {{}}

  // package.json oder requirements.txt (Abhängigkeiten)
  for (const depFile of ['package.json', 'requirements.txt', 'pyproject.toml']) {{
    try {{
      const p = path.join(projectDir, depFile);
      if (fs.existsSync(p)) {{
        const content = fs.readFileSync(p, 'utf-8');
        ctx.push('[' + depFile + ']\\n' + content.substring(0, 1500));
      }}
    }} catch {{}}
  }}

  return ctx.join('\\n\\n---\\n\\n');
}}

async function send(content, context) {{
  const messages = [
    {{ role: 'user', content: 'Claude Code Session (' + PROJECT_NAME + '): ' + sessionId }},
    {{ role: 'assistant', content: content.substring(content.length > 5000 ? content.length-5000 : 0) }}
  ];

  const body = JSON.stringify({{
    messages: messages,
    outcome: 'neutral',
    metadata: {{
      project: PROJECT_NAME,
      session_id: sessionId,
      source: 'dreamline-hook',
      project_dir: projectDir,
      project_context: context ? context.substring(0, 8000) : null,
    }}
  }});
  return new Promise(r => {{
    const url = new URL('/api/v1/sessions', DREAMLINE_URL);
    const req = http.request({{
      hostname: url.hostname, port: url.port, path: url.pathname,
      method: 'POST', timeout: 8000,
      headers: {{ 'Content-Type':'application/json', 'Authorization':'Bearer '+DREAMLINE_API_KEY, 'Content-Length':Buffer.byteLength(body) }}
    }}, res => {{ let d=''; res.on('data',c=>d+=c); res.on('end',()=>r(res.statusCode)); }});
    req.on('error', () => r(0));
    req.on('timeout', () => {{ req.destroy(); r(0); }});
    req.write(body); req.end();
  }});
}}

async function main() {{
  if (loadSynced().has(sessionId)) process.exit(0);

  // Projektkontext sammeln
  const context = gatherProjectContext();

  // Session-Inhalt lesen
  let content = await readStdin();
  if (!content || content.length < 50) {{
    try {{
      const home = process.env.HOME || process.env.USERPROFILE || '';
      const projKey = projectDir.replace(/[:\\\\/]/g, '-').replace(/^-+/, '');
      const dirs = [
        path.join(home, '.claude', 'projects', projKey),
        path.join(home, '.claude', 'projects'),
      ];
      for (const dir of dirs) {{
        if (!fs.existsSync(dir)) continue;
        const files = fs.readdirSync(dir).filter(f => f.endsWith('.jsonl') && !f.startsWith('agent-'))
          .map(f => ({{ name: f, mt: fs.statSync(path.join(dir,f)).mtimeMs }})).sort((a,b) => b.mt-a.mt);
        if (files.length > 0) {{ content = fs.readFileSync(path.join(dir, files[0].name),'utf-8').split('\\n').slice(-50).join('\\n'); break; }}
      }}
    }} catch {{}}
  }}
  if (!content || content.length < 50) process.exit(0);

  const status = await send(content, context);
  if (status === 200 || status === 201) {{
    try {{ fs.appendFileSync(trackerPath, sessionId+'\\n'); }} catch {{}}
  }}
}}
main().catch(() => process.exit(0));
'''


class QuickAddRequest(BaseModel):
    """Request zum schnellen Hinzufügen eines Projekts – ein Klick."""
    dir_name: str = Field(..., description="Claude-Projektordner-Name (z.B. C--Users-max--Desktop-MeinProjekt)")
    dream_interval_hours: int = Field(12, ge=1)
    min_sessions_for_dream: int = Field(3, ge=1)
    quick_extract: bool = Field(True)


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


def _verify_admin_key(x_admin_key: str = Header(...)):
    if x_admin_key != settings.dreamline_secret_key:
        raise HTTPException(status_code=403, detail="Ungültiger Admin-Key")
    return True


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


@router.get("/scan", response_model=ScanResponse)
async def scan_local_projects(_: bool = Depends(_verify_admin_key)):
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
        # C--Users-max--Desktop-MeinProjekt → C:/.../MeinProjekt (Hinweis)
        path_hint = dir_name.replace("--", "/").replace("-", "/")
        if len(path_hint) > 1 and path_hint[1] == "/":
            path_hint = path_hint[0] + ":/" + path_hint[2:]

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
    _: bool = Depends(_verify_admin_key),
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

    # Lokalen Pfad aus dem Claude-Ordnernamen rekonstruieren
    # z.B. "C--Users-acea--Desktop-liedatlas" → "C:/Users/acea-/Desktop/liedatlas"
    local_path = data.dir_name.replace("--", "/").replace("-", "/")
    if len(local_path) > 1 and local_path[1] == "/":
        local_path = local_path[0] + ":/" + local_path[2:]

    # 1. Projekt in DB erstellen
    api_key = f"dl_{_secrets.token_hex(28)}"
    project = Project(
        name=display_name,
        api_key=api_key,
        ai_provider="claude-abo",
        ai_model="claude-sonnet-4-5-20250514",
        dream_interval_hours=data.dream_interval_hours,
        min_sessions_for_dream=data.min_sessions_for_dream,
        quick_extract=data.quick_extract,
        local_path=local_path,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)

    # 2. Hook-Skript in das Claude-Projektverzeichnis schreiben
    helpers_dir = project_dir / "helpers"
    helpers_dir.mkdir(parents=True, exist_ok=True)

    hook_content = HOOK_TEMPLATE.format(
        dreamline_url="http://localhost:8100",
        api_key=api_key,
        project_name=display_name,
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

    # 4. Vorhandene lokale Sessions automatisch importieren
    imported_count = 0
    try:
        # import_local_sessions intern aufrufen (gleiche Logik)
        from app.routers.link import _import_sessions_for_project
        imported_count = await _import_sessions_for_project(db, project.id, project_dir)
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
        "message": f"Projekt '{display_name}' erstellt, Hook installiert, {imported_count} Sessions importiert.",
    }


@router.post("", response_model=LinkResponse)
async def link_project(
    data: LinkRequest,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(_verify_admin_key),
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
    hook_content = HOOK_TEMPLATE.format(
        dreamline_url=data.dreamline_url,
        api_key=project.api_key,
        project_name=project.name,
    )

    # Versuche Hook zu installieren (funktioniert wenn projects-Volume gemountet)
    hook_installed = False
    try:
        hook_installed = _install_hook(
            local_path=local_path,
            api_key=project.api_key,
            project_name=project.name,
            dreamline_url=data.dreamline_url,
        )
    except Exception as e:
        logger.warning("Auto-Hook-Installation nicht möglich: %s", str(e))

    return LinkResponse(
        success=True,
        project_name=project.name,
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
    _: bool = Depends(_verify_admin_key),
):
    """Gibt das Hook-Skript für ein Projekt zurück (zum manuellen Installieren)."""
    stmt = select(Project).where(Project.id == project_id)
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")

    hook_content = HOOK_TEMPLATE.format(
        dreamline_url="http://localhost:8100",
        api_key=project.api_key,
        project_name=project.name,
    )

    return {
        "project": project.name,
        "hook_script": hook_content,
        "install_instructions": {
            "1": f"Speichere das Skript als: {project.local_path or '<dein-projekt>'}/.claude/helpers/dreamline-sync.cjs",
            "2": "Füge in .claude/settings.json unter hooks.Stop hinzu:",
            "hook_config": {
                "type": "command",
                "command": "cmd /c node %CLAUDE_PROJECT_DIR%/.claude/helpers/dreamline-sync.cjs",
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
    Wird vom import-sessions Endpoint UND vom quick-add Auto-Import genutzt.
    Gibt die Anzahl importierter Sessions zurück.
    """
    import json as json_module
    from app.models.session import Session as DreamlineSession

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
                meta = json_module.loads(row)
                src = meta.get("source_file")
                if src:
                    existing_files.add(src)
            except (json_module.JSONDecodeError, TypeError):
                pass

    imported = 0
    for jsonl_file in jsonl_files:
        if jsonl_file.name in existing_files:
            continue

        try:
            lines = jsonl_file.read_text(encoding="utf-8", errors="replace").strip().split("\n")
            if not lines:
                continue

            messages = []
            for line in lines[-30:]:
                try:
                    entry = json_module.loads(line)
                    msg_type = entry.get("type", "")
                    if msg_type == "user":
                        msg = entry.get("message", {})
                        if isinstance(msg, dict):
                            cf = msg.get("content", "")
                            content = cf if isinstance(cf, str) else " ".join(
                                b.get("text", "") for b in cf if isinstance(b, dict) and b.get("type") == "text"
                            ) if isinstance(cf, list) else ""
                            if content and len(content) > 10:
                                messages.append({"role": "user", "content": content[:3000]})
                    elif msg_type == "assistant":
                        msg = entry.get("message", {})
                        blocks = msg.get("content", []) if isinstance(msg, dict) else []
                        if isinstance(blocks, list):
                            content = " ".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
                            if content and len(content) > 10:
                                messages.append({"role": "assistant", "content": content[:3000]})
                except (json_module.JSONDecodeError, TypeError, KeyError):
                    continue

            if len(messages) < 2:
                continue

            session = DreamlineSession(
                project_id=project_id,
                messages_json=json_module.dumps(messages[-10:], ensure_ascii=False),
                outcome="neutral",
                metadata_json=json_module.dumps({
                    "source": "jsonl-import",
                    "source_file": jsonl_file.name,
                    "session_id": jsonl_file.stem,
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
    _: bool = Depends(_verify_admin_key),
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
    _: bool = Depends(_verify_admin_key),
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
        hook_content = HOOK_TEMPLATE.format(
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
        hook_cmd = "cmd /c node %CLAUDE_PROJECT_DIR%/.claude/helpers/dreamline-sync.cjs"
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
