"""
CQRS migration tests.

Tests the behavioral contracts introduced by the CQRS architecture:
1. Full export correctness — SQLite tables contain data matching the ledger
2. Read-after-write consistency — writes are immediately visible in reads
3. Staleness detection — external modifications are caught and recovered

These tests verify the specification, not the implementation. Expected values
are hand-derived from the fixture files, not computed from the code under test.
"""

import os
import shutil
import sqlite3
import time
from decimal import Decimal
from pathlib import Path

import pytest
from beancount import loader

from app.services.sqlite_exporter import SQLiteExporter
from app.services.sqlite_reader import SqliteReader


FIXTURES_DIR = Path(__file__).parent / "fixtures"

# ── Hand-derived expected values from fixture files ──────────────────────────
# These are computed by reading the .beancount files directly, not by running
# any code. If a fixture changes, these must be updated manually.

# small_ledger.beancount: 9 accounts, all opened 1970-01-01, none closed
SMALL_LEDGER_ACCOUNTS = {
    "Assets:Bank:Checking",
    "Assets:Bank:Savings",
    "Liabilities:CreditCard",
    "Income:Salary",
    "Expenses:Food",
    "Expenses:Rent",
    "Expenses:Transport",
    "Expenses:Unknown",
    "Equity:Opening-Balances",
}

# Hand-computed balances from small_ledger.beancount (sum every posting per account)
SMALL_LEDGER_BALANCES = {
    # +5000 +4500 -1500 -120.50 -85.30 -500 +4500 -1500 -110.25 -120 -135 -500 +4500 -1500 -95.75
    "Assets:Bank:Checking": Decimal("12333.20"),
    # +500 +500
    "Assets:Bank:Savings": Decimal("1000.00"),
    # -45 -55 -35 +135 -32.50 -78.90
    "Liabilities:CreditCard": Decimal("-111.40"),
    # -4500 -4500 -4500
    "Income:Salary": Decimal("-13500.00"),
    # +120.50 +45 +85.30 +110.25 +35 +95.75 +78.90
    "Expenses:Food": Decimal("570.70"),
    # +1500 +1500 +1500
    "Expenses:Rent": Decimal("4500.00"),
    # +55 +120 +32.50
    "Expenses:Transport": Decimal("207.50"),
    # (no transactions)
    "Expenses:Unknown": Decimal("0"),
    # -5000
    "Equity:Opening-Balances": Decimal("-5000.00"),
}

# Hand-counted transaction counts per account from small_ledger.beancount
SMALL_LEDGER_TXN_COUNTS = {
    "Assets:Bank:Checking": 15,
    "Assets:Bank:Savings": 2,
    "Liabilities:CreditCard": 6,
    "Income:Salary": 3,
    "Expenses:Food": 7,
    "Expenses:Rent": 3,
    "Expenses:Transport": 3,
    "Equity:Opening-Balances": 1,
    # Expenses:Unknown: 0 — no transactions, no balance row expected
}

# Training data: every transaction with a payee where the first expense/income
# posting becomes the category. Hand-counted from small_ledger.beancount:
# Employer→Income:Salary (x3), Landlord→Expenses:Rent (x3),
# Grocery Store→Expenses:Food (x4), Restaurant→Expenses:Food (x3),
# Gas Station→Expenses:Transport, Train→Expenses:Transport,
# Uber→Expenses:Transport = 16 total, but some have no expense posting
# Actually: "Transfer", "Credit Card", "Opening Balance" have no payee or
# no expense account. Let me recount:
# Payee transactions with expense/income first posting:
# Employer x3 → Income:Salary
# Landlord x3 → Expenses:Rent
# Grocery Store x4 → Expenses:Food
# Restaurant x3 → Expenses:Food
# Gas Station x1 → Expenses:Transport
# Train x1 → Expenses:Transport
# Uber x1 → Expenses:Transport
# = 16 training rows
SMALL_LEDGER_TRAINING_COUNT = 16

# edge_cases.beancount expected values
EDGE_CASE_ACCOUNTS = {
    "Assets:Bank:Checking",
    "Assets:Bank:Euro",
    "Assets:Investments:Stocks",
    "Liabilities:CreditCard",
    "Income:Salary",
    "Income:Dividends",
    "Expenses:Food",
    "Expenses:Fees:Banking",
    "Expenses:Unknown",
    "Equity:Opening-Balances",
    "Expenses:Travel:Flights:Domestic:Economy",
    "Expenses:Adjustments",
}

EDGE_CASE_COMMODITIES = {"USD", "EUR", "AAPL"}

# EUR balance in edge_cases: +2000 (opening) -25.50 (café) = 1974.50
EDGE_CASE_EUR_BALANCE = Decimal("1974.50")


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def small_ledger_path(tmp_path):
    """Copy small_ledger to a temp dir, return path to the .beancount file."""
    src = FIXTURES_DIR / "small_ledger.beancount"
    dst = tmp_path / "main.beancount"
    shutil.copy2(src, dst)
    return dst


@pytest.fixture
def edge_case_ledger_path(tmp_path):
    """Copy edge_cases ledger to a temp dir, return path to the .beancount file."""
    src = FIXTURES_DIR / "edge_cases.beancount"
    dst = tmp_path / "main.beancount"
    shutil.copy2(src, dst)
    return dst


@pytest.fixture
def exported_db(small_ledger_path, tmp_path):
    """Run a full export of small_ledger and return (db_path, entries, errors, options)."""
    db_path = tmp_path / "ledger.db"
    exporter = SQLiteExporter(str(db_path))
    entries, errors, options = loader.load_file(str(small_ledger_path))
    exporter._export_full_to_sqlite(entries, errors, options)
    return db_path, entries, errors, options


@pytest.fixture
def edge_case_db(edge_case_ledger_path, tmp_path):
    """Run a full export of edge_cases and return (db_path, entries, errors, options)."""
    db_path = tmp_path / "ledger.db"
    exporter = SQLiteExporter(str(db_path))
    entries, errors, options = loader.load_file(str(edge_case_ledger_path))
    exporter._export_full_to_sqlite(entries, errors, options)
    return db_path, entries, errors, options


@pytest.fixture
def sqlite_reader_for_small(small_ledger_path, tmp_path):
    """SqliteReader wired to small_ledger. No pre-export — reader handles it."""
    db_path = tmp_path / "ledger.db"
    exporter = SQLiteExporter(str(db_path))
    return SqliteReader(
        sqlite_path=db_path,
        ledger_file=small_ledger_path,
        exporter=exporter,
    )


# ============================================================================
# 1. Full Export Correctness
# ============================================================================


class TestExportAccounts:
    """The accounts table must match Open/Close directives in the ledger."""

    def test_account_set_matches_fixture(self, exported_db):
        """The exported accounts must be exactly the 9 accounts in small_ledger."""
        db_path, _, _, _ = exported_db
        con = sqlite3.connect(str(db_path))
        actual = {
            r[0] for r in con.execute("SELECT name FROM accounts").fetchall()
        }
        con.close()

        assert actual == SMALL_LEDGER_ACCOUNTS

    def test_open_date_matches_directive(self, exported_db):
        """All accounts in small_ledger are opened on 1970-01-01."""
        db_path, _, _, _ = exported_db
        con = sqlite3.connect(str(db_path))
        dates = {
            r[0] for r in con.execute("SELECT DISTINCT open_date FROM accounts").fetchall()
        }
        con.close()
        # Every account in small_ledger opens on the same date
        assert dates == {"1970-01-01"}

    def test_no_accounts_closed_in_small_ledger(self, exported_db):
        """small_ledger has no Close directives — every close_date must be NULL."""
        db_path, _, _, _ = exported_db
        con = sqlite3.connect(str(db_path))
        non_null_count = con.execute(
            "SELECT COUNT(*) FROM accounts WHERE close_date IS NOT NULL"
        ).fetchone()[0]
        con.close()
        assert non_null_count == 0

    def test_currencies_preserved_from_open_directive(self, edge_case_db):
        """Assets:Investments:Stocks is opened with USD,AAPL."""
        import json
        db_path, _, _, _ = edge_case_db
        con = sqlite3.connect(str(db_path))
        row = con.execute(
            "SELECT currencies_json FROM accounts "
            "WHERE name = 'Assets:Investments:Stocks'"
        ).fetchone()
        con.close()
        currencies = json.loads(row[0])
        assert set(currencies) == {"USD", "AAPL"}

    def test_edge_case_account_set(self, edge_case_db):
        """edge_cases has 12 accounts including deeply nested ones."""
        db_path, _, _, _ = edge_case_db
        con = sqlite3.connect(str(db_path))
        actual = {
            r[0] for r in con.execute("SELECT name FROM accounts").fetchall()
        }
        con.close()
        assert actual == EDGE_CASE_ACCOUNTS


class TestExportAccountBalances:
    """account_balances must reflect the sum of all postings per account+currency."""

    def test_all_account_balances_match_hand_computed(self, exported_db):
        """Verify every non-zero account balance against hand-computed values.

        This is the most important export test: if the balance computation
        is wrong, every dashboard/widget that reads from SQLite is wrong.
        """
        db_path, _, _, _ = exported_db
        con = sqlite3.connect(str(db_path))
        rows = con.execute(
            "SELECT account, currency, balance FROM account_balances"
        ).fetchall()
        con.close()

        actual = {(r[0], r[1]): Decimal(r[2]) for r in rows}

        for account, expected_balance in SMALL_LEDGER_BALANCES.items():
            if expected_balance == 0:
                # Zero-balance accounts may or may not have a row
                if (account, "USD") in actual:
                    assert actual[(account, "USD")] == Decimal("0"), \
                        f"{account}: expected 0, got {actual[(account, 'USD')]}"
                continue

            key = (account, "USD")
            assert key in actual, f"Missing balance row for {account}"
            assert actual[key] == expected_balance, \
                f"{account}: expected {expected_balance}, got {actual[key]}"

    def test_transaction_counts_match_hand_counted(self, exported_db):
        """Verify transaction_count per account against hand-counted values."""
        db_path, _, _, _ = exported_db
        con = sqlite3.connect(str(db_path))
        rows = con.execute(
            "SELECT account, transaction_count FROM account_balances "
            "WHERE currency = 'USD'"
        ).fetchall()
        con.close()

        actual = {r[0]: r[1] for r in rows}

        for account, expected_count in SMALL_LEDGER_TXN_COUNTS.items():
            if expected_count == 0:
                continue
            assert actual.get(account) == expected_count, \
                f"{account}: expected {expected_count} txns, got {actual.get(account)}"

    def test_multi_currency_balances(self, edge_case_db):
        """edge_cases has both USD and EUR accounts with separate balances."""
        db_path, _, _, _ = edge_case_db
        con = sqlite3.connect(str(db_path))
        eur_row = con.execute(
            "SELECT balance FROM account_balances "
            "WHERE account = 'Assets:Bank:Euro' AND currency = 'EUR'"
        ).fetchone()
        con.close()

        assert eur_row is not None, "Missing EUR balance for Assets:Bank:Euro"
        assert Decimal(eur_row[0]) == EDGE_CASE_EUR_BALANCE

    def test_double_entry_balances_sum_to_zero(self, exported_db):
        """The sum of all balances across all accounts must be zero.

        This is a fundamental double-entry bookkeeping invariant. If the
        export breaks this, the data is corrupt.
        """
        db_path, _, _, _ = exported_db
        con = sqlite3.connect(str(db_path))
        rows = con.execute(
            "SELECT balance FROM account_balances WHERE currency = 'USD'"
        ).fetchall()
        con.close()

        total = sum(Decimal(r[0]) for r in rows)
        assert total == Decimal("0"), \
            f"Double-entry violated: all balances sum to {total}, expected 0"


class TestExportZeroValuePostings:
    """Beancount's Amount type is falsy when its number is zero. Naive
    truthiness checks (``if posting.units``, ``if posting.price``) treat
    zero-value postings as absent, writing NULLs to the database.

    These tests verify that zero amounts, zero costs, and zero prices are
    all exported with their currency and numeric value intact."""

    def test_zero_amount_posting_has_currency(self, edge_case_db):
        """edge_cases has a 0.00 USD posting for Expenses:Fees:Banking.
        It must appear in the postings table with currency='USD', not NULL."""
        db_path, _, _, _ = edge_case_db
        con = sqlite3.connect(str(db_path))
        rows = con.execute(
            "SELECT amount, currency FROM postings "
            "WHERE account = 'Expenses:Fees:Banking'"
        ).fetchall()
        con.close()

        assert len(rows) == 1
        assert Decimal(rows[0][0]) == Decimal("0")
        assert rows[0][1] == "USD"

    def test_zero_cost_posting_has_cost_fields(self, edge_case_db):
        """edge_cases has a posting with {0.00 USD} cost (worthless shares).
        cost_amount and cost_currency must not be NULL."""
        db_path, _, _, _ = edge_case_db
        con = sqlite3.connect(str(db_path))
        rows = con.execute(
            "SELECT cost_amount, cost_currency FROM postings "
            "WHERE transaction_narration LIKE '%worthless%' "
            "AND account = 'Assets:Investments:Stocks'"
        ).fetchall()
        con.close()

        assert len(rows) >= 1
        for cost_amount, cost_currency in rows:
            assert cost_amount is not None, "cost_amount is NULL for zero-cost posting"
            assert cost_currency == "USD", \
                f"cost_currency should be 'USD', got {cost_currency!r}"
            assert Decimal(cost_amount) == Decimal("0")

    def test_zero_price_posting_has_price_fields(self, edge_case_db):
        """edge_cases has a posting with @ 0.00 USD price (mark to zero).
        price_amount and price_currency must not be NULL."""
        db_path, _, _, _ = edge_case_db
        con = sqlite3.connect(str(db_path))
        rows = con.execute(
            "SELECT price_amount, price_currency FROM postings "
            "WHERE transaction_narration LIKE '%Mark shares to zero%' "
            "AND account = 'Assets:Investments:Stocks'"
        ).fetchall()
        con.close()

        assert len(rows) == 1
        assert rows[0][0] is not None, "price_amount is NULL for zero-price posting"
        assert Decimal(rows[0][0]) == Decimal("0")
        assert rows[0][1] == "USD", \
            f"price_currency should be 'USD', got {rows[0][1]!r}"

    def test_no_null_currencies_in_postings(self, edge_case_db):
        """No posting should ever have a NULL currency."""
        db_path, _, _, _ = edge_case_db
        con = sqlite3.connect(str(db_path))
        null_count = con.execute(
            "SELECT COUNT(*) FROM postings WHERE currency IS NULL"
        ).fetchone()[0]
        con.close()
        assert null_count == 0, \
            f"Found {null_count} postings with NULL currency"

    def test_no_null_cost_currency_when_cost_exists(self, edge_case_db):
        """If a posting has a cost_amount, it must also have a cost_currency."""
        db_path, _, _, _ = edge_case_db
        con = sqlite3.connect(str(db_path))
        broken = con.execute(
            "SELECT COUNT(*) FROM postings "
            "WHERE cost_amount IS NOT NULL AND cost_currency IS NULL"
        ).fetchone()[0]
        con.close()
        assert broken == 0, \
            f"Found {broken} postings with cost_amount but NULL cost_currency"


class TestExportCommodities:
    """commodities table must match Commodity directives."""

    def test_declared_commodities_match_fixture(self, edge_case_db):
        """edge_cases declares exactly USD, EUR, AAPL."""
        db_path, _, _, _ = edge_case_db
        con = sqlite3.connect(str(db_path))
        codes = {
            r[0] for r in con.execute("SELECT code FROM commodities").fetchall()
        }
        con.close()
        assert codes == EDGE_CASE_COMMODITIES

    def test_commodity_usage_count_for_usd(self, exported_db):
        """small_ledger has 20 transactions, all in USD. Each transaction has
        2 postings, so USD appears in 40 postings total."""
        db_path, _, _, _ = exported_db
        con = sqlite3.connect(str(db_path))
        row = con.execute(
            "SELECT transaction_count FROM commodity_usage WHERE code = 'USD'"
        ).fetchone()
        con.close()
        # 20 transactions × 2 postings each = 40 posting-level usages
        assert row[0] == 40


class TestExportBalanceAssertions:
    """balance_assertions table must capture Balance directives."""

    def test_balance_assertions_for_edge_cases(self, edge_case_db):
        """edge_cases has 2 balance assertions for Assets:Bank:Checking:
        2024-03-15 balance 7428.00 USD and 2024-04-01 balance 7500.00 USD."""
        db_path, _, _, _ = edge_case_db
        con = sqlite3.connect(str(db_path))
        rows = con.execute(
            "SELECT date, amount_number, amount_currency "
            "FROM balance_assertions "
            "WHERE account = 'Assets:Bank:Checking' "
            "ORDER BY date"
        ).fetchall()
        con.close()

        assert len(rows) == 2

        assert rows[0][0] == "2024-03-15"
        assert Decimal(rows[0][1]) == Decimal("7428.00")
        assert rows[0][2] == "USD"

        assert rows[1][0] == "2024-04-01"
        assert Decimal(rows[1][1]) == Decimal("7500.00")
        assert rows[1][2] == "USD"

    def test_no_balance_assertions_in_small_ledger(self, exported_db):
        """small_ledger has no balance directives."""
        db_path, _, _, _ = exported_db
        con = sqlite3.connect(str(db_path))
        count = con.execute(
            "SELECT COUNT(*) FROM balance_assertions"
        ).fetchone()[0]
        con.close()
        assert count == 0


class TestExportPadDirectives:
    """pad_directives table must capture Pad directives."""

    def test_pad_directive_in_edge_cases(self, edge_case_db):
        """edge_cases: 2024-03-31 pad Assets:Bank:Checking Expenses:Adjustments"""
        db_path, _, _, _ = edge_case_db
        con = sqlite3.connect(str(db_path))
        rows = con.execute(
            "SELECT date, account, source_account FROM pad_directives"
        ).fetchall()
        con.close()

        assert len(rows) == 1
        assert rows[0][0] == "2024-03-31"
        assert rows[0][1] == "Assets:Bank:Checking"
        assert rows[0][2] == "Expenses:Adjustments"


class TestExportTrainingData:
    """training_data table must contain payee+narration → category pairs."""

    def test_training_data_count_matches_fixture(self, exported_db):
        """small_ledger has exactly 16 transactions with payees mapping to
        expense/income accounts."""
        db_path, _, _, _ = exported_db
        con = sqlite3.connect(str(db_path))
        count = con.execute("SELECT COUNT(*) FROM training_data").fetchone()[0]
        con.close()
        assert count == SMALL_LEDGER_TRAINING_COUNT

    def test_grocery_store_maps_to_food(self, exported_db):
        """Every 'Grocery Store' transaction should map to Expenses:Food."""
        db_path, _, _, _ = exported_db
        con = sqlite3.connect(str(db_path))
        rows = con.execute(
            "SELECT description, category FROM training_data "
            "WHERE description LIKE '%Grocery Store%'"
        ).fetchall()
        con.close()

        # 4 Grocery Store transactions in small_ledger
        assert len(rows) == 4
        for desc, category in rows:
            assert category == "Expenses:Food"

    def test_salary_maps_to_income(self, exported_db):
        """'Employer' transactions should map to Income:Salary."""
        db_path, _, _, _ = exported_db
        con = sqlite3.connect(str(db_path))
        rows = con.execute(
            "SELECT category FROM training_data "
            "WHERE description LIKE '%Employer%'"
        ).fetchall()
        con.close()

        assert len(rows) == 3
        for row in rows:
            assert row[0] == "Income:Salary"

    def test_all_categories_are_expense_or_income(self, exported_db):
        """Training categories must only be Expenses: or Income: accounts."""
        db_path, _, _, _ = exported_db
        con = sqlite3.connect(str(db_path))
        rows = con.execute(
            "SELECT DISTINCT category FROM training_data"
        ).fetchall()
        con.close()

        assert len(rows) > 0, "Training data should not be empty"
        for row in rows:
            assert row[0].startswith("Expenses:") or row[0].startswith("Income:"), \
                f"Category '{row[0]}' is not an expense or income account"


class TestExportOptions:
    """ledger_options table must contain Beancount options."""

    def test_operating_currency_is_usd(self, exported_db):
        """small_ledger sets 'option \"operating_currency\" \"USD\"'."""
        import json
        db_path, _, _, _ = exported_db
        con = sqlite3.connect(str(db_path))
        row = con.execute(
            "SELECT value_json FROM ledger_options WHERE key = 'operating_currency'"
        ).fetchone()
        con.close()

        assert row is not None, "operating_currency option not exported"
        value = json.loads(row[0])
        assert value == ["USD"]


class TestExportCrossTableConsistency:
    """Tables produced by a single export must be mutually consistent."""

    def test_every_posting_account_has_an_account_row(self, exported_db):
        """Every account referenced in postings must exist in accounts table."""
        db_path, _, _, _ = exported_db
        con = sqlite3.connect(str(db_path))

        posting_accounts = {
            r[0] for r in con.execute(
                "SELECT DISTINCT account FROM postings"
            ).fetchall()
        }
        known_accounts = {
            r[0] for r in con.execute("SELECT name FROM accounts").fetchall()
        }
        con.close()

        missing = posting_accounts - known_accounts
        assert missing == set(), \
            f"Postings reference accounts not in accounts table: {missing}"

    def test_every_balance_account_has_an_account_row(self, exported_db):
        """Every account in account_balances must exist in accounts table."""
        db_path, _, _, _ = exported_db
        con = sqlite3.connect(str(db_path))

        balance_accounts = {
            r[0] for r in con.execute(
                "SELECT DISTINCT account FROM account_balances"
            ).fetchall()
        }
        known_accounts = {
            r[0] for r in con.execute("SELECT name FROM accounts").fetchall()
        }
        con.close()

        missing = balance_accounts - known_accounts
        assert missing == set(), \
            f"Balances reference accounts not in accounts table: {missing}"


# ============================================================================
# 2. Read-After-Write Consistency (API-level)
# ============================================================================


class TestReadAfterWriteConsistency:
    """After a write operation completes, all subsequent reads must reflect it.

    This is the core CQRS guarantee. If any of these tests fail, the system
    has a stale-read bug — the most serious failure mode of the architecture.
    """

    def test_create_account_immediately_visible(self, test_client):
        """A newly created account must appear in the next GET /accounts
        with the correct open_date and currencies."""
        resp = test_client.post(
            "/api/accounts",
            json={
                "name": "Assets:Brokerage",
                "open_date": "2024-07-01",
                "currencies": ["USD"],
            },
        )
        assert resp.status_code == 201

        list_resp = test_client.get("/api/accounts")
        accounts = list_resp.json()["data"]["accounts"]
        names = {a["name"] for a in accounts}
        assert "Assets:Brokerage" in names

        new_acct = next(a for a in accounts if a["name"] == "Assets:Brokerage")
        assert new_acct["open_date"] == "2024-07-01"

    def test_create_account_does_not_lose_existing_accounts(self, test_client):
        """Creating a new account must not cause any existing account to disappear."""
        before_resp = test_client.get("/api/accounts")
        names_before = {a["name"] for a in before_resp.json()["data"]["accounts"]}

        test_client.post(
            "/api/accounts",
            json={
                "name": "Expenses:NewCategory",
                "open_date": "2024-08-01",
                "currencies": ["USD"],
            },
        )

        after_resp = test_client.get("/api/accounts")
        names_after = {a["name"] for a in after_resp.json()["data"]["accounts"]}

        # All original accounts must still be present
        assert names_before.issubset(names_after), \
            f"Lost accounts after create: {names_before - names_after}"
        # Plus the new one
        assert "Expenses:NewCategory" in names_after

    def test_close_account_immediately_visible(self, test_client):
        """After closing an account, close_date must appear in the next read."""
        close_resp = test_client.post(
            "/api/accounts/Expenses:Unknown/close",
            json={"close_date": "2024-12-31"},
        )
        assert close_resp.status_code == 200

        list_resp = test_client.get("/api/accounts")
        acct = next(
            a for a in list_resp.json()["data"]["accounts"]
            if a["name"] == "Expenses:Unknown"
        )
        assert acct["close_date"] == "2024-12-31"

    def test_reopen_account_clears_close_date(self, test_client):
        """After closing then reopening, close_date must be null."""
        test_client.post(
            "/api/accounts/Expenses:Unknown/close",
            json={"close_date": "2024-12-31"},
        )
        test_client.post("/api/accounts/Expenses:Unknown/reopen")

        list_resp = test_client.get("/api/accounts")
        acct = next(
            a for a in list_resp.json()["data"]["accounts"]
            if a["name"] == "Expenses:Unknown"
        )
        assert acct["close_date"] is None

    def test_delete_account_immediately_gone(self, test_client):
        """After deleting an account, it must not appear in the next read."""
        before = test_client.get("/api/accounts")
        names_before = {a["name"] for a in before.json()["data"]["accounts"]}
        assert "Expenses:Unknown" in names_before

        del_resp = test_client.delete("/api/accounts/Expenses:Unknown")
        assert del_resp.status_code == 200

        after = test_client.get("/api/accounts")
        names_after = {a["name"] for a in after.json()["data"]["accounts"]}
        assert "Expenses:Unknown" not in names_after
        # Exactly one account fewer
        assert len(names_after) == len(names_before) - 1

    def test_rename_account_immediately_visible(self, test_client):
        """After renaming, old name gone, new name present — in the next read."""
        rename_resp = test_client.put(
            "/api/accounts/Expenses:Unknown",
            json={"new_name": "Expenses:Misc"},
        )
        assert rename_resp.status_code == 200

        list_resp = test_client.get("/api/accounts")
        names = {a["name"] for a in list_resp.json()["data"]["accounts"]}
        assert "Expenses:Misc" in names
        assert "Expenses:Unknown" not in names

    def test_balance_changes_after_account_delete(self, test_client):
        """Deleting an account that has transactions should change other
        accounts' balances (since the counterparty postings are removed)."""
        # Get Checking balance before
        before = test_client.get("/api/accounts")
        checking_before = next(
            a for a in before.json()["data"]["accounts"]
            if a["name"] == "Assets:Bank:Checking"
        )
        checking_balance_before = sum(
            Decimal(c["balance"]) for c in checking_before["currencies"]
        )

        # Delete Expenses:Rent (has 3 transactions, each -1500 from Checking)
        test_client.delete("/api/accounts/Expenses:Rent")

        # Get Checking balance after — should differ because the rent
        # transactions (which had Checking counterparties) are gone
        after = test_client.get("/api/accounts")
        checking_after = next(
            a for a in after.json()["data"]["accounts"]
            if a["name"] == "Assets:Bank:Checking"
        )
        checking_balance_after = sum(
            Decimal(c["balance"]) for c in checking_after["currencies"]
        )

        assert checking_balance_after != checking_balance_before, \
            "Checking balance should change after deleting Expenses:Rent"


# ============================================================================
# 3. Staleness Detection and Recovery
# ============================================================================


class TestStalenessDetection:
    """SqliteReader must detect when the .beancount file is newer than SQLite
    and trigger a re-export before serving reads."""

    def test_external_account_addition_detected(self, sqlite_reader_for_small, small_ledger_path):
        """If a new account is added to the .beancount file externally,
        the next read must include it."""
        reader = sqlite_reader_for_small

        # First read — triggers initial export
        accounts_before = reader.get_account_names()
        assert len(accounts_before) == 9  # exact count from fixture
        assert "Assets:NewAccount" not in accounts_before

        # Modify the .beancount file externally (append a new account)
        time.sleep(0.05)  # Ensure mtime differs
        with open(small_ledger_path, "a") as f:
            f.write("\n2024-06-01 open Assets:NewAccount USD\n")

        # Next read must see the new account
        accounts_after = reader.get_account_names()
        assert "Assets:NewAccount" in accounts_after
        assert len(accounts_after) == 10

    def test_external_transaction_changes_balance(self, sqlite_reader_for_small, small_ledger_path):
        """If a transaction is appended externally, balances must update."""
        reader = sqlite_reader_for_small

        accounts_before = reader.get_accounts()
        savings_before = next(a for a in accounts_before if a.name == "Assets:Bank:Savings")
        savings_balance_before = sum(c.balance for c in savings_before.currencies)

        time.sleep(0.05)
        with open(small_ledger_path, "a") as f:
            f.write(
                "\n2024-06-01 * \"Transfer\" \"Extra savings\"\n"
                "  Assets:Bank:Savings          2000.00 USD\n"
                "  Assets:Bank:Checking        -2000.00 USD\n"
            )

        accounts_after = reader.get_accounts()
        savings_after = next(a for a in accounts_after if a.name == "Assets:Bank:Savings")
        savings_balance_after = sum(c.balance for c in savings_after.currencies)

        assert savings_balance_after == savings_balance_before + Decimal("2000.00")

    def test_missing_sqlite_triggers_export(self, tmp_path):
        """If the SQLite DB doesn't exist, the first read must create it
        and return correct data."""
        src = FIXTURES_DIR / "small_ledger.beancount"
        ledger_path = tmp_path / "main.beancount"
        shutil.copy2(src, ledger_path)
        db_path = tmp_path / "ledger.db"

        assert not db_path.exists()

        exporter = SQLiteExporter(str(db_path))
        reader = SqliteReader(
            sqlite_path=db_path,
            ledger_file=ledger_path,
            exporter=exporter,
        )

        accounts = reader.get_account_names()
        assert accounts == SMALL_LEDGER_ACCOUNTS
        assert db_path.exists()

    def test_legacy_db_without_new_tables_triggers_reexport(self, tmp_path):
        """A SQLite DB with only the postings table (from before the CQRS
        migration) must be detected and re-exported with all new tables."""
        src = FIXTURES_DIR / "small_ledger.beancount"
        ledger_path = tmp_path / "main.beancount"
        shutil.copy2(src, ledger_path)
        db_path = tmp_path / "ledger.db"

        # Create a legacy DB with only the postings table
        exporter = SQLiteExporter(str(db_path))
        entries, errors, options = loader.load_file(str(ledger_path))
        transactions = [e for e in entries if hasattr(e, 'postings')]
        exporter._export_transactions_to_sqlite(transactions)

        # Verify precondition: postings exists, accounts does NOT
        con = sqlite3.connect(str(db_path))
        assert con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='postings'"
        ).fetchone() is not None
        assert con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='accounts'"
        ).fetchone() is None
        con.close()

        # Make DB newer than ledger so mtime check alone wouldn't trigger
        os.utime(str(db_path), (time.time() + 1, time.time() + 1))

        # Read must detect missing tables and re-export
        reader = SqliteReader(
            sqlite_path=db_path,
            ledger_file=ledger_path,
            exporter=exporter,
        )
        accounts = reader.get_account_names()
        assert accounts == SMALL_LEDGER_ACCOUNTS


# ============================================================================
# 4. Date-Filtered Balance Queries
# ============================================================================


class TestDateFilteredBalances:
    """Date-filtered queries must apply different rules for different account types:
    - Income/Expenses: only count transactions within [start_date, end_date]
    - Assets/Liabilities: count all transactions up to end_date (cumulative)
    """

    def test_expense_balance_filtered_to_february(self, sqlite_reader_for_small):
        """Expenses:Food in Feb 2024 only:
        Feb 18: Grocery Store 110.25
        Feb 22: Restaurant 35.00
        Total: 145.25
        """
        from datetime import date
        reader = sqlite_reader_for_small
        filtered = reader.get_accounts_filtered(
            start_date=date(2024, 2, 1),
            end_date=date(2024, 2, 29),
        )

        food = next(a for a in filtered if a.name == "Expenses:Food")
        feb_balance = sum(c.balance for c in food.currencies)
        assert feb_balance == pytest.approx(145.25, abs=0.01)

    def test_asset_balance_is_cumulative_to_end_date(self, sqlite_reader_for_small):
        """Assets:Bank:Savings filtered to end_date=2024-02-29 should include
        both the Jan transfer (+500) and the Feb transfer (+500) = 500.
        Wait — only Jan has a transfer before Feb. Let me recheck:
        2024-02-01: +500 (savings transfer)
        That's the only one before end of Feb (Mar transfer is 2024-03-01).
        Actually: savings gets +500 on 2024-02-01 and +500 on 2024-03-01.
        With end_date=2024-02-29, cumulative = 500.
        """
        from datetime import date
        reader = sqlite_reader_for_small
        filtered = reader.get_accounts_filtered(
            start_date=date(2024, 2, 1),
            end_date=date(2024, 2, 29),
        )

        savings = next(a for a in filtered if a.name == "Assets:Bank:Savings")
        balance = sum(c.balance for c in savings.currencies)
        # Cumulative through Feb: only the Feb 1 transfer of 500
        assert balance == pytest.approx(500.0, abs=0.01)

    def test_income_only_in_period(self, sqlite_reader_for_small):
        """Income:Salary filtered to Feb only should show 1 salary (-4500),
        not the full 3 months."""
        from datetime import date
        reader = sqlite_reader_for_small
        filtered = reader.get_accounts_filtered(
            start_date=date(2024, 2, 1),
            end_date=date(2024, 2, 29),
        )

        salary = next(a for a in filtered if a.name == "Income:Salary")
        balance = sum(c.balance for c in salary.currencies)
        # 1 salary in Feb = -4500
        assert balance == pytest.approx(-4500.0, abs=0.01)

    def test_unfiltered_returns_all_time_balances(self, sqlite_reader_for_small):
        """Calling get_accounts_filtered with no dates returns the same
        as get_accounts (all-time balances)."""
        reader = sqlite_reader_for_small
        unfiltered = reader.get_accounts()
        also_unfiltered = reader.get_accounts_filtered()

        def balance_map(accounts):
            return {
                a.name: sum(c.balance for c in a.currencies)
                for a in accounts
                if a.currencies
            }

        assert balance_map(unfiltered) == balance_map(also_unfiltered)


# ── Export resilience: latent crash regressions ─────────────────────────────
#
# These tests guard against constraint-violation crashes in `_export_full_ledger`
# triggered by plausible user ledger states. Each crash would have rolled back
# the entire SQLite mirror, leaving the app unable to serve reads.


def _export_ledger_to_db(tmp_path, ledger_text):
    """Helper: write *ledger_text* to a temp .beancount, export to a fresh DB,
    return (db_path, errors)."""
    ledger_path = tmp_path / "main.beancount"
    ledger_path.write_text(ledger_text)
    db_path = tmp_path / "ledger.db"
    exporter = SQLiteExporter(str(db_path))
    entries, errors, options = loader.load_file(str(ledger_path))
    exporter._export_full_to_sqlite(entries, errors, options)
    return db_path, errors


class TestExportResilience:
    """Regressions for latent UNIQUE/PK constraint crashes in the exporter."""

    def test_multi_cost_lot_does_not_crash_export(self, tmp_path):
        """Two cost lots on the same security must export without crashing.

        Previously: ``account_balances`` PK was ``(account, currency)`` but the
        export emitted one row per Position from the realized Inventory, which
        keys positions by ``(currency, cost)``. Two lots → two rows with the
        same PK → ``UNIQUE constraint failed`` → whole transaction rolled back
        → empty SQLite mirror → every read 500'd.
        """
        ledger = """
2020-01-01 open Assets:Cash USD
2020-01-01 open Assets:Stocks:GOOG GOOG
2020-01-01 open Income:PnL USD
2020-01-01 open Equity:Opening-Balances

2020-01-01 * "Seed"
  Assets:Cash             100000 USD
  Equity:Opening-Balances

2024-01-01 * "Buy GOOG lot 1"
  Assets:Stocks:GOOG  100 GOOG {500 USD}
  Assets:Cash         -50000 USD

2024-02-01 * "Buy GOOG lot 2 at a different cost"
  Assets:Stocks:GOOG  50 GOOG {600 USD}
  Assets:Cash         -30000 USD

2024-03-01 * "Partial sell from lot 1"
  Assets:Stocks:GOOG  -80 GOOG {500 USD} @ 700 USD
  Assets:Cash          56000 USD
  Income:PnL          -16000 USD
"""
        db_path, errors = _export_ledger_to_db(tmp_path, ledger)
        assert not errors, errors

        con = sqlite3.connect(str(db_path))
        # Exactly one summary row per (account, currency).
        goog_rows = con.execute(
            "SELECT account, currency, balance FROM account_balances "
            "WHERE currency = 'GOOG'"
        ).fetchall()
        assert len(goog_rows) == 1
        assert goog_rows[0] == ("Assets:Stocks:GOOG", "GOOG", "70")

        # Lot-level detail is preserved in the lots table.
        lot_rows = con.execute(
            "SELECT units_number, cost_number FROM lots "
            "WHERE account = 'Assets:Stocks:GOOG' ORDER BY cost_number"
        ).fetchall()
        assert lot_rows == [("20", "500"), ("50", "600")]
        con.close()

    def test_duplicate_open_does_not_crash_export(self, tmp_path):
        """A ledger with duplicate ``open`` directives must export without
        crashing. Beancount reports duplicates as a parse error but keeps
        every Open entry in the result; the exporter has to collapse them
        because ``accounts.name`` is the primary key.
        """
        ledger = """
2020-01-01 open Assets:Cash USD
2021-01-01 open Assets:Cash USD
2020-01-01 open Equity:Opening-Balances

2020-01-02 * "First write"
  Assets:Cash              100 USD
  Equity:Opening-Balances
"""
        db_path, errors = _export_ledger_to_db(tmp_path, ledger)
        assert len(errors) >= 1  # duplicate-open parse error

        con = sqlite3.connect(str(db_path))
        # Exactly one row per account name; the duplicate must be collapsed.
        rows = con.execute(
            "SELECT name, open_date FROM accounts ORDER BY name"
        ).fetchall()
        assert rows == [
            ("Assets:Cash", "2020-01-01"),  # earliest open_date wins
            ("Equity:Opening-Balances", "2020-01-01"),
        ]

        # The parse error must still surface in ledger_errors so the user/AI
        # can diagnose the underlying ledger problem.
        err_count = con.execute(
            "SELECT COUNT(*) FROM ledger_errors"
        ).fetchone()[0]
        assert err_count >= 1
        con.close()

    def test_sub_export_failure_carries_step_name(self, tmp_path, monkeypatch):
        """When a sub-export raises, the wrapped error names which step failed
        so logs and downstream messages point at the right place.
        """
        ledger = """
2020-01-01 open Assets:Cash USD
"""
        ledger_path = tmp_path / "main.beancount"
        ledger_path.write_text(ledger)
        db_path = tmp_path / "ledger.db"
        exporter = SQLiteExporter(str(db_path))
        entries, errors, options = loader.load_file(str(ledger_path))

        # Force a failure inside the 'lots' sub-export.
        def _boom(*_a, **_k):
            raise RuntimeError("synthetic failure")
        monkeypatch.setattr(exporter, "_export_lots", _boom)

        with pytest.raises(Exception) as excinfo:
            exporter._export_full_to_sqlite(entries, errors, options)
        assert "lots" in str(excinfo.value)
        assert "synthetic failure" in str(excinfo.value)
