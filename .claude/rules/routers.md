---
paths: ["app/routers/**"]
description: Router-Architektur-Regeln
---

# Router-Regeln

- Router enthalten NUR HTTP-Handling: Request parsen → Service aufrufen → Response bauen
- Funktionen >20 Zeilen Business-Logik → in `app/services/` extrahieren
- Keine Dateisystem-Operationen direkt in Routern
- JEDER Endpoint braucht `@limiter.limit()` + `request: Request` Parameter
- Fehler: `HTTPException` mit deutschem `detail`-Text, passender Status-Code
- Paginierung: `limit`/`offset` mit `ge=`/`le=` Bounds
- API-Responses: NUR erweitern, nie Felder entfernen/umbenennen
