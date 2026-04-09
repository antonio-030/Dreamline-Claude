// ─── Admin-Key ───────────────────────────────────────────────────
function saveAdminKey() {
  state.adminKey = document.getElementById('adminKeyInput').value.trim();
  localStorage.setItem('dreamline_admin_key', state.adminKey);
  sessionStorage.removeItem('dreamline_admin_key');
  toast('Admin-Key gespeichert', 'success');
  loadProjects();
}

// ─── Settings UI ─────────────────────────────────────────────────
const GROUP_LABELS = {
  dream: 'autoDream & Scheduling',
  extract: 'Quick-Extract',
  ollama: 'Ollama (Lokale LLMs)',
  codex: 'Codex-Watcher',
  ai: 'KI-Client',
  tuning: 'Tuning-Parameter',
  system: 'System',
};

async function loadSettings() {
  // Auth-Karte immer laden (kein Admin-Key nötig)
  _loadAuthCard();

  const container = document.getElementById('settingsForm');
  if (!container || !state.adminKey) return;
  try {
    const items = await apiFetch('/api/v1/settings', { headers: adminHeaders() });

    // Restliche Settings (ohne auth-Gruppe)
    const groups = {};
    items.filter(s => s.group !== 'auth').forEach(s => {
      if (!groups[s.group]) groups[s.group] = [];
      groups[s.group].push(s);
    });

    let html = '';
    for (const [group, settings] of Object.entries(groups)) {
      html += `<div style="margin-bottom:20px;">
        <h4 style="font-size:13px; color:var(--accent); margin-bottom:10px; text-transform:uppercase; letter-spacing:0.5px;">${GROUP_LABELS[group] || group}</h4>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">`;
      for (const s of settings) {
        const inputId = `setting-${s.key}`;
        if (s.type === 'bool') {
          html += `<label style="display:flex; align-items:center; gap:8px; grid-column:span 2; font-size:13px; cursor:pointer;">
            <input type="checkbox" id="${inputId}" ${s.value === 'true' || s.value === 'True' ? 'checked' : ''}>
            ${esc(s.label)}${s.has_override ? ' <span style="color:var(--accent); font-size:10px;">(geändert)</span>' : ''}
          </label>`;
        } else {
          html += `<div>
            <label style="font-size:12px; color:var(--text-muted); display:block; margin-bottom:4px;">
              ${esc(s.label)}${s.has_override ? ' <span style="color:var(--accent);">(geändert)</span>' : ''}
            </label>
            <input type="${s.type === 'int' || s.type === 'float' ? 'number' : 'text'}" id="${inputId}" value="${esc(s.value)}"
              ${s.min !== undefined ? `min="${s.min}"` : ''} ${s.max !== undefined ? `max="${s.max}"` : ''}
              ${s.type === 'float' ? 'step="0.1"' : ''}
              class="form-input" style="width:100%; padding:6px 10px; font-size:13px;">
          </div>`;
        }
      }
      html += `</div></div>`;
    }
    container.innerHTML = html;
  } catch {
    container.innerHTML = '<div class="empty">Einstellungen konnten nicht geladen werden.</div>';
  }
}

// ─── Auth-Status-Karte (alles aus /auth/status, kein Admin-Key) ──
async function _loadAuthCard() {
  const card = document.getElementById('authCard');
  if (!card) return;

  let auth = {};
  try { auth = await fetch('/auth/status').then(r => r.json()); } catch {}

  const ok = auth.authenticated;
  const masked = auth.token_masked || '';
  const src = auth.token_source === 'db' ? 'Datenbank' : (auth.token_source === 'env' ? '.env' : '');
  const savedAt = auth.token_saved_at || '';

  // Ablaufdatum berechnen (Token gültig ~1 Jahr)
  let expiryHtml = '';
  if (savedAt) {
    const saved = new Date(savedAt);
    const expires = new Date(saved.getTime() + 365 * 24 * 60 * 60 * 1000);
    const daysLeft = Math.round((expires - Date.now()) / (24 * 60 * 60 * 1000));
    const expStr = expires.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' });

    if (daysLeft < 0) {
      expiryHtml = `<span style="color:var(--danger); font-weight:600;">Abgelaufen!</span>`;
    } else if (daysLeft < 30) {
      expiryHtml = `<span style="color:var(--warning); font-weight:600;">${expStr} (${daysLeft} Tage)</span>`;
    } else {
      expiryHtml = `<span style="color:var(--success);">${expStr} (${daysLeft} Tage)</span>`;
    }
  } else if (masked) {
    expiryHtml = '<span style="color:var(--text-muted);">Datum unbekannt</span>';
  }

  // Codex-Auth-Status
  const codex = auth.codex || {};
  const codexOk = codex.authenticated;

  card.innerHTML = `
    <div style="display:flex; gap:16px; flex-wrap:wrap;">
      <div style="flex:1; min-width:280px;">
        <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:${masked ? '12px' : '0'};">
          <div style="display:flex; align-items:center; gap:12px;">
            <div style="width:36px; height:36px; border-radius:10px; background:${ok ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)'}; display:flex; align-items:center; justify-content:center; font-size:18px; color:${ok ? 'var(--success)' : 'var(--danger)'};">
              ${ok ? '&#10003;' : '&#10007;'}
            </div>
            <div>
              <div style="font-size:15px; font-weight:600; color:${ok ? 'var(--success)' : 'var(--danger)'};">
                Claude ${ok ? 'verbunden' : 'nicht verbunden'}
              </div>
              <div style="font-size:12px; color:var(--text-muted);">
                ${ok ? (auth.email || auth.subscription || auth.method || 'oauth_token') : 'Wird f\u00fcr claude-abo Dreams ben\u00f6tigt'}
              </div>
            </div>
          </div>
          <button class="btn btn-primary btn-sm" onclick="openAuthModal('claude')">
            ${ok ? 'Token erneuern' : 'Anmelden'}
          </button>
        </div>
        ${masked ? `
        <div style="display:flex; gap:12px; font-size:13px; flex-wrap:wrap;">
          <div style="padding:8px 12px; background:var(--bg); border-radius:8px; flex:1; min-width:160px;">
            <div style="font-size:11px; color:var(--text-muted); margin-bottom:2px;">Token</div>
            <div style="font-family:monospace; font-size:12px;">${esc(masked)}</div>
          </div>
          ${src ? `<div style="padding:8px 12px; background:var(--bg); border-radius:8px;">
            <div style="font-size:11px; color:var(--text-muted); margin-bottom:2px;">Quelle</div>
            <div style="font-size:12px;">${src}</div>
          </div>` : ''}
          ${expiryHtml ? `<div style="padding:8px 12px; background:var(--bg); border-radius:8px;">
            <div style="font-size:11px; color:var(--text-muted); margin-bottom:2px;">L\u00e4uft ab</div>
            <div style="font-size:12px;">${expiryHtml}</div>
          </div>` : ''}
        </div>` : ''}
      </div>

      <div style="width:1px; background:var(--border); align-self:stretch;"></div>

      <div style="flex:1; min-width:280px;">
        <div style="display:flex; align-items:center; justify-content:space-between;">
          <div style="display:flex; align-items:center; gap:12px;">
            <div style="width:36px; height:36px; border-radius:10px; background:${codexOk ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)'}; display:flex; align-items:center; justify-content:center; font-size:18px; color:${codexOk ? 'var(--success)' : 'var(--danger)'};">
              ${codexOk ? '&#10003;' : '&#10007;'}
            </div>
            <div>
              <div style="font-size:15px; font-weight:600; color:${codexOk ? 'var(--success)' : 'var(--danger)'};">
                Codex ${codexOk ? 'verbunden' : 'nicht verbunden'}
              </div>
              <div style="font-size:12px; color:var(--text-muted);">
                ${codexOk ? esc(codex.method || '') : 'Wird f\u00fcr codex-sub Dreams ben\u00f6tigt'}
              </div>
            </div>
          </div>
          ${!codexOk ? `<button class="btn btn-primary btn-sm" onclick="openAuthModal('codex')">Anleitung</button>` : ''}
        </div>
        ${!codexOk ? `
        <div style="padding:10px 12px; background:rgba(239,68,68,0.08); border:1px solid rgba(239,68,68,0.2); border-radius:8px; font-size:12px; color:var(--text-muted); margin-top:10px;">
          Im Terminal: <code style="background:var(--bg); padding:2px 6px; border-radius:4px;">docker exec -it dreamline-claude-dreamline-1 codex login</code>
        </div>` : ''}
      </div>
    </div>`;
}

// ─── Anmelde-Modal ───────────────────────────────────────────────
function openAuthModal(provider = 'claude') {
  document.getElementById('auth-modal')?.remove();

  const overlay = document.createElement('div');
  overlay.id = 'auth-modal';
  overlay.className = 'modal-overlay';
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });

  const box = document.createElement('div');
  box.className = 'modal-box';
  box.style.maxWidth = '520px';

  const claudeContent = `
    <div style="text-align:center; margin-bottom:20px;">
      <button class="btn btn-primary" onclick="_copySetupCommand()" id="auth-copy-btn" style="font-size:15px; padding:12px 28px;">
        Befehl kopieren und im Terminal einf\u00fcgen
      </button>
      <div style="font-size:12px; color:var(--text-muted); margin-top:8px;">
        Kopiert <code style="background:var(--bg); padding:2px 6px; border-radius:4px;">claude setup-token</code> \u2014 f\u00fcge es in dein Terminal ein.<br>
        Der Browser \u00f6ffnet sich, du meldest dich an, und bekommst einen Token.
      </div>
    </div>
    <div style="border-top:1px solid var(--border); padding-top:16px;">
      <label style="font-size:13px; font-weight:600; display:block; margin-bottom:6px;">Token einf\u00fcgen:</label>
      <input type="text" id="auth-token-input" placeholder="sk-ant-oat01-..."
        class="form-input" style="width:100%; font-family:monospace; font-size:13px; padding:10px 14px;"
        oninput="_validateTokenInput()">
      <div id="auth-modal-status" style="margin-top:8px; font-size:12px;"></div>
    </div>
    <div class="modal-actions" style="margin-top:16px;">
      <button class="btn btn-sm btn-ghost" onclick="document.getElementById('auth-modal').remove()">Abbrechen</button>
      <button class="btn btn-primary btn-sm" onclick="_saveTokenFromModal()" id="auth-save-btn" disabled>Speichern &amp; Aktivieren</button>
    </div>`;

  const codexContent = `
    <div style="padding:16px 0;">
      <div style="font-size:14px; margin-bottom:16px;">Codex wird direkt im Docker-Container angemeldet \u2014 kein Token n\u00f6tig.</div>
      <div style="margin-bottom:16px;">
        <div style="font-size:13px; font-weight:600; margin-bottom:6px;">1. Terminal \u00f6ffnen und einloggen:</div>
        <div style="background:var(--bg); border:1px solid var(--border); border-radius:8px; padding:12px; font-family:monospace; font-size:13px; cursor:pointer; position:relative;" onclick="_copyCodexCommand()">
          <span id="codex-cmd-text">docker exec -it dreamline-claude-dreamline-1 codex login</span>
          <span id="codex-cmd-copied" style="position:absolute; right:12px; top:12px; font-size:11px; color:var(--success); display:none;">Kopiert!</span>
        </div>
        <div style="font-size:12px; color:var(--text-muted); margin-top:6px;">
          Ein Browser-Fenster \u00f6ffnet sich. Melde dich mit deinem OpenAI-Konto an.
        </div>
      </div>
      <div style="margin-bottom:16px;">
        <div style="font-size:13px; font-weight:600; margin-bottom:6px;">2. Pr\u00fcfen ob es geklappt hat:</div>
        <div style="background:var(--bg); border:1px solid var(--border); border-radius:8px; padding:12px; font-family:monospace; font-size:13px;">
          docker exec dreamline-claude-dreamline-1 codex login status
        </div>
        <div style="font-size:12px; color:var(--text-muted); margin-top:6px;">
          Sollte <strong style="color:var(--success);">Logged in using ChatGPT</strong> zeigen.
        </div>
      </div>
      <div style="padding:10px 12px; background:rgba(37,99,235,0.08); border:1px solid rgba(37,99,235,0.2); border-radius:8px; font-size:12px; color:var(--text-muted);">
        Voraussetzung: OpenAI Plus- oder Pro-Abo. Die Anmeldung bleibt \u00fcber Container-Neustarts erhalten wenn <code style="background:var(--bg); padding:2px 4px; border-radius:4px;">.codex/</code> als Volume gemountet ist.
      </div>
    </div>
    <div class="modal-actions">
      <button class="btn btn-sm btn-ghost" onclick="document.getElementById('auth-modal').remove()">Schlie\u00dfen</button>
      <button class="btn btn-primary btn-sm" onclick="document.getElementById('auth-modal').remove(); loadSettings();">Status aktualisieren</button>
    </div>`;

  box.innerHTML = `
    <div class="modal-header">
      <h3 class="modal-title">KI-Provider anmelden</h3>
      <button class="btn btn-sm modal-close" onclick="document.getElementById('auth-modal').remove()">\u2715</button>
    </div>
    <div class="tab-bar" style="margin-bottom:16px;">
      <button class="tab-btn ${provider === 'claude' ? 'active' : ''}" onclick="_switchAuthTab('claude')">Claude (Abo)</button>
      <button class="tab-btn ${provider === 'codex' ? 'active' : ''}" onclick="_switchAuthTab('codex')">Codex (Abo)</button>
    </div>
    <div id="auth-tab-claude" style="display:${provider === 'claude' ? 'block' : 'none'};">${claudeContent}</div>
    <div id="auth-tab-codex" style="display:${provider === 'codex' ? 'block' : 'none'};">${codexContent}</div>
  `;

  overlay.appendChild(box);
  document.body.appendChild(overlay);
  if (provider === 'claude') setTimeout(() => document.getElementById('auth-token-input')?.focus(), 100);
}

function _switchAuthTab(tab) {
  document.getElementById('auth-tab-claude').style.display = tab === 'claude' ? 'block' : 'none';
  document.getElementById('auth-tab-codex').style.display = tab === 'codex' ? 'block' : 'none';
  const btns = document.querySelectorAll('#auth-modal .tab-btn');
  btns.forEach((b, i) => b.classList.toggle('active', (i === 0 && tab === 'claude') || (i === 1 && tab === 'codex')));
  if (tab === 'claude') setTimeout(() => document.getElementById('auth-token-input')?.focus(), 50);
}

function _copyCodexCommand() {
  navigator.clipboard.writeText('docker exec -it dreamline-claude-dreamline-1 codex login').then(() => {
    const el = document.getElementById('codex-cmd-copied');
    if (el) { el.style.display = 'inline'; setTimeout(() => el.style.display = 'none', 2000); }
  });
}

function _copySetupCommand() {
  navigator.clipboard.writeText('claude setup-token').then(() => {
    const btn = document.getElementById('auth-copy-btn');
    btn.textContent = 'Kopiert! Jetzt im Terminal einfügen';
    btn.style.background = 'var(--success)';
    setTimeout(() => {
      btn.textContent = 'Befehl kopieren und im Terminal einfügen';
      btn.style.background = '';
    }, 3000);
  });
}

function _validateTokenInput() {
  const input = document.getElementById('auth-token-input');
  const btn = document.getElementById('auth-save-btn');
  const status = document.getElementById('auth-modal-status');
  const val = (input?.value || '').trim();

  if (!val) {
    btn.disabled = true;
    status.innerHTML = '';
  } else if (!val.startsWith('sk-ant-oat01-')) {
    btn.disabled = true;
    status.innerHTML = '<span style="color:var(--danger);">Token muss mit sk-ant-oat01- beginnen</span>';
  } else if (val.length < 40) {
    btn.disabled = true;
    status.innerHTML = '<span style="color:var(--warning);">Token scheint unvollständig</span>';
  } else {
    btn.disabled = false;
    status.innerHTML = '<span style="color:var(--success);">Format OK</span>';
  }
}

async function _saveTokenFromModal() {
  const token = document.getElementById('auth-token-input')?.value?.trim();
  const status = document.getElementById('auth-modal-status');
  if (!token) return;

  status.innerHTML = '<span style="color:var(--accent);">Speichere und teste...</span>';

  try {
    await apiFetch('/api/v1/settings', {
      method: 'PATCH', headers: adminHeaders(),
      body: JSON.stringify({ settings: { claude_oauth_token: token } }),
    });

    const result = await fetch('/auth/status').then(r => r.json());
    if (result.authenticated) {
      toast('Token gespeichert und aktiviert', 'success');
      document.getElementById('auth-modal')?.remove();
      loadSettings();
    } else {
      status.innerHTML = `<span style="color:var(--danger);">Token gespeichert, aber Auth fehlgeschlagen: ${esc(result.hint || result.reason || 'Unbekannt')}</span>`;
    }
  } catch (e) {
    status.innerHTML = `<span style="color:var(--danger);">Fehler: ${esc(e.message)}</span>`;
  }
}

// ─── Settings speichern ──────────────────────────────────────────
async function saveAllSettings() {
  const items = document.querySelectorAll('[id^="setting-"]');
  const changes = {};
  items.forEach(el => {
    const key = el.id.replace('setting-', '');
    if (el.type === 'password' && !el.value) return;
    changes[key] = el.type === 'checkbox' ? String(el.checked) : el.value;
  });

  try {
    const result = await apiFetch('/api/v1/settings', {
      method: 'PATCH',
      headers: adminHeaders(),
      body: JSON.stringify({ settings: changes }),
    });
    if (result.errors && result.errors.length > 0) {
      toast('Fehler: ' + result.errors.join(', '), 'error');
    } else {
      toast(`${result.updated.length} Einstellungen gespeichert`, 'success');
    }
    loadSettings();
  } catch (e) {
    toast('Speichern fehlgeschlagen: ' + e.message, 'error');
  }
}
