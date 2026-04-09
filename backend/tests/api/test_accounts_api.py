"""
API tests for /api/accounts endpoints.

Tests the HTTP contract: correct status codes, ApiResponse envelope structure,
and business behavior based on double-entry accounting rules.
"""

import pytest


class TestListAccounts:
    """GET /api/accounts — list all accounts in the ledger."""

    def test_returns_success_envelope(self, test_client):
        resp = test_client.get("/api/accounts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["error"] is None
        assert "accounts" in body["data"]

    def test_returns_all_accounts_from_fixture(self, test_client):
        """The small_ledger fixture has exactly 9 accounts."""
        resp = test_client.get("/api/accounts")
        accounts = resp.json()["data"]["accounts"]
        names = {a["name"] for a in accounts}
        expected = {
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
        assert names == expected

    def test_account_has_required_fields(self, test_client):
        resp = test_client.get("/api/accounts")
        accounts = resp.json()["data"]["accounts"]
        assert len(accounts) > 0
        account = accounts[0]
        assert "name" in account
        assert "open_date" in account
        assert "currencies" in account
        assert "metadata" in account

    def test_filters_by_date_range(self, test_client):
        """Date filtering should affect reported balances.

        The small_ledger has transactions in Jan, Feb, and Mar 2024.
        Filtering to Feb only should produce different balance figures
        for income/expense accounts than an unfiltered request.
        """
        unfiltered = test_client.get("/api/accounts")
        filtered = test_client.get(
            "/api/accounts",
            params={"start_date": "2024-02-01", "end_date": "2024-02-28"},
        )
        assert filtered.status_code == 200
        assert filtered.json()["success"] is True

        # Expenses:Food has transactions in Jan, Feb, and Mar.
        # A Feb-only filter should show a smaller balance than unfiltered.
        def get_food_balance(resp):
            accounts = resp.json()["data"]["accounts"]
            food = next(a for a in accounts if a["name"] == "Expenses:Food")
            return sum(c["balance"] for c in food["currencies"])

        full_balance = get_food_balance(unfiltered)
        feb_balance = get_food_balance(filtered)
        assert feb_balance < full_balance


class TestCreateAccount:
    """POST /api/accounts — create a new account."""

    def test_create_returns_201(self, test_client):
        resp = test_client.post(
            "/api/accounts",
            json={
                "name": "Assets:Bank:Investment",
                "open_date": "2024-01-01",
                "currencies": ["USD"],
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["account_created"] is True

    def test_created_account_appears_in_list(self, test_client):
        """After creating an account, it must appear in GET /accounts."""
        test_client.post(
            "/api/accounts",
            json={
                "name": "Expenses:Subscriptions",
                "open_date": "2024-06-01",
                "currencies": ["USD"],
            },
        )
        resp = test_client.get("/api/accounts")
        names = {a["name"] for a in resp.json()["data"]["accounts"]}
        assert "Expenses:Subscriptions" in names

    def test_duplicate_account_returns_error(self, test_client):
        """Creating an account that already exists should fail."""
        resp = test_client.post(
            "/api/accounts",
            json={
                "name": "Assets:Bank:Checking",
                "open_date": "2024-01-01",
                "currencies": ["USD"],
            },
        )
        assert resp.status_code != 201
        body = resp.json()
        assert body["success"] is False
        assert body["error"] is not None
        assert body["error"]["code"] is not None

    def test_invalid_account_format_returns_error(self, test_client):
        """Account names must start with a valid type prefix."""
        resp = test_client.post(
            "/api/accounts",
            json={
                "name": "InvalidPrefix:Something",
                "open_date": "2024-01-01",
                "currencies": ["USD"],
            },
        )
        assert resp.status_code != 201
        body = resp.json()
        assert body["success"] is False

    def test_create_with_metadata(self, test_client):
        resp = test_client.post(
            "/api/accounts",
            json={
                "name": "Assets:Bank:HighYield",
                "open_date": "2024-01-01",
                "currencies": ["USD"],
                "description": "High yield savings",
                "metadata": {"institution": "BankCo"},
            },
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["account_created"] is True

        # Verify metadata was actually persisted
        list_resp = test_client.get("/api/accounts")
        acct = next(
            a for a in list_resp.json()["data"]["accounts"]
            if a["name"] == "Assets:Bank:HighYield"
        )
        assert acct["metadata"].get("description") == "High yield savings"
        assert acct["metadata"].get("institution") == "BankCo"


class TestUpdateAccount:
    """PUT /api/accounts/{account_name} — update account properties."""

    def test_rename_account(self, test_client):
        resp = test_client.put(
            "/api/accounts/Expenses:Unknown",
            json={"new_name": "Expenses:Miscellaneous"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["account_updated"] is True

        # Old name gone, new name present
        list_resp = test_client.get("/api/accounts")
        names = {a["name"] for a in list_resp.json()["data"]["accounts"]}
        assert "Expenses:Miscellaneous" in names
        assert "Expenses:Unknown" not in names

    def test_update_nonexistent_account_returns_error(self, test_client):
        resp = test_client.put(
            "/api/accounts/Expenses:DoesNotExist",
            json={"new_name": "Expenses:StillDoesNotExist"},
        )
        assert resp.status_code != 200
        assert resp.json()["success"] is False


class TestCloseAccount:
    """POST /api/accounts/{account_name}/close"""

    def test_close_account(self, test_client):
        resp = test_client.post(
            "/api/accounts/Expenses:Unknown/close",
            json={"close_date": "2024-12-31"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["account_closed"] is True

        # Verify the account actually reports a close_date
        list_resp = test_client.get("/api/accounts")
        acct = next(
            a for a in list_resp.json()["data"]["accounts"]
            if a["name"] == "Expenses:Unknown"
        )
        assert acct["close_date"] == "2024-12-31"

    def test_close_nonexistent_returns_error(self, test_client):
        resp = test_client.post(
            "/api/accounts/Expenses:Ghost/close",
            json={"close_date": "2024-12-31"},
        )
        assert resp.status_code != 200
        assert resp.json()["success"] is False


class TestReopenAccount:
    """POST /api/accounts/{account_name}/reopen"""

    def test_close_then_reopen(self, test_client):
        # Close first
        test_client.post(
            "/api/accounts/Expenses:Unknown/close",
            json={"close_date": "2024-12-31"},
        )
        # Reopen
        resp = test_client.post("/api/accounts/Expenses:Unknown/reopen")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["account_reopened"] is True

        # Verify close_date is actually gone
        list_resp = test_client.get("/api/accounts")
        acct = next(
            a for a in list_resp.json()["data"]["accounts"]
            if a["name"] == "Expenses:Unknown"
        )
        assert acct["close_date"] is None


class TestDeleteAccount:
    """DELETE /api/accounts/{account_name}"""

    def test_delete_account_with_no_transactions(self, test_client):
        """Deleting an unused account should succeed."""
        # Create a fresh account with no transactions
        test_client.post(
            "/api/accounts",
            json={
                "name": "Expenses:Temp",
                "open_date": "2024-01-01",
                "currencies": ["USD"],
            },
        )
        resp = test_client.delete("/api/accounts/Expenses:Temp")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["account_deleted"] is True

        # Verify it's gone
        list_resp = test_client.get("/api/accounts")
        names = {a["name"] for a in list_resp.json()["data"]["accounts"]}
        assert "Expenses:Temp" not in names

    def test_delete_account_with_transactions(self, test_client):
        """Deleting an account that has transactions (with delete_transactions=true)."""
        resp = test_client.delete(
            "/api/accounts/Expenses:Food",
            params={"delete_transactions": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

        # Account should be gone
        list_resp = test_client.get("/api/accounts")
        names = {a["name"] for a in list_resp.json()["data"]["accounts"]}
        assert "Expenses:Food" not in names


class TestBalanceDirectives:
    """Balance directive CRUD on /api/accounts/{name}/balance-directives"""

    def test_list_balance_directives(self, test_client):
        resp = test_client.get("/api/accounts/Assets:Bank:Checking/balance-directives")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "directives" in body["data"]
        assert body["data"]["account"] == "Assets:Bank:Checking"

    def test_create_balance_directive(self, test_client):
        resp = test_client.post(
            "/api/accounts/Assets:Bank:Checking/balance-directives",
            json={
                "date": "2024-04-01",
                "currency": "USD",
                "amount": 10000.00,
                "include_pad": False,
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["created"] is True

    def test_created_directive_appears_in_list(self, test_client):
        test_client.post(
            "/api/accounts/Assets:Bank:Checking/balance-directives",
            json={
                "date": "2024-04-15",
                "currency": "USD",
                "amount": 9500.00,
                "include_pad": False,
            },
        )
        resp = test_client.get("/api/accounts/Assets:Bank:Checking/balance-directives")
        directives = resp.json()["data"]["directives"]
        dates = [d["date"] for d in directives]
        assert "2024-04-15" in dates

    def test_delete_balance_directive(self, test_client):
        # Create one first
        test_client.post(
            "/api/accounts/Assets:Bank:Checking/balance-directives",
            json={
                "date": "2024-05-01",
                "currency": "USD",
                "amount": 8000.00,
                "include_pad": False,
            },
        )
        # Delete it
        resp = test_client.delete(
            "/api/accounts/Assets:Bank:Checking/balance-directives",
            params={
                "date": "2024-05-01",
                "currency": "USD",
                "amount": 8000.00,
                "delete_pad": True,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # Verify it's actually gone
        list_resp = test_client.get("/api/accounts/Assets:Bank:Checking/balance-directives")
        dates = [d["date"] for d in list_resp.json()["data"]["directives"]]
        assert "2024-05-01" not in dates


class TestEdgeCases:
    """Test with the edge_cases ledger fixture."""

    def test_special_characters_in_accounts(self, edge_case_client):
        """Accounts from the edge_cases fixture should all load correctly."""
        resp = edge_case_client.get("/api/accounts")
        assert resp.status_code == 200
        names = {a["name"] for a in resp.json()["data"]["accounts"]}
        assert "Expenses:Travel:Flights:Domestic:Economy" in names

    def test_multi_currency_accounts(self, edge_case_client):
        """Accounts with multiple currencies should report all currencies."""
        resp = edge_case_client.get("/api/accounts")
        accounts = resp.json()["data"]["accounts"]
        euro_acct = next(a for a in accounts if a["name"] == "Assets:Bank:Euro")
        currencies = {c["currency"] for c in euro_acct["currencies"]}
        assert "EUR" in currencies

    def test_balance_directives_on_edge_cases(self, edge_case_client):
        resp = edge_case_client.get(
            "/api/accounts/Assets:Bank:Checking/balance-directives"
        )
        assert resp.status_code == 200
        directives = resp.json()["data"]["directives"]
        # The edge_cases fixture has balance assertions for Checking
        assert len(directives) >= 1
