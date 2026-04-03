"""
Authentifizierung für Dreamline Dashboard.

Zwei Wege sich anzumelden:
1. Claude CLI Auth: User hat sich bereits über 'claude' CLI eingeloggt →
   Token wird aus ~/.claude/.credentials.json gelesen (automatisch)
2. Admin-Key: Manueller Zugang über den DREAMLINE_SECRET_KEY

Der OAuth-Token wird NICHT direkt von Anthropic geholt (Redirect-URI nicht erlaubt),
sondern von der Claude Code CLI übernommen – gleicher Mechanismus wie OpenClaw.
"""

import json
import logging
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
    Schaut ob Claude CLI Credentials vorhanden und gültig sind.
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
            "expired_since_minutes": int((now_ms - expires_at) / 60000),
        })

    return JSONResponse({
        "authenticated": True,
        "method": "claude_cli",
        "token_preview": creds["accessToken"][:20] + "...",
        "expires_at": expires_at,
        "expires_in_hours": round((expires_at - now_ms) / 3600000, 1),
        "has_refresh_token": bool(creds.get("refreshToken")),
    })


@router.get("/login")
async def login_page(request: Request):
    """
    Login-Seite mit Anleitung.
    Erklärt wie man sich über Claude CLI authentifiziert.
    """
    creds = _read_credentials()
    if creds and creds.get("expiresAt", 0) > time.time() * 1000:
        # Bereits eingeloggt → zum Dashboard weiterleiten
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
    <h1>🌙 Dream<span>line</span></h1>
    <p>Self-Evolving AI Memory</p>
  </div>

  <h2>Mit Claude-Abo anmelden</h2>

  <div class="step">
    <span class="step-num">1</span>
    <h3>Claude Code CLI öffnen</h3>
    <p>Öffne ein Terminal auf deinem Computer und führe aus:</p>
    <div class="code">
      <span>claude</span>
      <button onclick="navigator.clipboard.writeText('claude')">Kopieren</button>
    </div>
  </div>

  <div class="step">
    <span class="step-num">2</span>
    <h3>Mit Claude-Abo einloggen</h3>
    <p>Folge den Anweisungen im Terminal. Wähle "Login mit Claude.ai" und melde dich mit deinem Abo an.</p>
  </div>

  <div class="step">
    <span class="step-num">3</span>
    <h3>Verbindung prüfen</h3>
    <p>Klicke auf den Button unten. Dreamline erkennt dein Claude-Abo automatisch.</p>
  </div>

  <div id="status" class="status checking">Prüfe Verbindung...</div>

  <button class="btn" id="checkBtn" onclick="checkAuth()">Verbindung prüfen</button>

  <div class="divider">─── oder ───</div>

  <div class="admin-section">
    <label>Zugang mit Admin-Key</label>
    <input type="password" id="adminKey" placeholder="Admin-Key eingeben..." onkeydown="if(event.key==='Enter')adminLogin()">
    <button class="btn" style="background:#475569;margin-top:8px" onclick="adminLogin()">Mit Admin-Key anmelden</button>
  </div>
</div>

<script>
async function checkAuth() {
  const status = document.getElementById('status');
  const btn = document.getElementById('checkBtn');
  btn.disabled = true;
  status.className = 'status checking';
  status.textContent = 'Prüfe Claude-Abo Verbindung...';

  try {
    const resp = await fetch('/auth/status');
    const data = await resp.json();

    if (data.authenticated) {
      status.className = 'status success';
      status.innerHTML = '✓ Claude-Abo verbunden!<br><small>Token gültig für ' + data.expires_in_hours + ' Stunden</small>';
      setTimeout(() => window.location = '/', 1500);
    } else {
      status.className = 'status error';
      status.innerHTML = '✗ ' + (data.hint || 'Nicht verbunden') +
        (data.reason === 'token_expired' ? '<br><small>Bitte "claude" im Terminal erneut ausführen</small>' : '');
    }
  } catch(e) {
    status.className = 'status error';
    status.textContent = 'Verbindungsfehler: ' + e.message;
  }
  btn.disabled = false;
}

function adminLogin() {
  const key = document.getElementById('adminKey').value;
  if (key) {
    localStorage.setItem('dreamline_admin_key', key);
    window.location = '/';
  }
}

// Sofort prüfen
checkAuth();
</script>
</body>
</html>
""")


@router.get("/auto-login")
async def auto_login():
    """
    Automatischer Login über Claude CLI Credentials.
    Wenn gültige Credentials vorhanden sind, wird der Admin-Key zurückgegeben,
    damit das Dashboard sofort funktioniert – ohne manuelle Eingabe.
    """
    creds = _read_credentials()

    if not creds:
        return JSONResponse(
            {"success": False, "reason": "Keine Claude CLI Credentials gefunden."},
            status_code=401,
        )

    expires_at = creds.get("expiresAt", 0)
    now_ms = int(time.time() * 1000)

    if expires_at < now_ms:
        return JSONResponse(
            {"success": False, "reason": "Claude-Token abgelaufen. Bitte 'claude' im Terminal ausführen."},
            status_code=401,
        )

    # Gültige Claude-Auth → Admin-Key herausgeben
    return JSONResponse({
        "success": True,
        "admin_key": settings.dreamline_secret_key,
        "expires_in_hours": round((expires_at - now_ms) / 3600000, 1),
    })


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
  <h2>Dreamline Logout</h2>
  <p style="color:#94a3b8;margin:16px 0">Um dich abzumelden, führe im Terminal aus:</p>
  <code style="background:#0f172a;padding:8px 16px;border-radius:8px;color:#38bdf8">claude logout</code>
  <p style="margin-top:20px"><a href="/">← Zurück zum Dashboard</a></p>
</div>
</body>
</html>
""")
