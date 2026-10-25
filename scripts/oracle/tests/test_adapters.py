"""Adapters build the payload the v0.3 frontend sent (review:C DEP-11)."""

from __future__ import annotations

import unittest

from helpers import OPENS, LedgerDir

import adapters
import semantic_diff as sd

SRC = '2020-01-02 * "Shop" "lunch"\n  id: "t-1"\n  Expenses:Food  10.50 USD\n  Assets:Cash\n'


class AdapterTest(unittest.TestCase):
    def test_update_fills_elided_amounts_from_the_booked_entry(self) -> None:
        d = LedgerDir({"main.beancount": OPENS + SRC})
        try:
            booked = sd.load_facts(d.path, "main.beancount").booked_by_id
        finally:
            d.close()
        req = adapters.update_transaction({"txn_id": "t-1", "entry": SRC.replace("lunch", "dinner")}, booked)
        (body,) = req.body["transactions"]
        self.assertEqual([(p["account"], p["amount"], p["currency"]) for p in body["postings"]],
                         [("Expenses:Food", "10.50", "USD"), ("Assets:Cash", "-10.50", "USD")])
        self.assertEqual((req.method, req.path, body["id"], body["narration"]), ("PUT", "/api/ledger/transactions", "t-1", "dinner"))

    def test_metadata_travels_as_strings_and_memo_separately(self) -> None:
        text = SRC.replace('  id: "t-1"\n', '  id: "t-1"\n  rate: 1.50\n  memo: "m"\n')
        (body,) = adapters.update_transaction({"txn_id": "t-1", "entry": text}, {}).body["transactions"]
        self.assertEqual((body["meta"], body["memo"]), ({"id": "t-1", "rate": "1.50"}, "m"))

    def test_a_total_price_is_sent_as_the_frontend_sent_it(self) -> None:
        text = '2024-03-01 * "B" "buy"\n  Assets:Broker  7 TOK @@ 10.00 USD\n  Assets:Cash  -10.00 USD\n'
        (body,) = adapters.import_append({"entries": [text]}, {}).body["transactions"]
        p = body["postings"][0]
        self.assertEqual((p["price_amount"], p["price_currency"], p["price_type"]), ("10.00", "USD", "@@"))

    def test_rename_quotes_the_account_in_the_path(self) -> None:
        req = adapters.rename_account({"old": "Expenses:Rent", "new": "Expenses:Housing"}, {})
        self.assertEqual((req.method, req.path, req.body), ("PUT", "/api/accounts/Expenses%3ARent", {"new_name": "Expenses:Housing"}))

    def test_import_sends_the_anchor_as_source_account(self) -> None:
        text = '2020-03-01 * "Cafe" "coffee"\n  Expenses:Food  3.00 USD\n  Assets:Cash  -3.00 USD\n    import-key: "v1:h:a"\n'
        (body,) = adapters.import_append({"entries": [text]}, {}).body["transactions"]
        self.assertEqual(body["source_account"], "Assets:Cash")

    def test_import_refuses_elided_amounts(self) -> None:
        with self.assertRaises(ValueError):
            adapters.import_append({"entries": [SRC]}, {})


if __name__ == "__main__":
    unittest.main()
