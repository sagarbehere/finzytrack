import logging

from fastapi import APIRouter, Depends, Path
from app.schemas.response_schemas import ApiResponse
from app.schemas.commodity_schemas import (
    CommodityCreateRequest, CommodityCreateData, CommodityListData,
    CommodityUpdateRequest, CommodityUpdateData, 
    CommodityDeleteData
)
from app.core.beancount_manager import BeancountManager
from app.core.config_manager import ConfigManager
from app.dependencies import get_config_manager, get_beancount_manager
from app.exceptions import APIError
from app.helpers.error_context import ledger_error_context
from app.helpers.response_helpers import success_json_response

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/commodities", response_model=ApiResponse[CommodityListData], operation_id="listCommodities")
async def list_commodities(
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    """
    Retrieve all commodities with full details including usage statistics and metadata.
    
    Returns commodities discovered from commodity directives, transactions, and price entries.
    """
    config = config_manager.get_config()

    with ledger_error_context(config.ledger_file):
        detailed_commodities = beancount_manager.get_detailed_commodities()
        commodities_data = CommodityListData(commodities=detailed_commodities)
        return success_json_response(commodities_data)

@router.post("/commodities", response_model=ApiResponse[CommodityCreateData], status_code=201, operation_id="createCommodity")
async def create_commodity_endpoint(
    request: CommodityCreateRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    """Create and add a new Beancount commodity with metadata."""
    # TODO: Implement commodity creation logic
    raise APIError(
        message="Commodity creation not yet implemented",
        code="NOT_IMPLEMENTED",
        status_code=501
    )

@router.put("/commodities/{commodity_code}", response_model=ApiResponse[CommodityUpdateData], operation_id="updateCommodity")
async def update_commodity(
    request: CommodityUpdateRequest,
    commodity_code: str = Path(..., description="Commodity code to update"),
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    """Update commodity details."""
    # TODO: Implement commodity update logic
    raise APIError(
        message="Commodity update not yet implemented",
        code="NOT_IMPLEMENTED",
        status_code=501
    )

@router.delete("/commodities/{commodity_code}", response_model=ApiResponse[CommodityDeleteData], operation_id="deleteCommodity")
async def delete_commodity(
    commodity_code: str = Path(..., description="Commodity code to delete"),
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    """Remove commodity from ledger (deletes the commodity directive)."""
    # TODO: Implement commodity deletion logic
    raise APIError(
        message="Commodity deletion not yet implemented",
        code="NOT_IMPLEMENTED",
        status_code=501
    )