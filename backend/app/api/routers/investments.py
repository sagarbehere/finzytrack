"""Investments router — the shared event assembler + holdings reads.

`POST /api/investments/assemble` turns typed investment events into balanced draft
transactions (review-table shape); `GET /api/investments/holdings` returns current
positions and their open lots. The assembler **returns drafts and never writes** —
Register goes through the existing `/api/import/commit` path
(dev-docs/investment-view.md §6).
"""

import logging

from fastapi import APIRouter, Depends

from app.schemas.response_schemas import ApiResponse
from app.schemas.investment_schemas import (
    AssembleRequest,
    AssembleData,
    HoldingsData,
)
from app.core.config_manager import ConfigManager
from app.services.sqlite_reader import SqliteReader
from app.services.investment_assembler import InvestmentAssembler
from app.dependencies import get_config_manager, get_sqlite_reader
from app.helpers.error_context import ledger_error_context
from app.helpers.response_helpers import success_json_response

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/investments/assemble",
    response_model=ApiResponse[AssembleData],
    operation_id="assembleInvestmentEvents",
)
async def assemble_investment_events(
    request: AssembleRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
):
    """Assemble one or more typed investment events into balanced draft transactions.

    Partial success is allowed: some events become drafts while others return typed
    warnings/errors. The response is never committed — drafts stage into the review
    table and Register uses the existing commit path.
    """
    config = config_manager.get_config()
    with ledger_error_context(config.ledger_file):
        assembler = InvestmentAssembler(sqlite_reader)
        data = assembler.assemble(request.events)
        return success_json_response(data)


@router.get(
    "/investments/holdings",
    response_model=ApiResponse[HoldingsData],
    operation_id="listInvestmentHoldings",
)
async def list_investment_holdings(
    config_manager: ConfigManager = Depends(get_config_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
):
    """Return every held position and its open lots (with latest price)."""
    config = config_manager.get_config()
    with ledger_error_context(config.ledger_file):
        assembler = InvestmentAssembler(sqlite_reader)
        return success_json_response(assembler.holdings())


@router.get(
    "/investments/holdings/{commodity}",
    response_model=ApiResponse[HoldingsData],
    operation_id="getInvestmentHoldingsForCommodity",
)
async def get_investment_holdings_for_commodity(
    commodity: str,
    config_manager: ConfigManager = Depends(get_config_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
):
    """Return the open lots for a single commodity (feeds the Sale lot picker)."""
    config = config_manager.get_config()
    with ledger_error_context(config.ledger_file):
        assembler = InvestmentAssembler(sqlite_reader)
        return success_json_response(assembler.holdings(commodity=commodity))
