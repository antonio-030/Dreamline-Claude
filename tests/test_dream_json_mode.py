"""Tests fuer den gehaerteten JSON-Modus der Dream-Konsolidierung.

Verifiziert Option-A-Fix: Im JSON-Modus nutzt Claude KEINE Tools
(--tools "") und genau EINEN Turn (--max-turns 1), und der System-Prompt
enthaelt keine Tool-Anweisungen mehr. Das verhindert error_max_turns /
abgeschnittenes JSON, die haeufigste Dream-Fehlerursache bei grossen Projekten.
"""

from unittest.mock import patch

import pytest

from app.services.ai_common import CliJsonResult
from app.services.dream_prompts import (
    CONSOLIDATION_SYSTEM_PROMPT,
    CONSOLIDATION_SYSTEM_PROMPT_JSON,
    build_consolidation_system_prompt,
)


# ─── CLI-Argumente: Tools aus + ein Turn ─────────────────────────

class TestClaudeAboJsonModeArgs:
    """_complete_claude_abo darf im JSON-Modus keine Tools und nur 1 Turn erlauben."""

    @pytest.mark.asyncio
    async def test_disables_tools_and_single_turn(self):
        from app.services.ai_cli_provider import _complete_claude_abo

        captured = {}

        async def capture_cli(binary, args, input_text, **kwargs):
            captured["binary"] = binary
            captured["args"] = args
            return '{"type":"result","subtype":"success","result":"{}"}'

        with patch("app.services.ai_cli_provider._invoke_cli", side_effect=capture_cli), \
             patch(
                 "app.services.ai_cli_provider._parse_cli_json_output",
                 return_value=CliJsonResult(content='{"operations": []}', total_tokens=10, num_turns=1),
             ):
            await _complete_claude_abo("System", "User")

        args = captured["args"]
        assert captured["binary"] == "claude"
        # Tools komplett deaktiviert: --tools gefolgt von leerem String
        assert "--tools" in args
        assert args[args.index("--tools") + 1] == ""
        # Genau ein Turn
        assert "--max-turns" in args
        assert args[args.index("--max-turns") + 1] == "1"
        # JSON-Output bleibt erhalten
        assert "--output-format" in args
        assert "json" in args


# ─── System-Prompt-Varianten ─────────────────────────────────────

class TestConsolidationPromptVariants:
    """Agent-Variante nutzt Tools, JSON-Variante nicht — Kernregeln in beiden."""

    def test_agent_prompt_has_tool_instructions(self):
        assert "the memory directory" in CONSOLIDATION_SYSTEM_PROMPT
        assert "grep -rn" in CONSOLIDATION_SYSTEM_PROMPT

    def test_json_prompt_has_no_tool_instructions(self):
        assert "grep -rn" not in CONSOLIDATION_SYSTEM_PROMPT_JSON
        assert "NO tools available" in CONSOLIDATION_SYSTEM_PROMPT_JSON
        assert "provided INLINE" in CONSOLIDATION_SYSTEM_PROMPT_JSON

    def test_both_share_core_rules(self):
        for prompt in (CONSOLIDATION_SYSTEM_PROMPT, CONSOLIDATION_SYSTEM_PROMPT_JSON):
            assert "Respond EXCLUSIVELY with valid JSON" in prompt
            assert "Deduplizierung" in prompt
            assert "Confidence scoring" in prompt

    def test_builder_matches_constants(self):
        assert build_consolidation_system_prompt(use_agent_mode=True) == CONSOLIDATION_SYSTEM_PROMPT
        assert build_consolidation_system_prompt(use_agent_mode=False) == CONSOLIDATION_SYSTEM_PROMPT_JSON

    def test_variants_differ(self):
        assert CONSOLIDATION_SYSTEM_PROMPT != CONSOLIDATION_SYSTEM_PROMPT_JSON
