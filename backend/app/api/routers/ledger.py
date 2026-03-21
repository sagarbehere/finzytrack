"""
Ledger Operations API Router - Provides utility operations for ledger manipulation.

Endpoints:
- POST /api/ledger/sort - Sort ledger directives chronologically
- POST /api/ledger/validate - Validate Beancount ledger syntax
"""

import tempfile
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
import beancount.loader
from beancount.parser import printer

from app.exceptions import APIError
from app.helpers.response_helpers import success_json_response
from app.schemas.response_schemas import ApiResponse
from app.core.beancount_manager import BeancountManager
from app.core.config_manager import ConfigManager
from app.dependencies import get_config_manager, get_beancount_manager

router = APIRouter()


# ============================================================================
# Request/Response Schemas
# ============================================================================

class LedgerOperationRequest(BaseModel):
    """Request for ledger utility operations."""
    content: str


class LedgerOperationResponse(BaseModel):
    """Response for ledger transformation operations."""
    content: str
    operation: str
    summary: str


class LedgerValidationError(BaseModel):
    """Individual validation error with line number."""
    line: int
    message: str
    source: str


class LedgerValidationResponse(BaseModel):
    """Response for ledger validation."""
    valid: bool
    errors: List[LedgerValidationError]
    warnings: List[LedgerValidationError]
    summary: str


# ============================================================================
# Ledger Utility Endpoints
# ============================================================================

@router.post("/ledger/sort", response_model=ApiResponse[LedgerOperationResponse])
async def sort_ledger(
    request: LedgerOperationRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    """
    Sort ledger directives chronologically.

    Takes current editor content, sorts it, and saves to ledger file.
    Uses BeancountManager's atomic_ledger_write for safe persistence.

    Returns success response. Frontend should issue GET request to reload content.

    Note: This is a simplified implementation that uses beancount's printer.
    Known limitations:
    - May not preserve all comments and formatting perfectly
    - Does not handle includes and options specially
    - Uses default spacing from beancount printer

    Future improvements could preserve comments and custom formatting.
    """
    config = config_manager.get_config()
    # Write content to temp file for parsing
    with tempfile.NamedTemporaryFile(mode='w', suffix='.beancount', delete=False) as tmp:
        tmp.write(request.content)
        tmp_path = tmp.name

    try:
        # Parse ledger
        entries, errors, options = beancount.loader.load_file(tmp_path)

        if errors:
            # Return parse errors
            error_msgs = []
            for e in errors[:10]:  # Limit to first 10 errors
                line = e.source.get('lineno', 0) if e.source else 0
                error_msgs.append(f"Line {line}: {e.message}")

            raise APIError(
                "Failed to parse ledger for sorting",
                "PARSE_ERROR",
                422,
                {"errors": error_msgs}
            )

        # Sort entries by date
        sorted_entries = sorted(entries, key=lambda e: e.date)

        # Format back to text using beancount printer
        # Note: This is a simplified approach - see Known Limitations above
        sorted_lines = []
        for entry in sorted_entries:
            entry_str = printer.format_entry(entry)
            sorted_lines.append(entry_str)

        sorted_content = '\n\n'.join(sorted_lines)

        # Write sorted content to ledger file using atomic write
        ledger_path = Path(config.ledger_file)
        with beancount_manager.atomic_ledger_write(str(ledger_path)) as f:
            f.seek(0)
            f.write(sorted_content)
            f.truncate()

        return success_json_response(LedgerOperationResponse(
            content="",  # No content echo - frontend will reload via GET
            operation="sort",
            summary=f"Sorted {len(sorted_entries)} directives chronologically"
        ))

    except APIError:
        raise  # Re-raise our own errors
    except Exception as e:
        raise APIError(
            f"Failed to sort ledger: {e}",
            "SORT_ERROR",
            500
        )
    finally:
        # Clean up temp file
        try:
            Path(tmp_path).unlink()
        except Exception:
            pass


@router.post("/ledger/validate", response_model=ApiResponse[LedgerValidationResponse])
async def validate_ledger(request: LedgerOperationRequest):
    """
    Validate Beancount ledger syntax.

    Runs beancount loader on current editor content without saving.
    Returns validation errors and warnings with line numbers.

    This is a read-only operation - does not modify the ledger file.
    """
    # Write content to temp file for validation
    with tempfile.NamedTemporaryFile(mode='w', suffix='.beancount', delete=False) as tmp:
        tmp.write(request.content)
        tmp_path = tmp.name

    try:
        # Load and validate using beancount
        entries, errors, options = beancount.loader.load_file(tmp_path)

        # Convert errors to our schema
        # Note: Beancount doesn't distinguish errors from warnings,
        # so we treat all as errors
        error_list = []
        for error in errors:
            error_list.append(LedgerValidationError(
                line=error.source.get('lineno', 0) if error.source else 0,
                message=error.message,
                source=f"{error.source.get('filename', 'unknown')}:{error.source.get('lineno', 0)}" if error.source else "unknown"
            ))

        valid = len(error_list) == 0

        if valid:
            summary = "Ledger is valid. No errors found."
        else:
            summary = f"Found {len(error_list)} error(s)"

        return success_json_response(LedgerValidationResponse(
            valid=valid,
            errors=error_list,
            warnings=[],  # Beancount doesn't separate warnings
            summary=summary
        ))

    except Exception as e:
        # If we can't even load the file, treat as validation error
        raise APIError(
            f"Validation failed: {e}",
            "VALIDATION_ERROR",
            500
        )
    finally:
        # Clean up temp file
        try:
            Path(tmp_path).unlink()
        except Exception:
            pass
