"""
Files API Router - Provides file editing capabilities for config and ledger files.

Endpoints:
- GET /api/files/config - Get config file content for editing
- PUT /api/files/config - Update config file with validation and hot-reload
- GET /api/files/ledger - Get ledger file content for editing
- PUT /api/files/ledger - Update ledger file (without validation)
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML

from app.config import Config
from app.exceptions import APIError
from app.helpers.response_helpers import success_json_response
from app.schemas.response_schemas import ApiResponse
from app.core.backup_manager import BackupManager
from app.core.config_manager import ConfigManager
from app.core.beancount_manager import BeancountManager
from app.dependencies import get_backup_manager, get_config_manager, get_beancount_manager

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


class ConfigUpdateResponse(BaseModel):
    """Response after config file update."""
    file: FileUpdateMetadata
    config: Config  # Full Config object for cache update
    restart_required: bool
    restart_reason: Optional[str] = None
    notice: Optional[str] = None


class LedgerUpdateResponse(BaseModel):
    """Response after ledger file update."""
    file: FileUpdateMetadata
    validation_skipped: bool = True


# ============================================================================
# Config File Endpoints
# ============================================================================

@router.get("/files/config", response_model=ApiResponse[FileContent])
async def get_config_file(config_manager: ConfigManager = Depends(get_config_manager)):
    """
    Get config file content for editing.

    Returns raw YAML content for Monaco editor.
    """
    config = config_manager.get_config()
    config_path = config.config_file_path or Path('./config/config.yaml')

    if not config_path.exists():
        raise APIError("Config file not found", "FILE_NOT_FOUND", 404)

    try:
        content = config_path.read_text()
        size = config_path.stat().st_size
    except PermissionError as e:
        raise APIError(
            f"Permission denied reading config file: {e}",
            "FILE_PERMISSION_ERROR",
            403
        )
    except Exception as e:
        raise APIError(
            f"Failed to read config file: {e}",
            "FILE_READ_ERROR",
            500
        )

    return success_json_response(FileContent(
        path=str(config_path),
        content=content,
        size_bytes=size,
        size_warning=None  # Config files are typically small
    ))


@router.put("/files/config", response_model=ApiResponse[ConfigUpdateResponse])
async def update_config_file(
    request: FileUpdateRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    backup_manager: BackupManager = Depends(get_backup_manager)
):
    """
    Update config file with validation and hot-reload.

    Steps:
    1. Validate YAML syntax
    2. Validate against Config schema (Pydantic)
    3. Write atomically with BackupManager
    4. Hot-reload config in memory (safe fields only)
    5. Return metadata + parsed config + restart info

    Note: Does NOT echo content back (frontend already has it).
    """
    config = config_manager.get_config()
    config_path = config.config_file_path or Path('./config/config.yaml')

    # Step 1: Validate YAML syntax
    try:
        yaml = YAML()
        data = yaml.load(request.content)
    except Exception as e:
        # Extract line/column info if available
        line = getattr(e, 'problem_mark', None)
        raise APIError(
            "Invalid YAML syntax",
            "YAML_SYNTAX_ERROR",
            422,
            {
                "line": line.line + 1 if line else None,
                "column": line.column + 1 if line else None,
                "problem": str(e)
            }
        )

    # Step 2: Validate against Config schema
    try:
        # Create temporary config to validate
        Config.model_validate(data)
    except ValidationError as e:
        # Parse Pydantic validation errors
        errors = []
        for error in e.errors():
            loc = " -> ".join(str(x) for x in error['loc'])
            errors.append(f"{loc}: {error['msg']}")

        raise APIError(
            "Configuration validation failed",
            "CONFIG_VALIDATION_ERROR",
            422,
            {"errors": errors}
        )
    except Exception as e:
        # Handle other validation errors
        raise APIError(
            f"Configuration validation failed: {str(e)}",
            "CONFIG_VALIDATION_ERROR",
            422,
            {"errors": [str(e)]}
        )

    # Step 3: Write atomically with backup
    try:
        with backup_manager.atomic_write(str(config_path)) as f:
            f.seek(0)
            f.write(request.content)
            f.truncate()
    except Exception as e:
        raise APIError(
            f"Failed to write config file: {e}",
            "FILE_WRITE_ERROR",
            500
        )

    # Step 4: Hot-reload config (see reload_config implementation in ConfigManager)
    restart_required, restart_reason, notice = config_manager.reload_config(data)

    # Step 5: Return metadata + parsed config (no content echo)
    return success_json_response(ConfigUpdateResponse(
        file=FileUpdateMetadata(
            path=str(config_path),
            size_bytes=len(request.content)
        ),
        config=config_manager.get_config(),  # Return the full Config object
        restart_required=restart_required,
        restart_reason=restart_reason,
        notice=notice,
    ))


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
