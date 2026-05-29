"""
BeancountEngine behaviour tests — written against the engine's documented
contract (parse / format / CRUD on entries), not its internal mechanics.
"""

import pytest
from datetime import date
from pathlib import Path

from beancount.core.data import Transaction, Posting
from beancount.core.amount import Amount
from decimal import Decimal


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def engine():
    from app.core.beancount_engine import BeancountEngine
    return BeancountEngine()


@pytest.fixture
def base_entries(engine):
    """A minimal set of entries: two accounts opened."""
    content = (FIXTURES_DIR / "small_ledger.beancount").read_text()
    return engine.parse(content)


class TestFormatAndParse:
    """Roundtrip: format → parse should preserve data."""

    def test_format_then_parse_preserves_accounts(self, engine, base_entries):
        """Open directives survive a format→parse roundtrip."""
        text = engine.format_entries(base_entries)
        reparsed = engine.parse(text)
        original_opens = {e.account for e in base_entries if hasattr(e, 'account') and e.__class__.__name__ == 'Open'}
        reparsed_opens = {e.account for e in reparsed if hasattr(e, 'account') and e.__class__.__name__ == 'Open'}
        assert original_opens == reparsed_opens

    def test_format_then_parse_preserves_transactions(self, engine, base_entries):
        """Transaction count and payees survive a format→parse roundtrip."""
        original_txns = [e for e in base_entries if isinstance(e, Transaction)]

        text = engine.format_entries(base_entries)
        reparsed = engine.parse(text)
        reparsed_txns = [e for e in reparsed if isinstance(e, Transaction)]

        assert len(reparsed_txns) == len(original_txns)
        original_payees = sorted(t.payee or "" for t in original_txns)
        reparsed_payees = sorted(t.payee or "" for t in reparsed_txns)
        assert original_payees == reparsed_payees

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

    def test_delete_account_no_transactions_returns_zero(self, engine, base_entries):
        """Deleting an account with no transactions returns count=0."""
        entries, count = engine.delete_account(base_entries, "Expenses:Unknown", delete_transactions=True)
        opens = {e.account for e in entries if e.__class__.__name__ == 'Open'}
        assert "Expenses:Unknown" not in opens
        assert count == 0

    def test_delete_account_with_transactions_raises_without_flag(self, engine, base_entries):
        """Deleting an account with transactions should raise if delete_transactions=False."""
        with pytest.raises(ValueError, match="has transactions"):
            engine.delete_account(base_entries, "Expenses:Food", delete_transactions=False)

    def test_delete_account_removes_all_transactions(self, engine, base_entries):
        """delete_account must remove ALL transactions, not just the first one.

        Expenses:Food has multiple transactions in the fixture. The returned
        count must match, and no transaction may reference the account.
        """
        # Count how many transactions reference Expenses:Food before deletion
        food_txn_count = sum(
            1 for e in base_entries
            if isinstance(e, Transaction)
            and any(p.account == "Expenses:Food" for p in e.postings)
        )
        assert food_txn_count > 1, "Test requires multiple transactions for Expenses:Food"

        entries, count = engine.delete_account(base_entries, "Expenses:Food", delete_transactions=True)
        assert count == food_txn_count
        for entry in entries:
            if isinstance(entry, Transaction):
                for posting in entry.postings:
                    assert posting.account != "Expenses:Food"

    def test_update_account_rename(self, engine, base_entries):
        """Renaming updates Open directive + all transaction postings."""
        entries = engine.update_account(
            base_entries, "Expenses:Food", new_name="Expenses:Groceries",
        )
        opens = {e.account for e in entries if e.__class__.__name__ == 'Open'}
        assert "Expenses:Groceries" in opens
        assert "Expenses:Food" not in opens

        for entry in entries:
            if isinstance(entry, Transaction):
                for posting in entry.postings:
                    assert posting.account != "Expenses:Food", \
                        f"Transaction still references old name: {entry.narration}"

    def test_update_nonexistent_account_raises(self, engine, base_entries):
        """Updating an account that doesn't exist should raise ValueError."""
        with pytest.raises(ValueError, match="not found"):
            engine.update_account(base_entries, "Expenses:DoesNotExist", new_name="Expenses:Nope")

    def test_update_account_preserves_other_opens(self, engine, base_entries):
        """Renaming one account must not alter any other account's Open."""
        original_opens = {
            e.account for e in base_entries
            if e.__class__.__name__ == 'Open' and e.account != "Expenses:Food"
        }
        entries = engine.update_account(
            base_entries, "Expenses:Food", new_name="Expenses:Groceries",
        )
        remaining_opens = {
            e.account for e in entries
            if e.__class__.__name__ == 'Open' and e.account != "Expenses:Groceries"
        }
        assert remaining_opens == original_opens

    def test_update_account_with_close_date(self, engine, base_entries):
        """update_account with close_date should add a Close directive.

        If the account has no existing Close, one should be appended.
        The Open directive should be preserved.
        """
        entries = engine.update_account(
            base_entries, "Expenses:Unknown", close_date=date(2024, 12, 31),
        )
        opens = {e.account for e in entries if e.__class__.__name__ == 'Open'}
        closes = {e.account: e for e in entries if e.__class__.__name__ == 'Close'}
        assert "Expenses:Unknown" in opens, "Open directive must be preserved"
        assert "Expenses:Unknown" in closes, "Close directive must be added"
        assert closes["Expenses:Unknown"].date == date(2024, 12, 31)

    def test_update_account_changes_existing_close_date(self, engine, base_entries):
        """If the account already has a Close, update_account should change its date."""
        # First close the account
        entries = engine.close_account(base_entries, "Expenses:Unknown", date(2024, 6, 30))
        # Now update with a new close date
        entries = engine.update_account(
            entries, "Expenses:Unknown", close_date=date(2024, 12, 31),
        )
        close_entries = [e for e in entries if e.__class__.__name__ == 'Close' and e.account == "Expenses:Unknown"]
        assert len(close_entries) == 1, "Should have exactly one Close directive"
        assert close_entries[0].date == date(2024, 12, 31)

    def test_update_account_currencies(self, engine, base_entries):
        """update_account should be able to change an account's currencies."""
        entries = engine.update_account(
            base_entries, "Expenses:Unknown", currencies=["USD", "EUR"],
        )
        open_entry = next(e for e in entries if e.__class__.__name__ == 'Open' and e.account == "Expenses:Unknown")
        assert set(open_entry.currencies) == {"USD", "EUR"}

    def test_close_account_with_reason(self, engine, base_entries):
        """Close directive should carry the reason metadata."""
        entries = engine.close_account(
            base_entries, "Expenses:Unknown", date(2024, 12, 31),
            reason="No longer needed",
        )
        close_entries = [e for e in entries if e.__class__.__name__ == 'Close' and e.account == "Expenses:Unknown"]
        assert len(close_entries) == 1
        assert close_entries[0].meta.get("reason") == "No longer needed"

    def test_rename_sub_accounts(self, engine):
        """Renaming a parent account should also rename child accounts in postings.

        E.g., renaming Expenses:Fees should also rename Expenses:Fees:Banking.
        """
        from beancount.core import data as bd
        from beancount.core.amount import Amount as Amt

        entries = [
            bd.Open({'filename': '', 'lineno': 0}, date(2024, 1, 1), "Expenses:Fees", ["USD"], None),
            bd.Open({'filename': '', 'lineno': 0}, date(2024, 1, 1), "Expenses:Fees:Banking", ["USD"], None),
            bd.Open({'filename': '', 'lineno': 0}, date(2024, 1, 1), "Assets:Bank", ["USD"], None),
            Transaction(
                meta={'filename': '', 'lineno': 0},
                date=date(2024, 3, 1), flag='*', payee="Bank", narration="Fee",
                tags=frozenset(), links=frozenset(),
                postings=[
                    Posting("Expenses:Fees:Banking", Amt(Decimal("5"), "USD"), None, None, None, None),
                    Posting("Assets:Bank", Amt(Decimal("-5"), "USD"), None, None, None, None),
                ],
            ),
        ]
        updated = engine.update_account(entries, "Expenses:Fees", new_name="Expenses:BankFees")

        # Parent renamed
        opens = {e.account for e in updated if e.__class__.__name__ == 'Open'}
        assert "Expenses:BankFees" in opens
        assert "Expenses:Fees" not in opens

        # Child renamed
        assert "Expenses:BankFees:Banking" in opens
        assert "Expenses:Fees:Banking" not in opens

        # Transaction posting also renamed
        for entry in updated:
            if isinstance(entry, Transaction):
                accounts = {p.account for p in entry.postings}
                assert "Expenses:BankFees:Banking" in accounts
                assert "Expenses:Fees:Banking" not in accounts


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
        # Always create a known transaction so the test is deterministic
        txn = engine.create_transaction(
            date(2024, 4, 1), "TestPayee", "Delete me",
            [
                Posting("Assets:Bank:Checking", Amount(Decimal("-10"), "USD"), None, None, None, None),
                Posting("Expenses:Food", Amount(Decimal("10"), "USD"), None, None, None, None),
            ],
            "Assets:Bank:Checking",
        )
        entries = list(base_entries) + [txn]
        txn_id = txn.meta["id"]
        count_before = len([e for e in entries if isinstance(e, Transaction)])

        remaining, count = engine.delete_transactions(entries, [txn_id])
        assert count == 1
        count_after = len([e for e in remaining if isinstance(e, Transaction)])
        assert count_after == count_before - 1
        # The specific transaction must be gone
        for e in remaining:
            if isinstance(e, Transaction) and e.meta:
                assert e.meta.get("id") != txn_id

    def test_delete_nonexistent_id_raises(self, engine, base_entries):
        """Deleting a non-existent transaction ID should raise."""
        with pytest.raises(Exception):
            engine.delete_transactions(list(base_entries), ["nonexistent-id"])

    def test_content_hash_is_deterministic(self, engine):
        """Same inputs must produce the same content_hash."""
        def make():
            return engine.create_transaction(
                date(2024, 4, 1), "Store", "Groceries",
                [
                    Posting("Assets:Bank:Checking", Amount(Decimal("-50"), "USD"), None, None, None, None),
                    Posting("Expenses:Food", Amount(Decimal("50"), "USD"), None, None, None, None),
                ],
                "Assets:Bank:Checking",
            )
        txn1 = make()
        txn2 = make()
        assert txn1.meta["content_hash"] == txn2.meta["content_hash"]

    def test_content_hash_changes_with_amount(self, engine):
        """Different amounts must produce different content_hashes."""
        txn1 = engine.create_transaction(
            date(2024, 4, 1), "Store", "Groceries",
            [
                Posting("Assets:Bank:Checking", Amount(Decimal("-50"), "USD"), None, None, None, None),
                Posting("Expenses:Food", Amount(Decimal("50"), "USD"), None, None, None, None),
            ],
            "Assets:Bank:Checking",
        )
        txn2 = engine.create_transaction(
            date(2024, 4, 1), "Store", "Groceries",
            [
                Posting("Assets:Bank:Checking", Amount(Decimal("-99"), "USD"), None, None, None, None),
                Posting("Expenses:Food", Amount(Decimal("99"), "USD"), None, None, None, None),
            ],
            "Assets:Bank:Checking",
        )
        assert txn1.meta["content_hash"] != txn2.meta["content_hash"]

    def test_create_transaction_with_external_id(self, engine):
        """External IDs (OFX FITID, UPI ref) should be stored in metadata."""
        txn = engine.create_transaction(
            date(2024, 4, 1), "Store", "Purchase",
            [
                Posting("Assets:Bank:Checking", Amount(Decimal("-30"), "USD"), None, None, None, None),
                Posting("Expenses:Food", Amount(Decimal("30"), "USD"), None, None, None, None),
            ],
            "Assets:Bank:Checking",
            external_id="OFX-12345",
            external_id_type="OFX",
        )
        assert txn.meta["external_id"] == "OFX-12345"
        assert txn.meta["external_id_type"] == "OFX"


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
            date(2024, 12, 31), "USD", Decimal("10000.00"),
        )
        from beancount.core import data as bd
        balances = [e for e in entries if isinstance(e, bd.Balance) and e.account == "Assets:Bank:Checking"]
        assert any(b.date == date(2024, 12, 31) for b in balances)

    def test_add_balance_with_pad(self, engine, base_entries):
        """Pad must be dated one day before the balance directive."""
        from beancount.core import data as bd
        balance_date = date(2024, 12, 31)
        entries = engine.add_balance_directive(
            base_entries, "Assets:Bank:Checking",
            balance_date, "USD", Decimal("10000.00"),
            include_pad=True,
            pad_source_account="Expenses:Adjustments",
        )
        pads = [e for e in entries if isinstance(e, bd.Pad) and e.account == "Assets:Bank:Checking"]
        assert len(pads) >= 1
        assert pads[-1].source_account == "Expenses:Adjustments"
        assert pads[-1].date == date(2024, 12, 30), "Pad must be dated one day before the balance"

    def test_delete_balance_directive(self, engine, base_entries):
        from beancount.core import data as bd
        # Add then delete
        entries = engine.add_balance_directive(
            base_entries, "Assets:Bank:Checking",
            date(2024, 12, 31), "USD", Decimal("10000.00"),
        )
        entries = engine.delete_balance_directive(
            entries, "Assets:Bank:Checking",
            date(2024, 12, 31), "USD", Decimal("10000.00"),
        )
        balances = [
            e for e in entries
            if isinstance(e, bd.Balance) and e.account == "Assets:Bank:Checking" and e.date == date(2024, 12, 31)
        ]
        assert len(balances) == 0

    def test_delete_balance_with_pad_removes_both(self, engine, base_entries):
        """Deleting a balance directive with delete_pad=True must also remove the pad."""
        from beancount.core import data as bd
        entries = engine.add_balance_directive(
            base_entries, "Assets:Bank:Checking",
            date(2024, 12, 31), "USD", Decimal("10000.00"),
            include_pad=True,
            pad_source_account="Expenses:Adjustments",
        )
        # Verify pad exists before deletion
        pads_before = [e for e in entries if isinstance(e, bd.Pad) and e.account == "Assets:Bank:Checking"]
        assert len(pads_before) >= 1

        entries = engine.delete_balance_directive(
            entries, "Assets:Bank:Checking",
            date(2024, 12, 31), "USD", Decimal("10000.00"),
            delete_pad=True,
        )
        # Both balance and pad should be gone
        balances = [e for e in entries if isinstance(e, bd.Balance) and e.account == "Assets:Bank:Checking" and e.date == date(2024, 12, 31)]
        pads = [e for e in entries if isinstance(e, bd.Pad) and e.account == "Assets:Bank:Checking" and e.date == date(2024, 12, 30)]
        assert len(balances) == 0
        assert len(pads) == 0

    def test_add_balance_without_pad_flag_creates_no_pad(self, engine, base_entries):
        """include_pad=False should not create a pad directive."""
        from beancount.core import data as bd
        count_before = len([e for e in base_entries if isinstance(e, bd.Pad)])
        entries = engine.add_balance_directive(
            base_entries, "Assets:Bank:Checking",
            date(2024, 12, 31), "USD", Decimal("10000.00"),
            include_pad=False,
        )
        count_after = len([e for e in entries if isinstance(e, bd.Pad)])
        assert count_after == count_before


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
