"""
Authentifizierung für Dreamline Dashboard.

Zwei Wege sich anzumelden:
1. Claude CLI Auth: OAuth-Token via CLAUDE_CODE_OAUTH_TOKEN (Setup-Token oder UI)
2. Admin-Key: Manueller Zugang über den DREAMLINE_SECRET_KEY
"""

import asyncio
import json
import logging
import os
import shutil
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


async def _check_codex_cli_auth() -> dict:
    """Prüft den Codex-CLI-Auth-Status per 'codex login status'."""
    binary = shutil.which("codex")
    if not binary:
        return {"authenticated": False, "reason": "cli_not_found",
                "hint": "Codex CLI nicht installiert."}

    try:
        process = await asyncio.create_subprocess_exec(
            binary, "login", "status",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
        # Codex gibt Login-Status auf stderr aus (stdout ist leer)
        combined = (stdout.decode("utf-8", errors="replace") + "\n" +
                    stderr.decode("utf-8", errors="replace")).strip()

        # Filtere harmlose Warnungen
        _skip = ("WARNING:", "Read-only file system", "proceeding, even though")
        lines = [l for l in combined.splitlines()
                 if l.strip() and not any(p in l for p in _skip)]
        clean_output = " ".join(lines).strip()

        if process.returncode == 0 and "Logged in" in clean_output:
            return {"authenticated": True, "method": clean_output}

        return {"authenticated": False, "reason": "not_logged_in",
                "hint": clean_output[:100] or "Codex nicht angemeldet. Im Terminal: docker exec -it dreamline-claude-dreamline-1 codex login"}

    except (asyncio.TimeoutError, OSError) as e:
        logger.warning("Codex auth status Fehler: %s", str(e)[:100])
        return {"authenticated": False, "reason": "check_failed",
                "hint": f"Auth-Check fehlgeschlagen: {str(e)[:80]}"}


async def _check_claude_cli_auth() -> dict:
    """Prüft den Claude-CLI-Auth-Status per 'claude auth status'."""
    binary = shutil.which("claude")
    if not binary:
        return {"authenticated": False, "method": None, "reason": "cli_not_found",
                "hint": "Claude CLI nicht installiert."}

    try:
        process = await asyncio.create_subprocess_exec(
            binary, "auth", "status",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
        data = json.loads(stdout.decode("utf-8").strip())

        if data.get("loggedIn"):
            return {
                "authenticated": True,
                "method": data.get("authMethod", "unknown"),
                "email": data.get("email"),
                "subscription": data.get("subscriptionType"),
            }
        return {"authenticated": False, "method": None, "reason": "not_logged_in",
                "hint": "Claude CLI nicht angemeldet. Token über Einstellungen hinterlegen."}

    except (asyncio.TimeoutError, json.JSONDecodeError, OSError) as e:
        logger.warning("Claude auth status Fehler: %s", str(e)[:100])
        return {"authenticated": False, "method": None, "reason": "check_failed",
                "hint": f"Auth-Check fehlgeschlagen: {str(e)[:80]}"}


@router.get("/status")
async def auth_status():
    """Prüft den Claude-CLI-Auth-Status inkl. Token-Metadaten aus der DB."""
    from sqlalchemy import select
    from app.database import async_session
    from app.models.runtime_settings import RuntimeSetting

    # Claude + Codex Auth-Checks parallel ausführen
    claude_result, codex_result = await asyncio.gather(
        _check_claude_cli_auth(),
        _check_codex_cli_auth(),
    )
    result = claude_result

    # Token-Metadaten aus DB laden (maskiert)
    try:
        async with async_session() as db:
            rows = await db.execute(
                select(RuntimeSetting).where(
                    RuntimeSetting.key.in_(["claude_oauth_token", "claude_oauth_token_saved_at"])
                )
            )
            db_values = {r.key: r.value for r in rows.scalars().all()}

        token = db_values.get("claude_oauth_token") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
        if token and len(token) >= 16:
            result["token_masked"] = token[:12] + "..." + token[-4:]
            result["token_source"] = "db" if "claude_oauth_token" in db_values else "env"
        elif token:
            result["token_masked"] = "***"
            result["token_source"] = "env"

        result["token_saved_at"] = db_values.get("claude_oauth_token_saved_at", "")
    except Exception as e:
        logger.warning("Token-Metadaten Fehler: %s", str(e)[:100])

    result["codex"] = codex_result

    return JSONResponse(result)


@router.get("/login")
async def login_page(request: Request):
    """Login-Seite mit Anleitung."""
    return HTMLResponse("""
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dreamline – Anmelden</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:system-ui,-apple-system,sans-serif; background:#1a1a2e; color:#e2e8f0; min-height:100vh; display:flex; align-items:center; justify-content:center; }
  .login-card { background:#16213e; border-radius:16px; padding:48px; max-width:520px; width:100%; margin:20px; box-shadow:0 25px 50px rgba(0,0,0,0.5); }
  .logo { text-align:center; margin-bottom:32px; }
  .logo h1 { font-size:28px; font-weight:700; }
  .logo h1 span { color:#2563EB; }
  .logo p { color:#64748b; font-size:14px; margin-top:4px; }
  h2 { font-size:20px; margin-bottom:16px; }
  .admin-section { margin-top:16px; }
  .admin-section input { width:100%; background:#1a1a2e; border:1px solid #334155; border-radius:8px; padding:12px; color:#e2e8f0; font-size:14px; }
  .admin-section input:focus { outline:none; border-color:#2563EB; }
  .admin-section label { font-size:13px; color:#94a3b8; display:block; margin-bottom:6px; }
  .btn { display:block; width:100%; background:#2563EB; color:white; border:none; padding:14px; border-radius:10px; font-size:16px; font-weight:600; cursor:pointer; margin-top:16px; }
  .btn:hover { background:#1d4ed8; }
  .divider { text-align:center; color:#475569; margin:24px 0; font-size:13px; }
</style>
</head>
<body>
<div class="login-card">
  <div class="logo">
    <h1>Dream<span>line</span></h1>
    <p>Self-Evolving AI Memory</p>
  </div>

  <h2>Anmelden</h2>

  <div class="admin-section">
    <label>Admin-Key eingeben</label>
    <input type="password" id="adminKey" placeholder="Admin-Key..." onkeydown="if(event.key==='Enter')adminLogin()">
    <button class="btn" style="margin-top:8px" onclick="adminLogin()">Anmelden</button>
  </div>

  <div class="divider">Der Admin-Key steht in der .env Datei (DREAMLINE_SECRET_KEY)</div>
</div>

<script>
function adminLogin() {
  const key = document.getElementById('adminKey').value;
  if (key) {
    sessionStorage.setItem('dreamline_admin_key', key);
    window.location = '/';
  }
}
</script>
</body>
</html>
""")


@router.get("/logout")
async def logout():
    """Info-Seite zum Ausloggen."""
    return HTMLResponse("""
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<title>Dreamline – Abgemeldet</title>
<style>
  body { font-family:system-ui; background:#1a1a2e; color:#e2e8f0; display:flex; align-items:center; justify-content:center; height:100vh; }
  .card { background:#16213e; border-radius:16px; padding:40px; text-align:center; }
  a { color:#2563EB; text-decoration:none; }
</style>
</head>
<body>
<div class="card">
  <h2>Abgemeldet</h2>
  <p style="margin-top:20px"><a href="/auth/login">Erneut anmelden</a></p>
</div>
</body>
</html>
""")
