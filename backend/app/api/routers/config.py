"""
Config API Router - Provides structured configuration access for frontend.

Endpoints:
- GET /api/config - Return parsed configuration for frontend consumption
"""

from fastapi import APIRouter, Depends

from app.config import Config
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response
from app.core.config_manager import ConfigManager
from app.dependencies import get_config_manager

router = APIRouter()


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
