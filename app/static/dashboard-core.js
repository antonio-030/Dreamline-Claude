// ─── Globaler Zustand ────────────────────────────────────────────
const state = {
  adminKey: window.__DREAMLINE_ADMIN_KEY__ || localStorage.getItem('dreamline_admin_key') || '',
  projects: [],
  allMemories: [],
  sessionPage: 0,
  sessionsPerPage: 20,
  memoryPage: 0,
  memoriesPerPage: 20,
  dreamPage: 0,
  dreamsPerPage: 20,
  allDreams: [],
  memorySearch: '',
};

// Interval-Tracking (Cleanup bei Tab-Wechsel + Page Visibility)
let _healthInterval = null;
let _overviewInterval = null;
let _dreamStatusTimeout = null;

// ─── Hilfsfunktionen ─────────────────────────────────────────────

/** HTML-Escaping per String-Replace (schneller als DOM-basiert) */
const _escMap = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
function esc(str) {
  if (!str) return '';
  return String(str).replace(/[&<>"']/g, c => _escMap[c]);
}

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
  return `<span class="badge badge-${map[status] || 'skipped'}">${esc(status)}</span>`;
}

function typeBadge(type) {
  const map = { user: 'user', feedback: 'feedback', project: 'project', reference: 'reference' };
  return `<span class="badge badge-${map[type] || 'project'}">${esc(type)}</span>`;
}

function outcomeBadge(outcome) {
  if (!outcome) return '<span class="badge badge-neutral">-</span>';
  return `<span class="badge badge-neutral">${esc(outcome)}</span>`;
}

function sourceToolLabel(tool) {
  const labels = { 'claude': 'Claude Code', 'codex': 'OpenAI Codex', 'both': 'Claude + Codex' };
  return labels[tool] || tool || 'Claude Code';
}

// ─── API-Aufrufe ─────────────────────────────────────────────────
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

// ─── Tab-Navigation ──────────────────────────────────────────────
function switchTab(tab) {
  if (_dreamStatusTimeout) { clearTimeout(_dreamStatusTimeout); _dreamStatusTimeout = null; }

  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelector(`[data-tab="${tab}"]`).classList.add('active');
  document.getElementById(`tab-${tab}`).classList.add('active');
  window.location.hash = tab;

  if (tab === 'uebersicht') refreshOverview();
  if (tab === 'projekte') loadProjects();
  if (tab === 'memories') loadMemories();
  if (tab === 'dreams') loadDreams();
  if (tab === 'sessions') loadSessions();
  if (tab === 'settings') loadSettings();
}

document.querySelectorAll('.nav-btn').forEach(btn => {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});

function initTab() {
  const hash = window.location.hash.replace('#', '') || 'uebersicht';
  switchTab(hash);
}

// ─── Health-Check ────────────────────────────────────────────────
async function checkHealth() {
  try {
    const res = await fetch('/health');
    document.getElementById('healthDot').className = res.ok ? 'status-dot' : 'status-dot offline';
  } catch {
    document.getElementById('healthDot').className = 'status-dot offline';
  }
}

// ─── Projekt-Selektoren fuellen ──────────────────────────────────
function populateProjectSelectors() {
  const selectors = ['memProjectSelect', 'dreamProjectSelect', 'sessionProjectSelect'];
  const firstId = state.projects.length ? state.projects[0].id : '';
  selectors.forEach(id => {
    const sel = document.getElementById(id);
    const current = sel.value || firstId;
    sel.innerHTML = '<option value="">-- Projekt wählen --</option>' +
      state.projects.map(p => `<option value="${p.id}" ${p.id === current ? 'selected' : ''}>${esc(p.name)}</option>`).join('');
  });
}

// ─── Bestaetigungs-Popup ─────────────────────────────────────────
function showPopup(title, message, onConfirm, type = 'danger') {
  const colors = {
    danger: { bg: 'var(--danger)', text: '#fff' },
    warning: { bg: 'var(--warning)', text: '#000' },
    info: { bg: 'var(--accent)', text: '#fff' },
  };
  const c = colors[type] || colors.info;
  const confirmLabel = type === 'danger' ? 'Löschen' : 'Bestätigen';

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

// ─── Page Visibility — Polling pausieren wenn Tab unsichtbar ─────
function _startPolling() {
  if (!_healthInterval) {
    checkHealth();
    _healthInterval = setInterval(checkHealth, 30000);
  }
  if (!_overviewInterval) {
    _overviewInterval = setInterval(() => {
      const activeTab = document.querySelector('.nav-btn.active');
      if (activeTab && activeTab.dataset.tab === 'uebersicht') {
        refreshOverview();
      }
    }, 30000);
  }
}

function _stopPolling() {
  if (_healthInterval) { clearInterval(_healthInterval); _healthInterval = null; }
  if (_overviewInterval) { clearInterval(_overviewInterval); _overviewInterval = null; }
  if (_dreamStatusTimeout) { clearTimeout(_dreamStatusTimeout); _dreamStatusTimeout = null; }
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    _stopPolling();
  } else {
    _startPolling();
    checkHealth();
  }
});

// ─── Escape-Key schliesst offene Modals ──────────────────────────
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const overlays = document.querySelectorAll('.modal-overlay, [style*="position:fixed"][style*="inset:0"]');
    if (overlays.length > 0) {
      overlays[overlays.length - 1].remove();
    }
  }
});

// ─── Initialisierung ─────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  document.getElementById('adminKeyInput').value = state.adminKey;

  _startPolling();

  if (state.adminKey) {
    await loadProjects();
  }

  initTab();
});

window.addEventListener('hashchange', () => {
  const hash = window.location.hash.replace('#', '') || 'uebersicht';
  switchTab(hash);
});
