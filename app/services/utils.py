"""Gemeinsame Hilfsfunktionen für Services."""

import os


def truncate_text(text: str, max_chars: int, suffix: str = "... [truncated]") -> str:
    """Kürzt Text auf max_chars Zeichen mit optionalem Suffix."""
    if not text or len(text) <= max_chars:
        return text
    return text[:max_chars - len(suffix)] + suffix


def escape_js_string(s: str) -> str:
    """Escaped einen String fuer die sichere Einbettung in JavaScript-Quellcode."""
    return (s
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("${", "\\${")
        .replace("</script>", "<\\/script>")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def guess_display_name(dir_name: str) -> str:
    """
    Erzeugt einen lesbaren Anzeigenamen aus dem Claude-Projektnamen.
    Nimmt das letzte Segment als wahrscheinlichsten Projektnamen.
    Beispiel: C--Users-max--Desktop-MeinProjekt -> MeinProjekt
    """
    parts = dir_name.split("--")
    last = parts[-1] if parts else dir_name
    sub = last.split("-")
    return sub[-1] if sub else last


def decode_claude_dir_name(dir_name: str) -> str:
    """
    Dekodiert einen Claude-Projektnamen zurueck in einen Dateipfad.

    Claude Code encodiert: ":" entfernt, "/" und "\\\\" zu "-".
    Auf macOS (kein Laufwerksbuchstabe) wird Filesystem-Validierung genutzt.

    Beispiele:
    - -Users-antonio-Desktop-SentinelClaw -> /Users/antonio/Desktop/SentinelClaw
    - C--Users-acea--Desktop-Techlogia -> C:/Users/acea/Desktop/Techlogia
    """
    # Windows: "--" als Pfadtrenner (Laufwerksbuchstabe vorhanden)
    if "--" in dir_name:
        path = dir_name.replace("--", "/")
        if len(path) > 1 and path[1] == "/":
            path = path[0] + ":/" + path[2:]
        return path

    # Unix (macOS/Linux): Filesystem-Validierung
    parts = dir_name.lstrip("-").split("-")
    path = "/"
    remaining = list(parts)

    while remaining:
        found = False
        for take in range(min(len(remaining), 5), 0, -1):
            for sep in ("-", " ", ""):
                candidate = os.path.join(path, sep.join(remaining[:take]))
                if os.path.exists(candidate):
                    path = candidate
                    remaining = remaining[take:]
                    found = True
                    break
            if found:
                break
        if not found:
            path = os.path.join(path, remaining[0])
            remaining = remaining[1:]

    return path
