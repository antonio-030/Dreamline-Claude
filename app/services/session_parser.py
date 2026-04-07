"""
Session-Parser – Normalisiert JSONL-Dateien aus Claude Code und OpenAI Codex
in Dreamlines einheitliches Messages-Format [{role, content}].

Claude Code Format:
  - Zeilen mit type: "user" → message.content (String oder Block-Liste)
  - Zeilen mit type: "assistant" → message.content[].text wo type == "text"

Codex Format:
  - Zeile 1: type: "session_meta" → payload.id, payload.cwd
  - Zeilen mit type: "response_item", payload.role: "user" → payload.content[].text (input_text)
  - Zeilen mit type: "response_item", payload.role: "assistant" → payload.content[].text (output_text)
  - Zeilen mit type: "event_msg", payload.type: "token_count" → Token-Nutzung
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# Maximale Zeichenlänge pro Nachricht (verhindert Speicher-Explosion)
MAX_MESSAGE_LENGTH = settings.max_message_length
# Mindestlänge für eine gültige Nachricht
MIN_MESSAGE_LENGTH = 3
# Maximale Anzahl Messages pro Session
MAX_MESSAGES_PER_SESSION = settings.max_messages_per_session


@dataclass
class ParsedSession:
    """Normalisiertes Ergebnis aus einer Session-Datei."""
    messages: list[dict]           # [{role: "user"|"assistant", content: "..."}]
    session_id: str                # Eindeutige Session-ID
    cwd: str | None = None         # Arbeitsverzeichnis (nur Codex)
    source_tool: str = "unknown"   # "claude" oder "codex"
    source_file: str = ""          # Dateiname der Quelldatei
    total_tokens: int = 0          # Geschätzte Token-Nutzung
    metadata: dict = field(default_factory=dict)


def detect_source_tool(first_line: str) -> str:
    """Erkennt das Quelltool anhand der ersten Zeile einer JSONL-Datei."""
    try:
        data = json.loads(first_line)
        # Codex: Erste Zeile ist immer session_meta mit originator
        if data.get("type") == "session_meta":
            payload = data.get("payload", {})
            if payload.get("originator", "").startswith("codex"):
                return "codex"
        # Claude: Erste Zeile hat type "user" oder "summary" auf Top-Level
        if data.get("type") in ("user", "assistant", "summary"):
            return "claude"
    except (json.JSONDecodeError, TypeError):
        pass
    return "unknown"


def parse_session_file(
    filepath: Path,
    source_tool: str = "auto",
    max_messages: int = MAX_MESSAGES_PER_SESSION,
) -> ParsedSession | None:
    """
    Parst eine JSONL-Session-Datei und gibt ein normalisiertes ParsedSession zurück.

    Erkennt automatisch ob die Datei von Claude Code oder Codex stammt.
    Gibt None zurück wenn die Session zu kurz ist (< 2 Messages).
    """
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace").strip()
    except (OSError, IOError) as e:
        logger.warning("Kann Datei nicht lesen: %s – %s", filepath, e)
        return None

    lines = content.split("\n")
    if not lines:
        return None

    # Auto-Detection
    if source_tool == "auto":
        source_tool = detect_source_tool(lines[0])

    if source_tool == "codex":
        return _parse_codex_session(lines, filepath, max_messages)
    elif source_tool == "claude":
        return _parse_claude_session(lines, filepath, max_messages)
    else:
        # Versuche beide Parser
        result = _parse_codex_session(lines, filepath, max_messages)
        if result and len(result.messages) >= 2:
            return result
        return _parse_claude_session(lines, filepath, max_messages)


# Muster die auf Codex-System-Messages hindeuten (keine echten User-Eingaben)
_CODEX_SYSTEM_PATTERNS = (
    "# AGENTS.md instructions",
    "<environment_context>",
    "<permissions instructions>",
    "<INSTRUCTIONS>",
)


def _is_codex_system_message(text: str) -> bool:
    """Prüft ob eine User-Message eigentlich Codex-System-Kontext ist."""
    text_start = text[:200]
    return any(pattern in text_start for pattern in _CODEX_SYSTEM_PATTERNS)


def _parse_codex_session(
    lines: list[str],
    filepath: Path,
    max_messages: int,
) -> ParsedSession | None:
    """Parst eine Codex-JSONL-Session."""
    session_id = filepath.stem  # Fallback: Dateiname als ID
    cwd = None
    messages = []
    total_tokens = 0

    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        entry_type = entry.get("type", "")
        payload = entry.get("payload", {})

        # Session-Metadaten extrahieren
        if entry_type == "session_meta":
            session_id = payload.get("id", session_id)
            cwd = payload.get("cwd")
            continue

        # User- und Assistant-Nachrichten extrahieren
        if entry_type == "response_item" and isinstance(payload, dict):
            role = payload.get("role", "")
            # Developer-Messages ignorieren (System-Instruktionen)
            if role == "developer":
                continue

            if role in ("user", "assistant"):
                content_blocks = payload.get("content", [])
                if isinstance(content_blocks, list):
                    # Textblöcke extrahieren
                    text_parts = []
                    for block in content_blocks:
                        if not isinstance(block, dict):
                            continue
                        block_type = block.get("type", "")
                        # Codex nutzt "input_text" für User, "output_text" für Assistant
                        if block_type in ("input_text", "output_text", "text"):
                            text = block.get("text", "")
                            if text:
                                text_parts.append(text)

                    text = " ".join(text_parts).strip()

                    # System-Kontext-Messages filtern:
                    # Codex sendet AGENTS.md-Instruktionen und environment_context
                    # als User-Messages – diese sind kein echter User-Input
                    if role == "user" and _is_codex_system_message(text):
                        continue

                    if text and len(text) >= MIN_MESSAGE_LENGTH:
                        messages.append({
                            "role": role,
                            "content": text[:MAX_MESSAGE_LENGTH],
                        })

        # Token-Nutzung aus event_msg extrahieren
        if entry_type == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info")
            if isinstance(info, dict):
                usage = info.get("total_token_usage", {})
                if isinstance(usage, dict):
                    total_tokens = usage.get("total_tokens", 0)

    if len(messages) < 2:
        return None

    return ParsedSession(
        messages=messages[-max_messages:],
        session_id=session_id,
        cwd=cwd,
        source_tool="codex",
        source_file=filepath.name,
        total_tokens=total_tokens,
    )


def _parse_claude_session(
    lines: list[str],
    filepath: Path,
    max_messages: int,
) -> ParsedSession | None:
    """Parst eine Claude Code JSONL-Session (1:1 aus link.py extrahiert)."""
    session_id = filepath.stem
    messages = []

    for line in lines[-50:]:  # Nur die letzten 50 Zeilen (wie im Original)
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = entry.get("type", "")

        if msg_type == "user":
            msg = entry.get("message", {})
            if isinstance(msg, dict):
                cf = msg.get("content", "")
                if isinstance(cf, str):
                    content = cf
                elif isinstance(cf, list):
                    content = " ".join(
                        b.get("text", "")
                        for b in cf
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                else:
                    content = ""

                if content and len(content) >= MIN_MESSAGE_LENGTH:
                    messages.append({
                        "role": "user",
                        "content": content[:MAX_MESSAGE_LENGTH],
                    })

        elif msg_type == "assistant":
            msg = entry.get("message", {})
            blocks = msg.get("content", []) if isinstance(msg, dict) else []
            if isinstance(blocks, list):
                content = " ".join(
                    b.get("text", "")
                    for b in blocks
                    if isinstance(b, dict) and b.get("type") == "text"
                )
                if content and len(content) >= MIN_MESSAGE_LENGTH:
                    messages.append({
                        "role": "assistant",
                        "content": content[:MAX_MESSAGE_LENGTH],
                    })

    if len(messages) < 2:
        return None

    return ParsedSession(
        messages=messages[-max_messages:],
        session_id=session_id,
        source_tool="claude",
        source_file=filepath.name,
    )
