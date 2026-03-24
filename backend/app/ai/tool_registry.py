import logging
from typing import Literal

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

        try:
            return await tool.execute(**filtered)
        except Exception as e:
            logger.error(f"Tool '{name}' raised an exception: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
