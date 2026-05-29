# Dreamline – Changelog

Kompakte Aenderungs-Doku. Nur aktuelle Version — aeltere Eintraege in `git log --oneline`.

---

## [2026-05-29] JSON-Modus gehaertet (Wurzelursache Dream-Fehler)

- **Wurzelursache gefunden**: Der Konsolidierungs-Prompt wies Claude an, per Tools zu explorieren (ls/Read/grep) — im JSON-Modus mit aktiven Tools verbrauchte Claude Turns und endete in `error_max_turns` / abgeschnittenem JSON
- `_complete_claude_abo`: `--tools ""` (keine Tools) + `--max-turns 1` statt `10` — reine JSON-Generierung in einem Turn (echter CLI-Test: subtype=success, num_turns=1)
- Tool-freie System-Prompt-Variante `CONSOLIDATION_SYSTEM_PROMPT_JSON` (Kontext steht inline); Agent-Variante byte-identisch erhalten (DRY-Builder)
- JSON-Modus-Provider (claude-abo/anthropic/codex/ollama) nutzen die tool-freie Variante
- Untersuchung dokumentiert: Agent-Modus bleibt deaktiviert (Claude-CLI blockiert Schreibzugriffe in `~/.claude/`, auch mit `bypassPermissions`)
- 6 neue Tests (CLI-Flags, Prompt-Varianten)

## [2026-05-29] Robustes Dream-Antwort-Parsing

- **Bug**: Dreams grosser Projekte schlugen fehl mit irrefuehrendem `line 1 column 1 (char 0)` — Ursache war abgeschnittenes/teilweise kaputtes KI-JSON, nicht eine leere Antwort
- Neues Modul `dream_parser.py` (Parse-Logik aus `dreamer.py` ausgelagert, dieses war >300 Z.)
- **String-bewusster** Klammer-Matcher: Klammern/Quotes im Memory-Content brechen das Parsing nicht mehr (alter Zaehler verschob sich an `{}` in Strings)
- **Salvage**: bei abgeschnittenem JSON werden alle vollstaendigen Operationen gerettet, nur die abgeschnittene letzte verworfen
- **Klare Diagnose**: unterscheidet "abgeschnitten (Memory zu gross)" von "kein JSON"
- **Besseres Fehlerprotokoll**: speichert Anfang + Ende + Gesamtlaenge der Antwort (vorher nur erste 500 Z. → Fehlerort am Ende war unsichtbar)
- 9 neue Tests (Truncation, Klammern im Content, Codeblock-Truncation, Auszug)

## [2026-04-10] Setup-Wizard Fix + Dream-Pipeline Stabilisierung

- Setup-Wizard zeigt jetzt "Erneut verbinden" statt volles Onboarding wenn Provider bereits verbunden
- Wizard wird bei Tab-Wechsel ausgeblendet (nur noch auf Übersicht sichtbar)
- `.env` mit festem `DREAMLINE_SECRET_KEY` — kein generierter Einmal-Key mehr bei Container-Restart
- **Dream-Fix**: `--max-turns` von 5 auf 10 erhöht — Konsolidierung mit vielen Sessions schlug fehl
- **Dream-Fix**: `error_max_turns` Fallback verbessert, bessere Log-Meldungen
- **Scheduler**: Check-Intervall von 60 auf 15 Min, initialer Check 60s nach Startup
- **Scan-Fix**: Pfad-Dekodierung für Projektnamen mit Bindestrichen (z.B. `Dreamline-Claude`)
- `guess_display_name()` nutzt jetzt `decode_claude_dir_name()` für korrekte Namen
- Docker-Fallback: heuristische Dekodierung wenn Host-Dateisystem nicht erreichbar
- 21 neue Tests für `utils.py` (Pfad-Dekodierung, Display-Names, Escaping)
- Version-Bump auf `?v=7` für alle JS/CSS-Assets

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
