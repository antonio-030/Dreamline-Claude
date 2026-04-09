---
paths: ["app/**/*.py"]
description: Security-Regeln fuer alle Python-Dateien
---

# Security-Checkliste

Bei JEDER Code-Aenderung automatisch pruefen:

- Neue Route → Hat sie `@limiter.limit()` + `request: Request` Parameter?
- Neue Route → Hat sie `Depends(verify_admin_key)` oder `Depends(get_current_project)`?
- Pydantic-Model → Alle Felder mit `max_length`, `ge`/`le`, `pattern`?
- DB-Query → Nur SQLAlchemy ORM `select()`, KEINE f-Strings oder `.format()`?
- Subprocess → Liste statt String? `shell=True` verboten? Timeout gesetzt?
- Datei-Zugriff → Pfad gegen Traversal geprueft (`_is_safe_project_path()`)?
- Logger-Aufruf → Keine Secrets (API-Keys, Tokens, Passwoerter)?
- API-Response → Keine internen IDs, Stack-Traces, oder Secrets im Output?
- Key-Vergleich → `secrets.compare_digest()` statt `==`?
