"""Defense-in-depth against prompt injection via tool results.

Two pieces, locked together:
  1. ``build_system_prompt`` includes the tool-output-safety preamble in
     both setup mode and analyst mode, naming the
     ``<tool_result>...</tool_result>`` wrapper as the marker for
     "data, not instructions."
  2. ``ToolRegistry.execute`` validates the LLM's argument shape against
     each tool's ``parameters_schema`` and returns an actionable error
     when the shape is wrong, instead of letting the tool crash mid-run.

Realistic threat: adversary-controlled content (CSV/OFX merchant names,
imported emails, third-party rule files) lands in the user's data and
the AI reads it via tools. The wrapper + system prompt give the model
explicit instructions to treat such content as data.
"""
import pytest

from app.ai.system_prompt import build_system_prompt
from app.ai.tool_registry import ToolRegistry
from app.ai.tools.base import BaseTool


class TestSafetyPreambleInSystemPrompt:
    def test_setup_mode_includes_tool_output_safety_section(self):
        prompt = build_system_prompt({"file_type": "csv"})
        assert "<tool_result>" in prompt
        assert "DATA, not INSTRUCTIONS" in prompt
        # Adversary-content rationale is the load-bearing reason — must be
        # spelled out so the model has the "why" alongside the "what."
        assert "adversary" in prompt.lower()

    def test_analyst_mode_includes_tool_output_safety_section(self):
        prompt = build_system_prompt({})
        assert "<tool_result>" in prompt
        assert "DATA, not INSTRUCTIONS" in prompt
        assert "adversary" in prompt.lower()


# ── Tool argument validation ────────────────────────────────────────────────


class _TypedTool(BaseTool):
    """A tiny stub tool with explicit types in its parameters_schema so we
    can exercise jsonschema validation paths without bringing in a real
    tool's I/O.
    """
    @property
    def name(self) -> str:
        return "typed_stub"

    @property
    def description(self) -> str:
        return "stub for tests"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
            },
            "required": ["query"],
        }

    async def execute(self, query: str, limit: int = 10) -> dict:
        return {"success": True, "echo": query, "limit": limit}


@pytest.fixture
def registry_with_typed_tool() -> ToolRegistry:
    r = ToolRegistry()
    r.register(_TypedTool())
    return r


async def _exec(registry: ToolRegistry, name: str, args: dict) -> dict:
    return await registry.execute(name, args)


class TestArgValidation:
    @pytest.mark.anyio
    async def test_valid_args_execute_normally(self, registry_with_typed_tool):
        result = await registry_with_typed_tool.execute(
            "typed_stub", {"query": "select 1", "limit": 5}
        )
        assert result["success"] is True
        assert result["echo"] == "select 1"

    @pytest.mark.anyio
    async def test_wrong_type_is_rejected_with_actionable_message(
        self, registry_with_typed_tool
    ):
        """LLM emits ``limit`` as a string ("ten") instead of an integer.
        The registry must catch this before the tool crashes with a
        TypeError, and return an error the LLM can recover from.
        """
        result = await registry_with_typed_tool.execute(
            "typed_stub", {"query": "select 1", "limit": "ten"}
        )
        assert result["success"] is False
        assert "Invalid arguments" in result["error"]
        # The path identifies which field tripped, so the LLM can fix it.
        assert "limit" in result["error"]

    @pytest.mark.anyio
    async def test_out_of_range_value_is_rejected(self, registry_with_typed_tool):
        """``limit`` has a max of 1000; the schema check enforces it."""
        result = await registry_with_typed_tool.execute(
            "typed_stub", {"query": "select 1", "limit": 999_999}
        )
        assert result["success"] is False
        assert "Invalid arguments" in result["error"]

    @pytest.mark.anyio
    async def test_missing_required_still_caught_with_friendly_message(
        self, registry_with_typed_tool
    ):
        """Pre-existing required-keys check still fires before schema
        validation — its error message is more pointed than jsonschema's.
        """
        result = await registry_with_typed_tool.execute("typed_stub", {})
        assert result["success"] is False
        assert "Missing required" in result["error"]

    @pytest.mark.anyio
    async def test_extra_hallucinated_keys_are_stripped_before_validation(
        self, registry_with_typed_tool
    ):
        """LLM-emitted extras don't fail the schema because we strip them
        before validating — keeping the pre-existing forgiveness intact.
        """
        result = await registry_with_typed_tool.execute(
            "typed_stub", {"query": "select 1", "limit": 5, "ghost": "extra"}
        )
        assert result["success"] is True


@pytest.fixture
def anyio_backend():
    return "asyncio"
