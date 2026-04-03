// Globaler Zustand
const state = {
  adminKey: sessionStorage.getItem('dreamline_admin_key') || '',
  projects: [],
  allMemories: [],
  sessionPage: 0,
  sessionsPerPage: 20,
};

// Hilfsfunktionen
function toast(msg, type = 'info') {
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  document.getElementById('toastContainer').appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

function maskKey(key) {
  if (!key || key.length < 12) return '***';
  return key.slice(0, 6) + '...' + key.slice(-4);
}

function formatDate(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  return d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' }) +
    ' ' + d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
}

function confidenceColor(c) {
  if (c >= 0.7) return 'var(--success)';
  if (c >= 0.4) return 'var(--warning)';
  return 'var(--danger)';
}

function statusBadge(status) {
  const map = {
    completed: 'completed', failed: 'failed', skipped: 'skipped', running: 'running',
  };
  return `<span class="badge badge-${map[status] || 'skipped'}">${status}</span>`;
}

function typeBadge(type) {
  const map = { user: 'user', feedback: 'feedback', project: 'project', reference: 'reference' };
  return `<span class="badge badge-${map[type] || 'project'}">${type}</span>`;
}

function outcomeBadge(outcome) {
  if (!outcome) return '<span class="badge badge-neutral">-</span>';
  return `<span class="badge badge-${outcome}">${outcome}</span>`;
}

// API-Aufrufe
async function apiFetch(url, options = {}) {
  try {
    const res = await fetch(url, options);
    if (!res.ok) {
      const body = await res.text();
      let detail = '';
      try { detail = JSON.parse(body).detail || body; } catch { detail = body; }
      throw new Error(`${res.status}: ${detail}`);
    }
    return await res.json();
  } catch (err) {
    toast(`Fehler: ${err.message}`, 'error');
    throw err;
  }
}

function adminHeaders() {
  return { 'Content-Type': 'application/json', 'X-Admin-Key': state.adminKey };
}

function bearerHeaders(apiKey) {
  return { 'Content-Type': 'application/json', 'Authorization': `Bearer ${apiKey}` };
}

function getSelectedProjectKey(selectId) {
  const sel = document.getElementById(selectId);
  if (!sel || !sel.value) return null;
  const proj = state.projects.find(p => p.id === sel.value);
  return proj ? proj.api_key : null;
}

// Tab-Navigation
function switchTab(tab) {
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelector(`[data-tab="${tab}"]`).classList.add('active');
  document.getElementById(`tab-${tab}`).classList.add('active');
  window.location.hash = tab;

  // Daten laden wenn Tab gewechselt wird
  if (tab === 'uebersicht') refreshOverview();
  if (tab === 'projekte') loadProjects();
  if (tab === 'memories') loadMemories();
  if (tab === 'dreams') loadDreams();
  if (tab === 'sessions') loadSessions();
}

document.querySelectorAll('.nav-btn').forEach(btn => {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});

// Initiale Tab-Erkennung aus Hash
function initTab() {
  const hash = window.location.hash.replace('#', '') || 'uebersicht';
  switchTab(hash);
}

// Health-Check
async function checkHealth() {
  try {
    const res = await fetch('/health');
    document.getElementById('healthDot').className = res.ok ? 'status-dot' : 'status-dot offline';
  } catch {
    document.getElementById('healthDot').className = 'status-dot offline';
  }
}

// Projekte laden (Admin-Key)
async function loadProjects() {
  if (!state.adminKey) {
    document.getElementById('projectsTable').innerHTML =
      '<tr><td colspan="6" class="empty">Bitte zuerst den Admin-Key in den Einstellungen hinterlegen.</td></tr>';
    return;
  }
  try {
    state.projects = await apiFetch('/api/v1/projects', { headers: adminHeaders() });
    renderProjects();
    populateProjectSelectors();
  } catch {
    document.getElementById('projectsTable').innerHTML =
      '<tr><td colspan="6" class="empty">Fehler beim Laden der Projekte.</td></tr>';
  }
}

function renderProjects() {
  const tb = document.getElementById('projectsTable');
  if (!state.projects.length) {
    tb.innerHTML = '<tr><td colspan="6" class="empty">Noch keine Projekte vorhanden.</td></tr>';
    return;
  }
  tb.innerHTML = state.projects.map(p => `
    <tr>
      <td>
        <strong>${esc(p.name)}</strong>
        <div class="project-info">${esc(p.ai_provider)} · ${esc(p.ai_model)}</div>
        <div class="project-info project-info--small">Quelle: ${sourceToolLabel(p.source_tool)}</div>
      </td>
      <td>
        <span class="api-key" onclick="toggleKeyReveal(this, '${esc(p.api_key)}')" title="Klicken zum Anzeigen/Kopieren">
          <span class="key-display">${maskKey(p.api_key)}</span>
        </span>
      </td>
      <td>${p.dream_interval_hours}h</td>
      <td>${p.min_sessions_for_dream}</td>
      <td><span class="badge badge-${p.quick_extract ? 'yes' : 'no'}">${p.quick_extract ? 'An' : 'Aus'}</span></td>
      <td class="actions-cell">
        <button class="btn btn-sm btn-warning" onclick="editProject('${p.id}')" title="Bearbeiten">&#9998;</button>
        <button class="btn btn-sm btn-purple" onclick="importSessions('${p.id}')" title="Lokale Sessions importieren">Import</button>
        <button class="btn btn-primary btn-sm" onclick="triggerDream('${p.api_key}')" title="Dream auslösen">Dream</button>
        ${p.ai_provider === 'ollama' ? `<button class="btn btn-sm btn-success" onclick="syncOllamaModel('${p.id}')" title="Ollama-Modell mit Memories aktualisieren">Sync</button>` : ''}
        <button class="btn btn-danger btn-sm" onclick="deleteProject('${p.id}')" title="Projekt löschen">&times;</button>
      </td>
    </tr>
  `).join('');
}

function esc(str) {
  if (!str) return '';
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

function sourceToolLabel(tool) {
  const labels = {
    'claude': 'Claude Code',
    'codex': 'OpenAI Codex',
    'both': 'Claude + Codex',
  };
  return labels[tool] || tool || 'Claude Code';
}

function toggleKeyReveal(el, fullKey) {
  const display = el.querySelector('.key-display');
  if (display.textContent === fullKey) {
    display.textContent = maskKey(fullKey);
  } else {
    display.textContent = fullKey;
    navigator.clipboard.writeText(fullKey).then(() => toast('API-Key kopiert', 'success'));
  }
}

// Projekt bearbeiten – öffnet ein Inline-Modal
function editProject(projectId) {
  const p = state.projects.find(x => x.id === projectId);
  if (!p) return;

  const html = `
    <div id="editModal" class="modal-overlay" onclick="if(event.target===this)this.remove()">
      <div class="modal-box">
        <h3 class="modal-title" style="margin-bottom:16px;">Projekt bearbeiten: ${esc(p.name)}</h3>
        <label class="form-label">Name</label>
        <input id="ep-name" value="${esc(p.name)}" class="form-input" style="margin-bottom:12px;">

        <div class="form-grid">
          <div>
            <label class="form-label">Dream-Provider</label>
            <select id="ep-provider" class="form-input">
              <option value="claude-abo" ${p.ai_provider === 'claude-abo' ? 'selected' : ''}>Claude (Abo)</option>
              <option value="codex-sub" ${p.ai_provider === 'codex-sub' ? 'selected' : ''}>Codex (Abo)</option>
              <option value="ollama" ${p.ai_provider === 'ollama' ? 'selected' : ''}>Ollama (lokal)</option>
              <option value="anthropic" ${p.ai_provider === 'anthropic' ? 'selected' : ''}>Anthropic (API)</option>
              <option value="openai" ${p.ai_provider === 'openai' ? 'selected' : ''}>OpenAI (API)</option>
            </select>
          </div>
          <div>
            <label class="form-label">Modell</label>
            <input id="ep-model" value="${esc(p.ai_model)}" placeholder="z.B. llama3.2:latest" class="form-input">
          </div>
        </div>

        <div class="form-section">
          <label class="form-label">Session-Quelle</label>
          <select id="ep-source-tool" class="form-input">
            <option value="claude" ${(p.source_tool || 'claude') === 'claude' ? 'selected' : ''}>Nur Claude Code</option>
            <option value="codex" ${p.source_tool === 'codex' ? 'selected' : ''}>Nur OpenAI Codex</option>
            <option value="both" ${p.source_tool === 'both' ? 'selected' : ''}>Claude + Codex (beide)</option>
          </select>
        </div>

        <div class="form-grid">
          <div>
            <label class="form-label">Dream-Intervall (h)</label>
            <input id="ep-interval" type="number" min="1" value="${p.dream_interval_hours}" class="form-input">
          </div>
          <div>
            <label class="form-label">Min. Sessions</label>
            <input id="ep-minsessions" type="number" min="1" value="${p.min_sessions_for_dream}" class="form-input">
          </div>
        </div>
        <label class="form-checkbox">
          <input id="ep-quickextract" type="checkbox" ${p.quick_extract ? 'checked' : ''}>
          Quick-Extract aktiv
        </label>
        <div class="modal-actions">
          <button class="btn btn-sm btn-ghost" onclick="document.getElementById('editModal').remove()">Abbrechen</button>
          <button class="btn btn-primary btn-sm" onclick="saveProject('${p.id}')">Speichern</button>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
}

async function saveProject(projectId) {
  const name = document.getElementById('ep-name').value.trim();
  const provider = document.getElementById('ep-provider').value;
  const model = document.getElementById('ep-model').value.trim();
  const interval = parseInt(document.getElementById('ep-interval').value);
  const minSessions = parseInt(document.getElementById('ep-minsessions').value);
  const quickExtract = document.getElementById('ep-quickextract').checked;
  const sourceTool = document.getElementById('ep-source-tool')?.value;

  try {
    await apiFetch(`/api/v1/projects/${projectId}`, {
      method: 'PATCH',
      headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: name || undefined,
        ai_provider: provider || undefined,
        ai_model: model || undefined,
        dream_interval_hours: interval || undefined,
        min_sessions_for_dream: minSessions || undefined,
        quick_extract: quickExtract,
        source_tool: sourceTool || undefined,
      }),
    });
    document.getElementById('editModal')?.remove();
    toast('Projekt aktualisiert', 'success');
    await loadProjects();
    renderProjects();
  } catch (e) {
    toast('Fehler: ' + e.message, 'error');
  }
}

// ─── Neues Projekt Popup ─────────────────────────────────────────
function openNewProjectPopup() {
  const popupId = 'new-project-popup';

  // Vorheriges Popup entfernen falls offen
  document.getElementById(popupId)?.remove();

  const overlay = document.createElement('div');
  overlay.id = popupId;
  overlay.className = 'modal-overlay';
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });

  const box = document.createElement('div');
  box.className = 'modal-box modal-box--wide';
  box.innerHTML = `
    <div class="modal-header">
      <h3 class="modal-title">Neues Projekt</h3>
      <button class="btn btn-sm modal-close" onclick="document.getElementById('${popupId}').remove()">✕</button>
    </div>
    <p class="modal-text">Klicke auf ein Projekt — Dreamline richtet alles automatisch ein und importiert vorhandene Sessions.</p>

    <div class="form-section form-section--boxed">
      <label class="form-label form-label--small">Dream-Provider (wer konsolidiert?)</label>
      <select id="popup-provider" onchange="onPopupProviderChange()" class="form-input form-input--card-bg">
        <option value="claude-abo">Claude (Abo)</option>
        <option value="codex-sub">Codex (Abo)</option>
        <option value="ollama">Ollama (lokal)</option>
        <option value="anthropic">Anthropic (API-Key)</option>
        <option value="openai">OpenAI (API-Key)</option>
      </select>
      <div id="popup-model-row" class="form-section" style="display:none;margin-top:8px;">
        <label class="form-label form-label--small">Modell</label>
        <input id="popup-model" placeholder="z.B. llama3.2:latest" class="form-input form-input--card-bg">
      </div>
    </div>

    <div class="tab-bar">
      <button class="tab-btn active" id="tab-claude" onclick="switchProjectTab('claude')">Claude Code</button>
      <button class="tab-btn" id="tab-codex" onclick="switchProjectTab('codex')">OpenAI Codex</button>
    </div>

    <div id="popup-project-list" class="scan-list">
      <div class="loading-center"><div class="spinner spinner--md"></div><div class="loading-text">Scanne lokale Projekte...</div></div>
    </div>
  `;

  overlay.appendChild(box);
  document.body.appendChild(overlay);

  // Claude-Projekte scannen (Standard-Tab)
  scanLocalProjectsPopup();
}

// Standard-Modelle pro Provider
const defaultModels = {
  'claude-abo': 'claude-sonnet-4-5-20250514',
  'codex-sub': 'gpt-5.2-codex',
  'ollama': 'llama3.2:latest',
  'anthropic': 'claude-sonnet-4-5-20250514',
  'openai': 'gpt-4o',
};

function onPopupProviderChange() {
  const provider = document.getElementById('popup-provider')?.value;
  const modelRow = document.getElementById('popup-model-row');
  const modelInput = document.getElementById('popup-model');
  // Ollama zeigt Modell-Input (User muss lokales Modell angeben)
  if (provider === 'ollama') {
    modelRow.style.display = 'block';
    modelInput.value = 'llama3.2:latest';
  } else {
    modelRow.style.display = 'none';
  }
}

function getPopupProviderAndModel() {
  const provider = document.getElementById('popup-provider')?.value || 'claude-abo';
  const customModel = document.getElementById('popup-model')?.value?.trim();
  const model = customModel || defaultModels[provider] || 'claude-sonnet-4-5-20250514';
  return { provider, model };
}

function switchProjectTab(tab) {
  const claudeTab = document.getElementById('tab-claude');
  const codexTab = document.getElementById('tab-codex');
  claudeTab.classList.toggle('active', tab === 'claude');
  codexTab.classList.toggle('active', tab === 'codex');
  if (tab === 'claude') {
    scanLocalProjectsPopup();
  } else {
    scanCodexProjectsPopup();
  }
}

async function scanCodexProjectsPopup() {
  const container = document.getElementById('popup-project-list');
  if (!container) return;

  container.innerHTML = '<div class="loading-center"><div class="spinner spinner--md"></div><div class="loading-text">Scanne Codex-Projekte...</div></div>';

  try {
    const data = await apiFetch('/api/v1/link/scan-codex', { headers: adminHeaders() });
    const linkedPaths = new Set(state.projects.map(p => (p.local_path || '').replace(/\\\\/g, '/').replace(/\\/g, '/').toLowerCase()));

    if (!data.projects || !data.projects.length) {
      container.innerHTML = '<div class="empty">Keine Codex-Sessions gefunden.<br><span class="loading-text">Starte eine Codex-Session in einem Projektordner.</span></div>';
      return;
    }

    container.innerHTML = '';
    data.projects.forEach(p => {
      const normalizedCwd = p.cwd.replace(/\\\\/g, '/').replace(/\\/g, '/').toLowerCase();
      const isLinked = linkedPaths.has(normalizedCwd);
      const card = document.createElement('div');
      card.className = `scan-card ${isLinked ? 'scan-card--linked' : 'scan-card--clickable'}`;

      card.innerHTML = `
        <div>
          <div class="scan-card__name">${esc(p.display_name)}</div>
          <div class="scan-card__path">${esc(p.cwd)}</div>
          <div class="scan-card__meta">${p.session_count} Codex Sessions</div>
        </div>
        <div>${isLinked ? '<span class="badge badge-completed">Verbunden</span>' : '<span class="scan-card__add">+</span>'}</div>
      `;

      if (!isLinked) {
        card.addEventListener('click', () => addCodexProjectPopup(card, p.cwd));
      }

      container.appendChild(card);
    });
  } catch {
    container.innerHTML = '<div class="empty text-danger">Codex-Scan fehlgeschlagen.</div>';
  }
}

async function addCodexProjectPopup(card, cwd) {
  card.classList.remove('scan-card--clickable');
  card.classList.add('scan-card--linked');
  const statusDiv = card.querySelector('div:last-child');
  statusDiv.innerHTML = '<div class="spinner spinner--sm"></div>';

  try {
    const { provider, model } = getPopupProviderAndModel();
    const result = await apiFetch('/api/v1/link/quick-add-codex', {
      method: 'POST',
      headers: adminHeaders(),
      body: JSON.stringify({ local_path: cwd, source_tool: 'codex', ai_provider: provider, ai_model: model }),
    });

    card.classList.add('scan-card--success');
    card.classList.remove('scan-card--linked');
    card.innerHTML = `
      <div>
        <div class="scan-card__name scan-card__name--success">${esc(result.project_name)}</div>
        <div class="scan-card__path">${result.sessions_imported || 0} Codex-Sessions importiert</div>
      </div>
      <div><span class="badge badge-completed">Verbunden</span></div>
    `;

    toast(`${result.project_name}: ${result.sessions_imported || 0} Sessions importiert`, 'success');
    loadProjects();
  } catch (err) {
    card.classList.remove('scan-card--linked');
    card.classList.add('scan-card--clickable');
    statusDiv.innerHTML = '<span class="badge badge-failed">Fehler</span>';
  }
}

async function scanLocalProjectsPopup() {
  const container = document.getElementById('popup-project-list');
  if (!container) return;

  try {
    const data = await apiFetch('/api/v1/link/scan', { headers: adminHeaders() });
    const linkedNames = new Set(state.projects.map(p => p.name?.toLowerCase()));

    if (!data.projects || !data.projects.length) {
      container.innerHTML = '<div class="empty">Keine lokalen Projekte gefunden.</div>';
      return;
    }

    container.innerHTML = '';
    data.projects.forEach(p => {
      const isLinked = linkedNames.has(p.display_name?.toLowerCase());
      const card = document.createElement('div');
      card.className = `scan-card ${isLinked ? 'scan-card--linked' : 'scan-card--clickable'}`;

      card.innerHTML = `
        <div>
          <div class="scan-card__name">${esc(p.display_name)}</div>
          <div class="scan-card__path">${esc(p.path_hint)}</div>
          <div class="scan-card__meta">${p.session_count} Claude Sessions</div>
        </div>
        <div>${isLinked ? '<span class="badge badge-completed">Verbunden</span>' : '<span class="scan-card__add">+</span>'}</div>
      `;

      if (!isLinked) {
        card.addEventListener('click', () => addProjectPopup(card, p.dir_name));
      }

      container.appendChild(card);
    });
  } catch {
    container.innerHTML = '<div class="empty text-danger">Scan fehlgeschlagen.</div>';
  }
}

async function addProjectPopup(card, dirName) {
  card.classList.remove('scan-card--clickable');
  card.classList.add('scan-card--linked');
  const statusDiv = card.querySelector('div:last-child');
  statusDiv.innerHTML = '<div class="spinner spinner--sm"></div>';

  try {
    const { provider, model } = getPopupProviderAndModel();
    const result = await apiFetch('/api/v1/link/quick-add', {
      method: 'POST',
      headers: adminHeaders(),
      body: JSON.stringify({ dir_name: dirName, ai_provider: provider, ai_model: model }),
    });

    card.classList.add('scan-card--success');
    card.classList.remove('scan-card--linked');
    card.innerHTML = `
      <div>
        <div class="scan-card__name scan-card__name--success">${esc(result.project_name)}</div>
        <div class="scan-card__path">Hook installiert · ${result.sessions_imported || 0} Sessions importiert</div>
      </div>
      <div><span class="badge badge-completed">Verbunden</span></div>
    `;

    toast(`${result.project_name}: ${result.sessions_imported || 0} Sessions importiert`, 'success');
    loadProjects();
  } catch (err) {
    card.classList.remove('scan-card--linked');
    card.classList.add('scan-card--clickable');
    statusDiv.innerHTML = '<span class="badge badge-failed">Fehler</span>';
  }
}

// ─── Ollama Modelfile-Sync ───────────────────────────────────────
async function syncOllamaModel(projectId) {
  showPopup('Ollama-Modell aktualisieren?', 'Das Custom-Modell wird mit den aktuellen Memories als Wissen aktualisiert. Ollama muss lokal laufen.', async () => {
    try {
      const result = await apiFetch(`/api/v1/projects/${projectId}/ollama/sync`, {
        method: 'POST', headers: adminHeaders(),
      });
      if (result.status === 'success') {
        toast(`Ollama-Modell "${result.model_name}" mit ${result.memories_included} Memories aktualisiert`, 'success');
      } else {
        toast(`Ollama-Sync fehlgeschlagen: ${result.error || 'Unbekannter Fehler'}`, 'error');
      }
    } catch (e) {
      toast('Ollama-Sync fehlgeschlagen: ' + e.message, 'error');
    }
  }, 'info');
}

// ─── Session-Import aus lokalen .jsonl Dateien ─────────────────
async function importSessions(projectId) {
  showPopup('Sessions importieren?', 'Alle lokalen Claude-Transkripte (.jsonl) werden in Dreamline importiert. Das kann einen Moment dauern.', async () => {
    const loadingId = 'import-loading-' + Date.now();
    const closeBtn = `<button class="btn btn-primary btn-sm" onclick="document.getElementById('${loadingId}').remove()" style="margin-top:16px;padding:8px 24px;">OK</button>`;

    document.body.insertAdjacentHTML('beforeend', `
      <div id="${loadingId}" class="modal-overlay" style="z-index:1001;">
        <div id="${loadingId}-box" class="modal-box modal-box--loading">
          <div class="spinner spinner--lg"></div>
          <div class="modal-subtitle">Importiere Sessions...</div>
          <div class="loading-text">Lese lokale .jsonl Transkripte</div>
        </div>
      </div>
    `);

    try {
      const res = await fetch(`/api/v1/link/import-sessions/${projectId}`, {
        method: 'POST', headers: adminHeaders(),
      });
      const result = await res.json();
      const box = document.getElementById(loadingId + '-box');
      if (!box) return;

      if (result.detail) {
        box.innerHTML = `
          <div style="font-size:32px;margin-bottom:12px;">✗</div>
          <div style="font-size:16px;font-weight:600;color:var(--danger);margin-bottom:8px;">Import fehlgeschlagen</div>
          <div style="color:var(--text-muted);font-size:13px;">${esc(result.detail)}</div>
          ${closeBtn}`;
        return;
      }

      box.innerHTML = `
        <div style="font-size:32px;margin-bottom:12px;">${result.imported > 0 ? '✓' : '💤'}</div>
        <div style="font-size:16px;font-weight:600;margin-bottom:12px;">
          ${result.imported > 0 ? result.imported + ' Sessions importiert' : 'Keine neuen Sessions'}
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;font-size:13px;margin-bottom:12px;">
          <div><div style="font-size:20px;font-weight:700;color:var(--success);">${result.imported}</div>Importiert</div>
          <div><div style="font-size:20px;font-weight:700;color:var(--text-muted);">${result.skipped}</div>Übersprungen</div>
          <div><div style="font-size:20px;font-weight:700;color:var(--danger);">${result.errors}</div>Fehler</div>
        </div>
        <div style="font-size:12px;color:var(--text-muted);">${result.total_files} Dateien geprüft</div>
        ${closeBtn}`;
      loadSessions();
    } catch (e) {
      const box = document.getElementById(loadingId + '-box');
      if (box) {
        box.innerHTML = `
          <div style="font-size:32px;margin-bottom:12px;">✗</div>
          <div style="font-size:16px;font-weight:600;color:var(--danger);">Fehler</div>
          <div style="color:var(--text-muted);font-size:13px;margin-top:8px;">${esc(e.message)}</div>
          ${closeBtn}`;
      }
    }
  }, 'info');
}

// ─── Bestätigungs-Popup (ersetzt browser-native confirm()) ───────
function showPopup(title, message, onConfirm, type = 'danger') {
  const colors = {
    danger: { bg: 'var(--danger)', text: '#fff' },
    warning: { bg: 'var(--warning)', text: '#000' },
    info: { bg: 'var(--accent)', text: '#fff' },
  };
  const c = colors[type] || colors.info;
  const confirmLabel = type === 'danger' ? 'Löschen' : 'Bestätigen';

  // Popup als DOM-Element erstellen (nicht als String — sonst gehen Closures verloren)
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });

  const box = document.createElement('div');
  box.className = 'modal-box modal-box--narrow';
  box.innerHTML = `
    <div class="modal-subtitle">${esc(title)}</div>
    <div class="modal-text">${esc(message)}</div>
    <div class="modal-actions modal-actions--center">
      <button class="btn btn-sm btn-ghost popup-cancel">Abbrechen</button>
      <button class="btn btn-sm popup-confirm" style="background:${c.bg};color:${c.text};">${confirmLabel}</button>
    </div>
  `;

  box.querySelector('.popup-cancel').addEventListener('click', () => overlay.remove());
  box.querySelector('.popup-confirm').addEventListener('click', () => {
    overlay.remove();
    onConfirm();
  });

  overlay.appendChild(box);
  document.body.appendChild(overlay);
}

async function deleteProject(id) {
  showPopup(
    'Projekt löschen?',
    'Alle Sessions, Memories und Dreams werden unwiderruflich gelöscht.',
    async () => {
      try {
        await apiFetch(`/api/v1/projects/${id}`, { method: 'DELETE', headers: adminHeaders() });
        toast('Projekt gelöscht', 'success');
        loadProjects();
      } catch { /* toast */ }
    },
    'danger'
  );
}

async function deleteSession(sessionId) {
  showPopup('Session löschen?', 'Diese Session wird unwiderruflich entfernt.', async () => {
    const key = getSelectedProjectKey('sessionProjectSelect');
    if (!key) return;
    try {
      await apiFetch(`/api/v1/sessions/${sessionId}`, { method: 'DELETE', headers: bearerHeaders(key) });
      toast('Session gelöscht', 'success');
      loadSessions();
    } catch { /* toast */ }
  }, 'danger');
}

async function deleteMemory(memoryId) {
  showPopup('Erinnerung löschen?', 'Diese Memory wird unwiderruflich entfernt.', async () => {
    const key = getSelectedProjectKey('memProjectSelect');
    if (!key) return;
    try {
      await apiFetch(`/api/v1/memories/${memoryId}`, { method: 'DELETE', headers: bearerHeaders(key) });
      toast('Erinnerung gelöscht', 'success');
      loadMemories();
    } catch { /* toast */ }
  }, 'danger');
}

async function deleteDream(dreamId) {
  showPopup(
    'Traum rückgängig machen?',
    'Der Traum-Eintrag wird gelöscht und die verarbeiteten Sessions werden zurückgesetzt, damit sie beim nächsten Traum erneut berücksichtigt werden. Die erstellten Erinnerungen bleiben erhalten — lösche sie bei Bedarf einzeln im Erinnerungen-Tab.',
    async () => {
      const key = getSelectedProjectKey('dreamProjectSelect');
      if (!key) return;
      try {
        // Dream löschen UND Sessions zurücksetzen
        await apiFetch(`/api/v1/dreams/${dreamId}?reset_sessions=true`, { method: 'DELETE', headers: bearerHeaders(key) });
        toast('Traum rückgängig gemacht — Sessions werden beim nächsten Traum erneut verarbeitet', 'success');
        loadDreams();
      } catch { /* toast */ }
    },
    'danger'
  );
}

async function triggerDream(apiKey) {
  showPopup('Dream auslösen?', 'Die Konsolidierung wird jetzt gestartet. Das kann bis zu 2 Minuten dauern.', async () => {
    const loadingId = 'dream-loading-' + Date.now();
    const closeBtn = `<button class="btn btn-primary btn-sm" onclick="document.getElementById('${loadingId}').remove()" style="margin-top:16px;padding:8px 24px;">OK</button>`;

    // Loading-Overlay mit Timer und Abbrechen-Button
    let dreamSeconds = 0;
    const timerInterval = setInterval(() => {
      dreamSeconds++;
      const timerEl = document.getElementById(loadingId + '-timer');
      if (timerEl) timerEl.textContent = dreamSeconds + 's';
    }, 1000);

    const dreamController = new AbortController();

    document.body.insertAdjacentHTML('beforeend', `
      <div id="${loadingId}" class="modal-overlay" style="z-index:1001;">
        <div id="${loadingId}-box" class="modal-box modal-box--loading">
          <div class="spinner spinner--lg"></div>
          <div class="modal-subtitle">Dream läuft...</div>
          <div class="loading-text">Konsolidiere Sessions zu Memories</div>
          <div id="${loadingId}-timer" style="font-size:24px;font-weight:700;color:var(--accent);margin-top:12px;">0s</div>
          <button id="${loadingId}-cancel" class="btn btn-sm btn-ghost" style="margin-top:12px;">Seite trotzdem nutzen</button>
        </div>
      </div>
    `);

    // "Seite trotzdem nutzen" entfernt nur das Overlay, der Dream läuft im Backend weiter
    document.getElementById(loadingId + '-cancel')?.addEventListener('click', () => {
      clearInterval(timerInterval);
      document.getElementById(loadingId)?.remove();
      toast('Dream läuft im Hintergrund weiter', 'info');
    });

    let result;
    try {
      const res = await fetch('/api/v1/dreams', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + apiKey },
        signal: dreamController.signal,
      });
      result = await res.json();
      clearInterval(timerInterval);
    } catch (e) {
      clearInterval(timerInterval);
      // Abbruch durch "Seite nutzen" → kein Fehler anzeigen
      if (e.name === 'AbortError') return;
      // Netzwerk-Fehler: Overlay in Fehlermeldung umwandeln
      const box = document.getElementById(loadingId + '-box');
      if (box) {
        box.innerHTML = `
          <div style="font-size:32px;margin-bottom:12px;">✗</div>
          <div style="font-size:16px;font-weight:600;color:var(--danger);margin-bottom:8px;">Dream fehlgeschlagen</div>
          <div style="color:var(--text-muted);font-size:13px;">${esc(e.message || 'Netzwerk-Fehler')}</div>
          ${closeBtn}
        `;
      }
      return;
    }

    // Ergebnis im Overlay anzeigen
    const box = document.getElementById(loadingId + '-box');
    if (!box) return;

    // Fehler-Antwort vom Server (z.B. 401, 500)
    if (result.detail) {
      box.innerHTML = `
        <div style="font-size:32px;margin-bottom:12px;">✗</div>
        <div style="font-size:16px;font-weight:600;color:var(--danger);margin-bottom:8px;">Fehler</div>
        <div style="color:var(--text-muted);font-size:13px;">${esc(result.detail)}</div>
        ${closeBtn}
      `;
      return;
    }

    const d = result.dream;
    const noSessions = d && d.sessions_reviewed === 0;

    box.innerHTML = `
      <div style="font-size:32px;margin-bottom:12px;">${noSessions ? '💤' : '✓'}</div>
      <div style="font-size:16px;font-weight:600;margin-bottom:8px;">
        ${noSessions ? 'Keine neuen Sessions' : 'Dream abgeschlossen'}
      </div>
      ${!noSessions && d ? `
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin:12px 0;font-size:13px;">
          <div><div style="font-size:20px;font-weight:700;color:var(--success);">${d.memories_created}</div>Erstellt</div>
          <div><div style="font-size:20px;font-weight:700;color:var(--info);">${d.memories_updated}</div>Aktualisiert</div>
          <div><div style="font-size:20px;font-weight:700;color:var(--danger);">${d.memories_deleted}</div>Gelöscht</div>
        </div>
        <div style="font-size:12px;color:var(--text-muted);">${d.sessions_reviewed} Sessions | ${d.duration_ms}ms | ${d.tokens_used} Tokens</div>
      ` : `
        <div style="color:var(--text-muted);font-size:13px;">Alle Sessions sind bereits konsolidiert.</div>
      `}
      ${closeBtn}
    `;
    loadDreams();
  }, 'info');
}

// Projekt-Selektoren füllen
function populateProjectSelectors() {
  const selectors = ['memProjectSelect', 'dreamProjectSelect', 'sessionProjectSelect'];
  selectors.forEach(id => {
    const sel = document.getElementById(id);
    const current = sel.value;
    sel.innerHTML = '<option value="">-- Projekt wählen --</option>' +
      state.projects.map(p => `<option value="${p.id}" ${p.id === current ? 'selected' : ''}>${esc(p.name)}</option>`).join('');
  });
}

// Übersicht laden
async function refreshOverview() {
  if (!state.adminKey) {
    document.getElementById('statProjects').textContent = '?';
    return;
  }
  try {
    await loadProjects();
    document.getElementById('statProjects').textContent = state.projects.length;

    // Stats des ersten Projekts laden falls vorhanden
    if (state.projects.length > 0) {
      let totalSessions = 0, totalMemories = 0, totalDreams = 0;
      let lastDreams = [];
      let memoryTypes = {};

      // Alle Projekte parallel abfragen statt sequentiell (N+1 → 2 Batches)
      const statsResults = await Promise.allSettled(
        state.projects.map(p => apiFetch('/api/v1/stats', { headers: bearerHeaders(p.api_key) }))
      );
      const dreamsResults = await Promise.allSettled(
        state.projects.map(p => apiFetch('/api/v1/dreams', { headers: bearerHeaders(p.api_key) }))
      );

      statsResults.forEach((r) => {
        if (r.status !== 'fulfilled') return;
        const stats = r.value;
        totalSessions += stats.total_sessions || 0;
        totalMemories += stats.total_memories || 0;
        totalDreams += stats.total_dreams || 0;
        if (stats.memories_by_type) {
          stats.memories_by_type.forEach(t => {
            memoryTypes[t.memory_type] = (memoryTypes[t.memory_type] || 0) + t.count;
          });
        }
      });

      dreamsResults.forEach((r, i) => {
        if (r.status !== 'fulfilled') return;
        r.value.forEach(d => d._project_name = state.projects[i].name);
        lastDreams = lastDreams.concat(r.value);
      });

      document.getElementById('statSessions').textContent = totalSessions;
      document.getElementById('statMemories').textContent = totalMemories;
      document.getElementById('statDreams').textContent = totalDreams;

      // Letzte 5 Dreams
      lastDreams.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
      const recent = lastDreams.slice(0, 5);
      if (recent.length) {
        document.getElementById('recentDreams').innerHTML = `
          <table>
            <thead><tr><th>Datum</th><th>Projekt</th><th>Status</th><th>Erstellt</th><th>Aktualisiert</th></tr></thead>
            <tbody>${recent.map(d => `
              <tr>
                <td style="font-size:13px;">${formatDate(d.created_at)}</td>
                <td style="font-size:13px;">${esc(d._project_name || '-')}</td>
                <td>${statusBadge(d.status)}</td>
                <td>${d.memories_created}</td>
                <td>${d.memories_updated}</td>
              </tr>
            `).join('')}</tbody>
          </table>`;
      }

      // Memory-Typ-Verteilung
      const typeEntries = Object.entries(memoryTypes);
      if (typeEntries.length) {
        const max = Math.max(...typeEntries.map(e => e[1]), 1);
        const colors = { user: 'var(--info)', feedback: 'var(--warning)', project: 'var(--success)', reference: 'var(--purple)' };
        document.getElementById('memoryChart').innerHTML = `
          <div class="bar-chart">
            ${typeEntries.map(([type, count]) => `
              <div class="bar-row">
                <span class="bar-label">${type}</span>
                <div class="bar-track">
                  <div class="bar-fill" style="width:${(count/max)*100}%; background:${colors[type] || 'var(--accent)'};">${count}</div>
                </div>
              </div>
            `).join('')}
          </div>`;
      }
    }
  } catch { /* Fehler bereits via toast */ }
}

// Memories laden
async function loadMemories() {
  const key = getSelectedProjectKey('memProjectSelect');
  const container = document.getElementById('memoriesList');
  if (!key) { container.innerHTML = '<div class="empty">Bitte ein Projekt auswählen.</div>'; return; }

  container.innerHTML = '<div class="loading"><div class="spinner"></div> Lade Erinnerungen...</div>';
  try {
    state.allMemories = await apiFetch('/api/v1/memories', { headers: bearerHeaders(key) });
    filterMemories();
  } catch {
    container.innerHTML = '<div class="empty">Fehler beim Laden der Erinnerungen.</div>';
  }
}

function filterMemories() {
  const typeFilter = document.getElementById('memTypeFilter').value;
  const filtered = typeFilter
    ? state.allMemories.filter(m => m.memory_type === typeFilter)
    : state.allMemories;

  const container = document.getElementById('memoriesList');
  if (!filtered.length) {
    container.innerHTML = '<div class="empty">Keine Erinnerungen gefunden.</div>';
    return;
  }

  container.innerHTML = filtered.map(m => `
    <div class="card" style="margin-bottom:12px;">
      <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:8px;">
        <div style="display:flex; align-items:center; gap:10px;">
          <strong class="mono">${esc(m.key)}</strong>
          ${typeBadge(m.memory_type)}
        </div>
        <div style="display:flex; align-items:center; gap:8px;">
          <span style="font-size:12px; color:var(--text-muted);">Quellen: ${m.source_count}</span>
          <button class="btn btn-danger btn-sm" onclick="deleteMemory('${m.id}')" title="Erinnerung löschen">&times;</button>
        </div>
      </div>
      <div style="margin-bottom:10px;">
        <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
          <span style="font-size:12px; color:var(--text-muted);">Konfidenz: ${(m.confidence * 100).toFixed(0)}%</span>
        </div>
        <div class="confidence-bar">
          <div class="confidence-fill" style="width:${m.confidence * 100}%; background:${confidenceColor(m.confidence)};"></div>
        </div>
      </div>
      <div style="font-size:14px; line-height:1.6; white-space:pre-wrap;">${esc(m.content)}</div>
      <div style="margin-top:8px; font-size:11px; color:var(--text-muted);">
        Erstellt: ${formatDate(m.created_at)} | Aktualisiert: ${formatDate(m.updated_at)} | Letzte Konsolidierung: ${formatDate(m.last_consolidated_at)}
      </div>
    </div>
  `).join('');
}

// Dream-Status laden (wie Claude Code DreamTask)
async function loadDreamStatus(key) {
  const statusDiv = document.getElementById('dreamStatus');
  try {
    const status = await apiFetch('/api/v1/dreams/status', { headers: bearerHeaders(key) });
    const isRunning = status.is_running;
    const pending = status.pending_sessions;
    const last = status.last_dream;

    statusDiv.innerHTML = `
      <div style="display:flex; gap:12px; margin-bottom:16px; flex-wrap:wrap;">
        <div style="padding:12px 16px; background:var(--card); border-radius:var(--radius); border:1px solid ${isRunning ? 'var(--accent)' : 'var(--border)'}; flex:1; min-width:150px;">
          <div style="font-size:12px; color:var(--text-muted);">Status</div>
          <div style="font-size:15px; font-weight:600; color:${isRunning ? 'var(--accent)' : 'var(--success)'};">
            ${isRunning ? '<span class="spinner" style="width:14px;height:14px;display:inline-block;vertical-align:middle;margin-right:6px;"></span>Dream läuft...' : 'Bereit'}
          </div>
        </div>
        <div style="padding:12px 16px; background:var(--card); border-radius:var(--radius); border:1px solid var(--border); flex:1; min-width:150px;">
          <div style="font-size:12px; color:var(--text-muted);">Wartende Sessions</div>
          <div style="font-size:15px; font-weight:600; color:${pending > 0 ? 'var(--warning)' : 'var(--text-muted)'};">${pending}</div>
        </div>
        ${last ? `
        <div style="padding:12px 16px; background:var(--card); border-radius:var(--radius); border:1px solid var(--border); flex:1; min-width:150px;">
          <div style="font-size:12px; color:var(--text-muted);">Letzter Dream</div>
          <div style="font-size:13px;">${formatDate(last.created_at)} · ${last.duration_ms}ms</div>
          <div style="font-size:12px; color:var(--text-muted);">${last.memories_created} neu, ${last.memories_updated} aktualisiert</div>
        </div>` : ''}
      </div>`;

    // Auto-Refresh wenn Dream läuft
    if (isRunning) {
      setTimeout(() => loadDreamStatus(key), 5000);
    }
  } catch {
    statusDiv.innerHTML = '';
  }
}

// Dreams laden
async function loadDreams() {
  const key = getSelectedProjectKey('dreamProjectSelect');
  const container = document.getElementById('dreamsList');
  if (!key) { container.innerHTML = '<div class="empty">Bitte ein Projekt auswählen.</div>'; return; }

  // Status laden
  loadDreamStatus(key);

  container.innerHTML = '<div class="loading"><div class="spinner"></div> Lade Träume...</div>';
  try {
    const dreams = await apiFetch('/api/v1/dreams', { headers: bearerHeaders(key) });
    if (!dreams.length) {
      container.innerHTML = '<div class="empty">Noch keine Träume vorhanden.</div>';
      return;
    }
    container.innerHTML = dreams.map(d => `
      <div class="card" style="margin-bottom:12px;">
        <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:12px;">
          <div style="display:flex; align-items:center; gap:12px;">
            <span style="font-size:15px; font-weight:600;">${formatDate(d.created_at)}</span>
            ${statusBadge(d.status)}
          </div>
          <div style="display:flex; align-items:center; gap:8px;">
            <span style="font-size:12px; color:var(--text-muted);">${d.duration_ms}ms | ${d.tokens_used} Tokens</span>
            <button class="btn btn-danger btn-sm" onclick="deleteDream('${d.id}')" title="Dream löschen">&times;</button>
          </div>
        </div>
        <div style="display:grid; grid-template-columns:repeat(4,auto); gap:20px; margin-bottom:12px;">
          <div>
            <div style="font-size:11px; color:var(--text-muted);">Sessions</div>
            <div style="font-size:18px; font-weight:600;">${d.sessions_reviewed}</div>
          </div>
          <div>
            <div style="font-size:11px; color:var(--text-muted);">Erstellt</div>
            <div style="font-size:18px; font-weight:600; color:var(--success);">${d.memories_created}</div>
          </div>
          <div>
            <div style="font-size:11px; color:var(--text-muted);">Aktualisiert</div>
            <div style="font-size:18px; font-weight:600; color:var(--warning);">${d.memories_updated}</div>
          </div>
          <div>
            <div style="font-size:11px; color:var(--text-muted);">Gelöscht</div>
            <div style="font-size:18px; font-weight:600; color:var(--danger);">${d.memories_deleted}</div>
          </div>
        </div>
        ${d.summary ? `<div style="font-size:13px; line-height:1.6; color:var(--text-muted); white-space:pre-wrap;">${esc(d.summary)}</div>` : ''}
      </div>
    `).join('');
  } catch {
    container.innerHTML = '<div class="empty">Fehler beim Laden der Träume.</div>';
  }
}

// Sessions laden
async function loadSessions() {
  const key = getSelectedProjectKey('sessionProjectSelect');
  const container = document.getElementById('sessionsList');
  if (!key) { container.innerHTML = '<div class="empty">Bitte ein Projekt auswählen.</div>'; return; }

  container.innerHTML = '<div class="loading"><div class="spinner"></div> Lade Sitzungen...</div>';
  try {
    // Echte Sessions und Stats parallel laden
    const [sessions, stats] = await Promise.all([
      apiFetch(`/api/v1/sessions?limit=${state.sessionsPerPage}&offset=${state.sessionPage * state.sessionsPerPage}`, { headers: bearerHeaders(key) }),
      apiFetch('/api/v1/stats', { headers: bearerHeaders(key) }),
    ]);

    // Statistik-Karten oben
    let html = `
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">Gesamt</div>
          <div class="stat-value">${stats.total_sessions}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Konsolidiert</div>
          <div class="stat-value" style="color:var(--success);">${stats.sessions_consolidated}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Nicht konsolidiert</div>
          <div class="stat-value" style="color:var(--warning);">${stats.sessions_unconsolidated}</div>
        </div>
      </div>`;

    // Echte Session-Liste
    if (!sessions.length) {
      html += '<div class="empty" style="margin-top:16px;">Noch keine Sitzungen vorhanden.</div>';
    } else {
      html += `
        <table class="table" style="margin-top:16px;">
          <thead><tr>
            <th>Zeitpunkt</th>
            <th>Vorschau</th>
            <th>Nachrichten</th>
            <th>Ergebnis</th>
            <th>Status</th>
            <th>Aktionen</th>
          </tr></thead>
          <tbody>
            ${sessions.map(s => `
              <tr>
                <td style="white-space:nowrap;">${formatDate(s.created_at)}</td>
                <td style="max-width:350px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${esc(s.preview)}">${esc(s.preview) || '<span style="color:var(--text-muted);">–</span>'}</td>
                <td style="text-align:center;">${s.message_count}</td>
                <td>${s.outcome ? outcomeBadge(s.outcome) : '<span style="color:var(--text-muted);">–</span>'}</td>
                <td>${s.is_consolidated
                  ? '<span class="badge badge-completed">Konsolidiert</span>'
                  : '<span class="badge badge-skipped">Offen</span>'}</td>
                <td style="white-space:nowrap;">
                  <button class="btn btn-ghost btn-sm" onclick="viewSession('${s.id}')">Details</button>
                  <button class="btn btn-danger btn-sm" onclick="deleteSession('${s.id}')" title="Session löschen" style="margin-left:4px;">&times;</button>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>`;

      // Paginierung
      const totalPages = Math.ceil(stats.total_sessions / state.sessionsPerPage);
      if (totalPages > 1) {
        html += `<div style="display:flex; align-items:center; justify-content:center; gap:12px; margin-top:16px;">
          <button class="btn btn-ghost btn-sm" onclick="prevSessionPage()" ${state.sessionPage <= 0 ? 'disabled' : ''}>← Zurück</button>
          <span style="font-size:13px; color:var(--text-muted);">Seite ${state.sessionPage + 1} von ${totalPages}</span>
          <button class="btn btn-ghost btn-sm" onclick="nextSessionPage(${totalPages})" ${state.sessionPage >= totalPages - 1 ? 'disabled' : ''}>Weiter →</button>
        </div>`;
      }
    }

    container.innerHTML = html;
  } catch {
    container.innerHTML = '<div class="empty">Fehler beim Laden der Sitzungen.</div>';
  }
}

function prevSessionPage() {
  if (state.sessionPage > 0) { state.sessionPage--; loadSessions(); }
}

function nextSessionPage(totalPages) {
  if (state.sessionPage < totalPages - 1) { state.sessionPage++; loadSessions(); }
}

// Session-Details in einem Modal anzeigen
async function viewSession(sessionId) {
  const key = getSelectedProjectKey('sessionProjectSelect');
  if (!key) return;

  try {
    const session = await apiFetch(`/api/v1/sessions/${sessionId}`, { headers: bearerHeaders(key) });

    // Modal erstellen
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    const modal = document.createElement('div');
    modal.className = 'modal-box modal-box--wide';
    modal.style.maxWidth = '800px';
    modal.style.maxHeight = '80vh';
    modal.style.overflowY = 'auto';

    let messagesHtml = session.messages.map(m => `
      <div style="margin-bottom:12px; padding:12px; border-radius:10px; background:${m.role === 'user' ? 'rgba(37,99,235,0.1)' : 'rgba(16,185,129,0.1)'}; border-left:3px solid ${m.role === 'user' ? 'var(--accent)' : 'var(--success)'};">
        <div style="font-size:12px; font-weight:600; color:${m.role === 'user' ? 'var(--accent)' : 'var(--success)'}; margin-bottom:6px; text-transform:uppercase;">${m.role}</div>
        <div style="font-size:14px; line-height:1.6; white-space:pre-wrap;">${esc(m.content.length > 2000 ? m.content.slice(0, 2000) + '...' : m.content)}</div>
      </div>
    `).join('');

    modal.innerHTML = `
      <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:20px;">
        <div>
          <h3 style="font-size:18px; font-weight:600;">Session-Details</h3>
          <div style="font-size:13px; color:var(--text-muted); margin-top:4px;">
            ${formatDate(session.created_at)} · ${session.message_count} Nachrichten
            ${session.outcome ? ' · ' + session.outcome : ''}
            · ${session.is_consolidated ? 'Konsolidiert' : 'Nicht konsolidiert'}
          </div>
        </div>
        <button class="btn btn-ghost btn-sm" onclick="this.closest('[style*=fixed]').remove()">✕ Schließen</button>
      </div>
      <div>${messagesHtml}</div>
    `;

    overlay.appendChild(modal);
    document.body.appendChild(overlay);
  } catch { /* Fehler bereits via toast */ }
}

// Einstellungen
function saveAdminKey() {
  state.adminKey = document.getElementById('adminKeyInput').value.trim();
  sessionStorage.setItem('dreamline_admin_key', state.adminKey);
  toast('Admin-Key gespeichert', 'success');
  loadProjects();
}

// Automatischer Login über Claude CLI
async function autoLogin() {
  if (state.adminKey) return true; // Bereits eingeloggt
  try {
    const res = await fetch('/auth/auto-login');
    if (res.ok) {
      const data = await res.json();
      if (data.success && data.admin_key) {
        state.adminKey = data.admin_key;
        sessionStorage.setItem('dreamline_admin_key', state.adminKey);
        document.getElementById('adminKeyInput').value = state.adminKey;
        toast('Automatisch über Claude-Abo angemeldet', 'success');
        return true;
      }
    }
  } catch { /* Kein Auto-Login möglich */ }
  return false;
}

// Initialisierung
// Escape-Key schliesst offene Modals/Popups
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const overlays = document.querySelectorAll('.modal-overlay, [style*="position:fixed"][style*="inset:0"]');
    if (overlays.length > 0) {
      overlays[overlays.length - 1].remove();
    }
  }
});

document.addEventListener('DOMContentLoaded', async () => {
  // Admin-Key aus localStorage laden
  document.getElementById('adminKeyInput').value = state.adminKey;

  // Auto-Login wenn kein Key vorhanden
  await autoLogin();

  // Health-Check starten
  checkHealth();
  setInterval(checkHealth, 30000);

  // Tab initialisieren
  initTab();

  // Automatische Aktualisierung alle 30 Sekunden
  setInterval(() => {
    const activeTab = document.querySelector('.nav-btn.active');
    if (activeTab && activeTab.dataset.tab === 'uebersicht') {
      refreshOverview();
    }
  }, 30000);
});

// Hash-Änderung abfangen
window.addEventListener('hashchange', () => {
  const hash = window.location.hash.replace('#', '') || 'uebersicht';
  switchTab(hash);
});
