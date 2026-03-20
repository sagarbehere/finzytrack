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
    AccountDetails, AccountCurrencyData, AccountCreateRequest, AccountCreateData,
    BalanceDirectiveData, BalanceDirectiveCreateRequest, BalanceDirectiveUpdateRequest
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

    def get_detailed_accounts_filtered(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> List[AccountDetails]:
        """
        Get account information with date-filtered balances.

        For Balance Sheet accounts (Assets, Liabilities, Equity):
            - Balance = sum of all transactions up to end_date
            - start_date is ignored (balance is a point-in-time snapshot)

        For Income Statement accounts (Income, Expenses):
            - Balance = sum of transactions within start_date to end_date

        Args:
            start_date: Start of period (inclusive). None means beginning of time.
            end_date: End of period (inclusive). None means today.

        Returns:
            List of AccountDetails with filtered currency data
        """
        if not os.path.exists(self.ledger_file):
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")

        # Default end_date to today if not specified
        if end_date is None:
            end_date = date.today()

        # Get cached accounts (for structure) and entries (for recomputing balances)
        cached_accounts = self.cache.get_accounts()
        entries = self.cache.get_entries()

        errors = self.cache.get_errors()
        if errors:
            logger.warning(f"Beancount parsing warnings: {len(errors)} issues found")

        # Balance sheet account types (balance = cumulative up to end_date)
        balance_sheet_prefixes = ('Assets:', 'Liabilities:', 'Equity:')
        # Also handle root accounts without colon
        balance_sheet_roots = ('Assets', 'Liabilities', 'Equity')

        result = []

        for account_name, account_detail in cached_accounts.items():
            # Determine if this is a balance sheet or income statement account
            is_balance_sheet = (
                account_name.startswith(balance_sheet_prefixes) or
                account_name in balance_sheet_roots
            )

            # Compute filtered currency data
            currency_info: Dict[str, Dict[str, Any]] = {}

            for entry in entries:
                if isinstance(entry, data.Transaction):
                    txn_date = entry.date

                    # Apply date filtering based on account type
                    if is_balance_sheet:
                        # Balance sheet: include if transaction is on or before end_date
                        if txn_date > end_date:
                            continue
                    else:
                        # Income statement: include if transaction is within the period
                        if start_date and txn_date < start_date:
                            continue
                        if txn_date > end_date:
                            continue

                    # Process postings for this account
                    for posting in entry.postings:
                        if posting.account == account_name and posting.units:
                            currency = posting.units.currency

                            if currency not in currency_info:
                                currency_info[currency] = {
                                    "transaction_count": 0,
                                    "last_transaction_date": None,
                                    "balance": Decimal(0)
                                }

                            currency_info[currency]["transaction_count"] += 1

                            if (currency_info[currency]["last_transaction_date"] is None or
                                txn_date > currency_info[currency]["last_transaction_date"]):
                                currency_info[currency]["last_transaction_date"] = txn_date

                            currency_info[currency]["balance"] += posting.units.number

            # Convert to AccountCurrencyData objects
            currencies = []
            for currency, info in currency_info.items():
                currencies.append(AccountCurrencyData(
                    currency=currency,
                    transaction_count=info["transaction_count"],
                    last_transaction_date=info["last_transaction_date"].isoformat() if info["last_transaction_date"] else None,
                    balance=float(info["balance"])
                ))

            # Create new AccountDetails with filtered currency data
            result.append(AccountDetails(
                name=account_detail.name,
                open_date=account_detail.open_date,
                close_date=account_detail.close_date,
                currencies=currencies,
                metadata=account_detail.metadata
            ))

        return result
    
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

    # =====================================
    # Shared ledger-write helper
    # =====================================

    def _write_entries(self, entries) -> None:
        """
        Write all entries to the ledger using the Beancount printer.

        This is the single authorised path for mutating the ledger file.
        All account and transaction write operations must go through here
        so that serialisation is always delegated to the Beancount library.

        Auto-generated padding transactions (flag 'P' with no stable ID) are
        silently dropped because Beancount recreates them at parse time from
        the surrounding pad+balance directives.
        """
        from beancount.parser import printer
        with self.atomic_ledger_write() as f:
            f.seek(0)
            f.truncate()
            for entry in entries:
                if isinstance(entry, Transaction) and entry.flag == 'P':
                    has_id = entry.meta and ('id' in entry.meta or 'transaction_id' in entry.meta)
                    if not has_id:
                        continue
                f.write(printer.format_entry(entry))
                f.write('\n\n')

    # =====================================
    # Account Management Methods
    # =====================================

    def _rename_account_in_entry(self, entry, old_name: str, new_name: str):
        """Return a copy of entry with all references to old_name replaced by new_name."""
        from beancount.core import data as bd

        def r(name: str) -> str:
            if name == old_name:
                return new_name
            if name.startswith(old_name + ':'):
                return new_name + name[len(old_name):]
            return name

        if isinstance(entry, (bd.Open, bd.Close, bd.Balance, bd.Note, bd.Document)):
            return entry._replace(account=r(entry.account))
        if isinstance(entry, bd.Pad):
            return entry._replace(account=r(entry.account), source_account=r(entry.source_account))
        if isinstance(entry, bd.Transaction):
            new_postings = [p._replace(account=r(p.account)) for p in entry.postings]
            return entry._replace(postings=new_postings)
        return entry

    def create_account_directive(self, request: AccountCreateRequest) -> AccountCreateData:
        """
        Create a new account by appending an Open directive to the ledger.

        Raises:
            ValueError: Invalid account format, account already exists, invalid date format
            FileNotFoundError: If ledger file doesn't exist
            PermissionError: If ledger file cannot be accessed
        """
        if not self.validate_account_format(request.name):
            raise ValueError(f"Invalid account format: {request.name}")
        if self.is_existing_account(request.name):
            raise ValueError(f"Account already exists: {request.name}")

        try:
            open_date_obj = datetime.strptime(request.open_date, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"Invalid open_date format: {request.open_date}")

        from beancount.core import data as bd
        meta: Dict[str, Any] = {'filename': str(self.ledger_file), 'lineno': 0}
        if request.description:
            meta['description'] = request.description
        if request.metadata:
            meta.update(request.metadata)

        open_entry = bd.Open(meta, open_date_obj, request.name, request.currencies or [], None)
        self._write_entries(list(self.cache.get_entries()) + [open_entry])

        # Return the newly created account details from the refreshed cache
        for account in self.get_detailed_accounts():
            if account.name == request.name:
                return AccountCreateData(
                    account_created=True,
                    account_details=account,
                    message=f"Account '{request.name}' created successfully",
                )
        raise ValueError(f"Account creation succeeded but account not found: {request.name}")

    def update_account_directive(
        self,
        account_name: str,
        *,
        new_name: Optional[str] = None,
        open_date: Optional[str] = None,
        currencies: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        close_date: Optional[str] = None,  # YYYY-MM-DD = set/update, "" = reopen, None = no change
    ) -> None:
        """
        Update an existing account's Open (and optionally Close) directive.

        Performs a full parse→modify→print cycle so the Beancount printer owns
        serialisation. When the account is renamed every other entry that
        references the old name (transactions, balance assertions, etc.) is
        updated in the same write.
        """
        from beancount.core import data as bd
        import datetime as _dt

        final_name = new_name or account_name
        entries = self.cache.get_entries()
        new_entries = []
        found_open = False
        found_close = False

        for entry in entries:
            if isinstance(entry, bd.Open) and entry.account == account_name:
                found_open = True
                final_date = _dt.datetime.strptime(open_date, "%Y-%m-%d").date() if open_date else entry.date
                final_currencies = currencies or list(entry.currencies or [])
                # Carry forward existing metadata and merge in new values
                carried = {k: v for k, v in (entry.meta or {}).items() if k not in ('filename', 'lineno')}
                if metadata:
                    carried.update(metadata)
                new_meta = {'filename': entry.meta.get('filename', str(self.ledger_file)), 'lineno': entry.meta.get('lineno', 0)}
                new_meta.update(carried)
                new_entries.append(bd.Open(new_meta, final_date, final_name, final_currencies, entry.booking))

            elif isinstance(entry, bd.Close) and entry.account == account_name:
                found_close = True
                if close_date is None:
                    # No change requested — keep directive, but rename if necessary
                    new_entries.append(entry._replace(account=final_name))
                elif close_date == '':
                    pass  # Reopen: drop the Close directive entirely
                else:
                    final_close = _dt.datetime.strptime(close_date, "%Y-%m-%d").date()
                    new_entries.append(bd.Close(entry.meta, final_close, final_name))

            elif final_name != account_name:
                new_entries.append(self._rename_account_in_entry(entry, account_name, final_name))

            else:
                new_entries.append(entry)

        if not found_open:
            raise ValueError(f"Account open directive not found: {account_name}")

        # If caller wants a close date and there was no existing Close, append one
        if close_date and close_date != '' and not found_close:
            import datetime as _dt2
            close_meta = {'filename': str(self.ledger_file), 'lineno': 0}
            final_close = _dt2.datetime.strptime(close_date, "%Y-%m-%d").date()
            new_entries.append(bd.Close(close_meta, final_close, final_name))

        self._write_entries(new_entries)

    def close_account_directive(self, account_name: str, close_date, reason: Optional[str] = None) -> None:
        """Append a Close directive for the given account."""
        from beancount.core import data as bd
        close_meta: Dict[str, Any] = {'filename': str(self.ledger_file), 'lineno': 0}
        if reason:
            close_meta['reason'] = reason
        close_entry = bd.Close(close_meta, close_date, account_name)
        self._write_entries(list(self.cache.get_entries()) + [close_entry])

    def reopen_account_directive(self, account_name: str) -> None:
        """Remove the Close directive for the given account (reopen it)."""
        from beancount.core import data as bd
        entries = self.cache.get_entries()
        new_entries = [e for e in entries if not (isinstance(e, bd.Close) and e.account == account_name)]
        self._write_entries(new_entries)

    def delete_account_directive(self, account_name: str) -> None:
        """Remove the Open and Close directives for the given account."""
        from beancount.core import data as bd
        entries = self.cache.get_entries()
        new_entries = [
            e for e in entries
            if not (
                (isinstance(e, bd.Open) and e.account == account_name) or
                (isinstance(e, bd.Close) and e.account == account_name)
            )
        ]
        self._write_entries(new_entries)

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
        external_id: Optional[str] = None,
        external_id_type: Optional[str] = None,
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
            external_id: Optional external transaction ID (OFX FITID, UPI ref, etc.)
            external_id_type: Optional type of external_id (OFX, UPI, NEFT, etc.)
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

        # Add external ID if provided
        if external_id and str(external_id).strip():
            meta['external_id'] = str(external_id).strip()
            if external_id_type:
                meta['external_id_type'] = str(external_id_type).strip()

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

    # =====================================
    # Transaction Update Methods
    # =====================================

    def update_transactions_by_id(
        self,
        transactions: List[Tuple[str, Transaction]]
    ) -> int:
        """
        Update transactions in the ledger by ID.

        Args:
            transactions: List of (transaction_id, updated_transaction) tuples

        Returns:
            Number of transactions updated

        Raises:
            APIError: If any transaction ID is not found or update fails
        """
        from app.exceptions import APIError
        from beancount.parser import printer

        # Build lookup map
        update_map = {txn_id: txn for txn_id, txn in transactions}
        transaction_ids = set(update_map.keys())

        # Read current entries
        entries = self.cache.get_entries()

        # Find and replace transactions
        updated_entries = []
        found_ids = set()

        for entry in entries:
            if isinstance(entry, Transaction):
                # Check if this transaction should be updated
                txn_id = entry.meta.get('id') if entry.meta else None

                if txn_id and txn_id in transaction_ids:
                    # Replace with updated transaction
                    updated_txn = update_map[txn_id]

                    # Preserve original line number for better error reporting
                    if entry.meta and 'lineno' in entry.meta:
                        if not updated_txn.meta:
                            updated_txn = updated_txn._replace(meta={'lineno': entry.meta['lineno']})
                        else:
                            updated_txn.meta['lineno'] = entry.meta['lineno']

                    updated_entries.append(updated_txn)
                    found_ids.add(txn_id)
                else:
                    # Keep original entry
                    updated_entries.append(entry)
            else:
                # Keep non-transaction entries
                updated_entries.append(entry)

        # Check if all transactions were found
        not_found = transaction_ids - found_ids
        if not_found:
            raise APIError(
                message=f"Transaction IDs not found: {not_found}",
                code="TRANSACTIONS_NOT_FOUND",
                status_code=404,
                details={"not_found_ids": list(not_found)}
            )

        # Write updated entries atomically
        # IMPORTANT: Skip auto-generated padding transactions (flag 'P')
        # These are created by Beancount when it sees pad + balance directives
        # and should not be written back to the file
        with self.atomic_ledger_write() as f:
            # Truncate the file to ensure old content is removed
            f.seek(0)
            f.truncate()

            for entry in updated_entries:
                # Skip auto-generated padding transactions
                if isinstance(entry, Transaction) and entry.flag == 'P':
                    # Check if this is an auto-generated padding (no transaction ID)
                    has_id = entry.meta and ('id' in entry.meta or 'transaction_id' in entry.meta)
                    if not has_id:
                        # This is an auto-generated padding transaction, skip it
                        logger.debug(f"Skipping auto-generated padding transaction: {entry.narration}")
                        continue

                entry_str = printer.format_entry(entry)
                f.write(entry_str)
                f.write('\n\n')

        logger.info(f"Updated {len(found_ids)} transactions in ledger")

        return len(found_ids)

    def delete_transactions_by_id(self, transaction_ids: List[str]) -> int:
        """
        Delete transactions from the ledger by ID.

        Args:
            transaction_ids: List of transaction IDs (UUIDv7) to delete

        Returns:
            Number of transactions deleted

        Raises:
            APIError: If any transaction ID is not found or delete fails
        """
        from app.exceptions import APIError
        from beancount.parser import printer

        transaction_ids_set = set(transaction_ids)

        # Read current entries
        entries = self.cache.get_entries()

        # Filter out transactions to be deleted
        remaining_entries = []
        found_ids = set()

        for entry in entries:
            if isinstance(entry, Transaction):
                # Check if this transaction should be deleted
                txn_id = entry.meta.get('id') if entry.meta else None

                if txn_id and txn_id in transaction_ids_set:
                    # Mark as found but don't add to remaining_entries (delete it)
                    found_ids.add(txn_id)
                    logger.debug(f"Marking transaction {txn_id} for deletion")
                else:
                    # Keep this transaction
                    remaining_entries.append(entry)
            else:
                # Keep all non-transaction entries
                remaining_entries.append(entry)

        # Check if all requested transactions were found
        not_found = transaction_ids_set - found_ids
        if not_found:
            raise APIError(
                message=f"Transaction IDs not found: {list(not_found)}",
                code="TRANSACTIONS_NOT_FOUND",
                status_code=404,
                details={"not_found_ids": list(not_found)}
            )

        # Write remaining entries atomically
        # IMPORTANT: Skip auto-generated padding transactions (flag 'P')
        with self.atomic_ledger_write() as f:
            # Truncate the file to ensure old content is removed
            f.seek(0)
            f.truncate()

            for entry in remaining_entries:
                # Skip auto-generated padding transactions
                if isinstance(entry, Transaction) and entry.flag == 'P':
                    # Check if this is an auto-generated padding (no transaction ID)
                    has_id = entry.meta and ('id' in entry.meta or 'transaction_id' in entry.meta)
                    if not has_id:
                        # This is an auto-generated padding transaction, skip it
                        logger.debug(f"Skipping auto-generated padding transaction: {entry.narration}")
                        continue

                entry_str = printer.format_entry(entry)
                f.write(entry_str)
                f.write('\n\n')

        logger.info(f"Deleted {len(found_ids)} transaction(s) from ledger")

        return len(found_ids)

    def delete_transactions_for_account(self, account_name: str) -> int:
        """
        Delete all transactions that have postings to a specific account.

        Args:
            account_name: The account name to delete transactions for

        Returns:
            Number of transactions deleted
        """
        from beancount.parser import printer

        # Read current entries
        entries = self.cache.get_entries()

        # Filter out transactions that have postings to this account
        remaining_entries = []
        deleted_count = 0

        for entry in entries:
            if isinstance(entry, Transaction):
                # Check if any posting references this account
                has_account_posting = any(
                    posting.account == account_name
                    for posting in entry.postings
                )

                if has_account_posting:
                    # Delete this transaction
                    deleted_count += 1
                    logger.debug(f"Deleting transaction with posting to {account_name}: {entry.narration}")
                else:
                    # Keep this transaction
                    remaining_entries.append(entry)
            else:
                # Keep all non-transaction entries
                remaining_entries.append(entry)

        if deleted_count == 0:
            return 0

        # Write remaining entries atomically
        # IMPORTANT: Skip auto-generated padding transactions (flag 'P')
        with self.atomic_ledger_write() as f:
            # Truncate the file to ensure old content is removed
            f.seek(0)
            f.truncate()

            for entry in remaining_entries:
                # Skip auto-generated padding transactions
                if isinstance(entry, Transaction) and entry.flag == 'P':
                    # Check if this is an auto-generated padding (no transaction ID)
                    has_id = entry.meta and ('id' in entry.meta or 'transaction_id' in entry.meta)
                    if not has_id:
                        # This is an auto-generated padding transaction, skip it
                        logger.debug(f"Skipping auto-generated padding transaction: {entry.narration}")
                        continue

                entry_str = printer.format_entry(entry)
                f.write(entry_str)
                f.write('\n\n')

        logger.info(f"Deleted {deleted_count} transaction(s) for account {account_name}")

        return deleted_count

    # =====================================
    # Balance & Pad Directive Management
    # =====================================

    def get_balance_directives(self, account_name: str) -> List[BalanceDirectiveData]:
        """
        Get all balance directives for an account, with associated pad info and error status.

        Args:
            account_name: The Beancount account name

        Returns:
            List of BalanceDirectiveData sorted by date ascending
        """
        entries = self.cache.get_entries()
        errors = self.cache.get_errors()

        # Build a set of error source lines for quick lookup
        # Beancount balance errors reference the balance entry's source line
        error_by_line: Dict[int, str] = {}
        for err in errors:
            if err.source:
                lineno = err.source.get('lineno', 0)
                if lineno and 'balance' in err.message.lower():
                    error_by_line[lineno] = err.message

        # Single-pass: track the most recent pad for this account, consume it
        # when the next balance for the same account is encountered.
        # This mirrors how beancount pairs pad→balance (a pad applies to the
        # next balance directive for that account, regardless of date).
        pending_pad_source: Optional[str] = None
        pending_pad_date: Optional[date] = None
        result: List[BalanceDirectiveData] = []

        for entry in entries:
            if isinstance(entry, data.Pad) and entry.account == account_name:
                # Remember the most recent pad; if another pad appears before
                # a balance, it overwrites (beancount would flag that anyway).
                pending_pad_source = entry.source_account
                pending_pad_date = entry.date

            elif isinstance(entry, data.Balance) and entry.account == account_name:
                entry_lineno = entry.meta.get('lineno', 0) if entry.meta else 0

                # A pending pad is associated if it's for the same account and
                # its date is on or before the balance date (typically 1 day before).
                pad_source: Optional[str] = None
                if pending_pad_source is not None and pending_pad_date is not None:
                    if pending_pad_date <= entry.date:
                        pad_source = pending_pad_source
                        # Consume the pad so it isn't reused for the next balance
                        pending_pad_source = None
                        pending_pad_date = None

                # Check for balance errors
                has_error = entry_lineno in error_by_line
                error_message = error_by_line.get(entry_lineno)

                result.append(BalanceDirectiveData(
                    date=entry.date.isoformat(),
                    currency=entry.amount.currency,
                    expected_balance=float(entry.amount.number),
                    has_pad=pad_source is not None,
                    pad_source_account=pad_source,
                    has_error=has_error,
                    error_message=error_message,
                ))

        # Sort by date ascending
        result.sort(key=lambda d: d.date)
        return result

    def add_balance_directive(self, account_name: str, request: BalanceDirectiveCreateRequest) -> None:
        """
        Add a balance assertion (and optionally a pad directive) to the ledger.

        Args:
            account_name: The Beancount account name
            request: Create request with date, currency, amount, and optional pad info

        Raises:
            ValueError: If account doesn't exist or pad_source_account is invalid
        """
        if not self.is_existing_account(account_name):
            raise ValueError(f"Account not found: {account_name}")

        if request.include_pad:
            if not request.pad_source_account:
                raise ValueError("pad_source_account is required when include_pad is True")
            if not self.is_existing_account(request.pad_source_account):
                raise ValueError(f"Pad source account not found: {request.pad_source_account}")

        # Build directive text
        # Pad must be dated before the balance (balance checks at start-of-day),
        # so we date the pad one day prior.
        directive_lines = []
        if request.include_pad and request.pad_source_account:
            pad_date = self._day_before(request.date)
            directive_lines.append(f"{pad_date} pad {account_name} {request.pad_source_account}")
        directive_lines.append(f"{request.date} balance {account_name} {request.amount} {request.currency}")

        directive_text = "\n".join(directive_lines)

        # Append to ledger
        with self.atomic_ledger_write() as f:
            current_content = f.read()

            if current_content and not current_content.endswith('\n'):
                current_content += '\n'
            if current_content and not current_content.endswith('\n\n'):
                current_content += '\n'

            new_content = current_content + directive_text + '\n'

            f.seek(0)
            f.write(new_content)
            f.truncate()

    def update_balance_directive(self, account_name: str, request: BalanceDirectiveUpdateRequest) -> None:
        """
        Update an existing balance directive (and optionally its associated pad) in the ledger.

        Args:
            account_name: The Beancount account name
            request: Update request with original identifiers and new values

        Raises:
            ValueError: If the directive is not found or validation fails
        """
        if not self.is_existing_account(account_name):
            raise ValueError(f"Account not found: {account_name}")

        if request.pad_source_account and not self.is_existing_account(request.pad_source_account):
            raise ValueError(f"Pad source account not found: {request.pad_source_account}")

        with self.atomic_ledger_write() as f:
            current_content = f.read()
            lines = current_content.split('\n')

            # Build match patterns for the original balance line
            # Pattern: "{date} balance {account} {amount} {currency}"
            balance_line_idx = -1
            pad_line_idx = -1

            original_amount_str = self._format_amount(request.original_amount)

            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith(';'):
                    continue

                # Match balance directive
                if balance_line_idx == -1 and ' balance ' in stripped:
                    parts = stripped.split()
                    if (len(parts) >= 4 and
                        parts[0] == request.original_date and
                        parts[1] == 'balance' and
                        parts[2] == account_name and
                        self._amounts_match(parts[3], original_amount_str) and
                        (len(parts) < 5 or parts[4] == request.original_currency)):
                        balance_line_idx = i

            if balance_line_idx == -1:
                raise ValueError(
                    f"Balance directive not found: {request.original_date} balance {account_name} "
                    f"{request.original_amount} {request.original_currency}"
                )

            # Search backward from the balance line for the nearest pad for this account
            pad_line_idx = self._find_pad_before_balance(lines, balance_line_idx, account_name)

            # Compute new values
            new_date = request.new_date or request.original_date
            new_amount = request.new_amount if request.new_amount is not None else request.original_amount
            new_currency = request.new_currency or request.original_currency
            new_amount_str = self._format_amount(new_amount)

            # Update the balance line
            lines[balance_line_idx] = f"{new_date} balance {account_name} {new_amount_str} {new_currency}"

            # Handle pad directive changes
            # Pad must be dated one day before the balance date
            new_pad_date = self._day_before(new_date)
            include_pad = request.include_pad
            if include_pad is True:
                pad_source = request.pad_source_account
                if not pad_source:
                    raise ValueError("pad_source_account is required when include_pad is True")
                if pad_line_idx >= 0:
                    # Update existing pad
                    lines[pad_line_idx] = f"{new_pad_date} pad {account_name} {pad_source}"
                else:
                    # Insert new pad line before the balance line
                    lines.insert(balance_line_idx, f"{new_pad_date} pad {account_name} {pad_source}")
            elif include_pad is False and pad_line_idx >= 0:
                # Remove existing pad
                lines.pop(pad_line_idx)
            elif pad_line_idx >= 0:
                # include_pad is None (no change requested), but update the date if it changed
                if new_date != request.original_date:
                    old_pad = lines[pad_line_idx].strip().split()
                    pad_source = old_pad[3] if len(old_pad) >= 4 else ''
                    lines[pad_line_idx] = f"{new_pad_date} pad {account_name} {pad_source}"

            # Write back
            new_content = '\n'.join(lines)
            f.seek(0)
            f.write(new_content)
            f.truncate()

    def delete_balance_directive(
        self,
        account_name: str,
        directive_date: str,
        currency: str,
        amount: float,
        delete_pad: bool = True
    ) -> None:
        """
        Delete a balance directive (and optionally its associated pad) from the ledger.

        Args:
            account_name: The Beancount account name
            directive_date: Date string (YYYY-MM-DD)
            currency: Currency code
            amount: Expected balance amount
            delete_pad: Whether to also delete the associated pad directive

        Raises:
            ValueError: If the directive is not found
        """
        with self.atomic_ledger_write() as f:
            current_content = f.read()
            lines = current_content.split('\n')

            amount_str = self._format_amount(amount)

            balance_line_idx = -1

            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith(';'):
                    continue

                # Match balance directive
                if balance_line_idx == -1 and ' balance ' in stripped:
                    parts = stripped.split()
                    if (len(parts) >= 4 and
                        parts[0] == directive_date and
                        parts[1] == 'balance' and
                        parts[2] == account_name and
                        self._amounts_match(parts[3], amount_str) and
                        (len(parts) < 5 or parts[4] == currency)):
                        balance_line_idx = i

            if balance_line_idx == -1:
                raise ValueError(
                    f"Balance directive not found: {directive_date} balance {account_name} "
                    f"{amount} {currency}"
                )

            # Search backward for associated pad
            pad_line_idx = -1
            if delete_pad:
                pad_line_idx = self._find_pad_before_balance(lines, balance_line_idx, account_name)

            # Remove lines (remove higher index first to preserve indices)
            indices_to_remove = [balance_line_idx]
            if pad_line_idx >= 0:
                indices_to_remove.append(pad_line_idx)
            indices_to_remove.sort(reverse=True)

            for idx in indices_to_remove:
                lines.pop(idx)

            # Clean up double blank lines
            new_content = '\n'.join(lines)
            f.seek(0)
            f.write(new_content)
            f.truncate()

    @staticmethod
    def _find_pad_before_balance(lines: List[str], balance_line_idx: int, account_name: str) -> int:
        """Search backward from a balance line for the nearest pad directive for the same account.

        Skips blank lines and comments. Stops at the first non-blank, non-comment,
        non-pad directive line to avoid matching a pad that belongs to a different
        balance.

        Returns:
            Line index of the pad, or -1 if not found.
        """
        for i in range(balance_line_idx - 1, -1, -1):
            stripped = lines[i].strip()
            if not stripped or stripped.startswith(';'):
                continue  # skip blank lines and comments
            parts = stripped.split()
            if len(parts) >= 4 and parts[1] == 'pad' and parts[2] == account_name:
                return i
            # Hit a non-pad directive — stop searching
            break
        return -1

    @staticmethod
    def _day_before(date_str: str) -> str:
        """Return the date string one day before the given YYYY-MM-DD date."""
        from datetime import timedelta
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (d - timedelta(days=1)).isoformat()

    @staticmethod
    def _format_amount(amount: float) -> str:
        """Format a float amount to a string, removing unnecessary trailing zeros."""
        if amount == int(amount):
            return str(int(amount))
        # Format with enough precision, then strip trailing zeros
        return f"{amount:.10f}".rstrip('0').rstrip('.')

    @staticmethod
    def _amounts_match(file_amount: str, expected_amount: str) -> bool:
        """Compare two amount strings, tolerating formatting differences."""
        try:
            return abs(float(file_amount) - float(expected_amount)) < 0.001
        except ValueError:
            return file_amount == expected_amount

    def validate_transaction(self, transaction: Transaction) -> List[str]:
        """
        Validate a Beancount transaction.

        Returns:
            List of error messages (empty if valid)
        """
        from beancount.parser import parser
        from beancount.parser import printer
        from collections import defaultdict

        # Format transaction to string
        txn_str = printer.format_entry(transaction)

        # Try to parse it back
        entries, errors, _ = parser.parse_string(txn_str)

        # Check for parsing errors
        if errors:
            return [str(e) for e in errors]

        # Check if transaction balances
        balance = defaultdict(Decimal)
        for posting in transaction.postings:
            if posting.units and posting.units.number is not None:
                balance[posting.units.currency] += posting.units.number

        # Allow small rounding errors (< 0.01 in any currency)
        for currency, amount in balance.items():
            if abs(amount) >= Decimal('0.01'):
                return [f"Transaction does not balance: {currency} off by {amount}"]

        return []
