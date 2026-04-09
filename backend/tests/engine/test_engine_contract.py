"""
LedgerEngine contract tests — written from the protocol specification, not
the implementation. Any LedgerEngine must pass these tests.

Parameterized by engine implementation so future engines can be plugged in.
"""

import pytest
from datetime import date
from pathlib import Path

from beancount.core.data import Transaction, Posting
from beancount.core.amount import Amount
from decimal import Decimal


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(params=["beancount"])
def engine(request):
    if request.param == "beancount":
        from app.core.beancount_engine import BeancountEngine
        return BeancountEngine()
    raise ValueError(f"Unknown engine: {request.param}")


@pytest.fixture
def base_entries(engine):
    """A minimal set of entries: two accounts opened."""
    content = (FIXTURES_DIR / "small_ledger.beancount").read_text()
    return engine.parse(content)


class TestFormatAndParse:
    """Roundtrip: format → parse should preserve data."""

    def test_format_then_parse_preserves_accounts(self, engine, base_entries):
        """Account Open directives survive a format→parse roundtrip."""
        text = engine.format_entries(base_entries)
        reparsed = engine.parse(text)
        original_opens = {e.account for e in base_entries if hasattr(e, 'account') and e.__class__.__name__ == 'Open'}
        reparsed_opens = {e.account for e in reparsed if hasattr(e, 'account') and e.__class__.__name__ == 'Open'}
        assert original_opens == reparsed_opens

    def test_padding_transactions_are_filtered(self, engine):
        """format_entries must drop auto-generated padding transactions (flag='P', no stable ID).

        In production, Beancount's loader generates padding transactions from
        pad+balance directives. We simulate this by injecting a synthetic
        padding transaction (flag='P', no id) into the entry list and verifying
        that format_entries drops it, while keeping a normal transaction and a
        user-created 'P' transaction that has a stable ID.
        """
        from beancount.core.data import Transaction as Txn
        from beancount.core.amount import Amount as Amt

        content = (FIXTURES_DIR / "small_ledger.beancount").read_text()
        entries = list(engine.parse(content))

        # Inject a synthetic padding transaction (no stable ID) — this is what
        # Beancount's loader generates from pad directives
        padding_txn = Txn(
            meta={'filename': 'test', 'lineno': 0},
            date=date(2024, 3, 31),
            flag='P',
            payee=None,
            narration="(Padding inserted for balance)",
            tags=frozenset(),
            links=frozenset(),
            postings=[
                Posting("Assets:Bank:Checking", Amt(Decimal("72"), "USD"), None, None, None, None),
                Posting("Expenses:Adjustments", Amt(Decimal("-72"), "USD"), None, None, None, None),
            ],
        )

        # Also inject a 'P'-flagged transaction WITH a stable ID — this should
        # be kept (it's a user-created pending transaction, not auto-padding)
        user_pending_txn = Txn(
            meta={'filename': 'test', 'lineno': 0, 'id': 'user-created-id'},
            date=date(2024, 3, 31),
            flag='P',
            payee="User",
            narration="Pending review",
            tags=frozenset(),
            links=frozenset(),
            postings=[
                Posting("Assets:Bank:Checking", Amt(Decimal("-20"), "USD"), None, None, None, None),
                Posting("Expenses:Food", Amt(Decimal("20"), "USD"), None, None, None, None),
            ],
        )

        entries.append(padding_txn)
        entries.append(user_pending_txn)

        text = engine.format_entries(entries)

        # The auto-padding (no ID) must be absent
        assert "(Padding inserted for balance)" not in text

        # The user-created 'P' transaction (has ID) must be present
        assert "Pending review" in text


class TestAccountFormat:
    """Account name validation based on Beancount format rules."""

    def test_valid_account_names(self, engine):
        assert engine.validate_account_format("Assets:Bank:Checking") is True
        assert engine.validate_account_format("Expenses:Food") is True
        assert engine.validate_account_format("Income:Salary") is True
        assert engine.validate_account_format("Liabilities:CreditCard") is True
        assert engine.validate_account_format("Equity:Opening-Balances") is True

    def test_invalid_prefixes(self, engine):
        assert engine.validate_account_format("Savings:Bank") is False
        assert engine.validate_account_format("InvalidType:Account") is False

    def test_invalid_format(self, engine):
        assert engine.validate_account_format("") is False
        assert engine.validate_account_format("assets:bank") is False  # lowercase first letter
        assert engine.validate_account_format("Assets") is False  # no sub-account


class TestAccountCRUD:
    """Account lifecycle: create, close, reopen, delete."""

    def test_create_account_appears(self, engine, base_entries):
        """Creating an account should add an Open directive."""
        entries = engine.create_account(
            base_entries, name="Assets:Bank:Investment",
            open_date=date(2024, 6, 1), currencies=["USD"],
        )
        opens = {e.account for e in entries if e.__class__.__name__ == 'Open'}
        assert "Assets:Bank:Investment" in opens

    def test_create_account_roundtrip(self, engine, base_entries):
        """Created account survives format→parse."""
        entries = engine.create_account(
            base_entries, name="Expenses:Travel",
            open_date=date(2024, 1, 1), currencies=["USD"],
        )
        text = engine.format_entries(entries)
        reparsed = engine.parse(text)
        opens = {e.account for e in reparsed if e.__class__.__name__ == 'Open'}
        assert "Expenses:Travel" in opens

    def test_close_account(self, engine, base_entries):
        """Closing an account should add a Close directive."""
        entries = engine.close_account(
            base_entries, "Expenses:Unknown", date(2024, 12, 31),
        )
        closes = {e.account for e in entries if e.__class__.__name__ == 'Close'}
        assert "Expenses:Unknown" in closes

    def test_reopen_account(self, engine, base_entries):
        """Reopening removes the Close directive."""
        entries = engine.close_account(base_entries, "Expenses:Unknown", date(2024, 12, 31))
        entries = engine.reopen_account(entries, "Expenses:Unknown")
        closes = {e.account for e in entries if e.__class__.__name__ == 'Close'}
        assert "Expenses:Unknown" not in closes

    def test_delete_account_removes_open(self, engine, base_entries):
        """Deleting an account removes its Open directive."""
        # Expenses:Unknown has no transactions in small_ledger
        entries, count = engine.delete_account(base_entries, "Expenses:Unknown", delete_transactions=True)
        opens = {e.account for e in entries if e.__class__.__name__ == 'Open'}
        assert "Expenses:Unknown" not in opens

    def test_delete_account_with_transactions_raises_without_flag(self, engine, base_entries):
        """Deleting an account with transactions should raise if delete_transactions=False."""
        with pytest.raises(ValueError, match="has transactions"):
            engine.delete_account(base_entries, "Expenses:Food", delete_transactions=False)

    def test_delete_account_with_transactions_removes_them(self, engine, base_entries):
        """With delete_transactions=True, transactions are also removed."""
        entries, count = engine.delete_account(base_entries, "Expenses:Food", delete_transactions=True)
        assert count > 0
        # No remaining transactions should reference the deleted account
        for entry in entries:
            if isinstance(entry, Transaction):
                for posting in entry.postings:
                    assert posting.account != "Expenses:Food"

    def test_update_account_rename(self, engine, base_entries):
        """Renaming an account updates Open + all referencing entries."""
        entries = engine.update_account(
            base_entries, "Expenses:Unknown", new_name="Expenses:Miscellaneous",
        )
        opens = {e.account for e in entries if e.__class__.__name__ == 'Open'}
        assert "Expenses:Miscellaneous" in opens
        assert "Expenses:Unknown" not in opens


class TestTransactionCRUD:
    """Transaction creation, update, and deletion."""

    def test_create_transaction_has_id(self, engine):
        """Created transactions must have a UUIDv7-format id."""
        posting_a = Posting("Assets:Bank:Checking", Amount(Decimal("-50"), "USD"), None, None, None, None)
        posting_b = Posting("Expenses:Food", Amount(Decimal("50"), "USD"), None, None, None, None)

        txn = engine.create_transaction(
            date(2024, 4, 1), "Store", "Groceries",
            [posting_a, posting_b], "Assets:Bank:Checking",
        )
        assert "id" in txn.meta
        assert len(txn.meta["id"]) == 36  # UUID format

    def test_create_transaction_has_content_hash(self, engine):
        txn = engine.create_transaction(
            date(2024, 4, 1), "Store", "Groceries",
            [
                Posting("Assets:Bank:Checking", Amount(Decimal("-50"), "USD"), None, None, None, None),
                Posting("Expenses:Food", Amount(Decimal("50"), "USD"), None, None, None, None),
            ],
            "Assets:Bank:Checking",
        )
        assert "content_hash" in txn.meta
        assert len(txn.meta["content_hash"]) > 0

    def test_delete_transaction_by_id(self, engine, base_entries):
        """Deleting by ID should remove exactly that transaction."""
        # Find a transaction with an ID
        txns_with_id = [
            e for e in base_entries
            if isinstance(e, Transaction) and e.meta and 'id' in e.meta
        ]

        if not txns_with_id:
            # Add a transaction with an ID for testing
            txn = engine.create_transaction(
                date(2024, 4, 1), "Test", "Test txn",
                [
                    Posting("Assets:Bank:Checking", Amount(Decimal("-10"), "USD"), None, None, None, None),
                    Posting("Expenses:Food", Amount(Decimal("10"), "USD"), None, None, None, None),
                ],
                "Assets:Bank:Checking",
            )
            entries = list(base_entries) + [txn]
            txn_id = txn.meta["id"]
        else:
            entries = list(base_entries)
            txn_id = txns_with_id[0].meta["id"]

        remaining, count = engine.delete_transactions(entries, [txn_id])
        assert count == 1
        for e in remaining:
            if isinstance(e, Transaction) and e.meta:
                assert e.meta.get("id") != txn_id

    def test_delete_nonexistent_id_raises(self, engine, base_entries):
        """Deleting a non-existent transaction ID should raise."""
        with pytest.raises(Exception):
            engine.delete_transactions(list(base_entries), ["nonexistent-id"])


class TestTransactionValidation:
    """Transaction validation based on double-entry accounting rules."""

    def test_balanced_transaction_is_valid(self, engine):
        txn = engine.create_transaction(
            date(2024, 4, 1), "Store", "Groceries",
            [
                Posting("Assets:Bank:Checking", Amount(Decimal("-50.00"), "USD"), None, None, None, None),
                Posting("Expenses:Food", Amount(Decimal("50.00"), "USD"), None, None, None, None),
            ],
            "Assets:Bank:Checking",
        )
        errors = engine.validate_transaction(txn)
        assert errors == []

    def test_unbalanced_transaction_reports_error(self, engine):
        txn = engine.create_transaction(
            date(2024, 4, 1), "Store", "Groceries",
            [
                Posting("Assets:Bank:Checking", Amount(Decimal("-50.00"), "USD"), None, None, None, None),
                Posting("Expenses:Food", Amount(Decimal("60.00"), "USD"), None, None, None, None),
            ],
            "Assets:Bank:Checking",
        )
        errors = engine.validate_transaction(txn)
        assert len(errors) > 0


class TestBalanceDirectives:
    """Balance and pad directive manipulation."""

    def test_add_balance_directive(self, engine, base_entries):
        entries = engine.add_balance_directive(
            base_entries, "Assets:Bank:Checking",
            date(2024, 12, 31), "USD", 10000.00,
        )
        from beancount.core import data as bd
        balances = [e for e in entries if isinstance(e, bd.Balance) and e.account == "Assets:Bank:Checking"]
        assert any(b.date == date(2024, 12, 31) for b in balances)

    def test_add_balance_with_pad(self, engine, base_entries):
        from beancount.core import data as bd
        entries = engine.add_balance_directive(
            base_entries, "Assets:Bank:Checking",
            date(2024, 12, 31), "USD", 10000.00,
            include_pad=True,
            pad_source_account="Expenses:Adjustments",
        )
        pads = [e for e in entries if isinstance(e, bd.Pad) and e.account == "Assets:Bank:Checking"]
        assert len(pads) >= 1
        assert pads[-1].source_account == "Expenses:Adjustments"

    def test_delete_balance_directive(self, engine, base_entries):
        from beancount.core import data as bd
        # Add then delete
        entries = engine.add_balance_directive(
            base_entries, "Assets:Bank:Checking",
            date(2024, 12, 31), "USD", 10000.00,
        )
        entries = engine.delete_balance_directive(
            entries, "Assets:Bank:Checking",
            date(2024, 12, 31), "USD", 10000.00,
        )
        balances = [
            e for e in entries
            if isinstance(e, bd.Balance) and e.account == "Assets:Bank:Checking" and e.date == date(2024, 12, 31)
        ]
        assert len(balances) == 0


class TestCommodityCRUD:

    def test_create_commodity(self, engine, base_entries):
        entries = engine.create_commodity(base_entries, code="EUR")
        from beancount.core import data as bd
        commodities = [e for e in entries if isinstance(e, bd.Commodity) and e.currency == "EUR"]
        assert len(commodities) >= 1

    def test_create_commodity_with_metadata(self, engine, base_entries):
        entries = engine.create_commodity(
            base_entries, code="BTC",
            name="Bitcoin", commodity_type="Cryptocurrency",
        )
        from beancount.core import data as bd
        btc = [e for e in entries if isinstance(e, bd.Commodity) and e.currency == "BTC"]
        assert len(btc) >= 1
        assert btc[0].meta.get("name") == "Bitcoin"
