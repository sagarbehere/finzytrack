"""
Config API Router - Provides structured configuration access for frontend.

Endpoints:
- GET /api/config - Return parsed configuration for frontend consumption
- PATCH /api/config - Update specific config fields (GUI settings editor)
"""

from collections.abc import MutableMapping
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML

from app.config import Config
from app.exceptions import APIError
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response
from app.core.backup_manager import BackupManager
from app.core.config_manager import ConfigManager
from app.dependencies import get_backup_manager, get_config_manager

router = APIRouter()


class ConfigPatchResponse(BaseModel):
    """Response after a partial config update via the GUI settings editor."""
    config: Config
    restart_required: bool
    restart_reason: Optional[str] = None
    notice: Optional[str] = None


def _deep_merge(base: Any, patch: dict) -> None:
    """Recursively merge patch into base in-place.

    Works with both plain dicts and ruamel.yaml CommentedMaps so that
    YAML comments and formatting are preserved during a GUI save.
    """
    for key, value in patch.items():
        if key in base and isinstance(base[key], MutableMapping) and isinstance(value, MutableMapping):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _to_plain_dict(obj: Any) -> Any:
    """Recursively convert ruamel.yaml CommentedMap / CommentedSeq to plain Python types."""
    if isinstance(obj, MutableMapping):
        return {k: _to_plain_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_plain_dict(item) for item in obj]
    return obj


@router.get("/config", response_model=ApiResponse[Config])
async def get_config_endpoint(config_manager: ConfigManager = Depends(get_config_manager)):
    """
    Get application configuration for frontend use.

    Returns the complete Config object including all settings.
    This is safe for single-user local deployment.

    The Config model is the single source of truth - OpenAPI codegen
    will automatically generate complete TypeScript types from it.
    """
    config = config_manager.get_config()
    return success_json_response(config)


@router.patch("/config", response_model=ApiResponse[ConfigPatchResponse])
async def patch_config_endpoint(
    patch: Dict[str, Any] = Body(...),
    config_manager: ConfigManager = Depends(get_config_manager),
    backup_manager: BackupManager = Depends(get_backup_manager),
):
    """
    Partially update application configuration from the GUI settings editor.

    Accepts a JSON object containing only the fields to change. Nested fields
    can be expressed as nested objects (e.g. {"ai": {"llm": {"api_url": "..."}}}).

    The update is merged into the existing YAML file using a round-trip parser
    so that comments and formatting are preserved. The result is validated
    against the full Config schema before writing.
    """
    config = config_manager.get_config()
    config_path = config.config_file_path or Path('./config/config.yaml')

    if not config_path.exists():
        raise APIError("Config file not found", "FILE_NOT_FOUND", status_code=404)

    # Load with round-trip parser to preserve comments and YAML structure
    yaml = YAML()
    with open(config_path, 'r') as f:
        data = yaml.load(f)

    # Merge the patch into the loaded YAML data
    _deep_merge(data, patch)

    # Validate the merged result against the Config schema
    try:
        Config.model_validate(_to_plain_dict(data))
    except ValidationError as e:
        errors = [
            f"{' -> '.join(str(loc) for loc in err['loc'])}: {err['msg']}"
            for err in e.errors()
        ]
        raise APIError("Validation failed", "CONFIG_VALIDATION_ERROR", status_code=422, details={"errors": errors})

    # Write back atomically (with backup)
    with backup_manager.atomic_write(str(config_path)) as f:
        f.seek(0)
        yaml.dump(data, f)
        f.truncate()

    # Reload the in-memory config
    restart_required, restart_reason, notice = config_manager.reload_config(_to_plain_dict(data))

    updated_config = config_manager.get_config()
    return success_json_response(ConfigPatchResponse(
        config=updated_config,
        restart_required=restart_required,
        restart_reason=restart_reason,
        notice=notice,
    ))
