import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Path, Query
from app.schemas.response_schemas import ApiResponse
from app.schemas.account_schemas import (
    AccountCreateRequest, AccountCreateData, AccountListData,
    AccountUpdateRequest, AccountUpdateData,
    AccountCloseRequest, AccountCloseData, AccountDeleteData,
    AccountReopenData, AccountDetails, AccountCurrencyData,
    BalanceDirectiveListData, BalanceDirectiveCreateRequest,
    BalanceDirectiveCreateData, BalanceDirectiveUpdateRequest,
    BalanceDirectiveUpdateData, BalanceDirectiveDeleteData,
)
from app.core.beancount_manager import BeancountManager
from app.core.config_manager import ConfigManager
from app.dependencies import get_config_manager, get_beancount_manager
from app.exceptions import APIError
from app.helpers.response_helpers import success_json_response

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/accounts", response_model=ApiResponse[AccountListData], operation_id="listAccounts")
async def list_accounts(
    start_date: Optional[str] = Query(None, description="Start date for balance filtering (YYYY-MM-DD). For Income/Expenses only."),
    end_date: Optional[str] = Query(None, description="End date for balance filtering (YYYY-MM-DD). Defaults to today."),
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
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

    try:
        # Parse date parameters if provided
        start_date_obj = None
        end_date_obj = None

        if start_date:
            try:
                start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
            except ValueError:
                raise APIError(
                    message="Invalid start_date format",
                    code="VALIDATION_ERROR",
                    status_code=422,
                    details={"field": "start_date", "value": start_date, "help": "Date must be in YYYY-MM-DD format"}
                )

        if end_date:
            try:
                end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
            except ValueError:
                raise APIError(
                    message="Invalid end_date format",
                    code="VALIDATION_ERROR",
                    status_code=422,
                    details={"field": "end_date", "value": end_date, "help": "Date must be in YYYY-MM-DD format"}
                )

        # Get detailed account information with optional date filtering
        if start_date_obj is not None or end_date_obj is not None:
            detailed_accounts = beancount_manager.get_detailed_accounts_filtered(
                start_date=start_date_obj,
                end_date=end_date_obj
            )
        else:
            detailed_accounts = beancount_manager.get_detailed_accounts()

        accounts_data = AccountListData(accounts=detailed_accounts)
        return success_json_response(accounts_data)
        
    except FileNotFoundError:
        raise APIError(
            message="Ledger file not found", 
            code="FILE_NOT_FOUND", 
            status_code=404, 
            details={"path": config.ledger_file}
        )
    except PermissionError:
        raise APIError(
            message="Permission denied accessing ledger file",
            code="FILE_PERMISSION_ERROR",
            status_code=403,
            details={"path": config.ledger_file}
        )
    except APIError:
        # Re-raise API errors (validation errors for date params, etc.)
        raise
    except Exception as e:
        raise APIError(
            message=f"Error accessing ledger: {str(e)}",
            code="UNKNOWN_SERVER_ERROR",
            status_code=500
        )

@router.post("/accounts", response_model=ApiResponse[AccountCreateData], status_code=201, operation_id="createAccount")
async def create_account_endpoint(
    request: AccountCreateRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    """Create and open a new Beancount account with comprehensive configuration."""
    try:
        # Delegate account creation to BeancountManager (business logic & ledger operations)
        create_data = beancount_manager.create_account_directive(request)
        
        return success_json_response(create_data, status_code=201)
        
    except ValueError as e:
        # Convert ValueError to appropriate APIError
        error_message = str(e)
        
        # Match specific error patterns
        if "Invalid account format" in error_message:
            raise APIError(
                message="Invalid account format",
                code="VALIDATION_ERROR",
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
                code="ACCOUNT_ALREADY_EXISTS",
                status_code=409,
                details={"account_name": request.name}
            )
        elif "Invalid open_date format" in error_message:
            raise APIError(
                message="Invalid open_date format",
                code="VALIDATION_ERROR",
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
                code="VALIDATION_ERROR",
                status_code=422,
                details={"validation_error": error_message}
            )
    
    except FileNotFoundError as e:
        raise APIError(
            message="Ledger file not found", 
            code="FILE_NOT_FOUND", 
            status_code=404, 
            details={"path": str(e).split(": ")[-1]}  # Extract file path from exception
        )
    except PermissionError as e:
        raise APIError(
            message="Permission denied accessing ledger file", 
            code="FILE_PERMISSION_ERROR", 
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
            code="UNKNOWN_SERVER_ERROR", 
            status_code=500
        )

# FIXME: This update_account() function needs to be rigorously reviewed and updated.
# FIXME: It has many business logic errors/inadquacies
# FIXME: Also, implementation sub-optimalities. E.g. at the end, it should not look for different types of errors and raise API errors. 
# There's already code in setup_error_handlers in error_handler.py that converts any uncaught exception to a 500 APIError.
@router.put("/accounts/{account_name}", response_model=ApiResponse[AccountUpdateData], operation_id="updateAccount")
async def update_account(
    request: AccountUpdateRequest,
    account_name: str = Path(..., description="Beancount account name to update"),
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    """Update account details."""
    config = config_manager.get_config()
    
    try:
        # Validate account exists
        if not beancount_manager.is_existing_account(account_name):
            raise APIError(
                message=f"Account not found: {account_name}",
                code="ACCOUNT_NOT_FOUND",
                status_code=404,
                details={"account_name": account_name}
            )
        
        # Validate no updates if account is closed (can only reopen via close_date=null)
        close_date = beancount_manager.get_account_close_date(account_name)
        if close_date and request.close_date is None:
            # Attempting to reopen a closed account
            pass  # This is allowed
        
        # Validate new name (if provided)
        if request.new_name:
            if not beancount_manager.validate_account_format(request.new_name):
                raise APIError(
                    message="Invalid new account format",
                    code="VALIDATION_ERROR",
                    status_code=422,
                    details={
                        "field": "new_name",
                        "new_name": request.new_name,
                        "help": "New account name must follow Beancount naming conventions (e.g., 'Assets:Bank:Checking')"
                    }
                )
            
            # Check if new name already exists (and it's not the same as current name)
            if request.new_name != account_name and beancount_manager.is_existing_account(request.new_name):
                raise APIError(
                    message=f"Account name already exists: {request.new_name}",
                    code="ACCOUNT_ALREADY_EXISTS",
                    status_code=409,
                    details={"new_name": request.new_name}
                )
        
        # Validate open_date (if provided)
        open_date_obj = None
        if request.open_date:
            try:
                open_date_obj = datetime.strptime(request.open_date, "%Y-%m-%d").date()
            except ValueError:
                raise APIError(
                    message="Invalid open_date format",
                    code="VALIDATION_ERROR",
                    status_code=422,
                    details={
                        "field": "open_date",
                        "open_date": request.open_date,
                        "help": "Date must be in YYYY-MM-DD format"
                    }
                )
        
        # Validate close_date (if provided)
        close_date_obj = None
        if request.close_date:
            try:
                close_date_obj = datetime.strptime(request.close_date, "%Y-%m-%d").date()
            except ValueError:
                raise APIError(
                    message="Invalid close_date format",
                    code="VALIDATION_ERROR",
                    status_code=422,
                    details={
                        "field": "close_date",
                        "close_date": request.close_date,
                        "help": "Date must be in YYYY-MM-DD format"
                    }
                )
            
            # Get current open date for validation
            current_open_date = beancount_manager.get_account_open_date(account_name)
            if close_date_obj < (open_date_obj or current_open_date or datetime.min.date()):
                raise APIError(
                    message="Close date must be after open date",
                    code="VALIDATION_ERROR",
                    status_code=422,
                    details={
                        "field": "close_date",
                        "close_date": request.close_date,
                        "help": "Accounts cannot be closed before they were opened"
                    }
                )
        
        # Perform the update using atomic write with cache invalidation
        with beancount_manager.atomic_ledger_write() as f:
            current_content = f.read()
            lines = current_content.split('\n')
            
            # Find the open directive for this account
            open_directive_index = -1
            open_directive_line = ""
            old_name = account_name
            new_name = request.new_name or account_name
            
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith(f'open {account_name}') and not stripped.startswith(';'):
                    open_directive_index = i
                    open_directive_line = line
                    break
            
            if open_directive_index == -1:
                raise APIError(
                    message=f"Account open directive not found: {account_name}",
                    code="ACCOUNT_DIRECTIVE_NOT_FOUND",
                    status_code=404,
                    details={"account_name": account_name}
                )
            
            # Parse the original open directive
            parts = open_directive_line.split()
            if len(parts) < 3:
                raise APIError(
                    message=f"Malformed open directive: {open_directive_line}",
                    code="MALFORMED_DIRECTIVE",
                    status_code=500
                )
            
            original_date = parts[0]
            original_currencies = parts[3:] if len(parts) > 3 else []
            
            # Build new open directive
            new_date = request.open_date or original_date
            new_currencies = request.currencies or original_currencies
            
            # Get current metadata from existing metadata in file
            current_metadata = beancount_manager.get_account_metadata(account_name)
            new_metadata = request.metadata or {}
            
            # Merge metadata (new metadata overwrites existing)
            merged_metadata = {**current_metadata, **new_metadata}
            
            # Build the new open directive
            new_open_directive = f"{new_date} open {new_name}"
            if new_currencies:
                new_open_directive += " " + " ".join(new_currencies)
            
            # Add metadata as inline comments
            if merged_metadata:
                for key, value in merged_metadata.items():
                    if isinstance(value, str):
                        new_open_directive += f"  ; {key}: {value}"
                    else:
                        new_open_directive += f"  ; {key}: {str(value)}"
            
            # Replace the open directive
            lines[open_directive_index] = new_open_directive
            
            # Find and replace/close any existing close directive if closing/reopening
            if request.close_date is not None:  # Either closing or reopening
                close_directive_modified = False
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    if stripped.startswith(f'close {old_name}') and not stripped.startswith(';'):
                        if request.close_date:  # Closing/updating close date
                            # Update the close directive date
                            close_parts = line.strip().split()
                            close_parts[0] = request.close_date
                            # Update name if it changed
                            if old_name != new_name:
                                close_parts[2] = new_name
                            lines[i] = " " + " ".join(close_parts)
                        else:  # Reopening (close_date = null)
                            # Remove the close directive
                            lines[i] = ""
                            # Remove any empty lines after
                            if i + 1 < len(lines) and lines[i + 1].strip() == "":
                                lines[i + 1] = ""
                        close_directive_modified = True
                        break
                
                # If closing and no existing close directive, add one
                if request.close_date and not close_directive_modified:
                    # Find insertion point for close directive
                    insert_index = len(lines)
                    for i, line in enumerate(lines):
                        if line.strip().startswith(f'open {new_name}'):
                            insert_index = i + 1  # Insert after open directive
                            break
                    
                    if insert_index < len(lines) and lines[insert_index].strip() != "":
                        lines.insert(insert_index, "")
                        insert_index += 1
                    
                    close_directive = f"{request.close_date} close {new_name}"
                    lines.insert(insert_index, close_directive)
            
            # Update account name in any other directives if name changed
            if old_name != new_name:
                for i, line in enumerate(lines):
                    if i == open_directive_index:  # Skip the open directive we already updated
                        continue
                    
                    stripped = line.strip()
                    if not stripped.startswith(';'):
                        # Check for account references in transactions
                        if f'{old_name}' in line and not stripped.startswith('open') and not stripped.startswith('close'):
                            lines[i] = line.replace(old_name, new_name)
            
            # Clean up any empty lines and write back
            new_lines = [line for line in lines if line.strip() != ""]
            while new_lines and new_lines[-1].strip() == "":
                new_lines.pop()
            
            new_content = '\n'.join(new_lines)
            if new_content:
                new_content += '\n'
            
            f.seek(0)
            f.write(new_content)
            f.truncate()
        

        
        # Get the updated account details for response
        try:
            updated_detailed_accounts = beancount_manager.get_detailed_accounts()
            updated_account_info = None
            for account_info in updated_detailed_accounts:
                if account_info.name == new_name:
                    updated_account_info = account_info
                    break
            
            if not updated_account_info:
                # Fallback: construct manually - use epoch date if open_date missing (indicates error)
                fallback_open_date = new_date if new_date else original_date
                # Validate fallback_open_date is valid
                if not fallback_open_date or fallback_open_date == "1970-01-01":
                    # Use epoch date to indicate error/missing open directive
                    fallback_open_date = "1970-01-01"
                
                # Create proper AccountDetails instance
                updated_account_info = AccountDetails(
                    name=new_name,
                    open_date=fallback_open_date,  # Already a string
                    close_date=request.close_date,  # Already a string
                    currencies=[],  # Empty for fallback
                    metadata=merged_metadata
                )
            
            # Get transaction summary
            try:
                transaction_summary = beancount_manager.get_account_transactions_summary(new_name)
            except Exception:
                transaction_summary = {}
            
            # Convert transaction summary to AccountCurrencyData objects
            currencies_list = []
            for currency, summary in transaction_summary.items():
                currency_data = AccountCurrencyData(
                    currency=summary.currency,
                    transaction_count=summary.transaction_count,
                    last_transaction_date=summary.last_transaction_date,  # Already a string from AccountCurrencyData
                    balance=float(summary.balance)
                )
                currencies_list.append(currency_data)
            
            account_details = AccountDetails(
                name=updated_account_info.name,
                open_date=updated_account_info.open_date,  # Already a string from AccountDetails
                close_date=updated_account_info.close_date,  # Already a string from AccountDetails
                currencies=currencies_list,
                metadata=updated_account_info.metadata
            )
            
            updated = (old_name != new_name or 
                      request.open_date is not None or 
                      request.currencies is not None or 
                      request.close_date is not None or
                      request.metadata is not None)
            
            update_data = AccountUpdateData(
                account_updated=updated,
                account_details=account_details if updated else None,
                message=f"Account '{new_name}' updated successfully" if updated else f"Account '{new_name}' unchanged"
            )
            
            return success_json_response(update_data)
            
        except Exception as e:
            logger.error(f"Error getting updated account details: {e}")
            # Fallback response
            update_data = AccountUpdateData(
                account_updated=True,
                account_details=None,
                message=f"Account '{new_name}' updated successfully (details unavailable)"
            )
            return success_json_response(update_data)
            
    except APIError:
        # Re-raise API errors as-is
        raise
    except FileNotFoundError:
        raise APIError(
            message="Ledger file not found", 
            code="FILE_NOT_FOUND", 
            status_code=404, 
            details={"path": config.ledger_file}
        )
    except PermissionError:
        raise APIError(
            message="Permission denied accessing ledger file", 
            code="FILE_PERMISSION_ERROR", 
            status_code=403, 
            details={"path": config.ledger_file}
        )
    except Exception as e:
        raise APIError(
            message=f"Error updating account: {str(e)}", 
            code="UNKNOWN_SERVER_ERROR", 
            status_code=500
        )

# FIXME: This function needs to be rigorously reviewed and updated. It has many business logic errors/inadquacies
@router.post("/accounts/{account_name}/close", response_model=ApiResponse[AccountCloseData], operation_id="closeAccount")
async def close_account(
    request: AccountCloseRequest,
    account_name: str = Path(..., description="Beancount account name to close"),
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    """Close an account by adding a closing directive to the Beancount ledger."""
    config = config_manager.get_config()
    
    try:
        # Validate account exists
        if not beancount_manager.is_existing_account(account_name):
            raise APIError(
                message=f"Account not found: {account_name}",
                code="ACCOUNT_NOT_FOUND",
                status_code=404,
                details={"account_name": account_name}
            )
        
        # Check if account is already closed
        existing_close_date = beancount_manager.get_account_close_date(account_name)
        if existing_close_date:
            raise APIError(
                message=f"Account is already closed: {account_name}",
                code="ACCOUNT_ALREADY_CLOSED",
                status_code=409,
                details={"account_name": account_name, "close_date": existing_close_date.isoformat()}
            )
        
        # Parse and validate close_date
        try:
            close_date_obj = datetime.strptime(request.close_date, "%Y-%m-%d").date()
        except ValueError:
            raise APIError(
                message="Invalid close_date format",
                code="VALIDATION_ERROR",
                status_code=422,
                details={
                    "field": "close_date",
                    "close_date": request.close_date,
                    "help": "Date must be in YYYY-MM-DD format"
                }
            )
        
        # Ensure close_date is after open_date
        open_date = beancount_manager.get_account_open_date(account_name)
        if open_date and close_date_obj < open_date:
            raise APIError(
                message="Close date must be after open date",
                code="VALIDATION_ERROR",
                status_code=422,
                details={
                    "field": "close_date",
                    "close_date": request.close_date,
                    "open_date": open_date.isoformat(),
                    "help": "Accounts cannot be closed before they were opened"
                }
            )
        
        # Create the closing directive
        close_directive = f"{close_date_obj} close {account_name}"
        
        # Add reason as inline comment if provided
        if request.reason:
            close_directive += f"  ; reason: {request.reason}"
        
        # Use atomic write to add the closing directive (SIMPLE APPEND) with cache invalidation
        with beancount_manager.atomic_ledger_write() as func:
            current_content = func.read()

            # Simple append with proper formatting
            if current_content and not current_content.endswith('\n'):
                current_content += '\n'
            if current_content and not current_content.endswith('\n\n'):
                current_content += '\n'

            new_content = current_content + close_directive + '\n'
            
            func.seek(0)
            func.write(new_content)
            func.truncate()
        

        
        close_data = AccountCloseData(
            account_closed=True,
            message=f"Account '{account_name}' closed successfully"
        )
        
        return success_json_response(close_data)
        
    except APIError:
        # Re-raise API errors as-is
        raise
    except FileNotFoundError:
        raise APIError(
            message="Ledger file not found", 
            code="FILE_NOT_FOUND", 
            status_code=404, 
            details={"path": config.ledger_file}
        )
    except PermissionError:
        raise APIError(
            message="Permission denied accessing ledger file", 
            code="FILE_PERMISSION_ERROR", 
            status_code=403, 
            details={"path": config.ledger_file}
        )
    except Exception as e:
        raise APIError(
            message=f"Error closing account: {str(e)}",
            code="UNKNOWN_SERVER_ERROR",
            status_code=500
        )

@router.post("/accounts/{account_name}/reopen", response_model=ApiResponse[AccountReopenData], operation_id="reopenAccount")
async def reopen_account(
    account_name: str = Path(..., description="Beancount account name to reopen"),
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    """Reopen a closed account by removing the close directive from the ledger."""
    config = config_manager.get_config()

    try:
        # Validate account exists
        if not beancount_manager.is_existing_account(account_name):
            raise APIError(
                message=f"Account not found: {account_name}",
                code="ACCOUNT_NOT_FOUND",
                status_code=404,
                details={"account_name": account_name}
            )

        # Check if account is actually closed
        close_date = beancount_manager.get_account_close_date(account_name)
        if not close_date:
            raise APIError(
                message=f"Account is not closed: {account_name}",
                code="ACCOUNT_NOT_CLOSED",
                status_code=409,
                details={"account_name": account_name}
            )

        # Remove the close directive using atomic write
        with beancount_manager.atomic_ledger_write() as f:
            current_content = f.read()
            lines = current_content.split('\n')

            close_directive_removed = False
            for i, line in enumerate(lines):
                stripped = line.strip()
                # Match close directive: "YYYY-MM-DD close AccountName"
                if ' close ' in stripped and account_name in stripped and not stripped.startswith(';'):
                    # Verify this is actually a close directive for this account
                    parts = stripped.split()
                    if len(parts) >= 3 and parts[1] == 'close' and parts[2] == account_name:
                        lines[i] = ""  # Remove the close directive
                        close_directive_removed = True
                        break

            if not close_directive_removed:
                raise APIError(
                    message=f"Close directive not found for account: {account_name}",
                    code="CLOSE_DIRECTIVE_NOT_FOUND",
                    status_code=404,
                    details={"account_name": account_name}
                )

            # Clean up empty lines and write back
            new_lines = [line for line in lines if line.strip() != ""]
            new_content = '\n'.join(new_lines)
            if new_content:
                new_content += '\n'

            f.seek(0)
            f.write(new_content)
            f.truncate()

        reopen_data = AccountReopenData(
            account_reopened=True,
            message=f"Account '{account_name}' reopened successfully"
        )

        return success_json_response(reopen_data)

    except APIError:
        raise
    except FileNotFoundError:
        raise APIError(
            message="Ledger file not found",
            code="FILE_NOT_FOUND",
            status_code=404,
            details={"path": config.ledger_file}
        )
    except PermissionError:
        raise APIError(
            message="Permission denied accessing ledger file",
            code="FILE_PERMISSION_ERROR",
            status_code=403,
            details={"path": config.ledger_file}
        )
    except Exception as e:
        raise APIError(
            message=f"Error reopening account: {str(e)}",
            code="UNKNOWN_SERVER_ERROR",
            status_code=500
        )

# FIXME: This function needs to be rigorously reviewed and updated. It has many business logic errors/inadquacies
@router.delete("/accounts/{account_name}", response_model=ApiResponse[AccountDeleteData], operation_id="deleteAccount")
async def delete_account(
    account_name: str = Path(..., description="Beancount account name to delete"),
    delete_transactions: bool = Query(True, description="Whether to delete transactions associated with this account"),
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    """Remove account from ledger. Optionally deletes associated transactions."""
    config = config_manager.get_config()
    warnings = []
    transactions_deleted = 0

    try:
        # Validate account exists
        if not beancount_manager.is_existing_account(account_name):
            raise APIError(
                message=f"Account not found: {account_name}",
                code="ACCOUNT_NOT_FOUND",
                status_code=404,
                details={"account_name": account_name}
            )

        # Check if account has transactions
        total_transactions = 0
        try:
            transaction_summary = beancount_manager.get_account_transactions_summary(account_name)
            total_transactions = sum(summary.transaction_count for summary in transaction_summary.values())

            if total_transactions > 0 and not delete_transactions:
                warnings.append(f"Account has {total_transactions} transactions associated with it (not deleted)")
        except Exception:
            # If we can't check transactions, proceed with caution
            warnings.append("Unable to verify account transaction history")

        # Check if account is closed
        close_date = beancount_manager.get_account_close_date(account_name)
        if not close_date:
            warnings.append("Account is still open")

        # Get additional account info for warning messages
        open_date = beancount_manager.get_account_open_date(account_name)
        if open_date:
            # Calculate age of account
            from datetime import date
            current_date = date.today()
            account_age = (current_date - open_date).days
            if account_age > 365:  # Older than 1 year
                warnings.append(f"Account has been active for {account_age} days")

        # Delete transactions if requested
        if delete_transactions and total_transactions > 0:
            transactions_deleted = beancount_manager.delete_transactions_for_account(account_name)

        # Perform the deletion of open/close directives using atomic write
        with beancount_manager.atomic_ledger_write() as f:
            current_content = f.read()
            lines = current_content.split('\n')

            # Find and remove the open directive
            open_directive_removed = False
            references_found = 0

            for i, line in enumerate(lines):
                stripped = line.strip()

                # Match open directive: "YYYY-MM-DD open AccountName ..."
                if ' open ' in stripped and account_name in stripped and not stripped.startswith(';'):
                    parts = stripped.split()
                    if len(parts) >= 3 and parts[1] == 'open' and parts[2] == account_name:
                        lines[i] = ""  # Remove the open directive
                        open_directive_removed = True

                # Match close directive: "YYYY-MM-DD close AccountName"
                elif ' close ' in stripped and account_name in stripped and not stripped.startswith(';'):
                    parts = stripped.split()
                    if len(parts) >= 3 and parts[1] == 'close' and parts[2] == account_name:
                        lines[i] = ""  # Remove the close directive

                # Count remaining references in transactions (for warning purposes)
                elif (not stripped.startswith(';') and
                      ' open ' not in stripped and
                      ' close ' not in stripped and
                      account_name in line):
                    references_found += 1

            if not open_directive_removed:
                raise APIError(
                    message=f"Account open directive not found: {account_name}",
                    code="ACCOUNT_DIRECTIVE_NOT_FOUND",
                    status_code=404,
                    details={"account_name": account_name}
                )

            # Add warning about transaction references if any found and not deleted
            if references_found > 0 and not delete_transactions:
                warnings.append(f"Account is referenced in {references_found} transaction lines (not deleted)")

            # Clean up empty lines and write back
            new_lines = [line for line in lines if line.strip() != ""]
            new_content = '\n'.join(new_lines)
            if new_content:
                new_content += '\n'

            f.seek(0)
            f.write(new_content)
            f.truncate()

        # Prepare success message
        if transactions_deleted > 0:
            message = f"Account '{account_name}' and {transactions_deleted} transaction(s) deleted successfully"
        elif warnings:
            message = f"Account '{account_name}' deleted with warnings"
        else:
            message = f"Account '{account_name}' deleted successfully"

        delete_data = AccountDeleteData(
            account_deleted=True,
            message=message,
            warnings=warnings if warnings else None,
            transactions_deleted=transactions_deleted if delete_transactions else None
        )
        
        return success_json_response(delete_data)
        
    except APIError:
        # Re-raise API errors as-is
        raise
    except FileNotFoundError:
        raise APIError(
            message="Ledger file not found", 
            code="FILE_NOT_FOUND", 
            status_code=404, 
            details={"path": config.ledger_file}
        )
    except PermissionError:
        raise APIError(
            message="Permission denied accessing ledger file", 
            code="FILE_PERMISSION_ERROR", 
            status_code=403, 
            details={"path": config.ledger_file}
        )
    except Exception as e:
        raise APIError(
            message=f"Error deleting account: {str(e)}",
            code="UNKNOWN_SERVER_ERROR",
            status_code=500
        )

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
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    """List all balance directives for an account, including pad and error info."""
    if not beancount_manager.is_existing_account(account_name):
        raise APIError(
            message=f"Account not found: {account_name}",
            code="ACCOUNT_NOT_FOUND",
            status_code=404,
            details={"account_name": account_name}
        )

    directives = beancount_manager.get_balance_directives(account_name)
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
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
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
                code="ACCOUNT_NOT_FOUND",
                status_code=404,
                details={"account_name": account_name}
            )
        raise APIError(
            message=error_msg,
            code="VALIDATION_ERROR",
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
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    """Update an existing balance directive."""
    if not beancount_manager.is_existing_account(account_name):
        raise APIError(
            message=f"Account not found: {account_name}",
            code="ACCOUNT_NOT_FOUND",
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
                code="DIRECTIVE_NOT_FOUND",
                status_code=404,
                details={"account_name": account_name}
            )
        raise APIError(
            message=error_msg,
            code="VALIDATION_ERROR",
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
    amount: float = Query(..., description="Expected balance amount"),
    delete_pad: bool = Query(True, description="Also delete associated pad directive"),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    """Delete a balance directive (and optionally its associated pad)."""
    if not beancount_manager.is_existing_account(account_name):
        raise APIError(
            message=f"Account not found: {account_name}",
            code="ACCOUNT_NOT_FOUND",
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
            code="DIRECTIVE_NOT_FOUND",
            status_code=404,
            details={"account_name": account_name}
        )