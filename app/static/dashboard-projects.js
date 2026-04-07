// ─── Projekte laden (Admin-Key) ──────────────────────────────────
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

function toggleKeyReveal(el, fullKey) {
  const display = el.querySelector('.key-display');
  if (display.textContent === fullKey) {
    display.textContent = maskKey(fullKey);
  } else {
    display.textContent = fullKey;
    navigator.clipboard.writeText(fullKey).then(() => toast('API-Key kopiert', 'success'));
  }
}

// ─── Projekt bearbeiten ──────────────────────────────────────────
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
      headers: adminHeaders(),
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
  } catch (e) {
    toast('Fehler: ' + e.message, 'error');
  }
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
      } catch (e) { toast('Fehler beim Löschen: ' + e.message, 'error'); }
    },
    'danger'
  );
}
