"""
Characterization + behaviour tests for POST /api/import/categorize.

Written BEFORE the categorize/dedup refactor to pin the endpoint's observable
behaviour (id correlation, engine dispatch, and — critically — duplicate
detection, which the new /api/ledger/categorize must NOT do). Re-run after the
refactor to prove import behaviour is preserved.
"""


def _categorize(client, transactions, source_account="Assets:Bank:Checking", currency="USD", force_engine=None):
    body = {"transactions": transactions, "source_account": source_account, "currency": currency}
    if force_engine:
        body["force_engine"] = force_engine
    resp = client.post("/api/import/categorize", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


class TestImportCategorize:
    def test_returns_a_result_per_transaction_keyed_by_id(self, test_client):
        data = _categorize(test_client, [
            {"id": "t1", "date": "2024-06-01", "payee": "Novel Vendor A", "narration": "", "amount": "-12.34"},
            {"id": "t2", "date": "2024-06-02", "payee": "Novel Vendor B", "narration": "", "amount": "-56.78"},
        ])
        results = {r["id"]: r for r in data["results"]}
        assert set(results.keys()) == {"t1", "t2"}
        assert data["stats"]["total_count"] == 2
        # Default config uses the classifier (small_ledger has ample training data).
        assert data["stats"]["engine_used"] in {"classifier", "default"}
        for r in results.values():
            assert isinstance(r["suggested_category"], str)

    def test_novel_transaction_is_not_flagged_duplicate(self, test_client):
        data = _categorize(test_client, [
            {"id": "t1", "date": "2024-06-01", "payee": "Totally Novel Vendor", "narration": "nope", "amount": "-999.99"},
        ])
        assert data["results"][0]["is_duplicate"] is False
        assert data["stats"]["duplicate_count"] == 0

    def test_dedup_runs_for_import_and_reports_fields(self, test_client):
        # Duplicate detection is part of the import flow: every result carries the
        # is_duplicate field and the batch reports a duplicate_count. (A positive
        # match is exercised in the dedup unit tests; here we pin that import runs
        # dedup at all — the /api/ledger/categorize path must NOT.)
        data = _categorize(test_client, [
            {"id": "t1", "date": "2024-06-01", "payee": "Vendor", "narration": "", "amount": "-10.00"},
        ])
        assert "is_duplicate" in data["results"][0]
        assert "duplicate_count" in data["stats"]

    def test_invalid_force_engine_is_rejected(self, test_client):
        resp = test_client.post("/api/import/categorize", json={
            "transactions": [{"id": "t1", "date": "2024-06-01", "payee": "X", "narration": "", "amount": "-1.00"}],
            "source_account": "Assets:Bank:Checking",
            "currency": "USD",
            "force_engine": "bogus",
        })
        assert resp.status_code == 422

    def test_ai_engine_requires_llm_config(self, test_client):
        # The default test config has no LLM configured → forcing AI is a 400.
        resp = test_client.post("/api/import/categorize", json={
            "transactions": [{"id": "t1", "date": "2024-06-01", "payee": "X", "narration": "", "amount": "-1.00"}],
            "source_account": "Assets:Bank:Checking",
            "currency": "USD",
            "force_engine": "ai",
        })
        assert resp.status_code == 400

    def test_passes_the_batch_source_account_uniformly_to_the_core(self, test_client):
        # Import sends one source_account for every transaction (one AI group).
        from unittest.mock import patch
        with patch("app.api.routers.importer.transaction.run_categorization") as mock_run:
            mock_run.return_value = ({"t1": ("Expenses:X", None), "t2": ("Expenses:Y", None)}, "ai", [], None)
            _categorize(test_client, [
                {"id": "t1", "date": "2024-06-01", "payee": "A", "narration": "", "amount": "-1.00"},
                {"id": "t2", "date": "2024-06-02", "payee": "B", "narration": "", "amount": "-2.00"},
            ], source_account="Assets:Bank:Checking")
        inputs = mock_run.call_args.kwargs["transactions"]
        assert [i.source_account for i in inputs] == ["Assets:Bank:Checking", "Assets:Bank:Checking"]
