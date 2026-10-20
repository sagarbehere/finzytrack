"""Prices router — fetch, list and edit prices in the price sidecar.

``POST /api/prices/update`` runs the keyless Yahoo fetcher and persists new
``price`` directives to ``<stem>.prices.beancount`` (the dedicated sidecar write
path, off the transaction edit path; dev-docs/valuations.md §3–§4). The
Investments → Prices tab reads ``GET /api/prices/overview`` and
``GET /api/prices/history`` and saves edits with ``PUT /api/prices``
(dev-docs/investment-view.md §10.2).
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from beancount.core import data
from beancount.core.amount import Amount
from fastapi import APIRouter, Depends, Query
from starlette.concurrency import run_in_threadpool

from app.dependencies import get_sqlite_reader, get_beancount_manager
from app.services.sqlite_reader import SqliteReader
from app.core.ledger_manager import LedgerManager
from app.services.price_fetcher import PriceFetcher
from app.services.price_overview import build_history, build_overview
from app.schemas.price_schemas import (
    PriceEditData,
    PriceEditRequest,
    PriceHistoryData,
    PriceOverviewData,
    PriceUpdateData,
)
from app.schemas.commodity_schemas import CommodityStr
from app.helpers.error_context import ledger_error_context
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
    except APIError:
        raise  # e.g. PRICE_SIDECAR_INVALID — already a specific, structured error
    except Exception as e:  # noqa: BLE001 — surfaced as a structured API error
        logger.error("Price update failed: %s", e, exc_info=True)
        raise APIError(
            message=f"Price update failed: {e}",
            code=ec.PRICE_UPDATE_FAILED,
            status_code=502,
        )

    return success_json_response(PriceUpdateData(
        added=result.get("added", 0),
        updated=result.get("updated", 0),
        total=result.get("total", 0),
        as_of=result.get("as_of"),
        symbols=result.get("symbols", []),
        skipped=result.get("skipped", []),
        failed=result.get("failed", []),
    ))


@router.get(
    "/prices/overview",
    response_model=ApiResponse[PriceOverviewData],
    operation_id="getPriceOverview",
)
async def get_price_overview(
    reader: SqliteReader = Depends(get_sqlite_reader),
    ledger_manager: LedgerManager = Depends(get_beancount_manager),
):
    """One row per (commodity, quote currency) with prices or held units: how
    the fetcher prices it, its latest price, and whether that is out of date."""
    with ledger_error_context(str(ledger_manager.ledger_file)):
        return success_json_response(
            build_overview(reader, str(ledger_manager.ledger_file), date.today())
        )


@router.get(
    "/prices/history",
    response_model=ApiResponse[PriceHistoryData],
    operation_id="getPriceHistory",
)
async def get_price_history(
    commodity: CommodityStr = Query(...),
    quote: CommodityStr = Query(...),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    reader: SqliteReader = Depends(get_sqlite_reader),
    ledger_manager: LedgerManager = Depends(get_beancount_manager),
):
    """One pair's prices, newest first, with where each one is written."""
    with ledger_error_context(str(ledger_manager.ledger_file)):
        return success_json_response(build_history(
            reader, str(ledger_manager.ledger_file), commodity, quote, limit, offset,
        ))


def _plain(n: Decimal) -> Decimal:
    """The number as Beancount writes it (no exponent: 1E+2 → 100)."""
    return Decimal(format(n, "f"))


@router.put(
    "/prices",
    response_model=ApiResponse[PriceEditData],
    operation_id="editPrices",
)
async def edit_prices(
    request: PriceEditRequest,
    ledger_manager: LedgerManager = Depends(get_beancount_manager),
):
    """Add, change and remove prices in the price file, as one write.

    ``set`` adds a price or replaces the one at the same (date, commodity, quote
    currency, unless ``replace`` is false: then a taken date fails with 409
    ``PRICE_EXISTS`` so the caller can ask first); ``remove`` deletes one. Prices
    written in the ledger file can't be changed here (422 ``PRICE_IN_LEDGER``),
    and none may be dated after tomorrow (422). Changing a price's date or
    currency is a remove plus a set.
    """
    # One day of slack: the browser's "today" can be ahead of the server's.
    latest_allowed = date.today() + timedelta(days=1)
    future = sorted({p.date for p in request.set if p.date > latest_allowed})
    if future:
        raise APIError(
            message="A price can't be dated after today.",
            code=ec.VALIDATION_ERROR,
            status_code=422,
            details={"dates": [d.isoformat() for d in future]},
        )

    set_keys = [(p.date, p.commodity, p.quote_currency) for p in request.set]
    remove_keys = [(k.date, k.commodity, k.quote_currency) for k in request.remove]
    if not set_keys and not remove_keys:
        raise APIError(message="Nothing to change", code=ec.VALIDATION_ERROR, status_code=422)
    dupes = sorted(
        {k for k in set_keys if set_keys.count(k) > 1}
        | {k for k in remove_keys if remove_keys.count(k) > 1}
        | (set(set_keys) & set(remove_keys))
    )
    if dupes:
        raise APIError(
            message="Each price may appear only once in a batch",
            code=ec.VALIDATION_ERROR,
            status_code=422,
            details={"prices": [
                {"date": d.isoformat(), "commodity": c, "quote_currency": q} for d, c, q in dupes
            ]},
        )

    new_prices = [
        data.Price({}, p.date, p.commodity, Amount(_plain(p.number), p.quote_currency))
        for p in request.set
    ]
    with ledger_error_context(str(ledger_manager.ledger_file)):
        result = await run_in_threadpool(
            ledger_manager.edit_price_directives, new_prices, remove_keys,
            {(p.date, p.commodity, p.quote_currency) for p in request.set if not p.replace},
        )
    return success_json_response(PriceEditData(
        added=result["added"], updated=result["updated"],
        removed=result["removed"], total=result["total"],
    ))
