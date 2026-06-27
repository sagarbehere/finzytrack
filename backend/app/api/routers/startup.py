"""Startup router — pending startup tasks (upgrades/notices) and applying them.

The frontend calls GET /api/startup/tasks at launch; any `action_required` task
gates the app until the user consents, at which point POST
/api/startup/tasks/{id}/apply runs it (with backups). See dev-docs/upgrades.md.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends

from app.dependencies import get_config_manager
from app.core.config_manager import ConfigManager
from app.startup_tasks import build_startup_registry
from app.schemas.startup_schemas import StartupTasksData, StartupApplyData
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response
from app.exceptions import APIError
from app import error_codes as ec

logger = logging.getLogger(__name__)

router = APIRouter()


def _registry(config_manager: ConfigManager):
    config = config_manager.get_config()
    return build_startup_registry(config.config_dir, Path(config.recipes_dir))


@router.get("/startup/tasks", response_model=ApiResponse[StartupTasksData], operation_id="getStartupTasks")
async def get_startup_tasks(config_manager: ConfigManager = Depends(get_config_manager)):
    """Read-only: list pending startup tasks. Nothing is mutated."""
    tasks = _registry(config_manager).detect()
    return success_json_response(StartupTasksData(tasks=tasks))


@router.post("/startup/tasks/{task_id}/apply", response_model=ApiResponse[StartupApplyData], operation_id="applyStartupTask")
async def apply_startup_task(task_id: str, config_manager: ConfigManager = Depends(get_config_manager)):
    """Apply a startup task after the user consents (e.g. run the recipe migration)."""
    registry = _registry(config_manager)
    if registry.get(task_id) is None:
        raise APIError(
            message=f"Unknown startup task '{task_id}'.",
            code=ec.STARTUP_TASK_NOT_FOUND,
            status_code=404,
        )
    try:
        result = registry.apply(task_id)
    except Exception as e:  # noqa: BLE001 — surface as a clean API error
        logger.error("Startup task '%s' failed: %s", task_id, e, exc_info=True)
        raise APIError(message=f"Upgrade failed: {e}", code=ec.STARTUP_TASK_FAILED, status_code=500)

    errors = result.get("errors") or []
    msg = result.get("summary", "Done.") if not errors else f"Completed with {len(errors)} issue(s)."
    return success_json_response(StartupApplyData(id=task_id, applied=True, message=msg, result=result))
