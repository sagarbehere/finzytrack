"""
LedgerManager — thin orchestrator that delegates pure ledger logic to a LedgerEngine.

Responsibilities:
- File I/O (atomic writes with backup)
- Cache management (LedgerCache)
- Cache invalidation callbacks (SQLite sync, etc.)
- Ledger file switching

The engine handles all Beancount-specific logic: parsing, formatting, entry
creation, account/transaction/commodity/balance CRUD.
"""

import os
import logging
from typing import Optional, List, Dict, Any, Callable, Tuple
from datetime import datetime, date
from contextlib import contextmanager
from beancount.core import data
from beancount.core.data import Transaction, Posting

from app.core.backup_manager import BackupManager
from app.core.beancount_engine import BeancountEngine
from app.core.constants import BALANCE_SHEET_PREFIXES, BALANCE_SHEET_TYPES, SOURCE_ACCOUNT_PREFIXES
from app.core.ledger_cache import LedgerCache
from app.write_lock import WriteLockManager
from .ledger_initializer import LedgerInitializer
from app.schemas.account_schemas import (
    AccountDetails, AccountCurrencyData, AccountCreateRequest, AccountCreateData,
    BalanceDirectiveData, BalanceDirectiveCreateRequest, BalanceDirectiveUpdateRequest
)
from app.schemas.commodity_schemas import (
    CommodityDetails, CommodityCreateRequest, CommodityCreateData
)

logger = logging.getLogger(__name__)


class LedgerManager:
    def __init__(
        self,
        ledger_file: str,
        backup_manager: BackupManager,
        ledger_initializer: LedgerInitializer,
        write_lock: Optional[WriteLockManager] = None,
    ):
        self.ledger_file = ledger_file
        self.backup_manager = backup_manager
        self.ledger_initializer = ledger_initializer
        self.cache = LedgerCache(ledger_file)
        self.engine = BeancountEngine()
        self._write_lock = write_lock
        self._on_cache_invalidated_callbacks: List[Callable[[List[Any]], None]] = []

    def register_cache_invalidation_callback(
        self,
        callback: Callable[[List[Any]], None]
    ) -> None:
        self._on_cache_invalidated_callbacks.append(callback)
        logger.debug(f"Registered cache invalidation callback: {callback.__name__}")

    @contextmanager
    def atomic_ledger_write(self, file_path: Optional[str] = None):
        """Context manager for atomic ledger writes with automatic cache invalidation."""
        target_file = file_path or self.ledger_file

        with self.backup_manager.atomic_write(target_file) as f:
            yield f

        self.cache.invalidate()
        logger.debug(f"Cache invalidated after write to {target_file}")

        entries = self.cache.get_entries()
        for callback in self._on_cache_invalidated_callbacks:
            try:
                callback(entries)
            except Exception as e:
                logger.error(f"Error in cache invalidation callback: {e}", exc_info=True)

    def switch_ledger(self, new_ledger_file: str) -> None:
        logger.info(f"Switching ledger file: {self.ledger_file} → {new_ledger_file}")
        self.ledger_file = new_ledger_file
        self.ledger_initializer.ledger_file = new_ledger_file
        self.cache.switch_ledger(new_ledger_file)

    # ── Delegated read helpers ───────────────────────────────────────────────

    def is_existing_account(self, account_name: str) -> bool:
        return account_name in self.cache.get_account_names()

    def validate_account_format(self, account_name: str) -> bool:
        return self.engine.validate_account_format(account_name)

    def has_parsing_errors(self) -> bool:
        if not os.path.exists(self.ledger_file):
            return False
        try:
            return len(self.cache.get_errors()) > 0
        except Exception:
            return True

    def get_detailed_accounts(self) -> List[AccountDetails]:
        if not os.path.exists(self.ledger_file):
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")
        accounts = self.cache.get_accounts()
        errors = self.cache.get_errors()
        if errors:
            logger.warning(f"Beancount parsing warnings: {len(errors)} issues found")
        return list(accounts.values())

    def get_detailed_accounts_filtered(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> List[AccountDetails]:
        """Get account information with date-filtered balances."""
        if not os.path.exists(self.ledger_file):
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")

        accounts = self.cache.get_accounts()
        entries = self.cache.get_entries()
        errors = self.cache.get_errors()
        if errors:
            logger.warning(f"Beancount parsing warnings: {len(errors)} issues found")

        if not start_date and not end_date:
            return list(accounts.values())

        effective_end = end_date or date.today()
        balance_sheet_prefixes = BALANCE_SHEET_PREFIXES
        balance_sheet_roots = tuple(BALANCE_SHEET_TYPES)

        result = []
        for acct_name, acct in accounts.items():
            filtered = self._compute_filtered_balances(
                acct_name, entries, start_date, effective_end,
                balance_sheet_prefixes, balance_sheet_roots
            )
            result.append(AccountDetails(
                name=acct.name,
                open_date=acct.open_date,
                close_date=acct.close_date,
                currencies=filtered,
                metadata=acct.metadata,
            ))

        return result

    def _compute_filtered_balances(
        self, account_name: str, entries: List[Any],
        start_date: Optional[date], end_date: date,
        balance_sheet_prefixes: Tuple, balance_sheet_roots: Tuple,
    ) -> List[AccountCurrencyData]:
        """Compute per-currency balances for an account within a date range."""
        from decimal import Decimal
        is_balance_sheet = any(account_name.startswith(p) for p in balance_sheet_prefixes) or \
                           account_name in balance_sheet_roots
        balances: Dict[str, Decimal] = {}
        counts: Dict[str, int] = {}
        last_dates: Dict[str, Optional[date]] = {}

        for entry in entries:
            if not isinstance(entry, data.Transaction):
                continue
            if entry.date > end_date:
                continue
            if not is_balance_sheet and start_date and entry.date < start_date:
                continue

            for posting in entry.postings:
                if posting.account != account_name:
                    continue
                if posting.units and posting.units.number is not None:
                    ccy = posting.units.currency
                    balances[ccy] = balances.get(ccy, Decimal(0)) + posting.units.number
                    counts[ccy] = counts.get(ccy, 0) + 1
                    prev = last_dates.get(ccy)
                    if prev is None or entry.date > prev:
                        last_dates[ccy] = entry.date

        return [
            AccountCurrencyData(
                currency=ccy,
                balance=float(balances[ccy]),
                transaction_count=counts.get(ccy, 0),
                last_transaction_date=last_dates.get(ccy),
            )
            for ccy in sorted(balances)
        ]

    def get_account_open_date(self, account_name: str) -> Optional[date]:
        if not os.path.exists(self.ledger_file):
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")
        accounts = self.cache.get_accounts()
        if account_name in accounts:
            return accounts[account_name].open_date
        return None

    def get_account_close_date(self, account_name: str) -> Optional[date]:
        if not os.path.exists(self.ledger_file):
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")
        accounts = self.cache.get_accounts()
        if account_name in accounts:
            return accounts[account_name].close_date
        return None

    def get_account_metadata(self, account_name: str) -> Dict[str, Any]:
        accounts = self.cache.get_accounts()
        if account_name in accounts:
            return accounts[account_name].metadata
        return {}

    def get_account_transactions_summary(self, account_name: str) -> Dict[str, AccountCurrencyData]:
        accounts = self.cache.get_accounts()
        if account_name in accounts:
            return {c.currency: c for c in accounts[account_name].currencies}
        return {}

    # ── Write helpers ────────────────────────────────────────────────────────

    def _write_entries(self, entries) -> None:
        """Write all entries to the ledger using the engine's formatter.

        This is the single authorised path for mutating the ledger file.
        When a WriteLockManager is present (multi-user / concurrent access),
        the write is serialised under the per-user lock.
        """
        if self._write_lock:
            with self._write_lock.acquire("_write_entries"):
                self._do_write_entries(entries)
        else:
            self._do_write_entries(entries)

    def _do_write_entries(self, entries) -> None:
        """Perform the actual atomic write (called by _write_entries)."""
        with self.atomic_ledger_write() as f:
            f.seek(0)
            f.truncate()
            f.write(self.engine.format_entries(entries))

    def append_entries(self, new_entries) -> None:
        """Append new entries via a full rewrite through _write_entries()."""
        self._write_entries(list(self.cache.get_entries()) + list(new_entries))

    # ── Account management (orchestrator: cache → engine → write) ────────────

    def create_account_directive(self, request: AccountCreateRequest) -> AccountCreateData:
        if not self.validate_account_format(request.name):
            raise ValueError(f"Invalid account format: {request.name}")
        if self.is_existing_account(request.name):
            raise ValueError(f"Account already exists: {request.name}")

        metadata = {}
        if request.description:
            metadata['description'] = request.description
        if request.metadata:
            metadata.update(request.metadata)

        new_entries = self.engine.create_account(
            list(self.cache.get_entries()),
            name=request.name,
            open_date=request.open_date,
            currencies=request.currencies or [],
            metadata=metadata,
            ledger_file=str(self.ledger_file),
        )
        self._write_entries(new_entries)

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
        open_date: Optional[date] = None,
        currencies: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        close_date: Optional[date] = None,
        reopen: bool = False,
    ) -> None:
        new_entries = self.engine.update_account(
            list(self.cache.get_entries()),
            account_name,
            new_name=new_name,
            open_date=open_date,
            currencies=currencies,
            metadata=metadata,
            close_date=close_date,
            reopen=reopen,
            ledger_file=str(self.ledger_file),
        )
        self._write_entries(new_entries)

    def close_account_directive(self, account_name: str, close_date, reason: Optional[str] = None) -> None:
        new_entries = self.engine.close_account(
            list(self.cache.get_entries()),
            account_name, close_date,
            reason=reason,
            ledger_file=str(self.ledger_file),
        )
        self._write_entries(new_entries)

    def reopen_account_directive(self, account_name: str) -> None:
        new_entries = self.engine.reopen_account(list(self.cache.get_entries()), account_name)
        self._write_entries(new_entries)

    def delete_account_directive(self, account_name: str) -> None:
        """Remove Open/Close directives only (no transaction deletion)."""
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

    def delete_account(self, account_name: str, delete_transactions: bool = True) -> int:
        remaining, txn_deleted = self.engine.delete_account(
            list(self.cache.get_entries()), account_name, delete_transactions
        )
        self._write_entries(remaining)
        logger.info(f"Deleted account {account_name} and {txn_deleted} transaction(s)")
        return txn_deleted

    # ── Commodity management ────────────────────────────────────────────────

    def get_detailed_commodities(self) -> List[CommodityDetails]:
        if not os.path.exists(self.ledger_file):
            raise FileNotFoundError(f"Ledger file not found: {self.ledger_file}")
        commodities = self.cache.get_commodities()
        errors = self.cache.get_errors()
        if errors:
            logger.warning(f"Beancount parsing warnings: {len(errors)} issues found")
        return list(commodities.values())

    def create_commodity_directive(self, request: CommodityCreateRequest) -> CommodityCreateData:
        existing_codes = {c.code for c in self.get_detailed_commodities()}
        if request.code in existing_codes:
            raise ValueError(f"Commodity already exists: {request.code}")

        new_entries = self.engine.create_commodity(
            list(self.cache.get_entries()),
            code=request.code,
            name=request.name,
            commodity_type=request.type,
            metadata=request.metadata,
            ledger_file=str(self.ledger_file),
        )
        self._write_entries(new_entries)

        for commodity in self.get_detailed_commodities():
            if commodity.code == request.code:
                return CommodityCreateData(
                    commodity_created=True,
                    commodity_details=commodity,
                    message=f"Commodity '{request.code}' created successfully",
                )
        return CommodityCreateData(
            commodity_created=True,
            commodity_details=None,
            message=f"Commodity '{request.code}' created successfully (details unavailable)",
        )

    # ── Transaction management ──────────────────────────────────────────────

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
        additional_meta: Optional[Dict] = None,
    ) -> Transaction:
        return self.engine.create_transaction(
            date_obj, payee, narration, postings, source_account,
            flag=flag, external_id=external_id,
            external_id_type=external_id_type,
            additional_meta=additional_meta,
        )

    def add_ids_to_transaction(self, txn: Transaction, force_regenerate: bool = False) -> Transaction:
        return self.engine.add_ids_to_transaction(txn, force_regenerate)

    def compute_hash_from_transaction(self, txn: Transaction) -> Tuple[str, str]:
        return self.engine.compute_hash_from_transaction(txn)

    def update_transactions_by_id(self, transactions: List[Tuple[str, Transaction]]) -> int:
        entries = list(self.cache.get_entries())
        updated_entries, count = self.engine.update_transactions(entries, transactions)
        self._write_entries(updated_entries)
        logger.info(f"Updated {count} transactions in ledger")
        return count

    def delete_transactions_by_id(self, transaction_ids: List[str]) -> int:
        entries = list(self.cache.get_entries())
        remaining, count = self.engine.delete_transactions(entries, transaction_ids)
        self._write_entries(remaining)
        logger.info(f"Deleted {count} transaction(s) from ledger")
        return count

    def delete_transactions_for_account(self, account_name: str) -> int:
        entries = list(self.cache.get_entries())
        remaining, count = self.engine.delete_transactions_for_account(entries, account_name)
        if count == 0:
            return 0
        self._write_entries(remaining)
        logger.info(f"Deleted {count} transaction(s) for account {account_name}")
        return count

    def validate_transaction(self, transaction: Transaction) -> List[str]:
        return self.engine.validate_transaction(transaction)

    # ── Balance & pad directive management ──────────────────────────────────

    def get_balance_directives(self, account_name: str) -> List[BalanceDirectiveData]:
        entries = self.cache.get_entries()
        errors = self.cache.get_errors()

        error_by_line: Dict[int, str] = {}
        for err in errors:
            if err.source:
                lineno = err.source.get('lineno', 0)
                if lineno and 'balance' in err.message.lower():
                    error_by_line[lineno] = err.message

        pending_pad_source: Optional[str] = None
        pending_pad_date: Optional[date] = None
        result: List[BalanceDirectiveData] = []

        for entry in entries:
            if isinstance(entry, data.Pad) and entry.account == account_name:
                pending_pad_source = entry.source_account
                pending_pad_date = entry.date

            elif isinstance(entry, data.Balance) and entry.account == account_name:
                entry_lineno = entry.meta.get('lineno', 0) if entry.meta else 0

                pad_source: Optional[str] = None
                if pending_pad_source is not None and pending_pad_date is not None:
                    if pending_pad_date <= entry.date:
                        pad_source = pending_pad_source
                        pending_pad_source = None
                        pending_pad_date = None

                has_error = entry_lineno in error_by_line
                error_message = error_by_line.get(entry_lineno)

                result.append(BalanceDirectiveData(
                    date=entry.date,
                    currency=entry.amount.currency,
                    expected_balance=float(entry.amount.number) if entry.amount.number is not None else 0.0,
                    has_pad=pad_source is not None,
                    pad_source_account=pad_source,
                    has_error=has_error,
                    error_message=error_message,
                ))

        result.sort(key=lambda d: d.date)
        return result

    def add_balance_directive(self, account_name: str, request: BalanceDirectiveCreateRequest) -> None:
        if not self.is_existing_account(account_name):
            raise ValueError(f"Account not found: {account_name}")
        if request.include_pad:
            if not request.pad_source_account:
                raise ValueError("pad_source_account is required when include_pad is True")
            if not self.is_existing_account(request.pad_source_account):
                raise ValueError(f"Pad source account not found: {request.pad_source_account}")

        new_entries = self.engine.add_balance_directive(
            list(self.cache.get_entries()),
            account_name, request.date, request.currency, request.amount,
            include_pad=request.include_pad,
            pad_source_account=request.pad_source_account,
            ledger_file=str(self.ledger_file),
        )
        self._write_entries(new_entries)

    def update_balance_directive(self, account_name: str, request: BalanceDirectiveUpdateRequest) -> None:
        if not self.is_existing_account(account_name):
            raise ValueError(f"Account not found: {account_name}")
        if request.pad_source_account and not self.is_existing_account(request.pad_source_account):
            raise ValueError(f"Pad source account not found: {request.pad_source_account}")

        new_entries = self.engine.update_balance_directive(
            list(self.cache.get_entries()),
            account_name,
            original_date=request.original_date,
            original_currency=request.original_currency,
            original_amount=request.original_amount,
            new_date=request.new_date,
            new_currency=request.new_currency,
            new_amount=request.new_amount,
            include_pad=request.include_pad,
            pad_source_account=request.pad_source_account,
            ledger_file=str(self.ledger_file),
        )
        self._write_entries(new_entries)

    def delete_balance_directive(
        self, account_name: str,
        directive_date: str, currency: str, amount: float,
        delete_pad: bool = True,
    ) -> None:
        from datetime import datetime as dt
        target_date = dt.strptime(directive_date, "%Y-%m-%d").date()

        new_entries = self.engine.delete_balance_directive(
            list(self.cache.get_entries()),
            account_name, target_date, currency, amount,
            delete_pad=delete_pad,
        )
        self._write_entries(new_entries)

    # ── Compat aliases ───────────────────────────────────────────────────────

    @staticmethod
    def _find_pad_before_balance_entry(entries: list, balance_idx: int, account_name: str):
        return BeancountEngine._find_pad_before_balance_entry(entries, balance_idx, account_name)

    @staticmethod
    def _day_before(date_str: str) -> str:
        from datetime import timedelta
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (d - timedelta(days=1)).isoformat()

    @staticmethod
    def _format_amount(amount: float) -> str:
        return BeancountEngine._format_amount(amount)

    @staticmethod
    def _amounts_match(file_amount: str, expected_amount: str) -> bool:
        return BeancountEngine._amounts_match(file_amount, expected_amount)

    def _rename_account_in_entry(self, entry, old_name: str, new_name: str):
        return self.engine.rename_account_in_entry(entry, old_name, new_name)
