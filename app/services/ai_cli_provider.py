"""
CLI-basierte KI-Provider: Claude-Abo (CLI) und Codex-Sub (CLI).

Nutzt die jeweilige CLI (claude / codex) ueber Subprocess-Aufrufe.
Kein API-Key noetig -- Authentifizierung laeuft ueber das bestehende Abo.
"""

import logging
import re

from app.config import settings
from app.services.ai_common import (
    CliJsonResult,
    _estimate_tokens_from_word_count,
    _invoke_cli,
    _parse_cli_json_output,
    _strip_cli_warnings,
)

logger = logging.getLogger(__name__)


# ─── Tool-Constraints fuer den Agent-Modus ────────────────────────

def _build_tool_constraints(memory_dir: str) -> str:
    """
    Erzeugt die Sicherheitsregeln fuer den Dream-Agent.

    Grundprinzip: Bash ist NUR zum Lesen erlaubt. Jede Datei-Aenderung
    muss ueber die Write/Edit-Tools laufen, und zwar ausschliesslich
    innerhalb des Memory-Verzeichnisses. So wird verhindert, dass der
    Agent versehentlich das Hostsystem veraendert.
    """
    return (
        "**Tool constraints for this run:**\n"
        "Bash is restricted to READ-ONLY commands (ls, find, grep, cat, stat, "
        "wc, head, tail, sort, uniq, diff, etc.). Any command that creates, "
        "modifies, or deletes files/directories is FORBIDDEN in Bash -- use "
        "the Write or Edit tool instead.\n\n"
        f"You may ONLY write/edit files inside: `{memory_dir}`\n"
        f"The absolute path prefix for all writes MUST start with: `{memory_dir}`\n"
        "Do NOT create, modify, or delete any files outside this directory.\n"
        "Do NOT use MCP tools, WebSearch, WebFetch, or Agent tools.\n"
        "Do NOT use the Bash tool to write, redirect, or modify ANY files."
    )


# ─── Claude-Abo (CLI mit JSON-Output fuer exakte Token-Zahlen) ───
#
# OAuth direkt ueber die Anthropic API funktioniert NICHT fuer Abo-Nutzer:
# "OAuth authentication is currently not supported." (API-Antwort)
# Claude Code nutzt intern einen speziellen First-Party-Endpunkt mit seiner
# eigenen Client-ID, der fuer externe Aufrufe nicht zugaenglich ist.
#
# Die CLI ist deshalb der einzige Weg fuer Abo-Nutzer. Sie nutzt intern
# die gleiche OAuth-Auth wie Claude Code und hat Zugriff auf den
# First-Party-Endpunkt mit Prompt-Caching.


async def _complete_claude_abo(
    system_prompt: str, user_prompt: str,
) -> tuple[str, int]:
    """
    Einfache Text-Completion ueber die Claude Code CLI.
    Kombiniert System- und User-Prompt und gibt Antwort + Token-Verbrauch zurueck.
    """
    full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"

    raw = await _invoke_cli(
        "claude",
        args=["--print", "--output-format", "json", "--max-turns", "5"],
        input_text=full_prompt,
    )

    result = _parse_cli_json_output(raw, fallback_word_sources=[full_prompt, raw])

    if not result.content or not result.content.strip():
        logger.warning("Claude-Abo CLI: Leere Antwort (Turns: %d)", result.num_turns)

    logger.info(
        "Claude-Abo CLI: %d est. Tokens, %d Turns",
        result.total_tokens, result.num_turns,
    )
    return result.content, result.total_tokens


async def _dream_claude_abo_agent(
    prompt: str, memory_dir: str, resume_session_id: str | None = None,
) -> tuple[str, int, str | None]:
    """
    Claude Code CLI im Agent-Modus mit Tool-Zugriff.
    1:1 wie Claude Code's runForkedAgent() mit createAutoMemCanUseTool().

    Erlaubte Tools (wie consolidationPrompt.ts):
    - Bash: nur read-only (ls, grep, cat, head, tail, stat, wc, find)
    - Read/Grep/Glob: unbeschraenkt
    - Write/Edit: nur im Memory-Verzeichnis

    Resume-Support: Wenn resume_session_id gesetzt, wird --resume genutzt
    fuer Cache-Sharing zwischen Dreams.
    """
    tool_constraints = _build_tool_constraints(memory_dir)
    cli_args = _build_dream_cli_args(tool_constraints, resume_session_id)

    # CWD auf das Memory-Dir setzen, damit die CLI das richtige Projektverzeichnis
    # findet und der Agent direkt dort schreiben kann.
    raw = await _invoke_cli(
        "claude",
        args=cli_args,
        input_text=prompt,
        timeout=settings.ai_cli_timeout_seconds,
        cwd=memory_dir,
    )

    result = _parse_cli_json_output(raw, fallback_word_sources=[prompt, prompt])

    logger.info(
        "Claude Dream-Agent abgeschlossen (%d Tokens, %d Turns, session=%s). "
        "Agent hat direkt ins Memory-Verzeichnis geschrieben.",
        result.total_tokens, result.num_turns, result.session_id or "none",
    )
    return result.content, result.total_tokens, result.session_id


def _build_dream_cli_args(
    tool_constraints: str, resume_session_id: str | None,
) -> list[str]:
    """
    Baut die CLI-Argumente fuer den Dream-Agent zusammen.
    Trennt die Argument-Logik von der Prozess-Ausfuehrung fuer bessere Testbarkeit.
    """
    args = [
        "--print",
        "--output-format", "json",
        "--permission-mode", "bypassPermissions",
        "--max-turns", str(settings.ai_agent_max_turns),
        "--allowedTools", "Bash,Read,Write,Edit,Grep,Glob",
        "--append-system-prompt", tool_constraints,
    ]

    # Resume-Support: Gleiche Session -> gleicher Prompt-Cache
    # Session-ID validieren (nur alphanumerisch + Bindestriche, keine CLI-Flags)
    if resume_session_id and re.match(r'^[a-f0-9\-]{20,100}$', resume_session_id):
        args.extend(["--resume", resume_session_id])
        logger.info("Dream-Agent nutzt --resume %s fuer Cache-Sharing", resume_session_id)

    return args


# ─── Codex-Abo (CLI mit OpenAI Subscription) ─────────────────────
#
# Analog zum claude-abo Provider: Nutzt die Codex CLI (codex exec)
# fuer Nutzer mit bestehendem OpenAI Plus/Pro Abo. Kein API-Key noetig.


async def _complete_codex_sub(
    model: str, system_prompt: str, user_prompt: str,
) -> tuple[str, int]:
    """
    Text-Completion ueber die Codex CLI (codex exec).
    Nutzt das bestehende OpenAI Abo -- kein API-Key noetig.
    Token-Tracking per Wortschaetzung (Codex gibt kein JSON-Usage zurueck).
    """
    full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"

    args = ["exec", "--full-auto", "--skip-git-repo-check", "--ephemeral"]
    if model:
        args.extend(["-m", model])
    args.append("-")

    raw = await _invoke_cli(
        "codex",
        args=args,
        input_text=full_prompt,
    )

    # Codex kann WARNING-Zeilen in stdout mischen -- herausfiltern
    raw = _strip_cli_warnings(raw)

    if not raw or not raw.strip():
        raise RuntimeError("Codex CLI: Leere Antwort erhalten (kein Output)")

    # Codex exec gibt Plain-Text zurueck, kein JSON
    total_tokens = _estimate_tokens_from_word_count(full_prompt, raw)

    logger.info("Codex-Sub CLI (%s): ~%d est. Tokens", model, total_tokens)
    return raw, total_tokens
