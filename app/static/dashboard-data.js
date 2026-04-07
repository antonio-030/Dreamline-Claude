// ─── Uebersicht laden ────────────────────────────────────────────
async function refreshOverview() {
  if (!state.adminKey) {
    document.getElementById('statProjects').textContent = '?';
    return;
  }
  try {
    await loadProjects();
    document.getElementById('statProjects').textContent = state.projects.length;

    if (state.projects.length > 0) {
      let totalSessions = 0, totalMemories = 0, totalDreams = 0;
      let lastDreams = [];
      let memoryTypes = {};

      // Alle Stats und Dreams in einem einzigen parallelen Batch laden
      const allRequests = state.projects.flatMap(p => [
        apiFetch('/api/v1/stats', { headers: bearerHeaders(p.api_key) }),
        apiFetch('/api/v1/dreams', { headers: bearerHeaders(p.api_key) }),
      ]);
      const allResults = await Promise.allSettled(allRequests);

      // Ergebnisse auswerten: gerade Indizes = Stats, ungerade = Dreams
      state.projects.forEach((p, i) => {
        const statsResult = allResults[i * 2];
        const dreamsResult = allResults[i * 2 + 1];

        if (statsResult.status === 'fulfilled') {
          const stats = statsResult.value;
          totalSessions += stats.total_sessions || 0;
          totalMemories += stats.total_memories || 0;
          totalDreams += stats.total_dreams || 0;
          if (stats.memories_by_type) {
            stats.memories_by_type.forEach(t => {
              memoryTypes[t.memory_type] = (memoryTypes[t.memory_type] || 0) + t.count;
            });
          }
        }

        if (dreamsResult.status === 'fulfilled') {
          dreamsResult.value.forEach(d => d._project_name = p.name);
          lastDreams = lastDreams.concat(dreamsResult.value);
        }
      });

      document.getElementById('statSessions').textContent = totalSessions;
      document.getElementById('statMemories').textContent = totalMemories;
      document.getElementById('statDreams').textContent = totalDreams;

      // Sub-Labels mit Kontext fuellen
      const totalUnconsolidated = state.projects.reduce((sum, _p, i) => {
        const r = allResults[i * 2];
        return sum + (r.status === 'fulfilled' ? (r.value.sessions_unconsolidated || 0) : 0);
      }, 0);
      document.getElementById('statSessionsSub').textContent = totalUnconsolidated > 0 ? `${totalUnconsolidated} warten auf Dream` : 'Alle konsolidiert';
      const typeCount = Object.keys(memoryTypes).length;
      document.getElementById('statMemoriesSub').textContent = typeCount > 0 ? `${typeCount} Typen` : '';
      const failedDreams = state.projects.reduce((sum, _p, i) => {
        const r = allResults[i * 2 + 1];
        return sum + (r.status === 'fulfilled' ? r.value.filter(d => d.status === 'failed').length : 0);
      }, 0);
      document.getElementById('statDreamsSub').textContent = failedDreams > 0 ? `${failedDreams} fehlgeschlagen` : totalDreams > 0 ? 'Alle erfolgreich' : '';

      // Letzte 5 Dreams
      lastDreams.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
      const recent = lastDreams.slice(0, 5);
      if (recent.length) {
        document.getElementById('recentDreams').innerHTML = `
          <table>
            <thead><tr><th>Datum</th><th>Projekt</th><th>Provider</th><th>Status</th><th>Erstellt</th><th>Aktualisiert</th></tr></thead>
            <tbody>${recent.map(d => `
              <tr${d.status === 'failed' ? ' style="background:rgba(239,68,68,0.05);"' : ''}>
                <td style="font-size:13px;">${formatDate(d.created_at)}</td>
                <td style="font-size:13px;">${esc(d._project_name || '-')}</td>
                <td style="font-size:12px; color:var(--text-muted);">${esc(d.ai_provider_used || '-')}</td>
                <td>${statusBadge(d.status)}${d.status === 'failed' && d.error_detail ? `<div style="font-size:11px; color:var(--danger); max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${esc(d.error_detail)}">${esc(d.error_detail)}</div>` : ''}</td>
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

    loadProviderStatus();
  } catch { /* Fehler bereits via toast */ }
}

async function loadProviderStatus() {
  const container = document.getElementById('providerStatus');
  if (!container || !state.adminKey) return;
  try {
    const results = await apiFetch('/api/v1/projects/provider-status', { headers: adminHeaders() });
    if (!results.length) {
      container.innerHTML = '<div class="empty">Keine Projekte vorhanden.</div>';
      return;
    }
    container.innerHTML = `<div style="display:flex; flex-wrap:wrap; gap:10px;">
      ${results.map(r => `
        <div style="padding:10px 14px; background:var(--bg); border-radius:8px; border:1px solid ${r.available ? 'var(--success)' : 'var(--danger)'}; min-width:180px;">
          <div style="font-size:13px; font-weight:600;">${esc(r.project_name)}</div>
          <div style="font-size:12px; color:var(--text-muted); margin-top:2px;">${esc(r.provider)} · ${esc(r.model)}</div>
          <div style="margin-top:6px;">
            ${r.available
              ? `<span class="badge badge-completed">Verfügbar</span>${r.latency_ms ? ` <span style="font-size:11px; color:var(--text-muted);">${r.latency_ms}ms</span>` : ''}`
              : `<span class="badge badge-failed">Nicht erreichbar</span>`}
          </div>
          ${!r.available && r.error ? `<div style="font-size:11px; color:var(--danger); margin-top:4px; word-break:break-word;">${esc(r.error)}</div>` : ''}
        </div>
      `).join('')}
    </div>`;
  } catch {
    container.innerHTML = '<div class="empty">Provider-Status konnte nicht geladen werden.</div>';
  }
}

// ─── Memories ────────────────────────────────────────────────────
async function exportMemories() {
  const key = getSelectedProjectKey('memProjectSelect');
  if (!key) { toast('Bitte ein Projekt auswählen', 'error'); return; }
  try {
    const data = await apiFetch('/api/v1/memories/export', { headers: bearerHeaders(key) });
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'memories_export.json';
    a.click();
    URL.revokeObjectURL(url);
    toast('Export erfolgreich', 'success');
  } catch (e) { toast('Export fehlgeschlagen: ' + e.message, 'error'); }
}

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

let _memSearchTimer = null;
function onMemorySearchInput() {
  clearTimeout(_memSearchTimer);
  _memSearchTimer = setTimeout(() => {
    state.memorySearch = document.getElementById('memSearchInput')?.value?.toLowerCase() || '';
    state.memoryPage = 0;
    filterMemories();
  }, 300);
}

function filterMemories() {
  const typeFilter = document.getElementById('memTypeFilter').value;
  const search = state.memorySearch;
  let filtered = state.allMemories;

  if (typeFilter) filtered = filtered.filter(m => m.memory_type === typeFilter);
  if (search) filtered = filtered.filter(m =>
    (m.key || '').toLowerCase().includes(search) || (m.content || '').toLowerCase().includes(search)
  );

  const container = document.getElementById('memoriesList');
  if (!filtered.length) {
    container.innerHTML = '<div class="empty">Keine Erinnerungen gefunden. Starte einen Dream im Projekte-Tab oder warte auf den automatischen Konsolidierungslauf.</div>';
    return;
  }

  const totalPages = Math.ceil(filtered.length / state.memoriesPerPage);
  if (state.memoryPage >= totalPages) state.memoryPage = totalPages - 1;
  const page = filtered.slice(state.memoryPage * state.memoriesPerPage, (state.memoryPage + 1) * state.memoriesPerPage);

  let html = page.map(m => `
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

  if (totalPages > 1) {
    html += `<div style="display:flex; align-items:center; justify-content:center; gap:12px; margin-top:16px;">
      <button class="btn btn-ghost btn-sm" onclick="state.memoryPage--; filterMemories();" ${state.memoryPage <= 0 ? 'disabled' : ''}>← Zurück</button>
      <span style="font-size:13px; color:var(--text-muted);">Seite ${state.memoryPage + 1} von ${totalPages} (${filtered.length} Erinnerungen)</span>
      <button class="btn btn-ghost btn-sm" onclick="state.memoryPage++; filterMemories();" ${state.memoryPage >= totalPages - 1 ? 'disabled' : ''}>Weiter →</button>
    </div>`;
  }

  container.innerHTML = html;
}

async function deleteMemory(memoryId) {
  showPopup('Erinnerung löschen?', 'Diese Memory wird unwiderruflich entfernt.', async () => {
    const key = getSelectedProjectKey('memProjectSelect');
    if (!key) return;
    try {
      await apiFetch(`/api/v1/memories/${memoryId}`, { method: 'DELETE', headers: bearerHeaders(key) });
      toast('Erinnerung gelöscht', 'success');
      loadMemories();
    } catch (e) { toast('Fehler beim Löschen: ' + e.message, 'error'); }
  }, 'danger');
}
