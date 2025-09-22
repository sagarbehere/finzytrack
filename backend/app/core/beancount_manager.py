import os
import re
import logging
from typing import Set, Optional, List, Dict, Any
from datetime import date
from beancount import loader
from beancount.core import data
from decimal import Decimal

from app.core.backup_manager import BackupManager
from .ledger_initializer import LedgerInitializer
from app.schemas.account_schemas import AccountDetails, AccountCurrencyData

logger = logging.getLogger(__name__)

class BeancountManager:
    def __init__(self, ledger_file: str, backup_manager: BackupManager, ledger_initializer: LedgerInitializer):
        self.ledger_file = ledger_file
        self._accounts_cache: Optional[Set[str]] = None
        self._last_modified: Optional[float] = None
        self.backup_manager = backup_manager
        self.ledger_initializer = ledger_initializer
    
    def get_accounts(self) -> Set[str]:
        """Get all account names from Beancount ledger with caching."""
        # The ledger should exist at startup, but double-check for safety
        if not os.path.exists(self.ledger_file):
            logger.error(f"Ledger file not found: {self.ledger_file}")
            return set()
        
        # Check if file was modified since last cache
        current_modified = os.path.getmtime(self.ledger_file)
        if (
            self._accounts_cache is None
            or self._last_modified is None
            or current_modified > self._last_modified
        ):
            
            self._load_accounts()
            self._last_modified = current_modified
        
        return self._accounts_cache or set()
    
    def _load_accounts(self) -> None:
        """Load accounts from Beancount ledger file."""
        entries, errors, _ = loader.load_file(self.ledger_file)
        
        if errors:
            # Log errors but don't fail completely
            logger.warning(f"Beancount parsing warnings: {len(errors)} issues found")
        
        accounts = set()
        for entry in entries:
            if isinstance(entry, data.Open):
                accounts.add(entry.account)
            elif isinstance(entry, data.Transaction):
                for posting in entry.postings:
                    if posting.account:
                        accounts.add(posting.account)
        
        self._accounts_cache = accounts
    
    def is_existing_account(self, account_name: str) -> bool:
        """Check if account name exists in ledger."""
        accounts = self.get_accounts()
        return account_name in accounts
    
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
    
    def create_account(self, account_name: str, currency: str) -> None:
        """
        Create a new account in the Beancount ledger.
        """
        # The ledger should exist at startup, but double-check for safety
        if not os.path.exists(self.ledger_file):
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")
        
        # Validate account name format
        if not self.validate_account_format(account_name):
            raise ValueError(f"Invalid account name format: {account_name}")

        # Check if account already exists
        if self.is_existing_account(account_name):
            return  # Account already exists, consider it success
        
        epoch_date = date(1970, 1, 1)
        open_directive = f"{epoch_date} open {account_name} {currency}"

        with self.backup_manager.atomic_write(self.ledger_file) as f:
            current_content = f.read()
            
            lines = current_content.split('\n')
            insert_index = 0
            
            for i, line in enumerate(lines):
                if line.strip().startswith('open ') and line.strip().endswith(currency):
                    insert_index = i + 1
                elif line.strip().startswith('open '):
                    insert_index = i + 1
            
            if insert_index == 0:
                for i, line in enumerate(lines):
                    if not line.strip().startswith(';') and line.strip():
                        insert_index = i
                        break
            
            lines.insert(insert_index, open_directive)
            lines.insert(insert_index + 1, "")
            new_content = '\n'.join(lines)

            f.seek(0)
            f.write(new_content)
            f.truncate()
        
        # Clear cache to force reload
        self._accounts_cache = None
        self._last_modified = None

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
                    currencies=[],  # Will populate from transaction analysis
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
                        currencies=[],
                        metadata={"error": "Account has close directive but no open directive"}
                    )
        
        # Get transaction data for each account
        for account_name in account_details_map:
            currency_data = self._get_account_currency_data(account_name, entries)
            account_details_map[account_name].currencies = currency_data
        
        return list(account_details_map.values())
    
    def _get_account_currency_data(self, account_name: str, entries) -> List[AccountCurrencyData]:
        """Extract currency data for an account from transaction entries."""
        currency_info = {}
        
        for entry in entries:
            if isinstance(entry, data.Transaction):
                for posting in entry.postings:
                    if posting.account == account_name:
                        currency = posting.units.currency if posting.units else "UNKNOWN"
                        
                        if currency not in currency_info:
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
