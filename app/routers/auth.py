"""
Authentifizierung für Dreamline Dashboard.

Zwei Wege sich anzumelden:
1. Claude CLI Auth: Token aus ~/.claude/.credentials.json (automatisch)
2. Admin-Key: Manueller Zugang über den DREAMLINE_SECRET_KEY
"""

import json
import logging
import secrets as _secrets
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Pfad für Claude CLI Credentials
CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"


def _read_credentials() -> dict | None:
    """Liest OAuth-Credentials aus der Claude CLI Datei."""
    if not CREDENTIALS_PATH.exists():
        return None
    try:
        data = json.loads(CREDENTIALS_PATH.read_text())
        oauth = data.get("claudeAiOauth")
        if not oauth or not oauth.get("accessToken"):
            return None
        return oauth
    except (json.JSONDecodeError, OSError):
        return None


@router.get("/status")
async def auth_status():
    """
    Prüft den Authentifizierungs-Status.
    Gibt KEINE sensiblen Daten zurück (kein Token, kein Key).
    """
    creds = _read_credentials()

    if not creds:
        return JSONResponse({
            "authenticated": False,
            "method": None,
            "reason": "no_credentials",
            "hint": "Bitte 'claude' im Terminal ausführen und einloggen.",
        })

    expires_at = creds.get("expiresAt", 0)
    now_ms = int(time.time() * 1000)

    if expires_at < now_ms:
        return JSONResponse({
            "authenticated": False,
            "method": "claude_cli",
            "reason": "token_expired",
            "hint": "Token abgelaufen. Bitte 'claude' im Terminal erneut ausführen.",
        })

    return JSONResponse({
        "authenticated": True,
        "method": "claude_cli",
        "expires_in_hours": round((expires_at - now_ms) / 3600000, 1),
    })


@router.get("/login")
async def login_page(request: Request):
    """Login-Seite mit Anleitung."""
    creds = _read_credentials()
    if creds and creds.get("expiresAt", 0) > time.time() * 1000:
        return RedirectResponse(url="/")

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
  .step { background:#1a1a2e; border-radius:12px; padding:20px; margin-bottom:16px; }
  .step-num { display:inline-block; background:#2563EB; color:white; width:28px; height:28px; border-radius:50%; text-align:center; line-height:28px; font-size:14px; font-weight:600; margin-right:10px; }
  .step h3 { display:inline; font-size:15px; }
  .step p { color:#94a3b8; font-size:13px; margin-top:8px; }
  .code { background:#0f172a; border:1px solid #334155; border-radius:8px; padding:12px 16px; font-family:'Fira Code',monospace; font-size:14px; color:#38bdf8; margin-top:8px; display:flex; align-items:center; justify-content:space-between; }
  .code button { background:#2563EB; color:white; border:none; padding:4px 12px; border-radius:6px; font-size:12px; cursor:pointer; }
  .code button:hover { background:#1d4ed8; }
  .status { margin-top:24px; padding:16px; border-radius:12px; text-align:center; }
  .status.checking { background:#1e3a5f; color:#60a5fa; }
  .status.success { background:#064e3b; color:#34d399; }
  .status.error { background:#450a0a; color:#f87171; }
  .btn { display:block; width:100%; background:#2563EB; color:white; border:none; padding:14px; border-radius:10px; font-size:16px; font-weight:600; cursor:pointer; margin-top:16px; }
  .btn:hover { background:#1d4ed8; }
  .btn:disabled { opacity:0.5; cursor:not-allowed; }
  .divider { text-align:center; color:#475569; margin:24px 0; font-size:13px; }
  .admin-section { margin-top:16px; }
  .admin-section input { width:100%; background:#1a1a2e; border:1px solid #334155; border-radius:8px; padding:12px; color:#e2e8f0; font-size:14px; }
  .admin-section input:focus { outline:none; border-color:#2563EB; }
  .admin-section label { font-size:13px; color:#94a3b8; display:block; margin-bottom:6px; }
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
