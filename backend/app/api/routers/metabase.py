"""
Metabase router - handles Metabase lifecycle and configuration.
"""
import logging
from fastapi import APIRouter, Depends

from app.schemas.response_schemas import ApiResponse
from app.schemas.metabase_schemas import (
    MetabaseStatusData,
    MetabaseStartData,
    MetabaseStopData,
    MetabaseInitializeData,
    MetabaseResetData,
    MetabaseSyncSchemaData
)
from app.dependencies import get_metabase_manager, get_config_manager
from app.services.metabase_manager import MetabaseManager
from app.core.config_manager import ConfigManager
from app.helpers.response_helpers import success_json_response

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/status",
    response_model=ApiResponse[MetabaseStatusData],
    operation_id="getMetabaseStatus"
)
async def get_status(
    metabase_manager: MetabaseManager = Depends(get_metabase_manager)
):
    """
    Get Metabase status.

    Returns information about whether Metabase is running, healthy, initialized, etc.
    """
    status = await metabase_manager.get_status()
    status_data = MetabaseStatusData(**status)
    return success_json_response(status_data)


@router.post(
    "/start",
    response_model=ApiResponse[MetabaseStartData],
    operation_id="startMetabase"
)
async def start_metabase(
    metabase_manager: MetabaseManager = Depends(get_metabase_manager)
):
    """
    Start Metabase subprocess.

    This endpoint starts the Metabase server. Metabase will run on the configured port
    and will be accessible via the browser. The first time Metabase starts, it needs
    to be initialized via the /initialize endpoint.
    """
    result = await metabase_manager.start()
    start_data = MetabaseStartData(**result)
    return success_json_response(start_data)


@router.post(
    "/stop",
    response_model=ApiResponse[MetabaseStopData],
    operation_id="stopMetabase"
)
async def stop_metabase(
    metabase_manager: MetabaseManager = Depends(get_metabase_manager)
):
    """
    Stop Metabase gracefully.

    This endpoint stops the Metabase server. It attempts a graceful shutdown first,
    and will force-kill the process if it doesn't stop within 10 seconds.
    """
    result = await metabase_manager.stop()
    stop_data = MetabaseStopData(**result)
    return success_json_response(stop_data)


@router.post(
    "/initialize",
    response_model=ApiResponse[MetabaseInitializeData],
    operation_id="initializeMetabase"
)
async def initialize_metabase(
    metabase_manager: MetabaseManager = Depends(get_metabase_manager)
):
    """
    Initialize Metabase (first-run setup).

    This endpoint performs the initial setup of Metabase:
    1. Creates an admin account with a randomly-generated password
    2. Connects to the DuckDB database
    3. Imports dashboard templates (if available)

    The admin password is returned once and should be saved by the user.
    """
    result = await metabase_manager.initialize_first_run()
    init_data = MetabaseInitializeData(**result)
    return success_json_response(init_data)


@router.post(
    "/sync-schema",
    response_model=ApiResponse[MetabaseSyncSchemaData],
    operation_id="syncMetabaseSchema"
)
async def sync_schema(
    metabase_manager: MetabaseManager = Depends(get_metabase_manager)
):
    """
    Trigger Metabase to refresh DuckDB schema.

    This endpoint tells Metabase to re-scan the DuckDB database and update
    its schema cache. This is useful after the ledger has been updated and
    exported to DuckDB, to ensure Metabase reflects the latest data structure.
    """
    result = await metabase_manager.trigger_schema_refresh()
    sync_data = MetabaseSyncSchemaData(**result)
    return success_json_response(sync_data)


@router.post(
    "/reset",
    response_model=ApiResponse[MetabaseResetData],
    operation_id="resetMetabase"
)
async def reset_metabase(
    metabase_manager: MetabaseManager = Depends(get_metabase_manager)
):
    """
    Perform a factory reset of the Metabase instance.

    This is a destructive operation that will:
    1. Stop the Metabase server.
    2. Delete the Metabase application database file, wiping out all users and settings.
    3. Reset the application's configuration to the un-initialized state.

    After this, Metabase can be initialized again from scratch.
    """
    result = await metabase_manager.reset()
    reset_data = MetabaseResetData(**result)
    return success_json_response(reset_data)
