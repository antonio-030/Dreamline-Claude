---
paths: ["app/static/**", "app/templates/**"]
description: Frontend-Regeln fuer Dashboard JS/CSS/HTML
---

# Frontend-Konventionen

- API-Aufrufe: NUR ueber `apiFetch()` Wrapper (Error-Handling + Toast)
- HTML-Escaping: `esc()` bei ALLEN dynamischen Inhalten (XSS-Schutz)
- Intervalle: `setInterval`/`setTimeout` in Tracking-Variablen, bei Tab-Wechsel clearen
- Sprache: Deutsche Labels, konsistent (Nav-Button = Tab-Titel)
- Theme: Dark-Theme CSS-Variablen nutzen (`var(--bg)`, `var(--accent)`, etc.)
- Leere Zustaende: Immer Handlungsanweisung, nie nur "Keine Daten"
- Auth-Status: ALLE Provider anzeigen (Claude + Codex), nicht nur einen
- **Version-Bump**: Bei JS/CSS-Aenderungen `?v=N` in `dashboard.html` hochzaehlen
