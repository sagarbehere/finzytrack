"""
Ledger API Router - Provides ledger health and utility endpoints.

Endpoints:
- GET /api/ledger/errors - Get current ledger parse errors from cache
"""

from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.helpers.response_helpers import success_json_response
from app.schemas.response_schemas import ApiResponse
from app.core.beancount_manager import BeancountManager
from app.dependencies import get_beancount_manager

router = APIRouter()


# ============================================================================
# Request/Response Schemas
# ============================================================================

class LedgerValidationError(BaseModel):
    """Individual ledger error with line number."""
    line: int
    message: str
    source: str


# ============================================================================
# Ledger Health Endpoint
# ============================================================================

class LedgerErrorsResponse(BaseModel):
    """Current ledger parse errors from the beancount cache."""
    error_count: int
    errors: List[LedgerValidationError]


@router.get(
    "/ledger/errors",
    response_model=ApiResponse[LedgerErrorsResponse],
    operation_id="getLedgerErrors",
)
async def get_ledger_errors(
    beancount_manager: BeancountManager = Depends(get_beancount_manager),
):
    """
    Return current ledger parse errors from the cache.

    This is a lightweight read — no re-parsing occurs.
    """
    raw_errors = beancount_manager.cache.get_errors()
    errors = [
        LedgerValidationError(
            line=e.source.get("lineno", 0) if e.source else 0,
            message=e.message,
            source=(
                f"{e.source.get('filename', 'unknown')}:{e.source.get('lineno', 0)}"
                if e.source
                else "unknown"
            ),
        )
        for e in raw_errors
    ]
    return success_json_response(LedgerErrorsResponse(
        error_count=len(errors),
        errors=errors,
    ))
