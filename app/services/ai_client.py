"""
KI-Client-Wrapper -- unterstützt Claude-Abo (via CLI), Anthropic API und OpenAI API.

Fuer das Claude-Abo wird die Claude Code CLI genutzt -- diese authentifiziert
sich automatisch über die gespeicherten OAuth-Credentials (~/.claude/).

Zwei Modi:
- complete(): Einfacher Text-Prompt -> Text-Antwort (für JSON-Operationen)
- dream_with_tools(): Agent-Modus mit Tool-Zugriff (1:1 wie Claude Code autoDream)

Aufgeteilt in Submodule:
- ai_common: Retry, CLI-Parsing, Token-Schaetzung, _invoke_cli
- ai_cli_provider: Claude-Abo (CLI), Codex-Sub (CLI)
- ai_api_provider: Anthropic API, OpenAI API, Ollama
"""

import asyncio
import logging
import shutil

from app.config import settings

from app.services.ai_common import _with_retry
from app.services.ai_cli_provider import (
    _complete_claude_abo,
    _dream_claude_abo_agent,
    _complete_codex_sub,
)
from app.services.ai_api_provider import (
    _complete_anthropic,
    _complete_openai,
    _complete_ollama,
    complete_with_cache,
)

# Re-Exports fuer Abwaertskompatibilitaet (Tests + Services importieren diese direkt)
from app.services.ai_common import (  # noqa: F401
    CliJsonResult,
    _estimate_tokens_from_word_count,
    _invoke_cli,
    _parse_cli_json_output,
    _tokens_from_cost,
)
from app.services.ai_cli_provider import (  # noqa: F401
    _build_dream_cli_args,
    _build_tool_constraints,
)

logger = logging.getLogger(__name__)


# ─── Oeffentliche API-Funktionen ─────────────────────────────────


async def complete(
    provider: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> tuple[str, int]:
    """
    Sendet einen Prompt an den konfigurierten KI-Anbieter.

    Unterstuetzte Provider:
    - "claude-abo": Nutzt Claude Code CLI (bestehendes Abo, kein API-Key noetig)
    - "codex-sub": Nutzt Codex CLI (bestehendes OpenAI Abo, kein API-Key noetig)
    - "anthropic": Nutzt die Anthropic API (API-Key noetig)
    - "openai": Nutzt die OpenAI API (API-Key noetig)
    - "ollama": Nutzt ein lokales Ollama-Modell

    Rueckgabe: (Antworttext, verbrauchte Tokens)
    """
    dispatch = {
        "claude-abo": (lambda: _complete_claude_abo(system_prompt, user_prompt), "Claude-Abo"),
        "codex-sub": (lambda: _complete_codex_sub(model, system_prompt, user_prompt), "Codex-Sub"),
        "anthropic": (lambda: _complete_anthropic(model, system_prompt, user_prompt), "Anthropic"),
        "openai": (lambda: _complete_openai(model, system_prompt, user_prompt), "OpenAI"),
        "ollama": (lambda: _complete_ollama(model, system_prompt, user_prompt), "Ollama"),
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

    Resume-Support: Wenn resume_session_id gesetzt, wird --resume fuer
    Cache-Sharing zwischen Dreams genutzt.

    Rueckgabe: (content, tokens, new_session_id)
    """
    if provider == "claude-abo":
        return await _dream_claude_abo_agent(prompt, memory_dir, resume_session_id)
    else:
        # Andere Provider unterstuetzen keinen Agent-Modus -- Fallback auf einfache Completion
        content, tokens = await complete(provider, model, prompt, "")
        return content, tokens, None


# ─── Provider Health Check ────────────────────────────────────────

async def check_provider_health(provider: str, model: str) -> dict:
    """
    Prueft ob ein KI-Provider erreichbar und funktionsfaehig ist.
    Gibt zurueck: {available: bool, error: str|None, provider: str, model: str}
    """
    import time
    start = time.monotonic()
    try:
        if provider in ("claude-abo", "codex-sub"):
            binary = "claude" if provider == "claude-abo" else "codex"
            path = shutil.which(binary)
            if not path:
                return {"available": False, "error": f"'{binary}' CLI nicht gefunden", "provider": provider, "model": model}
            proc = await asyncio.create_subprocess_exec(
                path, "--version",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            latency_ms = int((time.monotonic() - start) * 1000)
            return {"available": True, "error": None, "provider": provider, "model": model, "latency_ms": latency_ms}

        elif provider == "anthropic":
            if not settings.anthropic_api_key:
                return {"available": False, "error": "ANTHROPIC_API_KEY nicht gesetzt", "provider": provider, "model": model}
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            await client.messages.create(
                model=model, max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
            latency_ms = int((time.monotonic() - start) * 1000)
            return {"available": True, "error": None, "provider": provider, "model": model, "latency_ms": latency_ms}

        elif provider == "openai":
            if not settings.openai_api_key:
                return {"available": False, "error": "OPENAI_API_KEY nicht gesetzt", "provider": provider, "model": model}
            import openai
            client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
            await client.chat.completions.create(
                model=model, max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
            latency_ms = int((time.monotonic() - start) * 1000)
            return {"available": True, "error": None, "provider": provider, "model": model, "latency_ms": latency_ms}

        elif provider == "ollama":
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{settings.ollama_base_url}/api/tags")
                resp.raise_for_status()
            latency_ms = int((time.monotonic() - start) * 1000)
            return {"available": True, "error": None, "provider": provider, "model": model, "latency_ms": latency_ms}

        else:
            return {"available": False, "error": f"Unbekannter Provider: {provider}", "provider": provider, "model": model}

    except Exception as e:
        return {"available": False, "error": str(e)[:500], "provider": provider, "model": model}
