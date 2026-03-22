import json
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
        try:
            return await tool.execute(**arguments)
        except TypeError as e:
            return {"success": False, "error": f"Invalid arguments for tool '{name}': {e}"}
        except Exception as e:
            logger.error(f"Tool '{name}' raised an exception: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
