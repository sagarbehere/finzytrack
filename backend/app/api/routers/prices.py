"""Prices router — fetch market prices into the price sidecar.

``POST /api/prices/update`` runs the keyless Yahoo fetcher and persists new
``price`` directives to ``prices.beancount`` (the fetcher's dedicated write path,
off the transaction edit path). It is developer/curl-triggered until the
investment dashboard's "Update prices" button ships (dev-docs/valuations.md §4).
"""

import logging

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.dependencies import get_sqlite_reader, get_beancount_manager
from app.services.sqlite_reader import SqliteReader
from app.core.ledger_manager import LedgerManager
from app.services.price_fetcher import PriceFetcher
from app.schemas.price_schemas import PriceUpdateData
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response
from app.exceptions import APIError
from app import error_codes as ec

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/prices/update",
    response_model=ApiResponse[PriceUpdateData],
    operation_id="updatePrices",
)
async def update_prices(
    reader: SqliteReader = Depends(get_sqlite_reader),
    ledger_manager: LedgerManager = Depends(get_beancount_manager),
):
    """Fetch prices for every priced holding and persist them to the sidecar."""
    fetcher = PriceFetcher(reader, ledger_manager)
    try:
        # Sync fetcher (network + file write under the write lock) off the event loop.
        result = await run_in_threadpool(fetcher.fetch_and_persist)
    except Exception as e:  # noqa: BLE001 — surfaced as a structured API error
        logger.error("Price update failed: %s", e, exc_info=True)
        raise APIError(
            message=f"Price update failed: {e}",
            code=ec.PRICE_UPDATE_FAILED,
            status_code=502,
        )

    return success_json_response(PriceUpdateData(
        added=result.get("added", 0),
        total=result.get("total", 0),
        as_of=result.get("as_of"),
        symbols=result.get("symbols", []),
        skipped=result.get("skipped", []),
        failed=result.get("failed", []),
    ))
