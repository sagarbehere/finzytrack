"""execute_compute tool — runs a single registered compute function and returns
its result. The compute analog of execute_query: lets the assistant answer
analytical questions that aren't SQL-expressible (e.g. "what's my Food budget in
June?" → budget_for_range) without authoring a whole dashboard.

Read-only and schema-validated, dispatched through the same /api/compute registry.
"""

import logging

from app.ai.tools.base import BaseTool
from app.api.routers.compute import build_registry
from app.services.sqlite_reader import SqliteReader

logger = logging.getLogger(__name__)


class ExecuteComputeTool(BaseTool):
    @property
    def name(self) -> str:
        return "execute_compute"

    @property
    def description(self) -> str:
        return (
            "Run a registered compute function with given arguments and return its "
            "result. Use for analytical questions that SQL can't express directly "
            "(e.g. budget_for_range for budgets). Call get_compute_functions first to "
            "see the available functions and their argument schemas."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "function": {"type": "string", "description": "Compute function name (e.g. budget_for_range)."},
                "args": {"type": "object", "description": "Scalar arguments for the function."},
            },
            "required": ["function"],
        }

    def __init__(self, sqlite_reader: SqliteReader):
        self._reader = sqlite_reader

    async def execute(self, function: str, args: dict | None = None) -> dict:
        registry = build_registry(self._reader)
        outcome = await registry.execute(function, args or {})
        if not outcome.get("success"):
            return {"success": False, "error": outcome.get("error"), "code": outcome.get("code")}
        return {"success": True, "function": function, "result": outcome["result"]}
