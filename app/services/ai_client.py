"""
KI-Client-Wrapper -- unterstützt Claude-Abo (via CLI), Anthropic API und OpenAI API.

Fuer das Claude-Abo wird die Claude Code CLI genutzt -- diese authentifiziert
sich automatisch über die gespeicherten OAuth-Credentials (~/.claude/).

Zwei Modi:
- complete(): Einfacher Text-Prompt -> Text-Antwort (für JSON-Operationen)
- dream_with_tools(): Agent-Modus mit Tool-Zugriff (1:1 wie Claude Code autoDream)
"""

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass

import anthropic
import openai

from app.config import settings

logger = logging.getLogger(__name__)

# ─── Retry-Logic (1:1 wie withRetry in claude.ts) ────────────────

MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2.0

# Fehler-Codes und Begriffe die einen Retry rechtfertigen
_RETRYABLE_TERMS = frozenset([
    "timeout", "rate_limit", "rate limit", "429",
    "500", "502", "503", "529", "overloaded",
    "connection", "econnreset", "epipe",
])


async def _with_retry(coro_factory, label: str = "API"):
    """
    Exponential-Backoff Retry-Wrapper.

    Retries bei: Timeout, Rate-Limit (429), Server-Fehler (5xx), Verbindungsfehler.
    Kein Retry bei: Auth-Fehler (401/403), Validierungsfehler (400), unbekannte Fehler.
    """
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await coro_factory()
        except Exception as e:
            last_error = e
            error_str = str(e).lower()

            is_retryable = any(term in error_str for term in _RETRYABLE_TERMS)

            if not is_retryable or attempt >= MAX_RETRIES:
                raise

            wait = BACKOFF_BASE_SECONDS * (2 ** attempt)
            logger.warning(
                "%s Fehler (Versuch %d/%d): %s -- Retry in %.1fs",
                label, attempt + 1, MAX_RETRIES + 1, str(e)[:200], wait,
            )
            await asyncio.sleep(wait)

    raise last_error


# ─── Datenklasse für CLI-Ergebnisse ─────────────────────────────

@dataclass
class CliJsonResult:
    """Strukturiertes Ergebnis aus dem JSON-Output der Claude CLI."""
    content: str
    total_tokens: int
    session_id: str | None = None
    num_turns: int = 0


# ─── CLI-Helper: JSON-Parsing und Prozess-Aufruf ─────────────────

def _estimate_tokens_from_word_count(*texts: str) -> int:
    """
    Grobe Token-Schätzung anhand der Wortanzahl.
    Wird als Fallback genutzt wenn weder Kosten noch Usage-Daten vorliegen.
    """
    return sum(len(t.split()) for t in texts)


def _tokens_from_cost(cost: float) -> int:
    """
    Berechnet geschätzte Tokens aus den USD-Kosten.

    Hintergrund: Claude Sonnet Pricing liegt bei ca. $3 Input / $15 Output
    pro 1M Tokens. Als Mittelwert ergibt sich ca. $10 pro 1M Tokens,
    also ca. 100.000 Tokens pro Dollar. Der Faktor 100000 ist daher eine
    brauchbare Näherung für die Gesamtzahl verbrauchter Tokens.
    """
    return int(cost * 100_000)


def _parse_cli_json_output(raw: str, fallback_word_sources: list[str] | None = None) -> CliJsonResult:
    """
    Parst den JSON-Output der Claude CLI und extrahiert Content, Tokens und Session-ID.

    Die CLI liefert entweder ein Dict (Standardfall) oder eine Liste (selten).
    Bei Parse-Fehlern wird der Rohtext als Content zurückgegeben mit einer
    Wortanzahl-basierten Token-Schätzung als Fallback.
    """
    fallback_tokens = _estimate_tokens_from_word_count(*(fallback_word_sources or [raw]))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # CLI hat keinen gültigen JSON-Output geliefert -- Rohtext verwenden
        return CliJsonResult(content=raw, total_tokens=fallback_tokens)

    # Listen-Format: Kommt selten vor, z.B. bei bestimmten CLI-Versionen
    if isinstance(data, list):
        content = "\n".join(
            item.get("content", "") if isinstance(item, dict) else str(item)
            for item in data
        )
        return CliJsonResult(content=content, total_tokens=fallback_tokens)

    # Unerwarteter Typ -- Rohtext verwenden
    if not isinstance(data, dict):
        return CliJsonResult(content=raw, total_tokens=fallback_tokens)

    # Standard-Dict-Format: Felder extrahieren
    content = data.get("result", raw)
    session_id = data.get("session_id")
    num_turns = data.get("num_turns", 0)

    # Token-Berechnung: Präferenz -> usage-Feld > Kosten-Feld > Wortschätzung
    usage = data.get("usage", {})
    if usage and isinstance(usage, dict):
        input_tokens = usage.get("input_tokens", 0) or 0
        output_tokens = usage.get("output_tokens", 0) or 0
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        cache_create = usage.get("cache_creation_input_tokens", 0) or 0
        total_tokens = input_tokens + output_tokens + cache_read + cache_create
        logger.info(
            "CLI Token-Usage: input=%d output=%d cache_read=%d cache_create=%d",
            input_tokens, output_tokens, cache_read, cache_create,
        )
    else:
        # Fallback: Tokens aus Kosten berechnen (siehe _tokens_from_cost Doku)
        cost = data.get("cost_usd", 0) or data.get("total_cost_usd", 0) or 0
        total_tokens = _tokens_from_cost(cost) if cost else fallback_tokens

    return CliJsonResult(
        content=content,
        total_tokens=total_tokens,
        session_id=session_id,
        num_turns=num_turns,
    )


async def _invoke_claude_cli(
    args: list[str],
    input_text: str,
    timeout: float = 300,
    cwd: str = "/tmp",
) -> str:
    """
    Startet die Claude CLI als Subprocess und gibt stdout zurück.

    Prüft zuerst ob die CLI installiert ist, führt dann den Prozess aus
    und behandelt Fehler (Exit-Code != 0, Timeout). Der Timeout verhindert
    dass ein hängender CLI-Prozess den Server blockiert.

    cwd: Arbeitsverzeichnis für den CLI-Prozess. Standard /tmp um zu
    verhindern dass die CLI ein verschachteltes Projektverzeichnis anlegt.
    Für den Agent-Modus kann hier das Memory-Dir übergeben werden.
    """
    claude_path = shutil.which("claude")
    if not claude_path:
        raise RuntimeError(
            "Claude CLI nicht gefunden. Installiere mit: "
            "npm install -g @anthropic-ai/claude-code"
        )

    # Vollständige Argumentliste: claude-Pfad + übergebene Argumente
    full_args = [claude_path, *args]

    process = await asyncio.create_subprocess_exec(
        *full_args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=input_text.encode("utf-8")),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        process.kill()
        raise RuntimeError(
            f"Claude CLI Timeout nach {timeout}s -- Prozess wurde beendet"
        )

    if process.returncode != 0:
        error_msg = stderr.decode("utf-8", errors="replace").strip()
        logger.error("Claude CLI Fehler (Exit %d): %s", process.returncode, error_msg)
        raise RuntimeError(f"Claude CLI fehlgeschlagen: {error_msg}")

    return stdout.decode("utf-8").strip()


# ─── Tool-Constraints für den Agent-Modus ────────────────────────

def _build_tool_constraints(memory_dir: str) -> str:
    """
    Erzeugt die Sicherheitsregeln für den Dream-Agent.

    Grundprinzip: Bash ist NUR zum Lesen erlaubt. Jede Datei-Aenderung
    muss über die Write/Edit-Tools laufen, und zwar ausschließlich
    innerhalb des Memory-Verzeichnisses. So wird verhindert, dass der
    Agent versehentlich das Hostsystem verändert.
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


# ─── Claude-Abo (CLI mit JSON-Output für exakte Token-Zahlen) ───
#
# OAuth direkt über die Anthropic API funktioniert NICHT für Abo-Nutzer:
# "OAuth authentication is currently not supported." (API-Antwort)
# Claude Code nutzt intern einen speziellen First-Party-Endpunkt mit seiner
# eigenen Client-ID, der für externe Aufrufe nicht zugänglich ist.
#
# Die CLI ist deshalb der einzige Weg für Abo-Nutzer. Sie nutzt intern
# die gleiche OAuth-Auth wie Claude Code und hat Zugriff auf den
# First-Party-Endpunkt mit Prompt-Caching.


async def _complete_claude_abo(
    system_prompt: str, user_prompt: str,
) -> tuple[str, int]:
    """
    Einfache Text-Completion über die Claude Code CLI.
    Kombiniert System- und User-Prompt und gibt Antwort + Token-Verbrauch zurück.
    """
    full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"

    raw = await _invoke_claude_cli(
        args=["--print", "--output-format", "json"],
        input_text=full_prompt,
    )

    result = _parse_cli_json_output(raw, fallback_word_sources=[full_prompt, raw])

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
    für Cache-Sharing zwischen Dreams.
    """
    tool_constraints = _build_tool_constraints(memory_dir)
    cli_args = _build_dream_cli_args(tool_constraints, resume_session_id)

    # CWD auf das Memory-Dir setzen, damit die CLI das richtige Projektverzeichnis
    # findet und der Agent direkt dort schreiben kann.
    raw = await _invoke_claude_cli(
        args=cli_args,
        input_text=prompt,
        timeout=300,  # 5 Minuten max für Agent-Modus
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
    Baut die CLI-Argumente für den Dream-Agent zusammen.
    Trennt die Argument-Logik von der Prozess-Ausführung für bessere Testbarkeit.
    """
    args = [
        "--print",
        "--output-format", "json",
        "--permission-mode", "bypassPermissions",
        "--max-turns", "20",
        "--allowedTools", "Bash,Read,Write,Edit,Grep,Glob",
        "--append-system-prompt", tool_constraints,
    ]

    # Resume-Support: Gleiche Session -> gleicher Prompt-Cache
    if resume_session_id:
        args.extend(["--resume", resume_session_id])
        logger.info("Dream-Agent nutzt --resume %s für Cache-Sharing", resume_session_id)

    return args


# ─── Öffentliche API-Funktionen ─────────────────────────────────


async def complete(
    provider: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> tuple[str, int]:
    """
    Sendet einen Prompt an den konfigurierten KI-Anbieter.

    Unterstuetzte Provider:
    - "claude-abo": Nutzt Claude Code CLI (bestehendes Abo, kein API-Key nötig)
    - "anthropic": Nutzt die Anthropic API (API-Key nötig)
    - "openai": Nutzt die OpenAI API (API-Key nötig)

    Rückgabe: (Antworttext, verbrauchte Tokens)
    """
    dispatch = {
        "claude-abo": (lambda: _complete_claude_abo(system_prompt, user_prompt), "Claude-Abo"),
        "anthropic": (lambda: _complete_anthropic(model, system_prompt, user_prompt), "Anthropic"),
        "openai": (lambda: _complete_openai(model, system_prompt, user_prompt), "OpenAI"),
    }

    if provider not in dispatch:
        raise ValueError(f"Unbekannter KI-Anbieter: {provider}")

    factory, label = dispatch[provider]
    return await _with_retry(factory, label=label)


async def dream_with_tools(
    provider: str,
    model: str,
    prompt: str,
    memory_dir: str,
    resume_session_id: str | None = None,
) -> tuple[str, int, str | None]:
    """
    Dream-Agent mit Tool-Zugriff -- 1:1 wie Claude Code's runForkedAgent().

    Der Agent kann:
    - Bash lesen (ls, grep, cat, head, tail -- read-only)
    - Dateien schreiben (Edit, Write -- nur im memory_dir)
    - Dateien lesen (Read, Grep, Glob)

    Resume-Support: Wenn resume_session_id gesetzt, wird --resume für
    Cache-Sharing zwischen Dreams genutzt.

    Rückgabe: (content, tokens, new_session_id)
    """
    if provider == "claude-abo":
        return await _dream_claude_abo_agent(prompt, memory_dir, resume_session_id)
    else:
        # Andere Provider unterstuetzen keinen Agent-Modus -- Fallback auf einfache Completion
        content, tokens = await complete(provider, model, prompt, "")
        return content, tokens, None


# ─── Anthropic API (API-Key) ───────────────────────────────────────

async def _complete_anthropic(
    model: str, system_prompt: str, user_prompt: str,
) -> tuple[str, int]:
    """Anfrage an die Anthropic Claude API mit API-Key."""
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    response = await client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    content = response.content[0].text
    tokens_used = (response.usage.input_tokens or 0) + (response.usage.output_tokens or 0)

    logger.info("Anthropic Dream abgeschlossen: %d Tokens", tokens_used)
    return content, tokens_used


async def complete_with_cache(
    model: str,
    system_prompt: str,
    user_prompt: str,
    existing_memories_context: str = "",
) -> tuple[str, int]:
    """
    Anthropic API mit Prompt-Caching (cache_control Bloecke).

    Cache-Strategie:
    - System-Prompt (Consolidation-Prompt) -> cache_control: ephemeral
      (bleibt gleich über viele Dreams, spart ~90% Input-Tokens)
    - Bestehende Memories -> cache_control: ephemeral
      (ändert sich selten, großer Block)
    - User-Prompt (neue Sessions) -> nicht gecached (ändert sich immer)

    Anthropic's API cached automatisch identische Prompt-Prefixe.
    Mit expliziten cache_control Bloecken maximieren wir die Cache-Hits.
    """
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    # System-Prompt mit Cache-Breakpoints aufbauen
    system_blocks = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        },
    ]

    # Bestehende Memories als separater gecachter Block
    if existing_memories_context:
        system_blocks.append({
            "type": "text",
            "text": f"\n\n## Existing memories context\n\n{existing_memories_context}",
            "cache_control": {"type": "ephemeral"},
        })

    response = await client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_blocks,
        messages=[{"role": "user", "content": user_prompt}],
    )

    content = response.content[0].text
    input_tokens = response.usage.input_tokens or 0
    output_tokens = response.usage.output_tokens or 0
    cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
    cache_create = getattr(response.usage, "cache_creation_input_tokens", 0) or 0

    total_tokens = input_tokens + output_tokens + cache_read + cache_create
    # Cache-Hit-Rate berechnen: Wie viel Prozent der Input-Tokens kamen aus dem Cache?
    total_input = input_tokens + cache_read + cache_create
    hit_pct = (cache_read / total_input * 100) if total_input > 0 else 0

    logger.info(
        "Anthropic Cached Dream: %d Tokens (input=%d output=%d "
        "cache_read=%d cache_create=%d, %.1f%% Cache-Hit)",
        total_tokens, input_tokens, output_tokens,
        cache_read, cache_create, hit_pct,
    )
    return content, total_tokens


# ─── OpenAI API ────────────────────────────────────────────────────

async def _complete_openai(
    model: str, system_prompt: str, user_prompt: str,
) -> tuple[str, int]:
    """Anfrage an die OpenAI API."""
    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=4096,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content or ""
    tokens_used = response.usage.total_tokens if response.usage else 0

    logger.info("OpenAI Dream abgeschlossen: %d Tokens", tokens_used)
    return content, tokens_used
