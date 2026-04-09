"""
API-basierte KI-Provider: Anthropic API, OpenAI API, Ollama (lokal).

Jeder Provider nutzt seinen eigenen API-Client mit Lazy-Import,
damit die jeweilige Abhaengigkeit nur geladen wird wenn der Provider
tatsaechlich genutzt wird.
"""

import logging

from app.config import settings

logger = logging.getLogger(__name__)


# ─── Anthropic API (API-Key) ───────────────────────────────────────

async def _complete_anthropic(
    model: str, system_prompt: str, user_prompt: str,
) -> tuple[str, int]:
    """Anfrage an die Anthropic Claude API mit API-Key."""
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    response = await client.messages.create(
        model=model,
        max_tokens=settings.ai_max_output_tokens,
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
      (bleibt gleich ueber viele Dreams, spart ~90% Input-Tokens)
    - Bestehende Memories -> cache_control: ephemeral
      (aendert sich selten, grosser Block)
    - User-Prompt (neue Sessions) -> nicht gecached (aendert sich immer)

    Anthropic's API cached automatisch identische Prompt-Prefixe.
    Mit expliziten cache_control Bloecken maximieren wir die Cache-Hits.
    """
    import anthropic
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
        max_tokens=settings.ai_max_output_tokens,
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
    import openai
    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=settings.ai_max_output_tokens,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content or ""
    tokens_used = response.usage.total_tokens if response.usage else 0

    logger.info("OpenAI Dream abgeschlossen: %d Tokens", tokens_used)
    return content, tokens_used


# ─── Ollama (lokale LLMs) ─────────────────────────────────────────

async def _complete_ollama(
    model: str, system_prompt: str, user_prompt: str,
) -> tuple[str, int]:
    """
    Sendet einen Prompt an ein lokales Ollama-Modell.

    Nutzt den /api/chat Endpoint mit Messages-Format.
    JSON-Mode wird ueber den format-Parameter erzwungen, damit
    strukturierte Antworten (Dream-Operationen, Extract-Fakten) zuverlaessig kommen.

    Token-Tracking: Ollama gibt eval_count (Output) und
    prompt_eval_count (Input) in der Response zurueck.
    """
    import httpx

    url = f"{settings.ollama_base_url}/api/chat"

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "format": "json",  # Erzwingt JSON-Output
        "stream": False,
        "options": {
            "temperature": settings.ai_ollama_temperature,  # Niedrig fuer konsistente, faktische Antworten
        },
    }

    async with httpx.AsyncClient(timeout=settings.ollama_timeout) as client:
        response = await client.post(url, json=body)
        response.raise_for_status()

    data = response.json()
    content = data.get("message", {}).get("content", "")

    # Token-Tracking aus Ollama-Response
    eval_count = data.get("eval_count", 0)
    prompt_eval_count = data.get("prompt_eval_count", 0)
    total_tokens = eval_count + prompt_eval_count

    logger.info(
        "Ollama (%s): %d Tokens (input=%d, output=%d)",
        model, total_tokens, prompt_eval_count, eval_count,
    )
    return content, total_tokens
