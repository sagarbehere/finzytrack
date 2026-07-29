"""
Tests for POST /api/ledger/categorize (categorize existing ledger transactions).

Key differences from import categorize: per-transaction source_account, and NO
duplicate detection.
"""


def _categorize_existing(client, transactions, force_engine=None):
    body = {"transactions": transactions}
    if force_engine:
        body["force_engine"] = force_engine
    resp = client.post("/api/ledger/categorize", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


class TestCategorizeExisting:
    def test_returns_a_suggestion_per_transaction_without_dedup(self, test_client):
        data = _categorize_existing(test_client, [
            {"id": "a", "payee": "Grocery Store", "narration": "Weekly groceries", "source_account": "Assets:Bank:Checking"},
            {"id": "b", "payee": "Gas Station", "narration": "Fuel", "source_account": "Liabilities:CreditCard"},
        ])
        results = {r["id"]: r for r in data["results"]}
        assert set(results.keys()) == {"a", "b"}
        assert data["stats"]["total_count"] == 2
        # No duplicate detection on existing transactions.
        assert data["stats"]["duplicate_count"] == 0
        for r in results.values():
            assert isinstance(r["suggested_category"], str)
            assert "is_duplicate" not in r  # field does not exist on this response

    def test_accepts_heterogeneous_per_transaction_source_accounts(self, test_client):
        # Different source accounts per row must be accepted (the import endpoint
        # only takes one batch-level source).
        data = _categorize_existing(test_client, [
            {"id": "a", "payee": "A", "narration": "", "source_account": "Assets:Bank:Checking"},
            {"id": "b", "payee": "B", "narration": "", "source_account": "Assets:Bank:Savings"},
            {"id": "c", "payee": "C", "narration": "", "source_account": "Liabilities:CreditCard"},
        ])
        assert {r["id"] for r in data["results"]} == {"a", "b", "c"}

    def test_force_engine_classifier(self, test_client):
        data = _categorize_existing(test_client, [
            {"id": "a", "payee": "Grocery Store", "narration": "", "source_account": "Assets:Bank:Checking"},
        ], force_engine="classifier")
        assert data["stats"]["engine_used"] in {"classifier", "default"}

    def test_invalid_force_engine_is_rejected(self, test_client):
        resp = test_client.post("/api/ledger/categorize", json={
            "transactions": [{"id": "a", "payee": "X", "narration": "", "source_account": "Assets:Bank:Checking"}],
            "force_engine": "bogus",
        })
        assert resp.status_code == 422

    def test_missing_source_account_is_rejected(self, test_client):
        resp = test_client.post("/api/ledger/categorize", json={
            "transactions": [{"id": "a", "payee": "X", "narration": ""}],
        })
        assert resp.status_code == 422

    def test_ai_engine_requires_llm_config(self, test_client):
        resp = test_client.post("/api/ledger/categorize", json={
            "transactions": [{"id": "a", "payee": "X", "narration": "", "source_account": "Assets:Bank:Checking"}],
            "force_engine": "ai",
        })
        assert resp.status_code == 400

    def test_passes_per_transaction_source_accounts_to_the_core_and_maps_results(self, test_client):
        from unittest.mock import patch
        with patch("app.api.routers.ledger_transactions.run_categorization") as mock_run:
            mock_run.return_value = ({"a": ("Expenses:X", 0.9), "b": ("Expenses:Y", 0.8)}, "classifier", [], None)
            data = _categorize_existing(test_client, [
                {"id": "a", "payee": "A", "narration": "", "source_account": "Assets:Bank:Checking"},
                {"id": "b", "payee": "B", "narration": "", "source_account": "Liabilities:CreditCard"},
            ], force_engine="classifier")
        inputs = mock_run.call_args.kwargs["transactions"]
        assert [(i.id, i.source_account) for i in inputs] == [
            ("a", "Assets:Bank:Checking"), ("b", "Liabilities:CreditCard"),
        ]
        results = {r["id"]: r for r in data["results"]}
        assert results["a"]["suggested_category"] == "Expenses:X"
        assert results["a"]["confidence"] == 0.9
