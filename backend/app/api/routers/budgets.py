"""Budgets router — CRUD over `custom "budget"` directives (dev-docs/budget.md §6.3).

Reads come from the SQLite mirror (custom_directives); writes go through
LedgerManager (the single authorised write path) and are verified by a
subsequent read. Money is a decimal string on the wire (money-types.md).
"""

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, Query, Body

from app.core.ledger_manager import LedgerManager
from app.core.budget_directives import budget_id, INTERVALS
from app.compute.budget_resolver import parse_budget_directives
from app.dependencies import get_sqlite_reader, get_beancount_manager
from app.services.sqlite_reader import SqliteReader
from app.schemas.budget_schemas import (
    BudgetItem, BudgetListData, BudgetWriteRequest, BudgetWriteData,
)
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response
from app.exceptions import APIError
from app import error_codes as ec

logger = logging.getLogger(__name__)

router = APIRouter()


def _load_budget_items(reader: SqliteReader) -> list[dict]:
    """All raw budget directives from the mirror, each as a dict with an id."""
    rows = reader.get_custom_directives("budget")
    directives = parse_budget_directives(rows)
    items = []
    for d in directives:
        items.append({
            "id": budget_id(d.source_file, d.source_lineno),
            "date": d.date,
            "account": d.account,
            "interval": d.interval,
            "amount": d.amount,
            "currency": d.currency,
            "source_file": d.source_file,
            "source_lineno": d.source_lineno,
        })
    return items


def _to_item(d: dict) -> BudgetItem:
    return BudgetItem(
        id=d["id"],
        date=d["date"].isoformat() if isinstance(d["date"], date) else str(d["date"]),
        account=d["account"],
        interval=d["interval"],
        amount=str(d["amount"]),
        currency=d["currency"],
        source_file=d.get("source_file"),
        source_lineno=d.get("source_lineno", 0),
    )


def _effective_as_of(items: list[dict], as_of: date) -> list[dict]:
    """One directive per (account, currency): the latest with date <= as_of."""
    by_key: dict[tuple, dict] = {}
    for d in sorted(items, key=lambda x: (x["date"], x.get("source_file") or "", x.get("source_lineno", 0))):
        if d["date"] <= as_of:
            by_key[(d["account"], d["currency"])] = d
    return list(by_key.values())


def _validate_write(body: BudgetWriteRequest) -> tuple[date, Decimal]:
    if body.interval not in INTERVALS:
        raise APIError(
            message=f"interval must be one of {list(INTERVALS)}.",
            code=ec.BUDGET_VALIDATION_ERROR, status_code=400,
        )
    try:
        d = date.fromisoformat(body.date)
    except (ValueError, TypeError):
        raise APIError(message="date must be YYYY-MM-DD.", code=ec.BUDGET_VALIDATION_ERROR, status_code=400)
    try:
        amount = Decimal(body.amount)
    except (InvalidOperation, TypeError):
        raise APIError(message="amount must be a decimal.", code=ec.BUDGET_VALIDATION_ERROR, status_code=400)
    return d, amount


@router.get("/budgets", response_model=ApiResponse[BudgetListData], operation_id="getBudgets")
async def get_budgets(
    account: Optional[str] = Query(None, description="Filter to one account"),
    currency: Optional[str] = Query(None, description="Filter to one currency"),
    as_of: Optional[str] = Query(None, description="Effective date (YYYY-MM-DD); defaults to today"),
    history: bool = Query(False, description="Return all raw directives (history) instead of the effective set"),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
):
    """Effective budgets as of ``as_of`` (default today), or all raw directives
    with ``history=true``."""
    items = _load_budget_items(sqlite_reader)
    if account:
        items = [d for d in items if d["account"] == account]
    if currency:
        items = [d for d in items if d["currency"] == currency]

    if not history:
        try:
            as_of_date = date.fromisoformat(as_of) if as_of else date.today()
        except (ValueError, TypeError):
            raise APIError(message="as_of must be YYYY-MM-DD.", code=ec.BUDGET_VALIDATION_ERROR, status_code=400)
        items = _effective_as_of(items, as_of_date)

    items.sort(key=lambda d: (d["account"], d["currency"], d["date"]))
    return success_json_response(BudgetListData(budgets=[_to_item(d) for d in items]))


def _find_item(reader: SqliteReader, budget_id_str: str) -> Optional[dict]:
    return next((d for d in _load_budget_items(reader) if d["id"] == budget_id_str), None)


@router.post("/budgets", response_model=ApiResponse[BudgetWriteData], operation_id="createBudget")
async def create_budget(
    body: BudgetWriteRequest = Body(...),
    manager: LedgerManager = Depends(get_beancount_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
):
    d, amount = _validate_write(body)
    manager.create_budget_directive(
        date_obj=d, account=body.account, interval=body.interval,
        amount=amount, currency=body.currency,
    )
    # Verify via read-back: find the directive we just wrote.
    written = next(
        (it for it in _load_budget_items(sqlite_reader)
         if it["account"] == body.account and it["currency"] == body.currency
         and it["interval"] == body.interval and it["date"] == d and it["amount"] == amount),
        None,
    )
    return success_json_response(BudgetWriteData(
        budget=_to_item(written) if written else None,
        message=f"Budget for {body.account} created.",
    ))


@router.put("/budgets/{budget_id}", response_model=ApiResponse[BudgetWriteData], operation_id="updateBudget")
async def update_budget(
    budget_id: str,
    body: BudgetWriteRequest = Body(...),
    manager: LedgerManager = Depends(get_beancount_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
):
    d, amount = _validate_write(body)
    ok = manager.update_budget_directive(
        budget_id, date_obj=d, account=body.account, interval=body.interval,
        amount=amount, currency=body.currency,
    )
    if not ok:
        raise APIError(message=f"Budget '{budget_id}' not found.", code=ec.BUDGET_NOT_FOUND, status_code=404)
    written = next(
        (it for it in _load_budget_items(sqlite_reader)
         if it["account"] == body.account and it["currency"] == body.currency
         and it["interval"] == body.interval and it["date"] == d and it["amount"] == amount),
        None,
    )
    return success_json_response(BudgetWriteData(
        budget=_to_item(written) if written else None,
        message=f"Budget for {body.account} updated.",
    ))


@router.delete("/budgets/{budget_id}", response_model=ApiResponse[BudgetWriteData], operation_id="deleteBudget")
async def delete_budget(
    budget_id: str,
    manager: LedgerManager = Depends(get_beancount_manager),
):
    ok = manager.delete_budget_directive(budget_id)
    if not ok:
        raise APIError(message=f"Budget '{budget_id}' not found.", code=ec.BUDGET_NOT_FOUND, status_code=404)
    return success_json_response(BudgetWriteData(budget=None, message="Budget deleted."))
