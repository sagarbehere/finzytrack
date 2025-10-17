"""
Centralized ledger cache with derived data structures.

This module provides a single-parse caching mechanism that builds all derived
data structures in one pass through the ledger, eliminating redundant file I/O
and parsing operations.
"""

import os
import logging
from typing import List, Dict, Set, Tuple, Optional, Any
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from beancount import loader
from beancount.core import data

from app.schemas.account_schemas import AccountDetails, AccountCurrencyData
from app.schemas.commodity_schemas import CommodityDetails, CommodityUsageData

logger = logging.getLogger(__name__)


@dataclass
class LedgerTransaction:
    """
    Simplified transaction representation for duplicate detection.

    This is the same structure used in duplicate_detector.py but defined here
    to avoid circular imports.
    """
    id: str  # UUIDv7 - stable identifier
    content_hash: str  # SHA256 - content fingerprint
    date: date
    payee: str
    narration: str
    amount: Decimal
    account: str
    ofx_id: Optional[str] = None


@dataclass
class LedgerData:
    """
    Complete cached ledger data with all derived structures.

    Built in a single pass through the ledger to eliminate redundant parsing.
    """
    # Raw Beancount data
    entries: List
    errors: List
    options: Dict

    # Account data
    accounts: Dict[str, AccountDetails]
    account_names: Set[str]

    # Commodity data
    commodities: Dict[str, CommodityDetails]
    commodity_codes: Set[str]

    # Transaction data (for duplicate detection)
    transactions: List[LedgerTransaction]
    transaction_ids: Set[str]

    # Training data (for ML categorization)
    training_data: List[Tuple[str, str]]  # (description, category) pairs


class LedgerCache:
    """
    Centralized ledger cache that parses once and provides multiple views.

    This cache eliminates redundant file I/O and parsing by:
    1. Parsing the ledger file only once
    2. Building all derived data structures in a single pass
    3. Invalidating the cache based on file modification time

    Usage:
        cache = LedgerCache(ledger_file)
        accounts = cache.get_accounts()
        transactions = cache.get_transactions()
        # ... all data comes from single parse
    """

    def __init__(self, ledger_file: str):
        """
        Initialize cache with ledger file path.

        Args:
            ledger_file: Path to Beancount ledger file
        """
        self.ledger_file = ledger_file
        self._cache: Optional[LedgerData] = None
        self._last_modified: Optional[float] = None

    def _needs_refresh(self) -> bool:
        """
        Check if cache needs to be refreshed based on file modification time.

        Returns:
            True if cache is stale or doesn't exist, False otherwise
        """
        if self._cache is None:
            return True

        if not os.path.exists(self.ledger_file):
            return False

        try:
            current_mtime = os.path.getmtime(self.ledger_file)
            return self._last_modified != current_mtime
        except OSError:
            return True

    def _parse_and_cache(self) -> None:
        """
        Parse ledger once and build all derived data structures in a single pass.

        This is the core optimization: instead of parsing the ledger multiple times
        for different purposes, we parse once and extract everything we need.
        """
        logger.info(f"Parsing and caching ledger: {self.ledger_file}")

        # Parse ledger
        entries, errors, options = loader.load_file(self.ledger_file)

        if errors:
            logger.warning(f"Beancount parsing warnings: {len(errors)} issues found")

        # Initialize all data structures
        accounts: Dict[str, AccountDetails] = {}
        commodities: Dict[str, CommodityDetails] = {}
        transactions: List[LedgerTransaction] = []
        transaction_ids: Set[str] = set()
        training_data: List[Tuple[str, str]] = []

        # Temporary structures for building complex derived data
        commodity_usage: Dict[str, Dict[str, Any]] = {}

        # Single pass through all entries
        for entry in entries:
            # Process Open directives for accounts
            if isinstance(entry, data.Open):
                account_name = entry.account
                metadata = entry.meta if hasattr(entry, 'meta') else {}

                accounts[account_name] = AccountDetails(
                    name=account_name,
                    open_date=entry.date.isoformat(),
                    close_date=None,
                    currencies=[],  # Will be populated in second pass
                    metadata=metadata
                )

            # Process Close directives for accounts
            elif isinstance(entry, data.Close):
                account_name = entry.account
                if account_name in accounts:
                    accounts[account_name].close_date = entry.date.isoformat()
                else:
                    # Account closed without open directive
                    accounts[account_name] = AccountDetails(
                        name=account_name,
                        open_date="1970-01-01",
                        close_date=entry.date.isoformat(),
                        currencies=[],
                        metadata={"error": "Account has close directive but no open directive"}
                    )

            # Process Commodity directives
            elif isinstance(entry, data.Commodity):
                commodity_code = entry.currency
                metadata = entry.meta if hasattr(entry, 'meta') else {}

                commodities[commodity_code] = CommodityDetails(
                    code=commodity_code,
                    name=metadata.get('name'),
                    type=metadata.get('type'),
                    first_seen=entry.date.isoformat(),
                    last_seen=entry.date.isoformat(),
                    usage=CommodityUsageData(transaction_count=0, total_volume=0.0),
                    metadata=metadata
                )

            # Process Transaction entries (most complex)
            elif isinstance(entry, data.Transaction):
                # Extract transaction IDs (new system: id and content_hash)
                if hasattr(entry, 'meta') and entry.meta:
                    # New system uses 'id'
                    txn_id = entry.meta.get('id')
                    # Fallback to old 'transaction_id' for backward compatibility
                    if not txn_id:
                        txn_id = entry.meta.get('transaction_id')
                    if txn_id:
                        transaction_ids.add(txn_id)

                # Extract training data for ML (if transaction has payee)
                if entry.payee:
                    ofx_memo = ""
                    if hasattr(entry, 'meta') and entry.meta:
                        ofx_memo = entry.meta.get('ofx_memo', '')
                    
                    description_parts = [entry.payee]
                    if ofx_memo:
                        description_parts.append(ofx_memo)
                    if entry.narration:
                        description_parts.append(entry.narration)
                    
                    description = " ".join(description_parts).strip()

                    # Find first Expenses or Income account for training
                    for posting in entry.postings:
                        if posting.account.startswith(('Expenses:', 'Income:')):
                            training_data.append((description, posting.account))
                            break

                # Build transaction for duplicate detection
                # Find source account (from metadata or posting)
                source_account = None
                transaction_amount = None
                ofx_id = None

                if hasattr(entry, 'meta') and entry.meta:
                    source_account = entry.meta.get('source_account')
                    ofx_id = entry.meta.get('ofx_id')

                # Find transaction amount from the posting that matches the source account
                for posting in entry.postings:
                    if posting.units:
                        # If source_account is not set, look for Assets/Liabilities
                        if not source_account and posting.account.startswith(('Assets:', 'Liabilities:')):
                            source_account = posting.account
                            transaction_amount = posting.units.number
                            break
                        # If source_account is already set (from metadata), find matching posting
                        elif source_account and posting.account == source_account:
                            transaction_amount = posting.units.number
                            break

                if source_account and transaction_amount is not None:
                    # Get IDs from metadata
                    txn_id = None
                    content_hash = None
                    if hasattr(entry, 'meta') and entry.meta:
                        # New system
                        txn_id = entry.meta.get('id')
                        content_hash = entry.meta.get('content_hash')
                        # Fallback to old system
                        if not txn_id:
                            txn_id = entry.meta.get('transaction_id', '')
                        if not content_hash:
                            content_hash = entry.meta.get('transaction_id', '')

                    transactions.append(LedgerTransaction(
                        id=txn_id or '',
                        content_hash=content_hash or '',
                        date=entry.date,
                        payee=entry.payee or '',
                        narration=entry.narration or '',
                        amount=transaction_amount,
                        account=source_account,
                        ofx_id=ofx_id
                    ))

                # Track commodity usage
                for posting in entry.postings:
                    if posting.units:
                        commodity_code = posting.units.currency

                        if commodity_code not in commodity_usage:
                            commodity_usage[commodity_code] = {
                                "transaction_count": 0,
                                "total_volume": Decimal(0),
                                "first_seen": entry.date,
                                "last_seen": entry.date
                            }

                        commodity_usage[commodity_code]["transaction_count"] += 1
                        if posting.units.number is not None:
                            commodity_usage[commodity_code]["total_volume"] += abs(posting.units.number)

                        if entry.date < commodity_usage[commodity_code]["first_seen"]:
                            commodity_usage[commodity_code]["first_seen"] = entry.date
                        if entry.date > commodity_usage[commodity_code]["last_seen"]:
                            commodity_usage[commodity_code]["last_seen"] = entry.date

                        # Create commodity entry if not from directive
                        if commodity_code not in commodities:
                            commodities[commodity_code] = CommodityDetails(
                                code=commodity_code,
                                name=None,
                                type=None,
                                first_seen=entry.date.isoformat(),
                                last_seen=entry.date.isoformat(),
                                usage=CommodityUsageData(transaction_count=0, total_volume=0.0),
                                metadata={}
                            )

            # Process Price directives
            elif isinstance(entry, data.Price):
                commodity_code = entry.currency

                if commodity_code not in commodities:
                    commodities[commodity_code] = CommodityDetails(
                        code=commodity_code,
                        name=None,
                        type=None,
                        first_seen=entry.date.isoformat(),
                        last_seen=entry.date.isoformat(),
                        usage=CommodityUsageData(transaction_count=0, total_volume=0.0),
                        metadata={}
                    )

        # Second pass: build account currency data
        # (This requires the full entries list, so done separately)
        for account_name in accounts:
            currency_data = self._build_account_currency_data(account_name, entries)
            accounts[account_name].currencies = currency_data

        # Merge commodity usage data
        for commodity_code, commodity_detail in commodities.items():
            if commodity_code in commodity_usage:
                usage_data = commodity_usage[commodity_code]
                commodity_detail.usage.transaction_count = usage_data["transaction_count"]
                commodity_detail.usage.total_volume = float(usage_data["total_volume"])

                # Update date range
                usage_first = usage_data["first_seen"]
                usage_last = usage_data["last_seen"]

                from datetime import datetime
                current_first = datetime.fromisoformat(commodity_detail.first_seen).date() if commodity_detail.first_seen else None
                current_last = datetime.fromisoformat(commodity_detail.last_seen).date() if commodity_detail.last_seen else None

                if current_first is None or usage_first < current_first:
                    commodity_detail.first_seen = usage_first.isoformat()
                if current_last is None or usage_last > current_last:
                    commodity_detail.last_seen = usage_last.isoformat()

        # Store in cache
        self._cache = LedgerData(
            entries=entries,
            errors=errors,
            options=options,
            accounts=accounts,
            account_names=set(accounts.keys()),
            commodities=commodities,
            commodity_codes=set(commodities.keys()),
            transactions=transactions,
            transaction_ids=transaction_ids,
            training_data=training_data
        )

        # Update modification time
        try:
            self._last_modified = os.path.getmtime(self.ledger_file)
        except OSError:
            self._last_modified = None

        logger.info(f"Cache built: {len(accounts)} accounts, {len(commodities)} commodities, "
                   f"{len(transactions)} transactions, {len(training_data)} training samples")

    def _build_account_currency_data(self, account_name: str, entries) -> List[AccountCurrencyData]:
        """
        Extract currency data for an account from open directives and transactions.

        This is kept as a separate method because it requires iterating entries
        for a specific account.
        """
        currency_info: Dict[str, Dict[str, Any]] = {}

        # Find open directive currencies
        for entry in entries:
            if isinstance(entry, data.Open) and entry.account == account_name:
                if hasattr(entry, 'currencies') and entry.currencies:
                    for currency in entry.currencies:
                        currency_info[currency] = {
                            "transaction_count": 0,
                            "last_transaction_date": None,
                            "balance": Decimal(0)
                        }

        # Process transactions
        for entry in entries:
            if isinstance(entry, data.Transaction):
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
                            entry.date > currency_info[currency]["last_transaction_date"]):
                            currency_info[currency]["last_transaction_date"] = entry.date

                        currency_info[currency]["balance"] += posting.units.number

        # Convert to AccountCurrencyData objects
        result = []
        for currency, info in currency_info.items():
            result.append(AccountCurrencyData(
                currency=currency,
                transaction_count=info["transaction_count"],
                last_transaction_date=info["last_transaction_date"].isoformat() if info["last_transaction_date"] else None,
                balance=float(info["balance"])
            ))

        return result

    def get_cached_data(self) -> LedgerData:
        """
        Get complete cached data, refreshing if necessary.

        Returns:
            LedgerData with all derived structures

        Raises:
            RuntimeError: If cache is None after parsing (should never happen)
        """
        if self._needs_refresh():
            self._parse_and_cache()

        if self._cache is None:
            raise RuntimeError("Cache is None after parsing - this should never happen")

        return self._cache

    def invalidate(self) -> None:
        """
        Manually invalidate the cache.

        Should be called after write operations to the ledger.
        """
        logger.info("Cache invalidated")
        self._cache = None
        self._last_modified = None

    # Convenience methods for accessing specific data structures

    def get_entries(self) -> List:
        """Get raw Beancount entries."""
        return self.get_cached_data().entries

    def get_errors(self) -> List:
        """Get Beancount parsing errors."""
        return self.get_cached_data().errors

    def get_accounts(self) -> Dict[str, AccountDetails]:
        """Get all accounts indexed by name."""
        return self.get_cached_data().accounts

    def get_account_names(self) -> Set[str]:
        """Get set of all account names (O(1) membership testing)."""
        return self.get_cached_data().account_names

    def get_commodities(self) -> Dict[str, CommodityDetails]:
        """Get all commodities indexed by code."""
        return self.get_cached_data().commodities

    def get_commodity_codes(self) -> Set[str]:
        """Get set of all commodity codes (O(1) membership testing)."""
        return self.get_cached_data().commodity_codes

    def get_transactions(self) -> List[LedgerTransaction]:
        """Get all transactions for duplicate detection."""
        return self.get_cached_data().transactions

    def get_transaction_ids(self) -> Set[str]:
        """Get set of all transaction IDs."""
        return self.get_cached_data().transaction_ids

    def get_training_data(self) -> List[Tuple[str, str]]:
        """Get training data for ML categorization."""
        return self.get_cached_data().training_data
