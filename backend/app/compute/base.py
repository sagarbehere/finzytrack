"""Base class for server-side compute functions.

A compute function is read-only and deterministic-given-ledger-state — NOT
"pure": it may read the SQLite mirror (via an injected read-only reader), which
is the whole point. The guarantee that matters: no writes, no side effects,
same output for the same ledger + args. Never inject a writer (§3.6 G3).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ComputeFunction(ABC):
    """One named computation. Subclasses declare a JSON-Schema for their args
    (small scalars), an output-shape description (for AI discovery / G1), and an
    async `execute`."""

    #: Unique function name addressed from a recipe `compute` step's `fn`.
    name: str
    #: One-line description (shown by get_compute_functions).
    description: str
    #: JSON Schema (object) for the scalar args this function accepts.
    parameters_schema: dict[str, Any]
    #: Short description of the result shape (so the model can wire a transform).
    output_shape: str = "arbitrary JSON"

    @abstractmethod
    async def execute(self, **args: Any) -> Any:
        """Run the computation and return JSON-serialisable output. Money values
        are returned as decimal strings (money-types.md)."""
        raise NotImplementedError
