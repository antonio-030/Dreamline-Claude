"""
Gemeinsame Hilfsfunktionen fuer alle KI-Provider.

Enthaelt Retry-Logik, CLI-JSON-Parsing, Token-Schaetzung und den
generischen CLI-Aufruf (_invoke_cli). Diese Funktionen werden von
ai_cli_provider und ai_api_provider genutzt.
"""

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)

# ─── Retry-Logic (1:1 wie withRetry in claude.ts) ────────────────

MAX_RETRIES = settings.ai_max_retries
BACKOFF_BASE_SECONDS = settings.ai_backoff_base_seconds

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
        except (RuntimeError, OSError, TimeoutError, ValueError) as e:
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


# ─── Datenklasse fuer CLI-Ergebnisse ─────────────────────────────

@dataclass
class CliJsonResult:
    """Strukturiertes Ergebnis aus dem JSON-Output der Claude CLI."""
    content: str
    total_tokens: int
    session_id: str | None = None
    num_turns: int = 0


# ─── Token-Schaetzung ────────────────────────────────────────────

def _estimate_tokens_from_word_count(*texts: str) -> int:
    """
    Grobe Token-Schaetzung anhand der Wortanzahl.
    Wird als Fallback genutzt wenn weder Kosten noch Usage-Daten vorliegen.
    """
    return sum(len(t.split()) for t in texts)


def _tokens_from_cost(cost: float) -> int:
    """
    Berechnet geschaetzte Tokens aus den USD-Kosten.

    Hintergrund: Claude Sonnet Pricing liegt bei ca. $3 Input / $15 Output
    pro 1M Tokens. Als Mittelwert ergibt sich ca. $10 pro 1M Tokens,
    also ca. 100.000 Tokens pro Dollar. Der Faktor 100000 ist daher eine
    brauchbare Naeherung fuer die Gesamtzahl verbrauchter Tokens.
    """
    return int(cost * 100_000)


# ─── CLI-Helper: JSON-Parsing ────────────────────────────────────

def _parse_cli_json_output(raw: str, fallback_word_sources: list[str] | None = None) -> CliJsonResult:
    """
    Parst den JSON-Output der Claude CLI und extrahiert Content, Tokens und Session-ID.

    Die CLI liefert entweder ein Dict (Standardfall) oder eine Liste (selten).
    Bei Parse-Fehlern wird der Rohtext als Content zurueckgegeben mit einer
    Wortanzahl-basierten Token-Schaetzung als Fallback.
    """
    fallback_tokens = _estimate_tokens_from_word_count(*(fallback_word_sources or [raw]))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # CLI hat keinen gueltigen JSON-Output geliefert -- Rohtext verwenden
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

    # Token-Berechnung: Praeferenz -> usage-Feld > Kosten-Feld > Wortschaetzung
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


# ─── CLI stderr-Filterung ───────────────────────────────────────

# Bekannte harmlose CLI-Warnungen (z.B. read-only FS in Docker)
_HARMLESS_STDERR_PATTERNS = (
    "could not update PATH",
    "Read-only file system",
    "proceeding, even though",
)


def _strip_cli_warnings(text: str) -> str:
    """Entfernt bekannte CLI-Warnzeilen (WARNING:...) aus dem Output."""
    lines = text.splitlines()
    cleaned = [
        line for line in lines
        if not any(p in line for p in _HARMLESS_STDERR_PATTERNS)
    ]
    return "\n".join(cleaned).strip()


def _filter_stderr(stderr_text: str) -> str:
    """Filtert harmlose Warnungen aus stderr, gibt nur echte Fehler zurueck."""
    lines = stderr_text.splitlines()
    real_errors = [
        line for line in lines
        if not any(p in line for p in _HARMLESS_STDERR_PATTERNS)
    ]
    return "\n".join(real_errors).strip()


# ─── Generischer CLI-Aufruf ─────────────────────────────────────

async def _invoke_cli(
    binary: str,
    args: list[str],
    input_text: str,
    timeout: float = settings.ai_cli_timeout_seconds,
    cwd: str = "/tmp",
) -> str:
    """
    Startet eine CLI (claude/codex) als Subprocess und gibt stdout zurueck.

    Prueft zuerst ob das Binary installiert ist, fuehrt dann den Prozess aus
    und behandelt Fehler (Exit-Code != 0, Timeout). Der Timeout verhindert
    dass ein haengender CLI-Prozess den Server blockiert.

    binary: Name des CLI-Binaries (z.B. "claude" oder "codex").
    cwd: Arbeitsverzeichnis fuer den CLI-Prozess.
    """
    binary_path = shutil.which(binary)
    if not binary_path:
        raise RuntimeError(f"CLI '{binary}' nicht gefunden (nicht auf PATH)")

    full_args = [binary_path, *args]

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
        await process.wait()  # Zombie-Prozess verhindern
        raise RuntimeError(f"{binary} CLI Timeout nach {timeout}s")

    raw_stdout = stdout.decode("utf-8", errors="replace").strip()

    if process.returncode != 0:
        # error_max_turns: CLI gab Exit 1, aber stdout enthaelt ggf. das Ergebnis
        # (passiert wenn Claude intern Tools nutzt und max-turns erreicht wird)
        if raw_stdout and '"result"' in raw_stdout:
            try:
                data = json.loads(raw_stdout)
                if data.get("result"):
                    logger.warning("%s CLI Exit %d aber result vorhanden (subtype: %s)", binary, process.returncode, data.get("subtype", "?"))
                    return raw_stdout
            except json.JSONDecodeError:
                pass

        raw_stderr = stderr.decode("utf-8", errors="replace").strip()
        error_msg = _filter_stderr(raw_stderr)
        stdout_hint = raw_stdout[:500] if raw_stdout else ""
        combined = error_msg or stdout_hint or "(keine Ausgabe)"
        logger.error("%s CLI Fehler (Exit %d): stderr=%s stdout=%s", binary, process.returncode, error_msg[:200], stdout_hint[:200])
        raise RuntimeError(f"{binary} CLI fehlgeschlagen: {combined}")

    return raw_stdout
