"""
LedgerEngine protocol — the abstract interface for a double-entry ledger engine.

All methods operate on in-memory data structures.  The engine has no knowledge
of files, caching, or I/O — that's the LedgerManager's job.  The engine handles
parsing, formatting, validation, and CRUD on ledger entries.
"""

from typing import Protocol, List, Dict, Any, Optional, Tuple
from datetime import date
from decimal import Decimal


class LedgerEngine(Protocol):

    # --- Parsing and serialization ---

    def parse(self, content: str) -> Any:
        """Parse raw ledger text into the engine's native entry list."""
        ...

    def format_entries(self, entries: list) -> str:
        """Serialize entries back to ledger text format.

        Responsible for filtering engine-specific auto-generated entries
        (e.g., Beancount padding transactions with flag='P').
        """
        ...

    # --- Validation ---

    def validate_account_format(self, account_name: str) -> bool:
        """Check whether an account name is syntactically valid."""
        ...

    def validate_transaction(self, transaction: Any) -> List[str]:
        """Return validation error strings (empty if valid)."""
        ...

    # --- Account CRUD (pure: entries in → entries out) ---

    def create_account(
        self,
        entries: list,
        *,
        name: str,
        open_date: date,
        currencies: List[str],
        metadata: Optional[Dict[str, Any]] = None,
        ledger_file: str = "",
    ) -> list:
        """Add an Open directive.  Returns the updated entry list."""
        ...

    def update_account(
        self,
        entries: list,
        account_name: str,
        *,
        new_name: Optional[str] = None,
        open_date: Optional[date] = None,
        currencies: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        close_date: Optional[date] = None,
        reopen: bool = False,
        ledger_file: str = "",
    ) -> list:
        """Modify an account's Open/Close directives.  Returns updated entries."""
        ...

    def close_account(
        self,
        entries: list,
        account_name: str,
        close_date: date,
        *,
        reason: Optional[str] = None,
        ledger_file: str = "",
    ) -> list:
        """Append a Close directive.  Returns updated entries."""
        ...

    def reopen_account(self, entries: list, account_name: str) -> list:
        """Remove the Close directive for an account.  Returns updated entries."""
        ...

    def delete_account(
        self,
        entries: list,
        account_name: str,
        delete_transactions: bool = True,
    ) -> Tuple[list, int]:
        """Remove Open/Close and optionally transactions.

        Returns (updated_entries, transactions_deleted_count).
        """
        ...

    # --- Transaction CRUD (pure: entries in → entries out) ---

    def create_transaction(
        self,
        date_obj: date,
        payee: str,
        narration: str,
        postings: list,
        source_account: str,
        *,
        flag: str = "*",
        external_id: Optional[str] = None,
        external_id_type: Optional[str] = None,
        additional_meta: Optional[Dict] = None,
    ) -> Any:
        """Create a new transaction entry with IDs.  Returns the transaction."""
        ...

    def add_ids_to_transaction(
        self, txn: Any, force_regenerate: bool = False
    ) -> Any:
        """Ensure a transaction has id and content_hash metadata."""
        ...

    def update_transactions(
        self, entries: list, updates: List[Tuple[str, Any]]
    ) -> Tuple[list, int]:
        """Replace transactions by ID.

        Returns (updated_entries, count_updated).
        Raises if any ID is missing.
        """
        ...

    def delete_transactions(
        self, entries: list, transaction_ids: List[str]
    ) -> Tuple[list, int]:
        """Remove transactions by ID.

        Returns (remaining_entries, count_deleted).
        Raises if any ID is missing.
        """
        ...

    def delete_transactions_for_account(
        self, entries: list, account_name: str
    ) -> Tuple[list, int]:
        """Remove all transactions with postings to the given account.

        Returns (remaining_entries, count_deleted).
        """
        ...

    # --- Commodity CRUD ---

    def create_commodity(
        self,
        entries: list,
        *,
        code: str,
        name: Optional[str] = None,
        commodity_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        ledger_file: str = "",
    ) -> list:
        """Add a Commodity directive.  Returns updated entries."""
        ...

    # --- Balance directive CRUD ---

    def add_balance_directive(
        self,
        entries: list,
        account_name: str,
        directive_date: date,
        currency: str,
        amount: Decimal,
        *,
        include_pad: bool = False,
        pad_source_account: Optional[str] = None,
        ledger_file: str = "",
    ) -> list:
        """Add a Balance (and optional Pad) directive.  Returns updated entries."""
        ...

    def update_balance_directive(
        self,
        entries: list,
        account_name: str,
        *,
        original_date: date,
        original_currency: str,
        original_amount: Decimal,
        new_date: Optional[date] = None,
        new_currency: Optional[str] = None,
        new_amount: Optional[Decimal] = None,
        include_pad: Optional[bool] = None,
        pad_source_account: Optional[str] = None,
        ledger_file: str = "",
    ) -> list:
        """Update an existing Balance directive.  Returns updated entries."""
        ...

    def delete_balance_directive(
        self,
        entries: list,
        account_name: str,
        directive_date: date,
        currency: str,
        amount: Decimal,
        delete_pad: bool = True,
    ) -> list:
        """Remove a Balance (and optional Pad) directive.  Returns updated entries."""
        ...

    # --- Read helpers ---

    def rename_account_in_entry(
        self, entry: Any, old_name: str, new_name: str
    ) -> Any:
        """Return a copy of entry with account references renamed."""
        ...

    def compute_hash_from_transaction(self, txn: Any) -> Tuple[str, str]:
        """Return (source_account, content_hash) for a transaction."""
        ...
