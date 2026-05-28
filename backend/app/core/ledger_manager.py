"""
LedgerManager — thin orchestrator that delegates pure ledger logic to a LedgerEngine.

Responsibilities:
- File I/O (atomic writes with backup)
- Transient ledger parsing via load_ledger_checked()
- Synchronous SQLite export after every write
- Ledger file switching

The engine handles all Beancount-specific logic: parsing, formatting, entry
creation, account/transaction/commodity/balance CRUD.
"""

import os
import logging
from typing import Optional, List, Dict, Any, Tuple
from datetime import date
from decimal import Decimal
from contextlib import contextmanager
from beancount.core import data
from beancount.core.data import Transaction, Posting

from app.core.backup_manager import BackupManager
from app.core.beancount_engine import BeancountEngine
from app.core.ledger_loader import load_ledger_checked
from app.write_lock import WriteLockManager
from .ledger_initializer import LedgerInitializer
from app.schemas.account_schemas import (
    AccountCreateRequest, AccountCreateData,
    BalanceDirectiveCreateRequest, BalanceDirectiveUpdateRequest
)
from app.schemas.commodity_schemas import (
    CommodityCreateRequest, CommodityCreateData
)

logger = logging.getLogger(__name__)


class LedgerManager:
    def __init__(
        self,
        ledger_file: str,
        backup_manager: BackupManager,
        ledger_initializer: LedgerInitializer,
        write_lock: Optional[WriteLockManager] = None,
        sqlite_exporter: Optional[Any] = None,
    ):
        self.ledger_file = ledger_file
        self.backup_manager = backup_manager
        self.ledger_initializer = ledger_initializer
        self.engine = BeancountEngine()
        self._write_lock = write_lock
        self._sqlite_exporter = sqlite_exporter

    def set_sqlite_exporter(self, exporter: Any) -> None:
        """Set the SQLite exporter for synchronous exports after writes."""
        self._sqlite_exporter = exporter

    def switch_ledger(self, new_ledger_file: str) -> None:
        logger.info(f"Switching ledger file: {self.ledger_file} → {new_ledger_file}")
        self.ledger_file = new_ledger_file
        self.ledger_initializer.ledger_file = new_ledger_file

    # ── Transient parse ─────────────────────────────────────────────────────

    def _parse_ledger(self) -> Tuple[List[Any], List[Any], Dict[str, Any]]:
        """Parse ledger from disk transiently. Memory released when caller returns."""
        return load_ledger_checked(self.ledger_file)

    # ── Delegated read helpers (for internal validation in write methods) ────

    def is_existing_account(self, account_name: str) -> bool:
        entries, _, _ = self._parse_ledger()
        for e in entries:
            if isinstance(e, data.Open) and e.account == account_name:
                return True
        return False

    def validate_account_format(self, account_name: str) -> bool:
        return self.engine.validate_account_format(account_name)

    def has_parsing_errors(self) -> bool:
        if not os.path.exists(self.ledger_file):
            return False
        try:
            _, errors, _ = self._parse_ledger()
            return len(errors) > 0
        except Exception:
            return True

    # ── Write helpers ────────────────────────────────────────────────────────

    @contextmanager
    def atomic_ledger_write(self, file_path: Optional[str] = None):
        """Context manager for atomic ledger writes."""
        target_file = file_path or self.ledger_file
        with self.backup_manager.atomic_write(target_file) as f:
            yield f

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

    def _write_and_export(self, entries, errors=None, options=None) -> None:
        """Write entries then synchronously export to SQLite.

        This replaces the old pattern of write → cache invalidation → debounced export.
        The export happens inline so that reads immediately reflect the write.

        Always re-parses after writing to get fresh errors/options, because the
        write may have resolved or introduced validation errors (e.g. changing an
        account open date can fix "inactive account" errors).

        Two-parse policy: this method intentionally re-parses (parse 2) after
        the write to refresh errors. Combined with parse 1 in the API-handler
        (which read current state to build ``entries``), every mutation pays
        two full ledger parses. This is the deliberate cost of the design
        constraints: (a) no in-memory ledger cache (parsing is the architectural
        choice — see ``ledger_loader.py``), and (b) post-write errors must be
        immediately visible. Bulk imports batch into a single ``append_entries``
        call, so a 1000-transaction import is still 2 parses, not 2000.
        """
        self._write_entries(entries)

        if self._sqlite_exporter:
            entries, errors, options = self._parse_ledger()
            try:
                self._sqlite_exporter.export_full_sync(entries, errors, options)
            except Exception as e:
                logger.error(
                    "SQLite export failed after write (data is in .beancount, "
                    "will be recovered on next read): %s", e
                )

    def append_entries(self, new_entries) -> None:
        """Append new entries via a full rewrite through _write_and_export()."""
        entries, errors, options = self._parse_ledger()
        all_entries = list(entries) + list(new_entries)
        self._write_and_export(all_entries, errors, options)

    # ── Account management (orchestrator: parse → engine → write) ────────────

    def create_account_directive(self, request: AccountCreateRequest) -> AccountCreateData:
        if not self.validate_account_format(request.name):
            raise ValueError(f"Invalid account format: {request.name}")

        entries, errors, options = self._parse_ledger()

        # Check if account already exists
        for e in entries:
            if isinstance(e, data.Open) and e.account == request.name:
                raise ValueError(f"Account already exists: {request.name}")

        metadata = {}
        if request.description:
            metadata['description'] = request.description
        if request.metadata:
            metadata.update(request.metadata)

        new_entries = self.engine.create_account(
            list(entries),
            name=request.name,
            open_date=request.open_date,
            currencies=request.currencies or [],
            metadata=metadata,
            ledger_file=str(self.ledger_file),
        )
        self._write_and_export(new_entries, errors, options)

        # Return success — account details come from SqliteReader
        return AccountCreateData(
            account_created=True,
            account_details=None,
            message=f"Account '{request.name}' created successfully",
        )

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
        entries, errors, options = self._parse_ledger()
        new_entries = self.engine.update_account(
            list(entries),
            account_name,
            new_name=new_name,
            open_date=open_date,
            currencies=currencies,
            metadata=metadata,
            close_date=close_date,
            reopen=reopen,
            ledger_file=str(self.ledger_file),
        )
        self._write_and_export(new_entries, errors, options)

    def close_account_directive(self, account_name: str, close_date, reason: Optional[str] = None) -> None:
        entries, errors, options = self._parse_ledger()
        new_entries = self.engine.close_account(
            list(entries),
            account_name, close_date,
            reason=reason,
            ledger_file=str(self.ledger_file),
        )
        self._write_and_export(new_entries, errors, options)

    def reopen_account_directive(self, account_name: str) -> None:
        entries, errors, options = self._parse_ledger()
        new_entries = self.engine.reopen_account(list(entries), account_name)
        self._write_and_export(new_entries, errors, options)

    def delete_account_directive(self, account_name: str) -> None:
        """Remove Open/Close directives only (no transaction deletion)."""
        entries, errors, options = self._parse_ledger()
        new_entries = [
            e for e in entries
            if not (
                (isinstance(e, data.Open) and e.account == account_name) or
                (isinstance(e, data.Close) and e.account == account_name)
            )
        ]
        self._write_and_export(new_entries, errors, options)

    def delete_account(self, account_name: str, delete_transactions: bool = True) -> int:
        entries, errors, options = self._parse_ledger()
        remaining, txn_deleted = self.engine.delete_account(
            list(entries), account_name, delete_transactions
        )
        self._write_and_export(remaining, errors, options)
        logger.info(f"Deleted account {account_name} and {txn_deleted} transaction(s)")
        return txn_deleted

    # ── Commodity management ────────────────────────────────────────────────

    def create_commodity_directive(self, request: CommodityCreateRequest) -> CommodityCreateData:
        entries, errors, options = self._parse_ledger()

        # Check if commodity already exists
        for e in entries:
            if isinstance(e, data.Commodity) and e.currency == request.code:
                raise ValueError(f"Commodity already exists: {request.code}")

        new_entries = self.engine.create_commodity(
            list(entries),
            code=request.code,
            name=request.name,
            commodity_type=request.type,
            metadata=request.metadata,
            ledger_file=str(self.ledger_file),
        )
        self._write_and_export(new_entries, errors, options)

        return CommodityCreateData(
            commodity_created=True,
            commodity_details=None,
            message=f"Commodity '{request.code}' created successfully",
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
        entries, errors, options = self._parse_ledger()
        updated_entries, count = self.engine.update_transactions(list(entries), transactions)
        self._write_and_export(updated_entries, errors, options)
        logger.info(f"Updated {count} transactions in ledger")
        return count

    def delete_transactions_by_id(self, transaction_ids: List[str]) -> int:
        entries, errors, options = self._parse_ledger()
        remaining, count = self.engine.delete_transactions(list(entries), transaction_ids)
        self._write_and_export(remaining, errors, options)
        logger.info(f"Deleted {count} transaction(s) from ledger")
        return count

    def delete_transactions_for_account(self, account_name: str) -> int:
        entries, errors, options = self._parse_ledger()
        remaining, count = self.engine.delete_transactions_for_account(list(entries), account_name)
        if count == 0:
            return 0
        self._write_and_export(remaining, errors, options)
        logger.info(f"Deleted {count} transaction(s) for account {account_name}")
        return count

    def validate_transaction(self, transaction: Transaction) -> List[str]:
        return self.engine.validate_transaction(transaction)

    # ── Balance & pad directive management ──────────────────────────────────

    def add_balance_directive(self, account_name: str, request: BalanceDirectiveCreateRequest) -> None:
        entries, errors, options = self._parse_ledger()

        # Validate account exists
        acct_exists = any(isinstance(e, data.Open) and e.account == account_name for e in entries)
        if not acct_exists:
            raise ValueError(f"Account not found: {account_name}")
        if request.include_pad:
            if not request.pad_source_account:
                raise ValueError("pad_source_account is required when include_pad is True")
            pad_exists = any(isinstance(e, data.Open) and e.account == request.pad_source_account for e in entries)
            if not pad_exists:
                raise ValueError(f"Pad source account not found: {request.pad_source_account}")

        new_entries = self.engine.add_balance_directive(
            list(entries),
            account_name, request.date, request.currency, request.amount,
            include_pad=request.include_pad,
            pad_source_account=request.pad_source_account,
            ledger_file=str(self.ledger_file),
        )
        self._write_and_export(new_entries, errors, options)

    def update_balance_directive(self, account_name: str, request: BalanceDirectiveUpdateRequest) -> None:
        entries, errors, options = self._parse_ledger()

        acct_exists = any(isinstance(e, data.Open) and e.account == account_name for e in entries)
        if not acct_exists:
            raise ValueError(f"Account not found: {account_name}")
        if request.pad_source_account:
            pad_exists = any(isinstance(e, data.Open) and e.account == request.pad_source_account for e in entries)
            if not pad_exists:
                raise ValueError(f"Pad source account not found: {request.pad_source_account}")

        new_entries = self.engine.update_balance_directive(
            list(entries),
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
        self._write_and_export(new_entries, errors, options)

    def delete_balance_directive(
        self, account_name: str,
        directive_date: str, currency: str, amount: Decimal,
        delete_pad: bool = True,
    ) -> None:
        from datetime import datetime as dt
        target_date = dt.strptime(directive_date, "%Y-%m-%d").date()

        entries, errors, options = self._parse_ledger()
        new_entries = self.engine.delete_balance_directive(
            list(entries),
            account_name, target_date, currency, amount,
            delete_pad=delete_pad,
        )
        self._write_and_export(new_entries, errors, options)

    # ── Compat aliases ───────────────────────────────────────────────────────

    @staticmethod
    def _find_pad_before_balance_entry(entries: list, balance_idx: int, account_name: str):
        return BeancountEngine._find_pad_before_balance_entry(entries, balance_idx, account_name)

    @staticmethod
    def _day_before(date_str: str) -> str:
        from datetime import timedelta, datetime
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (d - timedelta(days=1)).isoformat()

    @staticmethod
    def _format_amount(amount: Decimal) -> str:
        return BeancountEngine._format_amount(amount)

    @staticmethod
    def _amounts_match(file_amount: Decimal, expected_amount: Decimal) -> bool:
        return BeancountEngine._amounts_match(file_amount, expected_amount)

    def _rename_account_in_entry(self, entry, old_name: str, new_name: str):
        return self.engine.rename_account_in_entry(entry, old_name, new_name)
