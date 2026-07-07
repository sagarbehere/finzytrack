"""Compute function registry — name→handler dispatch with jsonschema arg
validation and structured results. Modeled on app/ai/tool_registry.py.

Returns structured dicts so the router can map cleanly to APIError codes:
  {success: True, result: ...} | {success: False, error: ..., code: ...}
"""

from __future__ import annotations

import logging
from typing import Any

import jsonschema

from app.compute.base import ComputeFunction
from app.error_codes import (
    COMPUTE_EXECUTION_ERROR,
    COMPUTE_FUNCTION_NOT_FOUND,
    COMPUTE_VALIDATION_ERROR,
)

logger = logging.getLogger(__name__)


class FunctionRegistry:
    def __init__(self) -> None:
        self._functions: dict[str, ComputeFunction] = {}

    def register(self, fn: ComputeFunction) -> None:
        self._functions[fn.name] = fn

    def get(self, name: str) -> ComputeFunction | None:
        return self._functions.get(name)

    def names(self) -> list[str]:
        return sorted(self._functions.keys())

    def get_schemas(self) -> list[dict[str, Any]]:
        """Discovery payload for get_compute_functions: name, description, arg
        schema, and output shape per function."""
        return [
            {
                "name": fn.name,
                "description": fn.description,
                "parameters_schema": fn.parameters_schema,
                "output_shape": fn.output_shape,
            }
            for fn in sorted(self._functions.values(), key=lambda f: f.name)
        ]

    async def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        fn = self._functions.get(name)
        if fn is None:
            return {
                "success": False,
                "code": COMPUTE_FUNCTION_NOT_FOUND,
                "error": f"Unknown compute function '{name}'. Available: {self.names()}",
            }

        schema = fn.parameters_schema
        declared = set(schema.get("properties", {}).keys())
        filtered = {k: v for k, v in args.items() if k in declared}

        try:
            jsonschema.validate(instance=filtered, schema=schema)
        except jsonschema.ValidationError as e:
            return {
                "success": False,
                "code": COMPUTE_VALIDATION_ERROR,
                "error": (
                    f"Invalid arguments for compute function '{name}': {e.message}. "
                    f"Path: {'/'.join(str(p) for p in e.absolute_path) or '(root)'}."
                ),
            }

        try:
            result = await fn.execute(**filtered)
            return {"success": True, "result": result}
        except Exception as e:  # noqa: BLE001 — surfaced as a structured error
            logger.error("Compute function '%s' raised: %s", name, e, exc_info=True)
            return {"success": False, "code": COMPUTE_EXECUTION_ERROR, "error": str(e)}
