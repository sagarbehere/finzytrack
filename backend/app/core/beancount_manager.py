import os
import re
import logging
from typing import Set, Optional, List, Dict, Any, Callable, Tuple
from datetime import datetime, date
from contextlib import contextmanager
from beancount import loader
from beancount.core import data
from beancount.core.data import Transaction, Posting
from beancount.core.amount import Amount
from decimal import Decimal
import uuid_utils as uuid

from app.core.backup_manager import BackupManager
from app.core.ledger_cache import LedgerCache
from .ledger_initializer import LedgerInitializer
from app.schemas.account_schemas import (
    AccountDetails, AccountCurrencyData, AccountCreateRequest, AccountCreateData
)
from app.schemas.commodity_schemas import (
    CommodityDetails, CommodityUsageData, CommodityCreateRequest, CommodityCreateData
)
from app.libs.content_hash import compute_content_hash

logger = logging.getLogger(__name__)

class BeancountManager:
    def __init__(self, ledger_file: str, backup_manager: BackupManager, ledger_initializer: LedgerInitializer):
        self.ledger_file = ledger_file
        self.backup_manager = backup_manager
        self.ledger_initializer = ledger_initializer
        self.cache = LedgerCache(ledger_file)
        self._on_cache_invalidated_callbacks: List[Callable[[List[Any]], None]] = []

    def register_cache_invalidation_callback(
        self,
        callback: Callable[[List[Any]], None]
    ) -> None:
        """
        Register a callback to be called when cache invalidates.

        Args:
            callback: Function that accepts a list of Beancount entries
        """
        self._on_cache_invalidated_callbacks.append(callback)
        logger.debug(f"Registered cache invalidation callback: {callback.__name__}")

    @contextmanager
    def atomic_ledger_write(self, file_path: Optional[str] = None):
        """
        Context manager for atomic ledger writes with automatic cache invalidation.

        This centralizes all ledger write operations and ensures the cache is
        invalidated after successful writes.

        Args:
            file_path: Optional path to write to (defaults to self.ledger_file)

        Yields:
            File handle from backup_manager.atomic_write()
        """
        target_file = file_path or self.ledger_file

        with self.backup_manager.atomic_write(target_file) as f:
            yield f

        # Invalidate cache after successful write
        self.cache.invalidate()
        logger.debug(f"Cache invalidated after write to {target_file}")

        # Notify observers
        entries = self.cache.get_entries()
        for callback in self._on_cache_invalidated_callbacks:
            try:
                callback(entries)
            except Exception as e:
                logger.error(f"Error in cache invalidation callback: {e}", exc_info=True)

    def is_existing_account(self, account_name: str) -> bool:
        """Check if account name exists in ledger (O(1) lookup)."""
        return account_name in self.cache.get_account_names()
    
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
            errors = self.cache.get_errors()
            return len(errors) > 0
        except Exception:
            return True


    def get_detailed_accounts(self) -> List[AccountDetails]:
        """Get comprehensive account information from cache."""
        if not os.path.exists(self.ledger_file):
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")

        # Get cached accounts
        accounts = self.cache.get_accounts()
        # Check for parsing errors
        errors = self.cache.get_errors()
        if errors:
            logger.warning(f"Beancount parsing warnings: {len(errors)} issues found")

        return list(accounts.values())
    
    def get_account_open_date(self, account_name: str) -> Optional[date]:
        """Get opening date for an account from cache."""
        if not os.path.exists(self.ledger_file):
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")

        accounts = self.cache.get_accounts()
        if account_name in accounts:
            open_date_str = accounts[account_name].open_date
            if open_date_str:
                return datetime.fromisoformat(open_date_str).date()

        return None

    def get_account_close_date(self, account_name: str) -> Optional[date]:
        """Get closing date for an account from cache."""
        if not os.path.exists(self.ledger_file):
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")

        accounts = self.cache.get_accounts()
        if account_name in accounts:
            close_date_str = accounts[account_name].close_date
            if close_date_str:
                return datetime.fromisoformat(close_date_str).date()

        return None

    def get_account_metadata(self, account_name: str) -> Dict[str, Any]:
        """Get metadata for an account from cache."""
        if not os.path.exists(self.ledger_file):
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")

        accounts = self.cache.get_accounts()
        if account_name in accounts:
            return accounts[account_name].metadata

        return {}

    def get_account_transactions_summary(self, account_name: str) -> Dict[str, AccountCurrencyData]:
        """Get transaction summary per currency for an account from cache."""
        if not os.path.exists(self.ledger_file):
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")

        # Get account from cache
        accounts = self.cache.get_accounts()

        errors = self.cache.get_errors()
        if errors:
            logger.warning(f"Beancount parsing warnings: {len(errors)} issues found")

        if account_name not in accounts:
            return {}

        # Convert currency list to dictionary format for backward compatibility
        result = {}
        for currency_data in accounts[account_name].currencies:
            result[currency_data.currency] = currency_data

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
            with self.atomic_ledger_write() as f:
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

    # =====================================
    # Commodity Management Methods
    # =====================================

    def get_detailed_commodities(self) -> List[CommodityDetails]:
        """Get comprehensive commodity information from cache."""
        if not os.path.exists(self.ledger_file):
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")

        # Get cached commodities
        commodities = self.cache.get_commodities()

        # Check for parsing errors
        errors = self.cache.get_errors()
        if errors:
            logger.warning(f"Beancount parsing warnings: {len(errors)} issues found")

        return list(commodities.values())

    def create_commodity_directive(self, request: CommodityCreateRequest) -> CommodityCreateData:
        """Create a new commodity directive in the Beancount ledger."""
        try:
            # Validate commodity doesn't already exist
            existing_commodities = self.get_detailed_commodities()
            existing_codes = {commodity.code for commodity in existing_commodities}
            
            if request.code in existing_codes:
                raise ValueError(f"Commodity already exists: {request.code}")
            
            # Build the commodity directive
            today = datetime.now().date()
            directive_lines = [f"{today} commodity {request.code}"]
            
            # Add name metadata if provided
            if request.name:
                directive_lines.append(f"  name: \"{request.name}\"")
            
            # Add type metadata (default to "Unknown" if not provided)
            commodity_type = request.type or "Unknown"
            directive_lines.append(f"  type: \"{commodity_type}\"")
            
            # Add additional metadata if provided
            if request.metadata:
                for key, value in request.metadata.items():
                    if key not in ['name', 'type']:  # Don't duplicate name/type
                        if isinstance(value, str):
                            directive_lines.append(f"  {key}: \"{value}\"")
                        else:
                            directive_lines.append(f"  {key}: {value}")
            
            directive_text = "\n".join(directive_lines)

            # Use atomic write to add the directive (SIMPLE APPEND)
            with self.atomic_ledger_write() as f:
                current_content = f.read()
                
                # Simple append with proper formatting
                if current_content and not current_content.endswith('\n'):
                    current_content += '\n'
                if current_content and not current_content.endswith('\n\n'):
                    current_content += '\n'
                    
                new_content = current_content + directive_text + '\n'
                
                f.seek(0)
                f.write(new_content)
                f.truncate()
            
            # Get the created commodity details
            try:
                updated_commodities = self.get_detailed_commodities()
                commodity_details = None
                for commodity in updated_commodities:
                    if commodity.code == request.code:
                        commodity_details = commodity
                        break
                
                return CommodityCreateData(
                    commodity_created=True,
                    commodity_details=commodity_details,
                    message=f"Commodity '{request.code}' created successfully"
                )
                
            except Exception as e:
                logger.error(f"Error getting created commodity details: {e}")
                # Commodity was created but we can't retrieve details
                return CommodityCreateData(
                    commodity_created=True,
                    commodity_details=None,
                    message=f"Commodity '{request.code}' created successfully (details unavailable)"
                )
                
        except FileNotFoundError:
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")
        except PermissionError:
            raise PermissionError(f"Permission denied accessing ledger file: {self.ledger_file}")
        except Exception as e:
            logger.error(f"Error creating commodity directive: {e}")
            raise Exception(f"Error creating commodity: {str(e)}")

    # =====================================
    # Transaction ID Management Methods
    # =====================================

    def create_transaction_with_ids(
        self,
        date_obj: date,
        payee: str,
        narration: str,
        postings: List[Posting],
        source_account: str,
        flag: str = '*',
        ofx_id: Optional[str] = None,
        additional_meta: Optional[Dict] = None
    ) -> Transaction:
        """
        Create a Beancount transaction with new UUIDv7 + content_hash ID system.

        Args:
            date_obj: Transaction date
            payee: Transaction payee
            narration: Transaction narration/description
            postings: List of Beancount Posting objects
            source_account: The account that originated this transaction
            flag: Transaction flag (* or !)
            ofx_id: Optional OFX transaction ID
            additional_meta: Optional additional metadata

        Returns:
            beancount.core.data.Transaction object with id and content_hash

        Note:
            This is the ONLY method that should create transactions with IDs.
            BeancountManager is the ONLY module that imports beancount library.
        """
        # Generate UUIDv7 (time-ordered)
        transaction_id = str(uuid.uuid7())

        # Compute content hash
        # Get amount from source account posting
        amount_str = "0"
        for posting in postings:
            if posting.account == source_account and posting.units:
                amount_str = f"{posting.units.number} {posting.units.currency}"
                break

        content_hash = compute_content_hash(
            date=date_obj.isoformat(),
            payee=payee,
            amount=amount_str,
            source_account=source_account,
            narration=narration
        )

        # Build metadata
        meta = {
            'id': transaction_id,
            'content_hash': content_hash,
            'source_account': source_account
        }

        # Add OFX ID if provided
        if ofx_id and str(ofx_id).strip():
            meta['ofx_id'] = str(ofx_id).strip()

        # Add additional metadata
        if additional_meta:
            meta.update(additional_meta)

        # Create Beancount transaction
        return Transaction(
            meta=meta,
            date=date_obj,
            flag=flag,
            payee=payee,
            narration=narration,
            tags=frozenset(),
            links=frozenset(),
            postings=postings
        )

    def add_ids_to_transaction(
        self,
        txn: Transaction,
        force_regenerate: bool = False
    ) -> Transaction:
        """
        Add id and content_hash to an existing transaction.

        Args:
            txn: Beancount Transaction object
            force_regenerate: If True, regenerate IDs even if they exist

        Returns:
            Transaction with id and content_hash in metadata

        Note:
            Used by migration script and for updating transactions without IDs.
        """
        # Check if already has IDs
        if not force_regenerate and txn.meta:
            if 'id' in txn.meta and 'content_hash' in txn.meta:
                return txn

        # Generate new UUIDv7
        transaction_id = str(uuid.uuid7())

        # Compute content hash from transaction
        source_account, content_hash = self.compute_hash_from_transaction(txn)

        # Update metadata
        new_meta = txn.meta.copy() if txn.meta else {}
        new_meta['id'] = transaction_id
        new_meta['content_hash'] = content_hash

        # Preserve source_account if not already set
        if 'source_account' not in new_meta and source_account:
            new_meta['source_account'] = source_account

        return txn._replace(meta=new_meta)

    def compute_hash_from_transaction(self, txn: Transaction) -> Tuple[str, str]:
        """
        Compute content hash from an existing Beancount transaction.

        Args:
            txn: Beancount Transaction object

        Returns:
            Tuple of (source_account, content_hash)

        Note:
            This helper method is here (instead of in content_hash.py) to avoid
            importing beancount in the utility file. BeancountManager is the ONLY
            module that should import beancount.
        """
        # Get source account from metadata or infer from postings
        source_account = None
        if txn.meta and 'source_account' in txn.meta:
            source_account = txn.meta['source_account']
        else:
            # Infer from first Assets/Liabilities posting
            for posting in txn.postings:
                if posting.account.startswith(('Assets:', 'Liabilities:')):
                    source_account = posting.account
                    break

        if not source_account:
            # Fallback: use first posting account
            source_account = txn.postings[0].account if txn.postings else ""

        # Get amount from source account posting
        amount_str = "0"
        for posting in txn.postings:
            if posting.account == source_account and posting.units:
                amount_str = f"{posting.units.number} {posting.units.currency}"
                break

        # Compute content hash
        content_hash = compute_content_hash(
            date=txn.date.isoformat(),
            payee=txn.payee or "",
            amount=amount_str,
            source_account=source_account,
            narration=txn.narration or ""
        )

        return source_account, content_hash
