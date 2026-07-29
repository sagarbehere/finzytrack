"""
Unit tests for the shared categorization core (app.services.categorization).

The AI and classifier services are mocked so the dispatch/grouping logic is
tested deterministically — this is the logic the import path is refactored onto,
so it must be behaviour-preserving (single source_account → one AI group).
"""

from unittest.mock import patch

from app.config import CategorizationEngine
from app.services.categorization import CategorizationInput, run_categorization


def _inputs(specs):
    # specs: list of (id, payee, source_account)
    return [CategorizationInput(id=i, payee=p, memo=None, narration="", source_account=s) for i, p, s in specs]


class TestAIEngine:
    def test_single_source_makes_one_ai_call(self):
        """Import case: all txns share a source → exactly one AI batch call."""
        txns = _inputs([("a", "Blue Bottle", "Assets:Bank"), ("b", "Shell", "Assets:Bank")])

        with patch("app.services.categorization.categorize_transactions_ai") as mock_ai:
            mock_ai.return_value = ({"a": "Expenses:Coffee", "b": "Expenses:Fuel"}, [])
            cat_map, engine_used, warnings, ml = run_categorization(
                txns, CategorizationEngine.AI, {"Expenses:Coffee", "Expenses:Fuel"}, [], "Expenses:Unknown", llm_config=object()
            )

        assert mock_ai.call_count == 1
        assert mock_ai.call_args.kwargs["source_account"] == "Assets:Bank"
        assert cat_map == {"a": ("Expenses:Coffee", None), "b": ("Expenses:Fuel", None)}
        assert engine_used == "ai"
        assert ml is None

    def test_groups_by_source_account(self):
        """Existing case: heterogeneous sources → one AI call per source group."""
        txns = _inputs([
            ("a", "Blue Bottle", "Assets:Bank"),
            ("b", "Shell", "Liabilities:CC"),
            ("c", "Peets", "Assets:Bank"),
        ])
        calls = {}

        def fake_ai(*, transactions, account_names, default_account, source_account, llm_config):
            calls[source_account] = [t["id"] for t in transactions]
            return ({t["id"]: "Expenses:X" for t in transactions}, [])

        with patch("app.services.categorization.categorize_transactions_ai", side_effect=fake_ai):
            cat_map, engine_used, warnings, ml = run_categorization(
                txns, CategorizationEngine.AI, {"Expenses:X"}, [], "Expenses:Unknown", llm_config=object()
            )

        assert calls == {"Assets:Bank": ["a", "c"], "Liabilities:CC": ["b"]}
        assert set(cat_map.keys()) == {"a", "b", "c"}
        assert engine_used == "ai"

    def test_missing_ai_result_falls_back_to_default(self):
        txns = _inputs([("a", "Mystery", "Assets:Bank")])
        with patch("app.services.categorization.categorize_transactions_ai", return_value=({}, ["warn"])):
            cat_map, engine_used, warnings, ml = run_categorization(
                txns, CategorizationEngine.AI, set(), [], "Expenses:Unknown", llm_config=object()
            )
        assert cat_map == {"a": ("Expenses:Unknown", None)}
        assert warnings == ["warn"]


class TestClassifierEngine:
    def test_classifier_maps_each_txn_with_confidence(self):
        txns = _inputs([("a", "Blue Bottle", "Assets:Bank"), ("b", "Shell", "Assets:Bank")])
        sentinel = object()

        def fake_categorize(description, classifier, default_account):
            return ("Expenses:Coffee", 0.9) if "Blue" in description else ("Expenses:Fuel", 0.7)

        with patch("app.services.categorization.initialize_classifier", return_value=(sentinel, None)), \
             patch("app.services.categorization.categorize_transaction", side_effect=fake_categorize):
            cat_map, engine_used, warnings, ml = run_categorization(
                txns, CategorizationEngine.CLASSIFIER, {"Expenses:Coffee", "Expenses:Fuel"}, [("x", "y")] * 10, "Expenses:Unknown", llm_config=object()
            )

        assert cat_map == {"a": ("Expenses:Coffee", 0.9), "b": ("Expenses:Fuel", 0.7)}
        assert engine_used == "classifier"
        assert ml is None

    def test_builds_description_from_payee_memo_narration(self):
        txns = [CategorizationInput(id="a", payee="P", memo="M", narration="N", source_account="Assets:Bank")]
        captured = {}

        def fake_categorize(description, classifier, default_account):
            captured["desc"] = description
            return ("Expenses:X", 0.5)

        with patch("app.services.categorization.initialize_classifier", return_value=(object(), None)), \
             patch("app.services.categorization.categorize_transaction", side_effect=fake_categorize):
            run_categorization(txns, CategorizationEngine.CLASSIFIER, set(), [], "Expenses:Unknown", llm_config=object())

        assert captured["desc"] == "P M N"

    def test_insufficient_training_data_uses_default_and_warns(self):
        txns = _inputs([("a", "Blue Bottle", "Assets:Bank")])
        with patch("app.services.categorization.initialize_classifier", return_value=(None, "not enough data")):
            cat_map, engine_used, warnings, ml = run_categorization(
                txns, CategorizationEngine.CLASSIFIER, set(), [], "Expenses:Unknown", llm_config=object()
            )
        assert cat_map == {"a": ("Expenses:Unknown", None)}
        assert engine_used == "default"
        assert warnings == ["not enough data"]
        assert ml == "not enough data"
