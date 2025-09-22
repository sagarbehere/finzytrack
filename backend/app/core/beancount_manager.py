import os
import re
import logging
import linecache
from typing import Set, Optional, List, Dict, Any
from datetime import datetime, date
from beancount import loader
from beancount.core import data
from decimal import Decimal

from app.core.backup_manager import BackupManager
from .ledger_initializer import LedgerInitializer
from app.schemas.account_schemas import (
    AccountDetails, AccountCurrencyData, AccountCreateRequest, AccountCreateData
)

logger = logging.getLogger(__name__)

class BeancountManager:
    def __init__(self, ledger_file: str, backup_manager: BackupManager, ledger_initializer: LedgerInitializer):
        self.ledger_file = ledger_file
        self.backup_manager = backup_manager
        self.ledger_initializer = ledger_initializer
      
    def is_existing_account(self, account_name: str) -> bool:
        """Check if account name exists in ledger."""
        detailed_accounts = self.get_detailed_accounts()
        account_names = {account.name for account in detailed_accounts}
        return account_name in account_names
    
    def validate_account_format(self, account_name: str) -> bool:
        """Validate Beancount account name format."""
        # Beancount account format: Type:Subtypes:Account (e.g., Assets:Bank:Checking)
        pattern = r'^[A-Z][A-Za-z0-9\-_]*(?::[A-Z][A-Za-z0-9\-_]*)*$'
        
        # Check if account name adheres to the regex pattern
        if not bool(re.match(pattern, account_name)):
            return False
        
        # Check if account name starts with one of the required account types (case insensitive)
        valid_account_types = ['Income:', 'Expenses:', 'Equity:', 'Assets:', 'Liabilities:']
        account_lower = account_name.lower()
        
        return any(account_lower.startswith(account_type.lower()) for account_type in valid_account_types)
    
    def has_parsing_errors(self) -> bool:
        """Check if ledger file has parsing errors."""
        if not os.path.exists(self.ledger_file):
            return False
        
        try:
            entries, errors, _ = loader.load_file(self.ledger_file)
            return len(errors) > 0
        except Exception:
            return True


    def get_detailed_accounts(self) -> List[AccountDetails]:
        """Get comprehensive account information including opening/closing dates and metadata."""
        if not os.path.exists(self.ledger_file):
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")
        
        entries, errors, _ = loader.load_file(self.ledger_file)
        
        if errors:
            logger.warning(f"Beancount parsing warnings: {len(errors)} issues found")
        
        # Track account information by name
        account_details_map: Dict[str, AccountDetails] = {}
        
        # First pass: collect Open directives
        for entry in entries:
            if isinstance(entry, data.Open):
                account_name = entry.account
                metadata = entry.meta if hasattr(entry, 'meta') else {}
                
                account_details_map[account_name] = AccountDetails(
                    name=account_name,
                    open_date=entry.date.isoformat(),  # Convert date to string
                    close_date=None,  # Will be set in second pass if closed
                    currencies=[],  # Will populate from currency analysis (both open & transactions)
                    metadata=metadata
                )
            elif isinstance(entry, data.Close):
                account_name = entry.account
                if account_name in account_details_map:
                    account_details_map[account_name].close_date = entry.date.isoformat()
                else:
                    # Account was closed without explicit open directive
                    account_details_map[account_name] = AccountDetails(
                        name=account_name,
                        open_date="1970-01-01",  # Epoch date indicates missing open
                        close_date=entry.date.isoformat(),
                        currencies=[],  # Will populate from currency analysis
                        metadata={"error": "Account has close directive but no open directive"}
                    )
        
        # Get currency data for each account (includes both open directive and transaction currencies)
        for account_name in account_details_map:
            currency_data = self._get_account_currency_data(account_name, entries)
            account_details_map[account_name].currencies = currency_data
        
        return list(account_details_map.values())
    
    def _get_account_currency_data(self, account_name: str, entries) -> List[AccountCurrencyData]:
        """Extract currency data for an account from open directives and transaction entries."""
        currency_info: Dict[str, Dict[str, Any]] = {}
        
        # First pass: Find the open directive for this account to get declared currencies
        open_currencies = set()
        for entry in entries:
            if isinstance(entry, data.Open) and entry.account == account_name:
                if hasattr(entry, 'currencies') and entry.currencies:
                    # Beancount's Open entry has currencies attribute
                    open_currencies.update(entry.currencies)
                else:
                    # Fallback: extract currencies from the raw entry text
                    open_currencies.update(self._extract_currencies_from_open_entry(entry))
        
        # Initialize currency_info with currencies from open directive (0 transactions)
        for currency in open_currencies:
            currency_info[currency] = {
                "transaction_count": 0,
                "last_transaction_date": None,
                "balance": Decimal(0)
            }
        
        # Second pass: Process transactions to add transaction data and discover additional currencies
        for entry in entries:
            if isinstance(entry, data.Transaction):
                for posting in entry.postings:
                    if posting.account == account_name:
                        currency = posting.units.currency if posting.units else "UNKNOWN"
                        
                        if currency not in currency_info:
                            # Currency discovered from transactions but not in open directive
                            currency_info[currency] = {
                                "transaction_count": 0,
                                "last_transaction_date": None,
                                "balance": Decimal(0)
                            }
                        
                        currency_info[currency]["transaction_count"] += 1
                        
                        if currency_info[currency]["last_transaction_date"] is None or entry.date > currency_info[currency]["last_transaction_date"]:
                            currency_info[currency]["last_transaction_date"] = entry.date
                        
                        if posting.units:
                            currency_info[currency]["balance"] += posting.units.number
        
        # Convert to AccountCurrencyData objects
        result = []
        for currency, info in currency_info.items():
            result.append(AccountCurrencyData(
                currency=currency,
                transaction_count=info["transaction_count"],
                last_transaction_date=info["last_transaction_date"].isoformat() if info["last_transaction_date"] else None,
                balance=float(info["balance"])  # Convert Decimal to float for API
            ))
        
        return result
    
    def _extract_currencies_from_open_entry(self, open_entry: data.Open) -> Set[str]:
        """Extract currencies from a Beancount open directive entry."""
        currencies = set()
        
        # Beancount's Open entry should have currencies attribute, but let's handle both cases
        if hasattr(open_entry, 'currencies') and open_entry.currencies:
            currencies.update(open_entry.currencies)
        else:
            # Fallback: Extract from the source text by finding the line and parsing it
            # This is less ideal but ensures we don't miss currencies
            try:
                # The entry may have meta.filename and meta.lineno that can help
                safe_meta = getattr(open_entry, 'meta', None)
                if safe_meta is not None:
                    filename = getattr(safe_meta, 'filename', None)
                    lineno = getattr(safe_meta, 'lineno', None)
                    if filename and lineno:
                        line = linecache.getline(filename, lineno).strip()
                        if line.startswith(str(open_entry.date)) and 'open' in line:
                            parts = line.split()
                            # Format: <date> open <account> [<currency1> <currency2> ...]
                            # Account is at index 2, currencies start at index 3
                            if len(parts) > 3:
                                potential_currencies = parts[3:]
                                for part in potential_currencies:
                                    # Filter out metadata comments
                                    if part and not part.startswith(';'):
                                        currencies.add(part)
            except Exception:
                # If we can't extract from source, that's okay - the main hasattr check should have caught most cases
                pass
        
        return currencies
    
    def get_account_open_date(self, account_name: str) -> Optional[date]:
        """Get opening date for an account."""
        if not os.path.exists(self.ledger_file):
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")
        
        entries, errors, _ = loader.load_file(self.ledger_file)
        
        for entry in entries:
            if isinstance(entry, data.Open) and entry.account == account_name:
                return entry.date
        
        return None
    
    def get_account_close_date(self, account_name: str) -> Optional[date]:
        """Get closing date for an account."""
        if not os.path.exists(self.ledger_file):
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")
        
        entries, errors, _ = loader.load_file(self.ledger_file)
        
        for entry in entries:
            if isinstance(entry, data.Close) and entry.account == account_name:
                return entry.date
        
        return None
    
    def get_account_metadata(self, account_name: str) -> Dict[str, Any]:
        """Get metadata for an account."""
        if not os.path.exists(self.ledger_file):
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")
        
        entries, errors, _ = loader.load_file(self.ledger_file)
        
        for entry in entries:
            if isinstance(entry, data.Open) and entry.account == account_name:
                return entry.meta if hasattr(entry, 'meta') else {}
        
        return {}
    
    def get_account_transactions_summary(self, account_name: str) -> Dict[str, AccountCurrencyData]:
        """Get transaction summary per currency for an account."""
        if not os.path.exists(self.ledger_file):
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")
        
        entries, errors, _ = loader.load_file(self.ledger_file)
        
        if errors:
            logger.warning(f"Beancount parsing warnings: {len(errors)} issues found")
        
        # Extract currency data using the same helper method
        currency_data_list = self._get_account_currency_data(account_name, entries)
        
        # Convert to dictionary format for backward compatibility
        result = {}
        for currency_summary in currency_data_list:
            result[currency_summary.currency] = currency_summary
        
        return result

    def create_account_directive(self, request: AccountCreateRequest) -> AccountCreateData:
        """
        Create a new account by adding an open directive to the ledger.
        
        Args:
            request: Account creation request with name, open_date, currencies, etc.
            
        Returns:
            AccountCreateData with creation result and created account details
            
        Raises:
            ValueError: Invalid account format, account already exists, invalid date format
            FileNotFoundError: If ledger file doesn't exist
            PermissionError: If ledger file cannot be accessed
            Exception: For other ledger operation errors
        """
        # Validate account name format
        if not self.validate_account_format(request.name):
            raise ValueError(f"Invalid account format: {request.name}")
        
        # Check if account already exists
        if self.is_existing_account(request.name):
            raise ValueError(f"Account already exists: {request.name}")
        
        # Parse and validate open_date
        try:
            open_date_obj = datetime.strptime(request.open_date, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"Invalid open_date format: {request.open_date}")
        
        # Create the open directive
        currencies_str = " ".join(request.currencies)
        open_directive = f"{open_date_obj} open {request.name} {currencies_str}"
        
        # Prepare metadata
        metadata = request.metadata or {}
        if request.description:
            metadata["description"] = request.description
        
        # Add metadata as inline comments if any
        if metadata:
            for key, value in metadata.items():
                if isinstance(value, str):
                    open_directive += f"  ; {key}: {value}"
                else:
                    open_directive += f"  ; {key}: {str(value)}"
        
        # Use atomic write to add the open directive (SIMPLE APPEND)
        try:
            with self.backup_manager.atomic_write(self.ledger_file) as f:
                current_content = f.read()
                
                # Simple append with proper formatting
                if current_content and not current_content.endswith('\n'):
                    current_content += '\n'
                if current_content and not current_content.endswith('\n\n'):
                    current_content += '\n'
                    
                new_content = current_content + open_directive + '\n'
                
                f.seek(0)
                f.write(new_content)
                f.truncate()
                
            # Get the created account details with proper currency detection
            try:
                detailed_accounts = self.get_detailed_accounts()
                account_details = None
                for account in detailed_accounts:
                    if account.name == request.name:
                        account_details = account
                        break
                
                if account_details is None:
                    # This indicates a serious program error
                    logger.error(f"Account creation succeeded but account not found: {request.name}")
                    raise ValueError(
                        f"Account creation succeeded but account not found: {request.name}"
                    )
                
                return AccountCreateData(
                    account_created=True,
                    account_details=account_details,
                    message=f"Account '{request.name}' created successfully"
                )
                
            except Exception as e:
                logger.error(f"Error getting created account details: {e}")
                # Account was created but we can't retrieve details
                return AccountCreateData(
                    account_created=True,
                    account_details=None,
                    message=f"Account '{request.name}' created successfully (details unavailable)"
                )
                
        except FileNotFoundError:
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")
        except PermissionError:
            raise PermissionError(f"Permission denied accessing ledger file: {self.ledger_file}")
        except Exception as e:
            logger.error(f"Error creating account directive: {e}")
            raise Exception(f"Error creating account: {str(e)}")
