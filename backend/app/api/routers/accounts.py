import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Path
from app.schemas.response_schemas import ApiResponse
from app.schemas.account_schemas import (
    AccountCreateRequest, AccountCreateData, AccountListData,
    AccountUpdateRequest, AccountUpdateData, 
    AccountCloseRequest, AccountCloseData, AccountDeleteData,
    AccountDetails, AccountCurrencyData
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
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    """
    Retrieve all accounts with full details including transaction history and balances.
    
    Returns both open and closed accounts. Frontend applications should filter
    accounts based on the close_date field to show open vs closed accounts.
    """
    config = config_manager.get_config()
    
    try:
        # Get detailed account information from BeancountManager
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
        
        # Perform the update using atomic write
        with beancount_manager.backup_manager.atomic_write(beancount_manager.ledger_file) as f:
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
        
        # Use atomic write to add the closing directive (SIMPLE APPEND)
        with beancount_manager.backup_manager.atomic_write(beancount_manager.ledger_file) as func:
            current_content = func.read()
            
            # Simple append with proper formatting
            if current_content.endswith('\n'):
                new_content = current_content + close_directive + '\n'
            else:
                new_content = current_content + '\n' + close_directive + '\n'
            
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

# FIXME: This function needs to be rigorously reviewed and updated. It has many business logic errors/inadquacies
@router.delete("/accounts/{account_name}", response_model=ApiResponse[AccountDeleteData], operation_id="deleteAccount")
async def delete_account(
    account_name: str = Path(..., description="Beancount account name to delete"),
    config_manager: ConfigManager = Depends(get_config_manager),
    beancount_manager: BeancountManager = Depends(get_beancount_manager)
):
    """Remove account from ledger (deletes the opening directive)."""
    config = config_manager.get_config()
    warnings = []
    
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
        try:
            transaction_summary = beancount_manager.get_account_transactions_summary(account_name)
            total_transactions = sum(summary.transaction_count for summary in transaction_summary.values())
            
            if total_transactions > 0:
                warnings.append(f"Account has {total_transactions} transactions associated with it")
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
        
        # Perform the deletion using atomic write
        with beancount_manager.backup_manager.atomic_write(beancount_manager.ledger_file) as f:
            current_content = f.read()
            lines = current_content.split('\n')
            
            # Find and remove the open directive
            open_directive_removed = False
            close_directive_removed = False
            references_found = 0
            
            for i, line in enumerate(lines):
                stripped = line.strip()
                
                # Remove open directive
                if stripped.startswith(f'open {account_name}') and not stripped.startswith(';'):
                    lines[i] = ""  # Remove the open directive
                    # Remove any adjacent empty lines
                    if i + 1 < len(lines) and lines[i + 1].strip() == "":
                        lines[i + 1] = ""
                    if i > 0 and lines[i - 1].strip() == "":
                        lines[i - 1] = ""
                    open_directive_removed = True
                
                # Remove close directive if it exists
                elif stripped.startswith(f'close {account_name}') and not stripped.startswith(';'):
                    lines[i] = ""  # Remove the close directive
                    # Remove any adjacent empty lines
                    if i + 1 < len(lines) and lines[i + 1].strip() == "":
                        lines[i + 1] = ""
                    if i > 0 and lines[i - 1].strip() == "":
                        lines[i - 1] = ""
                    close_directive_removed = True
                
                # Count references in transactions (for warning purposes)
                elif (not stripped.startswith('open') and 
                      not stripped.startswith('close') and 
                      not stripped.startswith(';') and 
                      f'{account_name}' in line):
                    references_found += 1
            
            if not open_directive_removed:
                raise APIError(
                    message=f"Account open directive not found: {account_name}",
                    code="ACCOUNT_DIRECTIVE_NOT_FOUND",
                    status_code=404,
                    details={"account_name": account_name}
                )
            
            # Add warning about transaction references if any found
            if references_found > 0:
                warnings.append(f"Account is referenced in {references_found} transaction lines (these will not be automatically removed)")
            
            # Clean up empty lines and write back
            new_lines = [line for line in lines if line.strip() != ""]
            while new_lines and new_lines[-1].strip() == "":
                new_lines.pop()
            
            new_content = '\n'.join(new_lines)
            if new_content:
                new_content += '\n'
            
            f.seek(0)
            f.write(new_content)
            f.truncate()
        

        
        # Prepare success message with warnings if any
        if warnings:
            message = f"Account '{account_name}' deleted with warnings"
        else:
            message = f"Account '{account_name}' deleted successfully"
        
        delete_data = AccountDeleteData(
            account_deleted=True,
            message=message,
            warnings=warnings if warnings else None
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