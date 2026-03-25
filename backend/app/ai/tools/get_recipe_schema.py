"""
get_recipe_schema tool — returns the dashboard recipe JSON schema on demand.

Keeps the system prompt small by only loading the schema when the LLM
actually needs to generate a dashboard recipe.
"""

import logging
from functools import lru_cache
from pathlib import Path

from app.ai.tools.base import BaseTool

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parents[3] / "resources" / "prompts"


@lru_cache(maxsize=1)
def _load_schema() -> str:
    path = _PROMPTS_DIR / "schema_recipe_dashboard.md"
    return path.read_text(encoding="utf-8").strip()


class GetRecipeSchemaTool(BaseTool):
    @property
    def name(self) -> str:
        return "get_recipe_schema"

    @property
    def description(self) -> str:
        return (
            "Return the complete dashboard recipe JSON schema documentation. "
            "Call this ONCE before building your first dashboard recipe. It describes "
            "all visualization types (KPI, bar, line, pie, treemap, table, pivot), "
            "CSS Grid layout, parameters, generators, transforms, click-through links, "
            "and includes complete examples. You do not need to call this for financial "
            "analysis questions — only when generating dashboards."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
        }

    async def execute(self) -> dict:
        try:
            schema = _load_schema()
            return {"success": True, "schema": schema}
        except FileNotFoundError:
            return {"success": False, "error": "Recipe schema file not found"}
