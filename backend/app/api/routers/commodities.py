import logging

from fastapi import APIRouter, Depends
from app.schemas.response_schemas import ApiResponse
from app.schemas.commodity_schemas import (
    CommodityListData,
    CommodityMetadataUpdateData,
    CommodityMetadataUpdateRequest,
    OperatingCurrenciesData,
    OperatingCurrenciesUpdateRequest,
)
from app.core.config_manager import ConfigManager
from app.core.ledger_manager import LedgerManager
from app.core.metadata_vocabulary import is_valid_key
from app.exceptions import APIError
from app import error_codes as ec
from app.services.sqlite_reader import SqliteReader
from app.dependencies import (
    get_config_manager,
    get_beancount_manager,
    get_sqlite_reader,
)
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


@router.get(
    "/commodities/operating-currencies",
    response_model=ApiResponse[OperatingCurrenciesData],
    operation_id="getOperatingCurrencies",
)
async def get_operating_currencies(
    config_manager: ConfigManager = Depends(get_config_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
):
    """Return the ledger's operating currencies — the authoritative currency whitelist.

    An empty list means no whitelist is declared; commodity currency-roles then
    fall back to asset-class classification. See
    dev-docs/commodities-and-currencies.md.
    """
    config = config_manager.get_config()

    with ledger_error_context(config.ledger_file):
        currencies = sqlite_reader.get_operating_currencies()
        return success_json_response(OperatingCurrenciesData(currencies=currencies))


@router.put(
    "/commodities/operating-currencies",
    response_model=ApiResponse[OperatingCurrenciesData],
    operation_id="setOperatingCurrencies",
)
async def set_operating_currencies(
    request: OperatingCurrenciesUpdateRequest,
    beancount_manager: LedgerManager = Depends(get_beancount_manager),
):
    """Replace the ledger's operating currencies (writes `option "operating_currency"`).

    Full-replacement semantics: the given list becomes the complete whitelist;
    an empty list clears it. Writes to the root ledger file via the single
    authorised write path.
    """
    updated = beancount_manager.set_operating_currencies(request.currencies)
    return success_json_response(OperatingCurrenciesData(currencies=updated))


@router.put(
    "/commodities/metadata",
    response_model=ApiResponse[CommodityMetadataUpdateData],
    operation_id="updateCommodityMetadata",
)
async def update_commodity_metadata(
    request: CommodityMetadataUpdateRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
    beancount_manager: LedgerManager = Depends(get_beancount_manager),
):
    """Set and remove metadata keys on commodities, as one ledger write.

    Upsert per commodity: the `commodity` directive is updated in place, or
    created if the commodity has none. Keys not named are kept. Only key syntax
    is checked — any key and any value may be written (recognized, never
    required; dev-docs/investment-view.md §10.3).
    """
    bad = sorted({
        k for c in request.changes for k in [*c.set.keys(), *c.remove]
        if not is_valid_key(k)
    })
    if bad:
        raise APIError(
            message="Invalid metadata key",
            code=ec.VALIDATION_ERROR,
            status_code=422,
            details={
                "keys": bad,
                "help": "Keys start with a lowercase letter and use only letters, digits, '-' and '_'.",
            },
        )

    config = config_manager.get_config()
    with ledger_error_context(config.ledger_file):
        changed = beancount_manager.upsert_commodity_metadata(request.changes)
        requested = {c.code for c in request.changes}
        commodities = [c for c in sqlite_reader.get_commodities() if c.code in requested]
        return success_json_response(CommodityMetadataUpdateData(
            changed=changed, commodities=commodities,
        ))
