# Dreamline – Changelog

Kompakte Aenderungs-Doku. Nur aktuelle Version — aeltere Eintraege in `git log --oneline`.

---

## [2026-04-09] Qualitaets-Offensive + Codex-Integration

- Codex-CLI repariert: `--full-auto --skip-git-repo-check --ephemeral -m MODEL`, stderr-Filterung, Zombie-Schutz
- 162 Tests gruen (vorher 0): Lazy Imports, `pyproject.toml`, `test_extractor.py`, `test_sessions_router.py`
- `ai_client.py` aufgeteilt: `ai_common.py` + `ai_cli_provider.py` + `ai_api_provider.py` + Fassade (alle <250 Z.)
- `except Exception` von 37 auf 9 reduziert (nur noch Top-Level Catch-Alls)
- `link.py` von 823 auf 569 Zeilen: `hook_installer.py`, `session_importer.py`, `utils.py` extrahiert
- Rate Limits auf alle 32 Endpoints (120/30/10/2 pro min Schema)
- Import-Validierung: `content max_length=50_000`, Listen max 500 Items
- `start.sh` zeigt jetzt Alembic-Fehler (kein `2>/dev/null` mehr)
- Dream-Pipeline: Phase 5 abgesichert, JSON-Parsing mit 3 Strategien, Input-Limit 500KB
- Dream-Provider separat konfigurierbar pro Projekt (`dream_provider`, `dream_model`)
- UI: Codex-Onboarding (Auth-Status, Login-Anleitung), Setup-Wizard fuer alle Provider
- CI-Pipeline: `.github/workflows/ci.yml` (lint + tests + migration + docker)

---

## Offene Punkte

- **Security**: Dashboard-Auth auf HttpOnly Cookie umstellen (P2)
- **Observability**: request_id Korrelation ueber alle Logs (P1)
- **Contract Tests**: Session -> Extract -> Dream -> Memory End-to-End (P1)
- **Audit-Report**: `docs/repo-audit-report.md` aktualisieren (veraltet)
