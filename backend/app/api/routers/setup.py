"""
Setup wizard API Router — handles first-run initialization.

Endpoints:
- POST /api/setup/complete - Apply wizard choices and finalize setup
"""

import logging
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.config import Config
from app.exceptions import APIError
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response
from app.core.config_manager import ConfigManager
from app.core.backup_manager import BackupManager
from app.dependencies import get_config_manager, get_backup_manager
from app.core.seed import seed_data_with_currency

logger = logging.getLogger(__name__)

router = APIRouter()


class SetupRequest(BaseModel):
    """Request body for the setup wizard completion."""
    currency: str = Field(..., description="Default currency code (e.g. USD, EUR, INR)")
    ledger_mode: str = Field(
        default="fresh",
        description="'fresh' to create a starter ledger, or 'existing' to import an existing file"
    )
    existing_ledger_path: Optional[str] = Field(
        default=None,
        description="Path to existing Beancount file (required when ledger_mode='existing')"
    )
    ai_config: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional AI/LLM configuration (provider, api_url, api_key, model)"
    )


class SetupResponse(BaseModel):
    """Response after setup completion."""
    config: Config


@router.post("/setup/complete", response_model=ApiResponse[SetupResponse])
async def complete_setup(
    request: SetupRequest,
    raw_request: Request,
    config_manager: ConfigManager = Depends(get_config_manager),
    backup_manager: BackupManager = Depends(get_backup_manager),
):
    """
    Finalize first-run setup.

    Applies the user's wizard choices: sets currency, creates or copies
    the ledger file, optionally configures AI, and marks setup as complete.
    """
    config = config_manager.get_config()

    if config.setup_complete:
        raise APIError(
            "Setup has already been completed",
            "SETUP_ALREADY_COMPLETE",
            status_code=409,
        )

    # Validate
    if not request.currency or not request.currency.strip():
        raise APIError("Currency is required", "VALIDATION_ERROR", status_code=422)

    currency = request.currency.strip().upper()
    config_path = config.config_file_path or Path('./config/config.yaml')

    # --- Ledger creation ---
    ledger_path = Path(config.ledger_file)

    if request.ledger_mode == "existing":
        if not request.existing_ledger_path:
            raise APIError(
                "Existing ledger path is required",
                "VALIDATION_ERROR",
                status_code=422,
            )
        src = Path(request.existing_ledger_path)
        if not src.exists():
            raise APIError(
                f"File not found: {request.existing_ledger_path}",
                "FILE_NOT_FOUND",
                status_code=404,
            )
        # Create ledger directory and copy the existing file
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(ledger_path))
    else:
        # Fresh start — seed data directory with currency substitution
        seed_data_with_currency(Path('./data'), currency)

    # --- Build config patch ---
    patch: Dict[str, Any] = {
        "setup_complete": True,
        "accounts": {
            "default_currency": currency,
        },
    }

    # AI config (if provided)
    if request.ai_config:
        ai_patch: Dict[str, Any] = {}
        llm_fields = {"provider", "api_url", "api_key", "model"}
        llm_patch = {k: v for k, v in request.ai_config.items() if k in llm_fields and v}
        if llm_patch:
            ai_patch["llm"] = llm_patch
        if ai_patch:
            patch["ai"] = ai_patch

    # --- Apply config patch (reuses the config router's pattern) ---
    from ruamel.yaml import YAML
    from app.api.routers.config import _deep_merge, _to_plain_dict

    yaml = YAML()
    with open(config_path, 'r') as f:
        data = yaml.load(f)

    _deep_merge(data, patch)

    # Validate
    try:
        Config.model_validate(_to_plain_dict(data))
    except Exception as e:
        raise APIError(
            f"Configuration validation failed: {e}",
            "CONFIG_VALIDATION_ERROR",
            status_code=422,
        )

    # Write
    with backup_manager.atomic_write(str(config_path)) as f:
        f.seek(0)
        yaml.dump(data, f)
        f.truncate()

    # Reload in-memory config
    config_manager.reload_config(_to_plain_dict(data))
    updated_config = config_manager.get_config()

    # Initialize ledger services that were skipped at startup because
    # setup_complete was false.  The BeancountManager and SQLiteExporter
    # already exist on app.state — they just haven't parsed/exported yet.
    try:
        beancount_manager = raw_request.app.state.beancount_manager
        entries = beancount_manager.cache.get_entries()
        logger.info(f"Ledger loaded after setup: {len(entries)} entries")

        sqlite_exporter = raw_request.app.state.sqlite_exporter
        await sqlite_exporter.export_entries(entries)
        logger.info("SQLite exported after setup completion")
    except Exception as e:
        logger.error(f"Post-setup initialization failed: {e}", exc_info=True)
        # Don't fail the wizard — config is saved, ledger exists.
        # The user can trigger a manual export from the UI.

    return success_json_response(SetupResponse(config=updated_config))
