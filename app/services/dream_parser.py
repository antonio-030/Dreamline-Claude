"""
Robustes Parsen der KI-Antwort einer Dream-Konsolidierung.

Die KI liefert im JSON-Modus ein Objekt der Form
``{"operations": [...], "summary": "..."}``. In der Praxis ist diese
Antwort oft nicht sauber:

1. Sie steckt in einem Markdown-Codeblock (```json ... ```).
2. Sie ist von Freitext umgeben (Provider ohne JSON-Enforcement).
3. Sie ist **abgeschnitten** — bei grossen Memories serialisiert das Modell
   sehr viel Text in einen Block und bricht mitten im JSON ab.
4. Der Memory-Content enthaelt selbst geschweifte Klammern oder Anfuehrungs-
   zeichen, an denen ein naiver Klammer-Zaehler zerbricht.

Dieses Modul parst string-bewusst (Klammern/Quotes innerhalb von JSON-Strings
werden ignoriert) und rettet bei abgeschnittenem JSON so viele vollstaendige
Operationen wie moeglich, statt den gesamten Dream zu verwerfen.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

# Schutz gegen pathologisch grosse Eingaben bei der Freitext-Suche.
_MAX_SCAN_LEN = 500_000


def strip_markdown_fence(response_text: str) -> str:
    """Entfernt einen umschliessenden Markdown-Codeblock (```...```).

    Sammelt alle Zeilen zwischen den Fences. Ein nicht geschlossener Fence
    (abgeschnittene Antwort) wird toleriert: ab der oeffnenden Fence wird bis
    zum Ende gesammelt.
    """
    clean_text = response_text.strip()
    if "```" not in clean_text:
        return clean_text

    in_block = False
    json_lines: list[str] = []
    for line in clean_text.split("\n"):
        if line.strip().startswith("```"):
            in_block = not in_block
            continue
        if in_block:
            json_lines.append(line)
    return "\n".join(json_lines) if json_lines else clean_text


def _match_balanced(text: str, start: int) -> int:
    """Index direkt hinter der schliessenden Klammer des bei ``start`` beginnenden Blocks.

    ``text[start]`` muss ``{`` oder ``[`` sein. Zaehlt string-bewusst: Klammern
    und Anfuehrungszeichen innerhalb von JSON-Strings werden ignoriert, ebenso
    escapte Zeichen. Gibt ``-1`` zurueck, wenn der Block unvollstaendig ist
    (z.B. abgeschnittene Antwort).
    """
    open_char = text[start]
    close_char = "}" if open_char == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def _find_complete_object_with_operations(text: str) -> dict | None:
    """Sucht im (ggf. von Freitext umgebenen) Text ein vollstaendiges JSON-Objekt mit ``operations``.

    String-bewusste Variante: ueberspringt Klammern in Strings korrekt. Gibt das
    erste passende, vollstaendig parsebare Objekt zurueck, sonst ``None``.
    """
    search_start = 0
    limit = min(len(text), _MAX_SCAN_LEN)
    while search_start < limit:
        brace_start = text.find("{", search_start)
        if brace_start < 0 or brace_start >= limit:
            return None
        end = _match_balanced(text, brace_start)
        if end < 0:
            # Ab hier ist alles unvollstaendig -> kein vollstaendiges Objekt mehr moeglich.
            return None
        try:
            data = json.loads(text[brace_start:end])
            if isinstance(data, dict) and "operations" in data:
                return data
        except json.JSONDecodeError:
            pass
        search_start = brace_start + 1
    return None


def _extract_summary(text: str) -> str:
    """Best-effort-Extraktion des ``summary``-Strings (auch aus Teil-JSON)."""
    match = re.search(r'"summary"\s*:\s*"', text)
    if not match:
        return ""
    chars: list[str] = []
    escaped = False
    for char in text[match.end():]:
        if escaped:
            chars.append(char)
            escaped = False
        elif char == "\\":
            chars.append(char)
            escaped = True
        elif char == '"':
            break
        else:
            chars.append(char)
    try:
        return json.loads('"' + "".join(chars) + '"')
    except json.JSONDecodeError:
        return ""


def _salvage_operations(text: str) -> list[dict]:
    """Rettet vollstaendige Operationen aus abgeschnittenem/teilweise kaputtem JSON.

    Findet das ``operations``-Array und parst seine Elemente einzeln. Eine
    abgeschnittene letzte Operation wird verworfen, alle davor liegenden
    vollstaendigen Operationen bleiben erhalten.
    """
    key_idx = text.find('"operations"')
    if key_idx < 0:
        return []
    bracket = text.find("[", key_idx)
    if bracket < 0:
        return []

    operations: list[dict] = []
    i = bracket + 1
    n = len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n,":
            i += 1
        if i >= n or text[i] == "]":
            break
        if text[i] != "{":
            break  # unerwartetes Zeichen -> abbrechen
        end = _match_balanced(text, i)
        if end < 0:
            break  # letzte Operation ist abgeschnitten
        try:
            operations.append(json.loads(text[i:end]))
        except json.JSONDecodeError:
            break  # Operation selbst kaputt -> hier aufhoeren
        i = end
    return operations


def parse_dream_operations(response_text: str) -> tuple[list[dict], str]:
    """Extrahiert Dream-Operationen und Summary aus der KI-Antwort.

    Strategien (in Reihenfolge):
    1. Direktes JSON-Parsing (schneller Pfad, vollstaendiges JSON).
    2. Vollstaendiges JSON-Objekt mit ``operations`` im Freitext (string-bewusst).
    3. Salvage: vollstaendige Operationen aus abgeschnittenem JSON retten.

    Wirft ``json.JSONDecodeError`` mit aussagekraeftiger Meldung, wenn keine
    Operation extrahierbar ist (unterscheidet zwischen "kein JSON" und
    "abgeschnitten").
    """
    clean_text = strip_markdown_fence(response_text)

    # Strategie 1: direktes Parsing
    try:
        data = json.loads(clean_text)
        if isinstance(data, dict):
            return data.get("operations", []), data.get("summary", "")
    except json.JSONDecodeError:
        pass

    # Strategie 2: vollstaendiges Objekt im Freitext finden
    obj = _find_complete_object_with_operations(clean_text)
    if obj is not None:
        return obj.get("operations", []), obj.get("summary", "")

    # Strategie 3: Salvage aus abgeschnittenem JSON
    operations = _salvage_operations(clean_text)
    if operations:
        logger.warning(
            "Dream-Antwort unvollstaendig — %d Operation(en) aus abgeschnittenem "
            "JSON gerettet (Antwortlaenge %d Zeichen)",
            len(operations), len(response_text),
        )
        return operations, _extract_summary(clean_text)

    # Alle Strategien fehlgeschlagen -> aussagekraeftige Diagnose
    if '"operations"' in clean_text:
        message = (
            "KI-Antwort enthaelt 'operations', ist aber unvollstaendig/abgeschnitten "
            f"({len(response_text)} Zeichen) — Memory vermutlich zu gross fuer JSON-Modus"
        )
    else:
        message = "Kein gueltiges JSON mit 'operations' in der KI-Antwort gefunden"
    raise json.JSONDecodeError(message, clean_text[:200] or " ", 0)


def build_response_excerpt(response_text: str, head: int = 600, tail: int = 600) -> str:
    """Erzeugt einen aussagekraeftigen Auszug fuer das Fehlerprotokoll.

    Bei langen Antworten werden Anfang UND Ende gezeigt — gerade das Ende ist
    bei abgeschnittenem JSON die relevante Stelle. Nur den Anfang zu speichern
    (frueheres Verhalten) verbarg genau den Fehlerort.
    """
    if not response_text:
        return "(keine Antwort)"
    n = len(response_text)
    if n <= head + tail:
        return response_text
    omitted = n - head - tail
    return (
        f"{response_text[:head]}\n"
        f"…[{omitted} Zeichen ausgelassen, Gesamtlaenge {n}]…\n"
        f"{response_text[-tail:]}"
    )
