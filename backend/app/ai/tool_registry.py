import logging
from typing import Literal

import jsonschema

from app.ai.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get_schemas(self, provider: Literal["openai", "anthropic"]) -> list[dict]:
        if provider == "anthropic":
            return [t.to_anthropic_schema() for t in self._tools.values()]
        return [t.to_openai_schema() for t in self._tools.values()]

    async def execute(self, name: str, arguments: dict) -> dict:
        tool = self._tools.get(name)
        if not tool:
            return {"success": False, "error": f"Unknown tool: {name}"}
        # Strip any keys the tool didn't declare — LLMs occasionally hallucinate extras
        schema = tool.parameters_schema
        declared = set(schema.get("properties", {}).keys())
        filtered = {k: v for k, v in arguments.items() if k in declared}

        # Check required args before calling — gives the LLM an actionable error
        required = set(schema.get("required", []))
        missing = required - set(filtered.keys())
        if missing:
            return {
                "success": False,
                "error": f"Missing required arguments: {', '.join(sorted(missing))}. "
                         f"Please call {name} again with all required arguments.",
            }

        # Full type validation against the declared schema. The above filter
        # only checks key membership; this catches LLM-emitted values whose
        # type or constraints don't match (e.g. a string where an integer
        # was declared). Returning an actionable error lets the model retry
        # with the right shape rather than crashing the tool with a
        # TypeError mid-execution.
        try:
            jsonschema.validate(instance=filtered, schema=schema)
        except jsonschema.ValidationError as e:
            return {
                "success": False,
                "error": (
                    f"Invalid arguments for tool '{name}': {e.message}. "
                    f"Path: {'/'.join(str(p) for p in e.absolute_path) or '(root)'}. "
                    f"Please call {name} again with arguments matching its schema."
                ),
            }

        try:
            return await tool.execute(**filtered)
        except Exception as e:
            logger.error(f"Tool '{name}' raised an exception: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
