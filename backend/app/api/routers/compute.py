"""Compute router — runs registered server-side compute functions.

`POST /api/compute` is a top-level computation engine and a peer of
`/api/ledger/query` (§9 decision 9): functions may read the ledger (read-only,
via the injected SqliteReader) but executing a computation is not a ledger
operation. Functions are vetted, schema-validated, and read-only by
construction — only a read-only reader is injected, never a writer (§3.6 G3).
"""

import asyncio
import logging
import time

from fastapi import APIRouter, Body, Depends

from app.compute.function_registry import FunctionRegistry
from app.compute.functions.budget_for_range import BudgetForRangeFunction
from app.dependencies import get_sqlite_reader
from app.services.sqlite_reader import SqliteReader
from app.schemas.compute_schemas import ComputeRequest, ComputeData
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response
from app.exceptions import APIError
from app import error_codes as ec

logger = logging.getLogger(__name__)

router = APIRouter()

# Map registry error codes → (APIError code, HTTP status).
_ERROR_STATUS = {
    ec.COMPUTE_FUNCTION_NOT_FOUND: 404,
    ec.COMPUTE_VALIDATION_ERROR: 400,
    ec.COMPUTE_EXECUTION_ERROR: 500,
}

# Wall-clock budget per compute call (mirrors the beanquery path).
_COMPUTE_TIMEOUT_S = 30


def build_registry(reader: SqliteReader) -> FunctionRegistry:
    """Construct the registry with each function bound to its read-only deps.
    Functions are stateless beyond the injected reader, so this is cheap."""
    registry = FunctionRegistry()
    registry.register(BudgetForRangeFunction(reader))
    return registry


@router.post(
    "/compute",
    response_model=ApiResponse[ComputeData],
    operation_id="executeCompute",
)
async def execute_compute(
    request: ComputeRequest = Body(...),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
):
    """Execute a registered compute function with validated scalar args."""
    registry = build_registry(sqlite_reader)
    start = time.perf_counter()
    try:
        outcome = await asyncio.wait_for(
            registry.execute(request.function, request.args),
            timeout=_COMPUTE_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise APIError(
            message=f"Compute function '{request.function}' timed out after {_COMPUTE_TIMEOUT_S}s.",
            code=ec.COMPUTE_TIMEOUT,
            status_code=504,
        )
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    if not outcome.get("success"):
        code = outcome.get("code", ec.COMPUTE_EXECUTION_ERROR)
        raise APIError(
            message=outcome.get("error", "Compute failed."),
            code=code,
            status_code=_ERROR_STATUS.get(code, 500),
        )

    return success_json_response(ComputeData(
        function=request.function,
        result=outcome["result"],
        execution_time_ms=elapsed_ms,
    ))
