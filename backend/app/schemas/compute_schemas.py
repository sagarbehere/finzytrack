"""Pydantic schemas for the compute endpoint (/api/compute)."""

from typing import Any, Dict
from pydantic import BaseModel, Field


class ComputeRequest(BaseModel):
    """Request to run a registered compute function."""
    function: str = Field(..., description="Name of the compute function to run")
    args: Dict[str, Any] = Field(default_factory=dict, description="Scalar arguments for the function")


class ComputeData(BaseModel):
    """Compute result data."""
    function: str = Field(..., description="The function that was executed")
    result: Any = Field(..., description="The function's result (arbitrary JSON)")
    execution_time_ms: int = Field(..., description="Execution time in milliseconds")
