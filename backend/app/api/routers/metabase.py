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
    MetabaseLoginUrlData,
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
    metabase_manager: MetabaseManager = Depends(get_metabase_manager),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """
    Initialize Metabase (first-run setup).

    This endpoint performs the initial setup of Metabase:
    1. Creates an admin account with a randomly-generated password
    2. Connects to the DuckDB database
    3. Imports dashboard templates (if available)

    The admin password is returned once and should be saved by the user.
    Future logins can use the auto-login URL from /login-url endpoint.
    """
    result = await metabase_manager.initialize_first_run(config_manager)
    init_data = MetabaseInitializeData(**result)
    return success_json_response(init_data)


@router.get(
    "/login-url",
    response_model=ApiResponse[MetabaseLoginUrlData],
    operation_id="getMetabaseLoginUrl"
)
async def get_login_url(
    metabase_manager: MetabaseManager = Depends(get_metabase_manager)
):
    """
    Get auto-login URL for Metabase.

    This endpoint generates a URL that automatically logs the user into Metabase
    without requiring manual password entry. The session token expires after
    a period of inactivity.
    """
    url = await metabase_manager.get_auto_login_url()
    login_data = MetabaseLoginUrlData(url=url, expires_at=None)
    return success_json_response(login_data)


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
