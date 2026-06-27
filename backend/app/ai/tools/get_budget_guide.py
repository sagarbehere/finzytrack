"""get_budget_guide tool — returns the budgeting primer on demand.

Loaded when the user asks about budgets or wants a budget dashboard (mirrors
get_recipe_schema). The primer covers the `custom "budget"` directive + semantics,
how budgets map onto the recipe DAG (budget_for_range + the budget transforms),
and the styles → demo-dashboard map (dev-docs/budget.md §17).
"""

import logging
from functools import lru_cache
from pathlib import Path

from app.ai.tools.base import BaseTool

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parents[3] / "resources" / "prompts"


@lru_cache(maxsize=1)
def _load_guide() -> str:
    return (_PROMPTS_DIR / "budget_guide.md").read_text(encoding="utf-8").strip()


class GetBudgetGuideTool(BaseTool):
    @property
    def name(self) -> str:
        return "get_budget_guide"

    @property
    def description(self) -> str:
        return (
            "Return the Finzytrack budgeting primer. Call this when the user asks about "
            "budgets or wants a budget dashboard. Covers the custom \"budget\" directive "
            "and its semantics, how budgets map onto the recipe DAG (budget_for_range + "
            "the budget transforms), and which seeded demo dashboard implements each "
            "budgeting style."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self) -> dict:
        try:
            return {"success": True, "guide": _load_guide()}
        except FileNotFoundError:
            return {"success": False, "error": "Budget guide file not found"}
