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
    Nutzt decode_claude_dir_name fuer korrekte Pfad-Aufloesung,
    dann letztes Pfad-Segment als Projektname.

    Beispiele:
    - -Users-antonio-Desktop-Dreamline-Claude -> Dreamline-Claude
    - C--Users-max--Desktop-MeinProjekt -> MeinProjekt
    """
    decoded = decode_claude_dir_name(dir_name)
    return os.path.basename(decoded) or dir_name


def decode_claude_dir_name(dir_name: str) -> str:
    """
    Dekodiert einen Claude-Projektnamen zurueck in einen Dateipfad.

    Claude Code encodiert: ":" entfernt, "/" und "\\\\" zu "-".
    Auf macOS (kein Laufwerksbuchstabe) wird Filesystem-Validierung genutzt.
    Im Docker-Container (kein Zugriff auf Host-Dateisystem) werden
    verbleibende Teile mit '-' zusammengehalten statt als separate
    Verzeichnisse behandelt.

    Beispiele:
    - -Users-antonio-Desktop-SentinelClaw -> /Users/antonio/Desktop/SentinelClaw
    - -Users-antonio-Desktop-Dreamline-Claude -> /Users/antonio/Desktop/Dreamline-Claude
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
    any_fs_match = False

    while remaining:
        found = False
        for take in range(min(len(remaining), 5), 0, -1):
            for sep in ("-", " ", ""):
                candidate = os.path.join(path, sep.join(remaining[:take]))
                if os.path.exists(candidate):
                    path = candidate
                    remaining = remaining[take:]
                    found = True
                    any_fs_match = True
                    break
            if found:
                break
        if not found:
            if any_fs_match:
                # Filesystem-Matches hatten Erfolg, aber jetzt kein Match mehr.
                # Verbleibende Teile sind wahrscheinlich ein Ordnername mit Bindestrichen.
                path = os.path.join(path, "-".join(remaining))
                break
            # Noch kein Match (z.B. Docker): einzeln weiter, damit
            # Standard-Pfadstruktur (/Users/name/...) erhalten bleibt.
            path = os.path.join(path, remaining[0])
            remaining = remaining[1:]

    # Docker-Fallback: Kein einziger Filesystem-Match —
    # die letzten Teile wurden einzeln angehaengt.
    # Versuche rueckwaerts zusammenzufuegen fuer haeufige Muster.
    if not any_fs_match:
        path = _decode_without_filesystem(parts)

    return path


def _decode_without_filesystem(parts: list[str]) -> str:
    """
    Fallback-Dekodierung wenn kein Filesystem-Zugriff moeglich ist (Docker).
    Nutzt heuristische Regeln fuer gaengige macOS/Linux-Pfadmuster.

    Strategie: Bekannte Verzeichnis-Praefixe erkennen, Rest mit '-' joinen.
    """
    # Bekannte Top-Level-Dirs die IMMER Verzeichnisse sind
    known_dirs = {"Users", "home", "tmp", "var", "opt", "app", "root", "private"}
    # Bekannte 3rd-Level-Dirs (nach /Users/<name>/)
    known_user_dirs = {
        "Desktop", "Documents", "Downloads", "Projects", "Developer",
        "Dev", "Code", "Sites", "repos", "workspace", "src", "Server",
        "Apps", "app", "Work", "git", "GitHub", "packages", "libs",
    }

    path_parts: list[str] = []
    i = 0

    while i < len(parts):
        part = parts[i]

        if i == 0 and part in known_dirs:
            # /Users, /home, /tmp etc.
            path_parts.append(part)
            i += 1
        elif i == 1 and path_parts and path_parts[0] in ("Users", "home"):
            # Username (immer ein Verzeichnis)
            path_parts.append(part)
            i += 1
        elif i == 2 and len(path_parts) == 2 and part in known_user_dirs:
            # Desktop, Documents etc.
            path_parts.append(part)
            i += 1
        elif i >= 3 and len(path_parts) >= 3 and part in known_user_dirs:
            # Weitere bekannte Unterverzeichnisse (Dev/Apps etc.)
            path_parts.append(part)
            i += 1
        else:
            # Rest als einzelnen Ordnernamen mit Bindestrichen zusammenfassen
            path_parts.append("-".join(parts[i:]))
            break

    return "/" + "/".join(path_parts)
