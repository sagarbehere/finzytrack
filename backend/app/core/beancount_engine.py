"""
BeancountEngine — pure in-memory ledger operations on Beancount entries.

All methods are pure: they take in-memory data and return in-memory data.
No file I/O, no caching, no side effects. File I/O and write atomicity
live in LedgerManager.
"""

import re
import logging
from typing import Optional, List, Dict, Any, Tuple
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from beancount.core import data as bd
from beancount.core.data import Transaction, Posting
from beancount.core.amount import Amount as BcAmount
from beancount.parser import parser as bc_parser, printer as bc_printer
import uuid_utils as uuid

from app.core.constants import ACCOUNT_TYPE_PREFIXES, SOURCE_ACCOUNT_PREFIXES
from app.libs.content_hash import compute_content_hash

logger = logging.getLogger(__name__)


class BeancountEngine:
    """Pure in-memory operations on Beancount entries."""

    # --- Parsing and serialization ---

    def parse(self, content: str) -> Any:
        """Parse raw ledger text using beancount.parser."""
        entries, errors, options = bc_parser.parse_string(content)
        return entries

    def format_entries(self, entries: list) -> str:
        """Serialize entries to Beancount text format.

        Auto-generated padding transactions (flag 'P' with no stable ID) are
        silently dropped because Beancount recreates them at parse time from
        the surrounding pad+balance directives.
        """
        parts = []
        for entry in entries:
            if isinstance(entry, Transaction) and entry.flag == 'P':
                has_id = entry.meta and ('id' in entry.meta or 'transaction_id' in entry.meta)
                if not has_id:
                    continue
            parts.append(bc_printer.format_entry(entry))
            parts.append('\n\n')
        return ''.join(parts)

    # --- Validation ---

    def validate_account_format(self, account_name: str) -> bool:
        """Validate Beancount account name format."""
        pattern = r'^[A-Z][A-Za-z0-9\-_]*(?::[A-Z][A-Za-z0-9\-_]*)*$'
        if not bool(re.match(pattern, account_name)):
            return False
        account_lower = account_name.lower()
        return any(account_lower.startswith(prefix.lower()) for prefix in ACCOUNT_TYPE_PREFIXES)

    def validate_transaction(self, transaction: Transaction) -> List[str]:
        """Validate a Beancount transaction. Returns error strings."""
        from collections import defaultdict

        txn_str = bc_printer.format_entry(transaction)
        entries, errors, _ = bc_parser.parse_string(txn_str)

        if errors:
            return [str(e) for e in errors]

        balance: defaultdict[str, Decimal] = defaultdict(Decimal)
        for posting in transaction.postings:
            if posting.units is not None and posting.units.number is not None:
                balance[posting.units.currency] += posting.units.number

        for currency, amount in balance.items():
            if abs(amount) >= Decimal('0.01'):
                return [f"Transaction does not balance: {currency} off by {amount}"]

        return []

    # --- Account CRUD ---

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
        meta: Dict[str, Any] = {'filename': ledger_file, 'lineno': 0}
        if metadata:
            meta.update(metadata)
        open_entry = bd.Open(meta, open_date, name, currencies or [], None)
        return list(entries) + [open_entry]

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
        final_name = new_name or account_name
        new_entries: list = []
        found_open = False
        found_close = False

        for entry in entries:
            if isinstance(entry, bd.Open) and entry.account == account_name:
                found_open = True
                final_date = open_date or entry.date
                final_currencies = currencies or list(entry.currencies or [])
                carried = {k: v for k, v in (entry.meta or {}).items() if k not in ('filename', 'lineno')}
                if metadata:
                    carried.update(metadata)
                new_meta = {'filename': entry.meta.get('filename', ledger_file), 'lineno': entry.meta.get('lineno', 0)}
                new_meta.update(carried)
                new_entries.append(bd.Open(new_meta, final_date, final_name, final_currencies, entry.booking))

            elif isinstance(entry, bd.Close) and entry.account == account_name:
                found_close = True
                if reopen:
                    pass  # Drop the Close directive
                elif close_date is not None:
                    new_entries.append(bd.Close(entry.meta, close_date, final_name))
                else:
                    new_entries.append(entry._replace(account=final_name))

            elif final_name != account_name:
                new_entries.append(self.rename_account_in_entry(entry, account_name, final_name))

            else:
                new_entries.append(entry)

        if not found_open:
            raise ValueError(f"Account open directive not found: {account_name}")

        if close_date is not None and not found_close:
            close_meta = {'filename': ledger_file, 'lineno': 0}
            new_entries.append(bd.Close(close_meta, close_date, final_name))

        return new_entries

    def close_account(
        self,
        entries: list,
        account_name: str,
        close_date: date,
        *,
        reason: Optional[str] = None,
        ledger_file: str = "",
    ) -> list:
        close_meta: Dict[str, Any] = {'filename': ledger_file, 'lineno': 0}
        if reason:
            close_meta['reason'] = reason
        close_entry = bd.Close(close_meta, close_date, account_name)
        return list(entries) + [close_entry]

    def reopen_account(self, entries: list, account_name: str) -> list:
        return [e for e in entries if not (isinstance(e, bd.Close) and e.account == account_name)]

    def delete_account(
        self,
        entries: list,
        account_name: str,
        delete_transactions: bool = True,
    ) -> Tuple[list, int]:
        remaining = []
        txn_deleted = 0

        for entry in entries:
            if isinstance(entry, (bd.Open, bd.Close)) and entry.account == account_name:
                continue
            if isinstance(entry, Transaction):
                has_posting = any(p.account == account_name for p in entry.postings)
                if has_posting:
                    txn_deleted += 1
                    if not delete_transactions:
                        raise ValueError(
                            f"Account '{account_name}' has transactions. "
                            f"Set delete_transactions=True to delete them, or remove them manually first."
                        )
                    continue
            remaining.append(entry)

        return remaining, txn_deleted

    # --- Transaction CRUD ---

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
    ) -> Transaction:
        transaction_id = str(uuid.uuid7())

        amount_str = "0"
        for posting in postings:
            if posting.account == source_account and posting.units is not None:
                amount_str = f"{posting.units.number} {posting.units.currency}"
                break

        content_hash = compute_content_hash(
            date=date_obj.isoformat(),
            payee=payee,
            amount=amount_str,
            source_account=source_account,
            narration=narration,
        )

        meta = {
            'id': transaction_id,
            'content_hash': content_hash,
            'source_account': source_account,
        }
        if external_id and str(external_id).strip():
            meta['external_id'] = str(external_id).strip()
            if external_id_type:
                meta['external_id_type'] = str(external_id_type).strip()
        if additional_meta:
            meta.update(additional_meta)

        return Transaction(
            meta=meta,
            date=date_obj,
            flag=flag,
            payee=payee,
            narration=narration,
            tags=frozenset(),
            links=frozenset(),
            postings=postings,
        )

    def add_ids_to_transaction(self, txn: Transaction, force_regenerate: bool = False) -> Transaction:
        if not force_regenerate and txn.meta:
            if 'id' in txn.meta and 'content_hash' in txn.meta:
                return txn

        transaction_id = str(uuid.uuid7())
        source_account, content_hash = self.compute_hash_from_transaction(txn)

        new_meta = txn.meta.copy() if txn.meta else {}
        new_meta['id'] = transaction_id
        new_meta['content_hash'] = content_hash
        if 'source_account' not in new_meta and source_account:
            new_meta['source_account'] = source_account

        return txn._replace(meta=new_meta)

    def update_transactions(
        self, entries: list, updates: List[Tuple[str, Transaction]]
    ) -> Tuple[list, int]:
        update_map = {txn_id: txn for txn_id, txn in updates}
        transaction_ids = set(update_map.keys())

        updated_entries = []
        found_ids = set()

        for entry in entries:
            if isinstance(entry, Transaction):
                txn_id = entry.meta.get('id') if entry.meta else None
                if txn_id and txn_id in transaction_ids:
                    updated_txn = update_map[txn_id]
                    if entry.meta and 'lineno' in entry.meta:
                        if not updated_txn.meta:
                            updated_txn = updated_txn._replace(meta={'lineno': entry.meta['lineno']})
                        else:
                            updated_txn.meta['lineno'] = entry.meta['lineno']
                    updated_entries.append(updated_txn)
                    found_ids.add(txn_id)
                else:
                    updated_entries.append(entry)
            else:
                updated_entries.append(entry)

        not_found = transaction_ids - found_ids
        if not_found:
            from app.exceptions import APIError
            raise APIError(
                message=f"Transaction IDs not found: {not_found}",
                code="TRANSACTIONS_NOT_FOUND",
                status_code=404,
                details={"not_found_ids": list(not_found)},
            )

        return updated_entries, len(found_ids)

    def delete_transactions(
        self, entries: list, transaction_ids: List[str]
    ) -> Tuple[list, int]:
        ids_set = set(transaction_ids)
        remaining = []
        found_ids = set()

        for entry in entries:
            if isinstance(entry, Transaction):
                txn_id = entry.meta.get('id') if entry.meta else None
                if txn_id and txn_id in ids_set:
                    found_ids.add(txn_id)
                    continue
            remaining.append(entry)

        not_found = ids_set - found_ids
        if not_found:
            from app.exceptions import APIError
            raise APIError(
                message=f"Transaction IDs not found: {list(not_found)}",
                code="TRANSACTIONS_NOT_FOUND",
                status_code=404,
                details={"not_found_ids": list(not_found)},
            )

        return remaining, len(found_ids)

    def delete_transactions_for_account(
        self, entries: list, account_name: str
    ) -> Tuple[list, int]:
        remaining = []
        deleted = 0

        for entry in entries:
            if isinstance(entry, Transaction):
                if any(p.account == account_name for p in entry.postings):
                    deleted += 1
                    continue
            remaining.append(entry)

        return remaining, deleted

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
        today = datetime.now().date()
        meta: Dict[str, Any] = {'filename': ledger_file, 'lineno': 0}
        if name:
            meta['name'] = name
        meta['type'] = commodity_type or "Unknown"
        if metadata:
            for key, value in metadata.items():
                if key not in ('name', 'type'):
                    meta[key] = value
        commodity_entry = bd.Commodity(meta, today, code)
        return list(entries) + [commodity_entry]

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
        new_entries = []
        if include_pad and pad_source_account:
            pad_date = directive_date - timedelta(days=1)
            pad_meta = {'filename': ledger_file, 'lineno': 0}
            new_entries.append(bd.Pad(pad_meta, pad_date, account_name, pad_source_account))

        meta = {'filename': ledger_file, 'lineno': 0}
        balance_amount = BcAmount(amount, currency)
        new_entries.append(bd.Balance(meta, directive_date, account_name, balance_amount, None, None))

        return list(entries) + new_entries

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
        entries = list(entries)

        balance_idx = None
        for i, entry in enumerate(entries):
            if (isinstance(entry, bd.Balance)
                    and entry.account == account_name
                    and entry.date == original_date
                    and entry.amount.currency == original_currency
                    and self._amounts_match(entry.amount.number, original_amount)):
                balance_idx = i
                break

        if balance_idx is None:
            raise ValueError(
                f"Balance directive not found: {original_date} balance {account_name} "
                f"{original_amount} {original_currency}"
            )

        pad_idx = self._find_pad_before_balance_entry(entries, balance_idx, account_name)

        final_date = new_date if new_date else original_date
        final_amount = new_amount if new_amount is not None else original_amount
        final_currency = new_currency or original_currency

        old_balance = entries[balance_idx]
        entries[balance_idx] = bd.Balance(old_balance.meta, final_date, account_name, BcAmount(final_amount, final_currency), None, None)

        new_pad_date = final_date - timedelta(days=1)

        if include_pad is True:
            if not pad_source_account:
                raise ValueError("pad_source_account is required when include_pad is True")
            new_pad = bd.Pad({'filename': ledger_file, 'lineno': 0}, new_pad_date, account_name, pad_source_account)
            if pad_idx is not None:
                entries[pad_idx] = new_pad
            else:
                entries.insert(balance_idx, new_pad)
        elif include_pad is False and pad_idx is not None:
            entries.pop(pad_idx)
        elif pad_idx is not None:
            if final_date != original_date:
                old_pad = entries[pad_idx]
                entries[pad_idx] = bd.Pad(old_pad.meta, new_pad_date, old_pad.account, old_pad.source_account)

        return entries

    def delete_balance_directive(
        self,
        entries: list,
        account_name: str,
        directive_date: date,
        currency: str,
        amount: Decimal,
        delete_pad: bool = True,
    ) -> list:
        entries = list(entries)

        balance_idx = None
        for i, entry in enumerate(entries):
            if (isinstance(entry, bd.Balance)
                    and entry.account == account_name
                    and entry.date == directive_date
                    and entry.amount.currency == currency
                    and self._amounts_match(entry.amount.number, amount)):
                balance_idx = i
                break

        if balance_idx is None:
            raise ValueError(
                f"Balance directive not found: {directive_date} balance {account_name} "
                f"{amount} {currency}"
            )

        pad_idx = None
        if delete_pad:
            pad_idx = self._find_pad_before_balance_entry(entries, balance_idx, account_name)

        for idx in sorted([i for i in [balance_idx, pad_idx] if i is not None], reverse=True):
            entries.pop(idx)

        return entries

    # --- Helpers ---

    def rename_account_in_entry(self, entry: Any, old_name: str, new_name: str) -> Any:
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
            new_meta = dict(entry.meta)
            for key, val in new_meta.items():
                if isinstance(val, str):
                    new_meta[key] = r(val)
            return entry._replace(meta=new_meta, postings=new_postings)
        return entry

    def compute_hash_from_transaction(self, txn: Transaction) -> Tuple[str, str]:
        source_account = None
        if txn.meta and 'source_account' in txn.meta:
            source_account = txn.meta['source_account']
        else:
            for posting in txn.postings:
                if posting.account.startswith(SOURCE_ACCOUNT_PREFIXES):
                    source_account = posting.account
                    break
        if not source_account:
            source_account = txn.postings[0].account if txn.postings else ""

        amount_str = "0"
        for posting in txn.postings:
            if posting.account == source_account and posting.units is not None:
                amount_str = f"{posting.units.number} {posting.units.currency}"
                break

        content_hash = compute_content_hash(
            date=txn.date.isoformat(),
            payee=txn.payee or "",
            amount=amount_str,
            source_account=source_account,
            narration=txn.narration or "",
        )
        return source_account, content_hash

    @staticmethod
    def _find_pad_before_balance_entry(entries: list, balance_idx: int, account_name: str):
        """Return the index of the Pad that feeds the balance at *balance_idx*.

        Mirrors Beancount semantics and the export-side pairing in
        ``SQLiteExporter._export_full_ledger``: a Pad sets a per-account
        candidate, later Pads for the same account overwrite earlier ones,
        and the next Balance for that account consumes the candidate. So the
        pad that "feeds" *balance_idx* is the most recent Pad for
        *account_name* in ``entries[:balance_idx]`` that wasn't already
        consumed by an earlier Balance for the same account.

        Returns ``None`` when no such pad exists. Crucially, this finds the
        pad even when non-pad entries (e.g. transactions) sit between the
        Pad and the Balance — the previous strict
        ``entries[balance_idx - 1]`` check missed those.
        """
        candidate_pad_idx = None
        for i in range(balance_idx):
            entry = entries[i]
            if isinstance(entry, bd.Pad) and entry.account == account_name:
                candidate_pad_idx = i
            elif isinstance(entry, bd.Balance) and entry.account == account_name:
                candidate_pad_idx = None
        return candidate_pad_idx

    @staticmethod
    def _format_amount(amount: Decimal) -> str:
        if amount == amount.to_integral_value():
            return str(int(amount))
        return format(amount.normalize(), 'f')

    @staticmethod
    def _amounts_match(file_amount: Decimal, expected_amount: Decimal) -> bool:
        # Both sides are Decimal end-to-end (see dev-docs/money-types.md);
        # value equality handles "100" vs "100.00" because Decimal compares by value.
        try:
            return Decimal(file_amount) == Decimal(expected_amount)
        except (InvalidOperation, TypeError, ValueError):
            return file_amount == expected_amount
