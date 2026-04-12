import logging

from fastapi import APIRouter, Depends
from app.schemas.response_schemas import ApiResponse
from app.schemas.commodity_schemas import CommodityListData
from app.core.config_manager import ConfigManager
from app.services.sqlite_reader import SqliteReader
from app.dependencies import get_config_manager, get_sqlite_reader
from app.helpers.error_context import ledger_error_context
from app.helpers.response_helpers import success_json_response

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/commodities", response_model=ApiResponse[CommodityListData], operation_id="listCommodities")
async def list_commodities(
    config_manager: ConfigManager = Depends(get_config_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
):
    """
    Retrieve all commodities with full details including usage statistics and metadata.

    Returns commodities discovered from commodity directives, transactions, and price entries.
    """
    config = config_manager.get_config()

    with ledger_error_context(config.ledger_file):
        detailed_commodities = sqlite_reader.get_commodities()
        commodities_data = CommodityListData(commodities=detailed_commodities)
        return success_json_response(commodities_data)
