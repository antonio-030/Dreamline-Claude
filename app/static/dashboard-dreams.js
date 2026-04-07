// ─── Dream-Status ────────────────────────────────────────────────
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

    if (isRunning) {
      _dreamStatusTimeout = setTimeout(() => loadDreamStatus(key), 5000);
    }
  } catch {
    statusDiv.innerHTML = '';
  }
}

// ─── Dreams laden und rendern ────────────────────────────────────
async function loadDreams() {
  const key = getSelectedProjectKey('dreamProjectSelect');
  const container = document.getElementById('dreamsList');
  if (!key) { container.innerHTML = '<div class="empty">Bitte ein Projekt auswählen.</div>'; return; }

  loadDreamStatus(key);

  container.innerHTML = '<div class="loading"><div class="spinner"></div> Lade Träume...</div>';
  try {
    state.allDreams = await apiFetch('/api/v1/dreams', { headers: bearerHeaders(key) });
    renderDreams();
  } catch {
    container.innerHTML = '<div class="empty">Fehler beim Laden der Träume.</div>';
  }
}

function renderDreams() {
  const container = document.getElementById('dreamsList');
  const dreams = state.allDreams;
  if (!dreams.length) {
    container.innerHTML = '<div class="empty">Noch keine Träume vorhanden. Starte einen manuell per Dream-Button im Projekte-Tab oder warte auf den automatischen Lauf.</div>';
    return;
  }

  const totalPages = Math.ceil(dreams.length / state.dreamsPerPage);
  if (state.dreamPage >= totalPages) state.dreamPage = totalPages - 1;
  const page = dreams.slice(state.dreamPage * state.dreamsPerPage, (state.dreamPage + 1) * state.dreamsPerPage);

  let html = page.map(d => `
    <div class="card" style="margin-bottom:12px; ${d.status === 'failed' ? 'border-left:3px solid var(--danger);' : ''}">
      <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:12px;">
        <div style="display:flex; align-items:center; gap:12px;">
          <span style="font-size:15px; font-weight:600;">${formatDate(d.created_at)}</span>
          ${statusBadge(d.status)}
          ${d.ai_provider_used ? `<span style="font-size:11px; color:var(--text-muted); background:var(--bg); padding:2px 8px; border-radius:4px;">${esc(d.ai_provider_used)}</span>` : ''}
        </div>
        <div style="display:flex; align-items:center; gap:8px;">
          <span style="font-size:12px; color:var(--text-muted);">${d.duration_ms}ms | ${d.tokens_used} Tokens</span>
          <button class="btn btn-danger btn-sm" onclick="deleteDream('${d.id}')" title="Dream löschen">&times;</button>
        </div>
      </div>
      ${d.status === 'failed' && d.error_detail ? `
        <div style="background:rgba(239,68,68,0.1); border:1px solid var(--danger); border-radius:8px; padding:12px; margin-bottom:12px;">
          <div style="font-size:12px; font-weight:600; color:var(--danger); margin-bottom:4px;">Provider-Fehler${d.ai_provider_used ? ' (' + esc(d.ai_provider_used) + ')' : ''}:</div>
          <div style="font-size:13px; color:var(--text); white-space:pre-wrap; word-break:break-word;">${esc(d.error_detail)}</div>
        </div>
      ` : ''}
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

  if (totalPages > 1) {
    html += `<div style="display:flex; align-items:center; justify-content:center; gap:12px; margin-top:16px;">
      <button class="btn btn-ghost btn-sm" onclick="state.dreamPage--; renderDreams();" ${state.dreamPage <= 0 ? 'disabled' : ''}>← Zurück</button>
      <span style="font-size:13px; color:var(--text-muted);">Seite ${state.dreamPage + 1} von ${totalPages} (${dreams.length} Träume)</span>
      <button class="btn btn-ghost btn-sm" onclick="state.dreamPage++; renderDreams();" ${state.dreamPage >= totalPages - 1 ? 'disabled' : ''}>Weiter →</button>
    </div>`;
  }

  container.innerHTML = html;
}

async function deleteDream(dreamId) {
  showPopup(
    'Traum rückgängig machen?',
    'Der Traum-Eintrag wird gelöscht und die verarbeiteten Sessions werden zurückgesetzt, damit sie beim nächsten Traum erneut berücksichtigt werden. Die erstellten Erinnerungen bleiben erhalten — lösche sie bei Bedarf einzeln im Erinnerungen-Tab.',
    async () => {
      const key = getSelectedProjectKey('dreamProjectSelect');
      if (!key) return;
      try {
        await apiFetch(`/api/v1/dreams/${dreamId}?reset_sessions=true`, { method: 'DELETE', headers: bearerHeaders(key) });
        toast('Traum rückgängig gemacht — Sessions werden beim nächsten Traum erneut verarbeitet', 'success');
        loadDreams();
      } catch (e) { toast('Fehler: ' + e.message, 'error'); }
    },
    'danger'
  );
}

async function triggerDream(apiKey) {
  showPopup('Dream auslösen?', 'Die Konsolidierung wird jetzt gestartet. Das kann bis zu 2 Minuten dauern.', async () => {
    const loadingId = 'dream-loading-' + Date.now();
    const closeBtn = `<button class="btn btn-primary btn-sm" onclick="document.getElementById('${loadingId}').remove()" style="margin-top:16px;padding:8px 24px;">OK</button>`;

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
      if (e.name === 'AbortError') return;
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

    const box = document.getElementById(loadingId + '-box');
    if (!box) return;

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

// ─── Sessions ────────────────────────────────────────────────────
async function loadSessions() {
  const key = getSelectedProjectKey('sessionProjectSelect');
  const container = document.getElementById('sessionsList');
  if (!key) { container.innerHTML = '<div class="empty">Bitte ein Projekt auswählen.</div>'; return; }

  container.innerHTML = '<div class="loading"><div class="spinner"></div> Lade Sitzungen...</div>';
  try {
    const [sessionsResult, statsResult] = await Promise.allSettled([
      apiFetch(`/api/v1/sessions?limit=${state.sessionsPerPage}&offset=${state.sessionPage * state.sessionsPerPage}`, { headers: bearerHeaders(key) }),
      apiFetch('/api/v1/stats', { headers: bearerHeaders(key) }),
    ]);
    const sessions = sessionsResult.status === 'fulfilled' ? sessionsResult.value : [];
    const stats = statsResult.status === 'fulfilled' ? statsResult.value : { total_sessions: 0, sessions_consolidated: 0, sessions_unconsolidated: 0 };

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

    if (!sessions.length) {
      html += '<div class="empty" style="margin-top:16px;">Noch keine Sitzungen vorhanden. Starte eine Konversation mit Claude Code oder importiere Sessions im Projekte-Tab.</div>';
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

async function viewSession(sessionId) {
  const key = getSelectedProjectKey('sessionProjectSelect');
  if (!key) return;

  try {
    const session = await apiFetch(`/api/v1/sessions/${sessionId}`, { headers: bearerHeaders(key) });

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
        <button class="btn btn-ghost btn-sm" onclick="this.closest('.modal-overlay').remove()">✕ Schließen</button>
      </div>
      <div>${messagesHtml}</div>
    `;

    overlay.appendChild(modal);
    document.body.appendChild(overlay);
  } catch { /* Fehler bereits via toast */ }
}

async function deleteSession(sessionId) {
  showPopup('Session löschen?', 'Diese Session wird unwiderruflich entfernt.', async () => {
    const key = getSelectedProjectKey('sessionProjectSelect');
    if (!key) return;
    try {
      await apiFetch(`/api/v1/sessions/${sessionId}`, { method: 'DELETE', headers: bearerHeaders(key) });
      toast('Session gelöscht', 'success');
      loadSessions();
    } catch (e) { toast('Fehler beim Löschen: ' + e.message, 'error'); }
  }, 'danger');
}
