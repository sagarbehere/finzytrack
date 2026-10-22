"""Statement import (CAS today) — Investments → Import statement.

`parse` reads the PDF locally (the password is used once, never stored or logged) and
keeps the parsed statement in memory under a token; `preview` plans the import for the
user's mapping and corrections; `commit` writes it in one ledger write through the
gate. `imports` lists earlier imports and undoes one as a unit.
dev-docs/cas-import.md §5, §9, §13.
"""

import logging
from typing import Dict

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app import error_codes as ec
from app.core.config_manager import ConfigManager
from app.core.ledger_manager import LedgerManager
from app.dependencies import get_beancount_manager, get_config_manager
from app.exceptions import APIError
from app.helpers.error_context import ledger_error_context
from app.helpers.response_helpers import success_json_response
from app.schemas.response_schemas import ApiResponse
from app.schemas.statement_import_schemas import (
    StatementCommitData, StatementCommitRequest, StatementDistributor, StatementFundSummary,
    StatementImportListData, StatementImportRecord, StatementMapping, StatementParseData,
    StatementPlanData, StatementPlanRequest, StatementPreselection, StatementUndoData,
)
from app.services.statement_import import store
from app.services.statement_import.parsers.cas import StatementParseError, parse_cas_pdf
from app.services.statement_import.plan import DIRECT

logger = logging.getLogger(__name__)

router = APIRouter()

_MAX_FILE_SIZE = 25 * 1024 * 1024


def _statement(token: str):
    stmt = store.get(token)
    if stmt is None:
        raise APIError(
            message="This statement is no longer loaded. Choose the file again.",
            code=ec.STATEMENT_EXPIRED, status_code=410,
        )
    return stmt


def _preselect(snap, source: str) -> StatementPreselection:
    """Only the user's own earlier choice for the same role (the latest import's
    record), or an existing account matched by metadata — never a proposed name (§5.1)."""
    pre = StatementPreselection()
    last = next((r for r in reversed(snap.imports) if str(r.meta.get("source", "")) == source), None)
    open_accounts = {n for n, a in snap.accounts.items() if a.close_date is None}
    if last is not None:
        for pair in str(last.meta.get("holding_accounts", "")).split(";"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                if v in open_accounts:
                    pre.holding_accounts[k] = v
        for key in ("cash_fallback_account", "cash_matching_account", "dividend_account", "taxes_account", "gains_account"):
            v = last.meta.get(key)
            if v and str(v) in open_accounts:
                setattr(pre, key, str(v))
    for key, income_type in (("dividend_account", "dividend"), ("gains_account", "capital-gains")):
        if getattr(pre, key) is None:
            tagged = [n for n in sorted(open_accounts) if snap.accounts[n].meta.get("income-type") == income_type]
            if len(tagged) == 1:
                setattr(pre, key, tagged[0])
    return pre


@router.post(
    "/investments/statement/parse",
    response_model=ApiResponse[StatementParseData],
    operation_id="parseStatement",
)
async def parse_statement(
    file: UploadFile = File(...),
    password: str = Form(default=""),
    config_manager: ConfigManager = Depends(get_config_manager),
    ledger_manager: LedgerManager = Depends(get_beancount_manager),
):
    """Read a CAS PDF locally and keep the parsed statement for 30 minutes.
    Returns what the mapping screen needs: distributors, funds, which accounts are
    needed, the ledger's files, and the user's own earlier choices."""
    content = await file.read()
    if len(content) > _MAX_FILE_SIZE:
        raise APIError(message="The file is too large for a statement.", code=ec.CAS_UNRECOGNIZED, status_code=413)
    try:
        stmt = parse_cas_pdf(content, password)
    except StatementParseError as e:
        raise APIError(message=e.message, code=e.code, status_code=e.status_code)
    finally:
        del password

    config = config_manager.get_config()
    with ledger_error_context(config.ledger_file):
        plan, snap = ledger_manager.plan_statement(stmt, StatementMapping(), [])
    token = store.put(stmt)

    names: Dict[str, str] = {s.key: s.name for s in stmt.securities}
    dists: Dict[str, list] = {}
    for p in stmt.positions:
        dists.setdefault(p.advisor or DIRECT, []).append(names[p.security_key])
    distributors = [
        StatementDistributor(key=k, label=("Direct plans" if k == DIRECT else k), funds=sorted(set(v)))
        for k, v in sorted(dists.items())
    ]
    funds = [
        StatementFundSummary(security_key=p.security_key, name=names[p.security_key],
                             institution=stmt.account(p.account_id).institution, close_units=p.close_units)
        for p in stmt.positions
    ]
    return success_json_response(StatementParseData(
        token=token, source=stmt.source, variant=stmt.variant,
        period_start=stmt.period_start, period_end=stmt.period_end,
        investors=[i.name for i in stmt.investors if i.name],
        distributors=distributors, funds=funds, row_count=len(stmt.rows),
        needs_dividend_account="dividend" in plan.roles,
        needs_taxes_account="taxes" in plan.roles,
        needs_gains_account="gains" in plan.roles,
        needs_cash_account=bool({"cash", "cash_opening"} & plan.roles),
        ledger_files=snap.files,
        preselected=_preselect(snap, stmt.source),
        warnings=list(stmt.warnings),
    ))


@router.post(
    "/investments/statement/preview",
    response_model=ApiResponse[StatementPlanData],
    operation_id="previewStatement",
)
async def preview_statement(
    request: StatementPlanRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    ledger_manager: LedgerManager = Depends(get_beancount_manager),
):
    """Plan the import for the user's mapping and row corrections. Nothing is written."""
    stmt = _statement(request.token)
    config = config_manager.get_config()
    with ledger_error_context(config.ledger_file):
        plan, _snap = ledger_manager.plan_statement(stmt, request.mapping, request.corrections)
        return success_json_response(plan.data)


@router.post(
    "/investments/statement/commit",
    response_model=ApiResponse[StatementCommitData],
    operation_id="commitStatement",
)
async def commit_statement(
    request: StatementCommitRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    ledger_manager: LedgerManager = Depends(get_beancount_manager),
):
    """Write the import the user previewed, in one ledger write (409 PREVIEW_STALE if
    the ledger changed since the preview)."""
    stmt = _statement(request.token)
    config = config_manager.get_config()
    with ledger_error_context(config.ledger_file):
        result = ledger_manager.import_statement(stmt, request.mapping, request.corrections, request.expected_preview_hash)
        return success_json_response(StatementCommitData(**result))


@router.get(
    "/investments/statement/imports",
    response_model=ApiResponse[StatementImportListData],
    operation_id="listStatementImports",
)
async def list_statement_imports(
    config_manager: ConfigManager = Depends(get_config_manager),
    ledger_manager: LedgerManager = Depends(get_beancount_manager),
):
    """Earlier statement imports recorded in the ledger, newest first."""
    config = config_manager.get_config()
    with ledger_error_context(config.ledger_file):
        records = [StatementImportRecord(**r) for r in ledger_manager.list_statement_imports()]
        return success_json_response(StatementImportListData(imports=records))


@router.delete(
    "/investments/statement/imports/{import_id}",
    response_model=ApiResponse[StatementUndoData],
    operation_id="undoStatementImport",
)
async def undo_statement_import(
    import_id: str,
    config_manager: ConfigManager = Depends(get_config_manager),
    ledger_manager: LedgerManager = Depends(get_beancount_manager),
):
    """Remove one import's transactions, balance assertions and record in one write."""
    config = config_manager.get_config()
    with ledger_error_context(config.ledger_file):
        return success_json_response(StatementUndoData(**ledger_manager.undo_statement_import(import_id)))
