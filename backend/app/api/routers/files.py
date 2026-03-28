"""
Files API Router - Provides ledger file editing capabilities.

Endpoints:
- GET /api/files/ledger - Get ledger file content for editing
- PUT /api/files/ledger - Update ledger file (without validation)
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.exceptions import APIError
from app.helpers.response_helpers import success_json_response
from app.schemas.response_schemas import ApiResponse
from app.core.config_manager import ConfigManager
from app.core.beancount_manager import BeancountManager
from app.dependencies import get_config_manager, get_beancount_manager

router = APIRouter()


# ============================================================================
# Request/Response Schemas
# ============================================================================

class FileContent(BaseModel):
    """File content response for editor."""
    path: str
    content: str
    size_bytes: int
    size_warning: Optional[str] = None


class FileUpdateRequest(BaseModel):
    """Request to update file content."""
    content: str


class FileUpdateMetadata(BaseModel):
    """Metadata about saved file (no content echo for bandwidth optimization)."""
    path: str
    size_bytes: int


class LedgerUpdateResponse(BaseModel):
    """Response after ledger file update."""
    file: FileUpdateMetadata
    validation_skipped: bool = True


# ============================================================================
# Ledger File Endpoints
# ============================================================================

@router.get("/files/ledger", response_model=ApiResponse[FileContent])
async def get_ledger_file(config_manager: ConfigManager = Depends(get_config_manager)):
    """
    Get ledger file content for viewing/editing.

    Returns raw Beancount text for Monaco editor.
    Includes size warning for large files (> 10 MB).
    """
    config = config_manager.get_config()
    ledger_path = Path(config.ledger_file)

    if not ledger_path.exists():
        raise APIError("Ledger file not found", "FILE_NOT_FOUND", 404)

    try:
        size = ledger_path.stat().st_size
    except Exception as e:
        raise APIError(
            f"Failed to access ledger file: {e}",
            "FILE_ACCESS_ERROR",
            500
        )

    # Check size and add warning if large
    size_warning = None
    size_mb = size / (1024 * 1024)

    if size > 50 * 1024 * 1024:  # 50 MB
        size_warning = (
            f"Very large file detected ({size_mb:.1f} MB). "
            f"The editor may be slow or unresponsive. "
            f"Consider using an external editor for files this large."
        )
    elif size > 10 * 1024 * 1024:  # 10 MB
        size_warning = (
            f"Large file detected ({size_mb:.1f} MB). "
            f"Editor performance may be affected."
        )

    try:
        content = ledger_path.read_text()
    except PermissionError as e:
        raise APIError(
            f"Permission denied reading ledger file: {e}",
            "FILE_PERMISSION_ERROR",
            403
        )
    except Exception as e:
        raise APIError(
            f"Failed to read ledger file: {e}",
            "FILE_READ_ERROR",
            500
        )

    return success_json_response(FileContent(
        path=str(ledger_path),
        content=content,
        size_bytes=size,
        size_warning=size_warning
    ))


@router.put("/files/ledger", response_model=ApiResponse[LedgerUpdateResponse])
async def update_ledger_file(
    request: FileUpdateRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    """
    Update ledger file without validation (validation is optional via separate endpoint).

    Uses BeancountManager's atomic_ledger_write for:
    - Atomic write with BackupManager
    - Automatic cache invalidation
    - Notification of registered callbacks

    Note: Does NOT echo content back (frontend already has it).
    Frontend should issue GET request to reload content after successful save.
    """
    config = config_manager.get_config()
    ledger_path = Path(config.ledger_file)

    # Write atomically with automatic cache invalidation
    try:
        with beancount_manager.atomic_ledger_write(str(ledger_path)) as f:
            f.seek(0)
            f.write(request.content)
            f.truncate()
    except Exception as e:
        raise APIError(
            f"Failed to write ledger file: {e}",
            "FILE_WRITE_ERROR",
            500
        )

    return success_json_response(LedgerUpdateResponse(
        file=FileUpdateMetadata(
            path=str(ledger_path),
            size_bytes=len(request.content)
        ),
        validation_skipped=True
    ))
