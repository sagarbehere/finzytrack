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
    return build_startup_registry(
        config.config_dir,
        Path(config.recipes_dir),
        data_dir=config.root_dir / "data",
        currency=config.accounts.default_currency,
        setup_complete=config.setup_complete,
    )


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
    logger.info("Applying startup task '%s'", task_id)
    try:
        result = registry.apply(task_id)
    except Exception as e:  # noqa: BLE001 — surface as a clean API error
        logger.error("Startup task '%s' failed: %s", task_id, e, exc_info=True)
        raise APIError(
            message="The upgrade could not be completed. See server logs for details.",
            code=ec.STARTUP_TASK_FAILED, status_code=500,
        )

    errors = result.get("errors") or []
    msg = result.get("summary", "Done.") if not errors else f"Completed with {len(errors)} issue(s)."
    if errors:
        logger.warning("Startup task '%s' applied with %d issue(s): %s", task_id, len(errors), msg)
    else:
        logger.info("Startup task '%s' applied: %s", task_id, msg)
    return success_json_response(StartupApplyData(id=task_id, applied=True, message=msg, result=result))


@router.post("/startup/tasks/{task_id}/dismiss", response_model=ApiResponse[StartupApplyData], operation_id="dismissStartupTask")
async def dismiss_startup_task(task_id: str, config_manager: ConfigManager = Depends(get_config_manager)):
    """Dismiss a non-blocking notice without applying it. For the seed-content
    notice this snoozes it for the current bundle (it reappears only when a later
    release ships different content); for a one-shot notice it marks it seen."""
    registry = _registry(config_manager)
    if registry.get(task_id) is None:
        raise APIError(
            message=f"Unknown startup task '{task_id}'.",
            code=ec.STARTUP_TASK_NOT_FOUND,
            status_code=404,
        )
    registry.dismiss(task_id)
    logger.info("Dismissed startup task '%s'", task_id)
    return success_json_response(
        StartupApplyData(id=task_id, applied=False, message="Dismissed.", result={"dismissed": True})
    )


@router.post("/startup/seed/reset", response_model=ApiResponse[StartupApplyData], operation_id="resetDemoData")
async def reset_demo_data(config_manager: ConfigManager = Depends(get_config_manager)):
    """Settings → "Reset demo data": restore the bundled demo dashboards and demo
    ledgers to their shipped state, ignoring provenance (backing up whatever's
    there first). The always-available manual path for a user who tinkered and
    wants the shipped demo back. See dev-docs/seed-content-refresh.md §9.3."""
    config = config_manager.get_config()
    from app.seed_refresh import apply_seed_refresh
    from app.startup_tasks.upgrade_state import UpgradeState

    state = UpgradeState(config.config_dir)
    try:
        report = apply_seed_refresh(
            state,
            config.config_dir,
            config.root_dir / "data",
            config.accounts.default_currency,
            reset=True,
        )
    except Exception as e:  # noqa: BLE001 — surface as a clean API error
        logger.error("Reset demo data failed: %s", e, exc_info=True)
        raise APIError(
            message="Could not reset demo data. See server logs for details.",
            code=ec.STARTUP_TASK_FAILED, status_code=500,
        )
    logger.info("Reset demo data: %s", report.summary())
    return success_json_response(
        StartupApplyData(id="seed-content", applied=True, message=report.summary(), result=report.to_result())
    )
