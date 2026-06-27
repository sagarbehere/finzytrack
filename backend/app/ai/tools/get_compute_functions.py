"""get_compute_functions tool — lists the server-side compute functions a
recipe `compute` step can call (name, description, arg schema, output shape).

The compute catalog is fixed and server-provided; the model selects from it and
cannot invent new functions (§4.11). The output shape is essential so the model
can wire the adapting transform/viz downstream (G1).
"""

from app.ai.tools.base import BaseTool
from app.api.routers.compute import build_registry


class GetComputeFunctionsTool(BaseTool):
    @property
    def name(self) -> str:
        return "get_compute_functions"

    @property
    def description(self) -> str:
        return (
            "List the server-side compute functions available to a recipe `compute` "
            "step (e.g. budget_for_range). Returns each function's name, description, "
            "argument schema, and output shape. Call this before authoring a compute "
            "step. The catalog is fixed — do not invent function names."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self) -> dict:
        return {"success": True, "functions": build_registry(None).get_schemas()}
