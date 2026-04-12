"""
Ledger API Router - Provides ledger health and utility endpoints.

Endpoints:
- GET /api/ledger/errors - Get current ledger parse errors from SQLite
"""

from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.helpers.response_helpers import success_json_response
from app.schemas.response_schemas import ApiResponse
from app.services.sqlite_reader import SqliteReader
from app.dependencies import get_sqlite_reader

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
    """Current ledger parse errors."""
    error_count: int
    errors: List[LedgerValidationError]


@router.get(
    "/ledger/errors",
    response_model=ApiResponse[LedgerErrorsResponse],
    operation_id="getLedgerErrors",
)
async def get_ledger_errors(
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
):
    """
    Return current ledger parse errors from SQLite.

    This is a lightweight read — no re-parsing occurs.
    """
    raw_errors = sqlite_reader.get_errors()
    errors = [
        LedgerValidationError(
            line=e.get("line_number", 0) or 0,
            message=e.get("message", ""),
            source=(
                f"{e.get('source_file', 'unknown')}:{e.get('line_number', 0)}"
                if e.get("source_file")
                else "unknown"
            ),
        )
        for e in raw_errors
    ]
    return success_json_response(LedgerErrorsResponse(
        error_count=len(errors),
        errors=errors,
    ))
