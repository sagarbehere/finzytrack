import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Path, Query
from app.schemas.response_schemas import ApiResponse
from app.schemas.account_schemas import (
    AccountCreateRequest, AccountCreateData, AccountListData,
    AccountUpdateRequest, AccountUpdateData,
    AccountCloseRequest, AccountCloseData, AccountDeleteData,
    AccountReopenData, BalanceDirectiveListData, BalanceDirectiveCreateRequest,
    BalanceDirectiveCreateData, BalanceDirectiveUpdateRequest,
    BalanceDirectiveUpdateData, BalanceDirectiveDeleteData,
)
from app.core.ledger_manager import LedgerManager
from app.core.config_manager import ConfigManager
from app.services.sqlite_reader import SqliteReader
from app.dependencies import get_config_manager, get_beancount_manager, get_sqlite_reader
from app.exceptions import APIError
from app.helpers.date_helpers import parse_optional_date_param
from app.helpers.error_context import ledger_error_context
from app.helpers.response_helpers import success_json_response
from app import error_codes as ec

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/accounts", response_model=ApiResponse[AccountListData], operation_id="listAccounts")
async def list_accounts(
    start_date: Optional[str] = Query(None, description="Start date for balance filtering (YYYY-MM-DD). For Income/Expenses only."),
    end_date: Optional[str] = Query(None, description="End date for balance filtering (YYYY-MM-DD). Defaults to today."),
    config_manager: ConfigManager = Depends(get_config_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
):
    """
    Retrieve all accounts with full details including transaction history and balances.

    Supports optional date filtering for balances:
    - Balance Sheet accounts (Assets, Liabilities, Equity): Balance as of end_date
    - Income Statement accounts (Income, Expenses): Balance within start_date to end_date

    Returns both open and closed accounts. Frontend applications should filter
    accounts based on the close_date field to show open vs closed accounts.
    """
    config = config_manager.get_config()

    with ledger_error_context(config.ledger_file):
        # Parse date parameters if provided
        start_date_obj = parse_optional_date_param(start_date, "start_date")
        end_date_obj = parse_optional_date_param(end_date, "end_date")

        # Get detailed account information with optional date filtering
        if start_date_obj is not None or end_date_obj is not None:
            detailed_accounts = sqlite_reader.get_accounts_filtered(
                start_date=start_date_obj,
                end_date=end_date_obj,
            )
        else:
            detailed_accounts = sqlite_reader.get_accounts()

        accounts_data = AccountListData(accounts=detailed_accounts)
        return success_json_response(accounts_data)

@router.post("/accounts", response_model=ApiResponse[AccountCreateData], status_code=201, operation_id="createAccount")
async def create_account_endpoint(
    request: AccountCreateRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: LedgerManager = Depends(get_beancount_manager)
):
    """Create and open a new Beancount account with comprehensive configuration."""
    try:
        # Delegate account creation to LedgerManager (business logic & ledger operations)
        create_data = beancount_manager.create_account_directive(request)
        
        return success_json_response(create_data, status_code=201)
        
    except ValueError as e:
        # Convert ValueError to appropriate APIError
        error_message = str(e)
        
        # Match specific error patterns
        if "Invalid account format" in error_message:
            raise APIError(
                message="Invalid account format",
                code=ec.VALIDATION_ERROR,
                status_code=422,
                details={
                    "field": "name",
                    "account_name": request.name,
                    "help": "Account name must follow Beancount naming conventions (e.g., 'Assets:Bank:Checking')"
                }
            )
        elif "Account already exists" in error_message:
            raise APIError(
                message="Account already exists",
                code=ec.ACCOUNT_ALREADY_EXISTS,
                status_code=409,
                details={"account_name": request.name}
            )
        elif "Invalid open_date format" in error_message:
            raise APIError(
                message="Invalid open_date format",
                code=ec.VALIDATION_ERROR,
                status_code=422,
                details={
                    "field": "open_date",
                    "open_date": request.open_date,
                    "help": "Date must be in YYYY-MM-DD format"
                }
            )
        else:
            # Generic validation error
            raise APIError(
                message="Validation failed",
                code=ec.VALIDATION_ERROR,
                status_code=422,
                details={"validation_error": error_message}
            )
    
    except FileNotFoundError as e:
        raise APIError(
            message="Ledger file not found", 
            code=ec.FILE_NOT_FOUND, 
            status_code=404, 
            details={"path": str(e).split(": ")[-1]}  # Extract file path from exception
        )
    except PermissionError as e:
        raise APIError(
            message="Permission denied accessing ledger file", 
            code=ec.FILE_PERMISSION_ERROR, 
            status_code=403, 
            details={"path": str(e).split(": ")[-1]}  # Extract file path from exception
        )
    except Exception as e:
        # Handle unexpected errors from the manager
        error_message = str(e)
        
        # Account creation succeeded but details unavailable (recoverable)
        if "details unavailable" in error_message:
            # This means the account was created but we can't get the details
            # Return a success response with account_details as None
            create_data = AccountCreateData(
                account_created=True,
                account_details=None,
                message=f"Account '{request.name}' created successfully (details unavailable)"
            )
            return success_json_response(create_data, status_code=201)
        
        # Handle other unexpected errors
        raise APIError(
            message=f"Error creating account: {error_message}", 
            code=ec.UNKNOWN_SERVER_ERROR, 
            status_code=500
        )

@router.put("/accounts/{account_name}", response_model=ApiResponse[AccountUpdateData], operation_id="updateAccount")
async def update_account(
    request: AccountUpdateRequest,
    account_name: str = Path(..., description="Beancount account name to update"),
    config_manager: ConfigManager = Depends(get_config_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
    beancount_manager: LedgerManager = Depends(get_beancount_manager),
):
    """Update account details."""
    config = config_manager.get_config()

    with ledger_error_context(config.ledger_file):
        # Validate account exists
        account_names = sqlite_reader.get_account_names()
        if account_name not in account_names:
            raise APIError(
                message=f"Account not found: {account_name}",
                code=ec.ACCOUNT_NOT_FOUND,
                status_code=404,
                details={"account_name": account_name}
            )

        # Validate new name (if provided)
        if request.new_name:
            if not beancount_manager.validate_account_format(request.new_name):
                raise APIError(
                    message="Invalid new account format",
                    code=ec.VALIDATION_ERROR,
                    status_code=422,
                    details={
                        "field": "new_name",
                        "new_name": request.new_name,
                        "help": "New account name must follow Beancount naming conventions (e.g., 'Assets:Bank:Checking')"
                    }
                )

            # Check if new name already exists (and it's not the same as current name)
            if request.new_name != account_name and request.new_name in account_names:
                raise APIError(
                    message=f"Account name already exists: {request.new_name}",
                    code=ec.ACCOUNT_ALREADY_EXISTS,
                    status_code=409,
                    details={"new_name": request.new_name}
                )

        # Validate close_date vs open_date
        if request.close_date:
            acct = sqlite_reader.get_account(account_name)
            current_open_date = acct.open_date if acct else None
            effective_open = request.open_date or current_open_date or datetime.min.date()
            if request.close_date < effective_open:
                raise APIError(
                    message="Close date must be after open date",
                    code=ec.VALIDATION_ERROR,
                    status_code=422,
                    details={
                        "field": "close_date",
                        "close_date": request.close_date.isoformat(),
                        "help": "Accounts cannot be closed before they were opened"
                    }
                )

        new_name = request.new_name or account_name

        # Delegate the write to LedgerManager (parse → modify entries → print)
        beancount_manager.update_account_directive(
            account_name,
            new_name=request.new_name or None,
            open_date=request.open_date,
            currencies=request.currencies or None,
            metadata=request.metadata or None,
            close_date=request.close_date,
        )

        # Build response from refreshed data
        updated_detailed_accounts = sqlite_reader.get_accounts()
        updated_account_info = next((a for a in updated_detailed_accounts if a.name == new_name), None)

        updated = bool(
            request.new_name or request.open_date or request.currencies
            or request.close_date is not None or request.metadata
        )

        update_data = AccountUpdateData(
            account_updated=updated,
            account_details=updated_account_info if updated else None,
            message=f"Account '{new_name}' updated successfully" if updated else f"Account '{new_name}' unchanged",
        )
        return success_json_response(update_data)

@router.post("/accounts/{account_name}/close", response_model=ApiResponse[AccountCloseData], operation_id="closeAccount")
async def close_account(
    request: AccountCloseRequest,
    account_name: str = Path(..., description="Beancount account name to close"),
    config_manager: ConfigManager = Depends(get_config_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
    beancount_manager: LedgerManager = Depends(get_beancount_manager),
):
    """Close an account by adding a closing directive to the Beancount ledger."""
    config = config_manager.get_config()

    with ledger_error_context(config.ledger_file):
        # Validate account exists
        acct = sqlite_reader.get_account(account_name)
        if not acct:
            raise APIError(
                message=f"Account not found: {account_name}",
                code=ec.ACCOUNT_NOT_FOUND,
                status_code=404,
                details={"account_name": account_name}
            )

        # Check if account is already closed
        if acct.close_date:
            raise APIError(
                message=f"Account is already closed: {account_name}",
                code=ec.ACCOUNT_ALREADY_CLOSED,
                status_code=409,
                details={"account_name": account_name, "close_date": acct.close_date.isoformat()}
            )

        # Ensure close_date is after open_date
        if acct.open_date and request.close_date < acct.open_date:
            raise APIError(
                message="Close date must be after open date",
                code=ec.VALIDATION_ERROR,
                status_code=422,
                details={
                    "field": "close_date",
                    "close_date": request.close_date.isoformat(),
                    "open_date": acct.open_date.isoformat(),
                    "help": "Accounts cannot be closed before they were opened"
                }
            )

        # Delegate the write to LedgerManager (parse → modify entries → print)
        beancount_manager.close_account_directive(account_name, request.close_date, reason=request.reason or None)

        close_data = AccountCloseData(
            account_closed=True,
            message=f"Account '{account_name}' closed successfully"
        )

        return success_json_response(close_data)

@router.post("/accounts/{account_name}/reopen", response_model=ApiResponse[AccountReopenData], operation_id="reopenAccount")
async def reopen_account(
    account_name: str = Path(..., description="Beancount account name to reopen"),
    config_manager: ConfigManager = Depends(get_config_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
    beancount_manager: LedgerManager = Depends(get_beancount_manager),
):
    """Reopen a closed account by removing the close directive from the ledger."""
    config = config_manager.get_config()

    with ledger_error_context(config.ledger_file):
        # Validate account exists
        acct = sqlite_reader.get_account(account_name)
        if not acct:
            raise APIError(
                message=f"Account not found: {account_name}",
                code=ec.ACCOUNT_NOT_FOUND,
                status_code=404,
                details={"account_name": account_name}
            )

        # Check if account is actually closed
        if not acct.close_date:
            raise APIError(
                message=f"Account is not closed: {account_name}",
                code=ec.ACCOUNT_NOT_CLOSED,
                status_code=409,
                details={"account_name": account_name}
            )

        # Delegate the write to LedgerManager (parse → modify entries → print)
        beancount_manager.reopen_account_directive(account_name)

        reopen_data = AccountReopenData(
            account_reopened=True,
            message=f"Account '{account_name}' reopened successfully"
        )

        return success_json_response(reopen_data)

@router.delete("/accounts/{account_name}", response_model=ApiResponse[AccountDeleteData], operation_id="deleteAccount")
async def delete_account(
    account_name: str = Path(..., description="Beancount account name to delete"),
    delete_transactions: bool = Query(True, description="Whether to delete transactions associated with this account"),
    config_manager: ConfigManager = Depends(get_config_manager),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
    beancount_manager: LedgerManager = Depends(get_beancount_manager),
):
    """Remove account from ledger. Optionally deletes associated transactions."""
    config = config_manager.get_config()

    with ledger_error_context(config.ledger_file):
        # Validate account exists
        if account_name not in sqlite_reader.get_account_names():
            raise APIError(
                message=f"Account not found: {account_name}",
                code=ec.ACCOUNT_NOT_FOUND,
                status_code=404,
                details={"account_name": account_name}
            )

        # Atomic delete: transactions + directives in one write
        try:
            transactions_deleted = beancount_manager.delete_account(account_name, delete_transactions=delete_transactions)
        except ValueError:
            # Account has transactions but delete_transactions is False
            acct_detail = sqlite_reader.get_account(account_name)
            transaction_summary = {c.currency: c for c in acct_detail.currencies} if acct_detail else {}
            total = sum(s.transaction_count for s in transaction_summary.values())
            raise APIError(
                message=f"Account '{account_name}' has {total} transaction(s). "
                        f"Either set delete_transactions=true or remove them manually first.",
                code=ec.ACCOUNT_HAS_TRANSACTIONS,
                status_code=409,
                details={"account_name": account_name, "transaction_count": total}
            )

        if transactions_deleted > 0:
            message = f"Account '{account_name}' and {transactions_deleted} transaction(s) deleted successfully"
        else:
            message = f"Account '{account_name}' deleted successfully"

        delete_data = AccountDeleteData(
            account_deleted=True,
            message=message,
            transactions_deleted=transactions_deleted if delete_transactions else None
        )

        return success_json_response(delete_data)

# =====================================
# Balance & Pad Directive Endpoints
# =====================================

@router.get(
    "/accounts/{account_name}/balance-directives",
    response_model=ApiResponse[BalanceDirectiveListData],
    operation_id="listBalanceDirectives"
)
async def list_balance_directives(
    account_name: str = Path(..., description="Beancount account name"),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
):
    """List all balance directives for an account, including pad and error info."""
    if account_name not in sqlite_reader.get_account_names():
        raise APIError(
            message=f"Account not found: {account_name}",
            code=ec.ACCOUNT_NOT_FOUND,
            status_code=404,
            details={"account_name": account_name}
        )

    directives = sqlite_reader.get_balance_directives(account_name)
    return success_json_response(BalanceDirectiveListData(
        account=account_name,
        directives=directives,
    ))


@router.post(
    "/accounts/{account_name}/balance-directives",
    response_model=ApiResponse[BalanceDirectiveCreateData],
    status_code=201,
    operation_id="createBalanceDirective"
)
async def create_balance_directive(
    request: BalanceDirectiveCreateRequest,
    account_name: str = Path(..., description="Beancount account name"),
    beancount_manager: LedgerManager = Depends(get_beancount_manager)
):
    """Create a balance assertion (optionally with a pad directive)."""
    try:
        beancount_manager.add_balance_directive(account_name, request)
        pad_msg = " with pad directive" if request.include_pad else ""
        return success_json_response(
            BalanceDirectiveCreateData(
                created=True,
                message=f"Balance directive created for {account_name}{pad_msg}",
            ),
            status_code=201,
        )
    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg:
            raise APIError(
                message=error_msg,
                code=ec.ACCOUNT_NOT_FOUND,
                status_code=404,
                details={"account_name": account_name}
            )
        raise APIError(
            message=error_msg,
            code=ec.VALIDATION_ERROR,
            status_code=422,
            details={"account_name": account_name}
        )


@router.put(
    "/accounts/{account_name}/balance-directives",
    response_model=ApiResponse[BalanceDirectiveUpdateData],
    operation_id="updateBalanceDirective"
)
async def update_balance_directive(
    request: BalanceDirectiveUpdateRequest,
    account_name: str = Path(..., description="Beancount account name"),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
    beancount_manager: LedgerManager = Depends(get_beancount_manager),
):
    """Update an existing balance directive."""
    if account_name not in sqlite_reader.get_account_names():
        raise APIError(
            message=f"Account not found: {account_name}",
            code=ec.ACCOUNT_NOT_FOUND,
            status_code=404,
            details={"account_name": account_name}
        )

    try:
        beancount_manager.update_balance_directive(account_name, request)
        return success_json_response(BalanceDirectiveUpdateData(
            updated=True,
            message=f"Balance directive updated for {account_name}",
        ))
    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            raise APIError(
                message=error_msg,
                code=ec.DIRECTIVE_NOT_FOUND,
                status_code=404,
                details={"account_name": account_name}
            )
        raise APIError(
            message=error_msg,
            code=ec.VALIDATION_ERROR,
            status_code=422,
            details={"account_name": account_name}
        )


@router.delete(
    "/accounts/{account_name}/balance-directives",
    response_model=ApiResponse[BalanceDirectiveDeleteData],
    operation_id="deleteBalanceDirective"
)
async def delete_balance_directive(
    account_name: str = Path(..., description="Beancount account name"),
    date: str = Query(..., description="Directive date (YYYY-MM-DD)"),
    currency: str = Query(..., description="Currency code"),
    amount: Decimal = Query(..., description="Expected balance amount"),
    delete_pad: bool = Query(True, description="Also delete associated pad directive"),
    sqlite_reader: SqliteReader = Depends(get_sqlite_reader),
    beancount_manager: LedgerManager = Depends(get_beancount_manager),
):
    """Delete a balance directive (and optionally its associated pad)."""
    if account_name not in sqlite_reader.get_account_names():
        raise APIError(
            message=f"Account not found: {account_name}",
            code=ec.ACCOUNT_NOT_FOUND,
            status_code=404,
            details={"account_name": account_name}
        )

    try:
        beancount_manager.delete_balance_directive(
            account_name, date, currency, amount, delete_pad
        )
        pad_msg = " and pad directive" if delete_pad else ""
        return success_json_response(BalanceDirectiveDeleteData(
            deleted=True,
            message=f"Balance directive{pad_msg} deleted for {account_name}",
        ))
    except ValueError as e:
        raise APIError(
            message=str(e),
            code=ec.DIRECTIVE_NOT_FOUND,
            status_code=404,
            details={"account_name": account_name}
        )