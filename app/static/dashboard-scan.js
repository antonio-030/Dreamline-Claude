// ─── Neues Projekt Popup ─────────────────────────────────────────
function openNewProjectPopup() {
  const popupId = 'new-project-popup';
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

// ─── Claude-Projekte scannen ─────────────────────────────────────
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

// ─── Codex-Projekte scannen ──────────────────────────────────────
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

// ─── Session-Import aus lokalen .jsonl Dateien ───────────────────
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
