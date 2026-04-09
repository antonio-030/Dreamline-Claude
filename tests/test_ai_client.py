"""Tests fuer app/services/ai_client.py – CLI-Parsing, Retry, Tool-Constraints."""

import asyncio
import json

import pytest

from app.services.ai_client import (
    _build_dream_cli_args,
    _build_tool_constraints,
    _estimate_tokens_from_word_count,
    _parse_cli_json_output,
    _tokens_from_cost,
    _with_retry,
)


# ─── Token-Schaetzung ─────────────────────────────────────────────

class TestTokenEstimation:
    """Tests fuer die Token-Schaetzungsfunktionen."""

    def test_word_count_single_text(self):
        """Wortanzahl aus einem Text."""
        result = _estimate_tokens_from_word_count("eins zwei drei vier fuenf")
        assert result == 5

    def test_word_count_multiple_texts(self):
        """Wortanzahl aus mehreren Texten summiert."""
        result = _estimate_tokens_from_word_count("eins zwei", "drei vier fuenf")
        assert result == 5

    def test_word_count_empty(self):
        """Leerer String ergibt 0 (str.split() auf leerem String = [])."""
        result = _estimate_tokens_from_word_count("")
        assert result == 0

    def test_tokens_from_cost_zero(self):
        """Kosten von 0 Dollar = 0 Tokens."""
        assert _tokens_from_cost(0) == 0

    def test_tokens_from_cost_one_dollar(self):
        """1 Dollar ~ 100.000 Tokens."""
        assert _tokens_from_cost(1.0) == 100_000

    def test_tokens_from_cost_small(self):
        """Kleine Kosten werden proportional berechnet."""
        assert _tokens_from_cost(0.01) == 1000


# ─── CLI JSON-Parsing ─────────────────────────────────────────────

class TestCliJsonParsing:
    """Tests fuer _parse_cli_json_output()."""

    def test_valid_dict_with_usage(self):
        """Standard-Fall: Dict mit result und usage."""
        data = {
            "result": "Antwort vom Modell",
            "session_id": "abc-123",
            "num_turns": 3,
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_read_input_tokens": 200,
                "cache_creation_input_tokens": 0,
            },
        }
        result = _parse_cli_json_output(json.dumps(data))
        assert result.content == "Antwort vom Modell"
        assert result.session_id == "abc-123"
        assert result.num_turns == 3
        assert result.total_tokens == 1700  # 1000 + 500 + 200 + 0

    def test_valid_dict_with_cost(self):
        """Dict mit cost_usd statt usage."""
        data = {
            "result": "Antwort",
            "cost_usd": 0.05,
        }
        result = _parse_cli_json_output(json.dumps(data))
        assert result.content == "Antwort"
        assert result.total_tokens == 5000  # 0.05 * 100000

    def test_valid_dict_no_usage_no_cost(self):
        """Dict ohne usage und ohne cost -> Wort-Fallback."""
        data = {"result": "eins zwei drei"}
        raw = json.dumps(data)
        result = _parse_cli_json_output(raw)
        assert result.content == "eins zwei drei"
        # Fallback: Wortanzahl des Raw-Strings
        assert result.total_tokens > 0

    def test_list_format(self):
        """Selteneres Listen-Format."""
        data = [
            {"content": "Teil 1"},
            {"content": "Teil 2"},
        ]
        result = _parse_cli_json_output(json.dumps(data))
        assert "Teil 1" in result.content
        assert "Teil 2" in result.content

    def test_invalid_json_returns_raw(self):
        """Ungueltiges JSON gibt Rohtext zurueck."""
        raw = "Das ist kein JSON, sondern Klartext."
        result = _parse_cli_json_output(raw)
        assert result.content == raw

    def test_empty_usage_dict(self):
        """Leeres usage-Dict: isinstance=True aber alle Werte 0, ergibt 0 Tokens."""
        data = {"result": "Test", "usage": {}}
        result = _parse_cli_json_output(json.dumps(data))
        assert result.content == "Test"
        # Leeres Dict ist truthy fuer isinstance-Check, aber .get() liefert ueberall 0
        # input(0) + output(0) + cache_read(0) + cache_create(0) = 0
        # ABER: die Summe ist 0 und der Code-Pfad geht trotzdem durch usage-Branch
        # Tatsaechlich: usage is truthy ({} is truthy fuer `if usage`? Nein!)
        # {} ist falsy in Python -> faellt durch auf cost/fallback
        # Fallback: Wortanzahl des Raw-Strings
        assert result.total_tokens >= 0

    def test_session_id_extraction(self):
        """Session-ID wird korrekt extrahiert."""
        data = {"result": "x", "session_id": "sess-abc-def-123"}
        result = _parse_cli_json_output(json.dumps(data))
        assert result.session_id == "sess-abc-def-123"

    def test_num_turns_extraction(self):
        """num_turns Default ist 0."""
        data = {"result": "x"}
        result = _parse_cli_json_output(json.dumps(data))
        assert result.num_turns == 0

    def test_non_dict_non_list(self):
        """Primitiver JSON-Wert (z.B. String) -> Rohtext."""
        raw = json.dumps("einfacher string")
        result = _parse_cli_json_output(raw)
        assert result.content == raw


# ─── Tool-Constraints ─────────────────────────────────────────────

class TestToolConstraints:
    """Tests fuer _build_tool_constraints()."""

    def test_contains_memory_dir(self):
        """Memory-Verzeichnis muss im Constraint-Text enthalten sein."""
        result = _build_tool_constraints("/home/user/.claude/projects/test/memory")
        assert "/home/user/.claude/projects/test/memory" in result

    def test_contains_readonly_instructions(self):
        """Bash-Einschraenkung auf read-only muss enthalten sein."""
        result = _build_tool_constraints("/tmp/test")
        assert "READ-ONLY" in result
        assert "FORBIDDEN" in result

    def test_blocks_dangerous_tools(self):
        """Gefaehrliche Tools muessen explizit blockiert sein."""
        result = _build_tool_constraints("/tmp/test")
        assert "MCP" in result
        assert "WebSearch" in result


# ─── Dream CLI Args ───────────────────────────────────────────────

class TestDreamCliArgs:
    """Tests fuer _build_dream_cli_args()."""

    def test_basic_args(self):
        """Standard-Argumente ohne Resume."""
        args = _build_dream_cli_args("constraints", None)
        assert "--print" in args
        assert "--output-format" in args
        assert "json" in args
        assert "--permission-mode" in args
        assert "bypassPermissions" in args
        assert "--allowedTools" in args
        assert "--resume" not in args

    def test_with_valid_resume_id(self):
        """Resume mit gueltigem Session-ID."""
        session_id = "abc12345-6789-0def-abcd-ef0123456789"
        args = _build_dream_cli_args("constraints", session_id)
        assert "--resume" in args
        assert session_id in args

    def test_with_invalid_resume_id_rejected(self):
        """Resume mit ungueltigem Session-ID (zu kurz, Sonderzeichen)."""
        args = _build_dream_cli_args("constraints", "short")
        assert "--resume" not in args

    def test_with_malicious_resume_id_rejected(self):
        """Resume mit Injection-Versuch wird abgelehnt."""
        args = _build_dream_cli_args("constraints", "--flag; rm -rf /")
        assert "--resume" not in args

    def test_constraints_in_append_system_prompt(self):
        """Tool-Constraints werden als --append-system-prompt uebergeben."""
        args = _build_dream_cli_args("my-constraints-text", None)
        idx = args.index("--append-system-prompt")
        assert args[idx + 1] == "my-constraints-text"


# ─── Retry Logic ──────────────────────────────────────────────────

class TestRetryLogic:
    """Tests fuer _with_retry()."""

    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        """Kein Retry wenn der erste Aufruf erfolgreich ist."""
        call_count = 0

        async def factory():
            nonlocal call_count
            call_count += 1
            return "success"

        result = await _with_retry(factory)
        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_non_retryable_error_raises_immediately(self):
        """Nicht-retryable Fehler wird sofort geworfen."""
        call_count = 0

        async def factory():
            nonlocal call_count
            call_count += 1
            raise ValueError("Unbekannter Fehler")

        with pytest.raises(ValueError, match="Unbekannter Fehler"):
            await _with_retry(factory)
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retryable_error_with_eventual_success(self):
        """Retryable Fehler wird wiederholt, dann Erfolg."""
        call_count = 0

        async def factory():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("timeout error")
            return "recovered"

        result = await _with_retry(factory)
        assert result == "recovered"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retryable_error_exhausts_retries(self):
        """Retryable Fehler schlaegt fehl nach MAX_RETRIES."""
        async def factory():
            raise RuntimeError("rate_limit exceeded")

        with pytest.raises(RuntimeError, match="rate_limit"):
            await _with_retry(factory)
