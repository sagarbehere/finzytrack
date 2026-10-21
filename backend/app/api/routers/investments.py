"""Investments router — the shared event assembler + holdings reads.

`POST /api/investments/assemble` turns typed investment events into balanced draft
transactions (review-table shape); `GET /api/investments/holdings` returns current
positions and their open lots. The assembler **returns drafts and never writes** —
Register goes through the existing `/api/import/commit` path
(dev-docs/investment-view.md §6).

`POST /api/investments/open-deposit` is the exception: it opens accounts, which the
review table can't stage, so it writes directly — in one write through the pre-write
gate — after the user has seen its `dry_run` preview.
"""

import logging

from fastapi import APIRouter, Depends

from app.schemas.response_schemas import ApiResponse
from app.schemas.investment_schemas import (
    AssembleRequest,
    AssembleData,
    HoldingsData,
    OpenDepositData,
    OpenDepositRequest,
)
from app.core.config_manager import ConfigManager
from app.services.sqlite_reader import SqliteReader
from app.services.investment_assembler import InvestmentAssembler
from app.core.ledger_manager import LedgerManager
from app.dependencies import get_beancount_manager, get_config_manager, get_sqlite_reader
from app.exceptions import APIError
from app.services.deposit_opener import AccountInfo, DepositError, preview_hash
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


@router.post(
    "/investments/open-deposit",
    response_model=ApiResponse[OpenDepositData],
    operation_id="openDeposit",
)
async def open_deposit(
    request: OpenDepositRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
    ledger_manager: LedgerManager = Depends(get_beancount_manager),
):
    """Open a savings, CD or bond account, optionally with its own interest income
    account (linked by `interest_account`) and its first deposit, as one write.

    With `dry_run`, nothing is written: the response is the text that would be
    written (apart from the transfer's IDs), checked against the current accounts,
    and its `preview_hash`. Send that back as `expected_preview_hash` so the write
    refuses (409 PREVIEW_STALE) if it would write anything different.
    """
    config = config_manager.get_config()
    with ledger_error_context(config.ledger_file):
        try:
            if request.dry_run:
                accounts = {
                    a.name: AccountInfo(
                        a.close_date is not None,
                        {k: v for k, v in (a.metadata or {}).items() if k not in ("filename", "lineno")},
                        tuple(a.declared_currencies or ()),
                        a.open_date,
                    )
                    for a in sqlite_reader.get_accounts()
                }
                preview, plan = ledger_manager.preview_open_deposit(request, accounts)
                created = False
            else:
                preview, plan = ledger_manager.open_deposit(request)
                created = True
        except DepositError as e:
            raise APIError(message=e.message, code=e.code, status_code=e.status_code, details=e.details)
        return success_json_response(OpenDepositData(
            preview=preview,
            preview_hash=preview_hash(preview, plan.warnings),
            created=created,
            accounts_created=[o.name for o in plan.opens] if created else [],
            warnings=plan.warnings,
        ))
