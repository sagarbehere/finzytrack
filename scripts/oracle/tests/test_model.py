"""Expected-change models for the three S9 mutators (provisional; the intent table is the authority)."""

from __future__ import annotations

import unittest

from helpers import OPENS, LedgerDir

import model
import semantic_diff as sd

LEDGER = {
    "main.beancount": 'include "txns.beancount"\n' + OPENS + """
2020-01-01 open Expenses:Food:Snacks USD
2021-01-01 close Expenses:Rent
2020-06-01 balance Assets:Cash -100 USD
2020-01-01 custom "budget" Expenses:Rent "monthly" 900 USD
2020-02-01 note Expenses:Rent "landlord changed"
""",
    "txns.beancount": """
2020-01-02 * "Shop" "lunch"
  id: "t-1"
  Expenses:Food  10.50 USD
  Assets:Cash
2020-01-05 * "Landlord" "rent"
  id: "t-2"
  Expenses:Rent  90 USD
  Assets:Cash   -90 USD
""",
}


def total(counter) -> int:
    return sum(counter.values())


class ModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.d = LedgerDir(LEDGER)
        self.before = sd.parse_set(self.d.path, "main.beancount")

    def tearDown(self) -> None:
        self.d.close()

    def test_update_replaces_the_one_entry_in_its_own_file(self) -> None:
        new = '2020-01-02 * "Shop" "dinner"\n  id: "t-1"\n  Expenses:Food  12.00 USD\n  Assets:Cash\n'
        e = model.update_transaction(self.before, "t-1", new)
        self.assertEqual(set(e.files), {"txns.beancount"})
        self.assertEqual((total(e.files["txns.beancount"].removed), total(e.files["txns.beancount"].added)), (1, 1))
        self.assertIn('"dinner"', next(iter(e.files["txns.beancount"].added)))

    def test_update_of_an_entry_without_a_legacy_id_is_non_comparable(self) -> None:
        with self.assertRaises(model.NonComparable):
            model.update_transaction(self.before, "no-such-id", '2020-01-02 * "x"\n  Assets:Cash 1 USD\n  Expenses:Food\n')

    def test_rename_rewrites_every_reference_of_a_leaf_account(self) -> None:
        e = model.rename_account(self.before, "Expenses:Rent", "Expenses:Housing")
        removed = [k for f in e.files.values() for k in f.removed]
        added = [k for f in e.files.values() for k in f.added]
        # open, close, budget, note in main; the rent transaction in txns
        self.assertEqual(len(removed), 5)
        self.assertEqual(len(added), 5)
        self.assertFalse(any("Expenses:Rent" in k for k in added))
        self.assertEqual(total(e.files["txns.beancount"].added), 1)

    def test_rename_follows_custom_strings_and_metadata_values(self) -> None:
        self.d.write({"main.beancount": (self.d.path / "main.beancount").read_text()
                      + '2020-01-01 custom "fava-sidebar-link" "Rent" "Expenses:Rent"\n'
                      + '2020-01-01 open Assets:Deposit USD\n  interest-account: "Expenses:Rent"\n  memo: "Expenses:Rent is old"\n'})
        before = sd.parse_set(self.d.path, "main.beancount")
        added = [k for f in model.rename_account(before, "Expenses:Rent", "Expenses:Housing").files.values() for k in f.added]
        self.assertTrue(any('"Expenses:Housing"' in k and "sidebar" in k for k in added))
        self.assertTrue(any('interest-account: "Expenses:Housing"' in k and '"Expenses:Rent is old"' in k for k in added))

    def test_rename_renames_the_subtree(self) -> None:
        e = model.rename_account(self.before, "Expenses:Food", "Expenses:Meals")
        added = " ".join(k for f in e.files.values() for k in f.added)
        self.assertIn("Expenses:Meals:Snacks", added)
        self.assertIn("Expenses:Meals ", added)
        self.assertNotIn("Expenses:Food", added)

    def test_rename_onto_an_existing_subaccount_name_is_non_comparable(self) -> None:
        with self.assertRaises(model.NonComparable):
            model.rename_account(self.before, "Expenses:Food:Snacks", "Expenses:Rent")

    def test_rename_onto_an_existing_account_is_non_comparable(self) -> None:
        with self.assertRaises(model.NonComparable):  # that is a merge, its own mutator (ADR-0049)
            model.rename_account(self.before, "Expenses:Rent", "Expenses:Food")

    def test_import_append_adds_to_the_root_file(self) -> None:
        new = '2020-03-01 * "Cafe" "coffee"\n  Expenses:Food  3.00 USD\n    import-key: "v1:h:abc"\n  Assets:Cash\n'
        e = model.import_append(self.before, [new])
        self.assertEqual(set(e.files), {"main.beancount"})
        self.assertEqual(total(e.files["main.beancount"].added), 1)
        self.assertEqual(total(e.files["main.beancount"].removed), 0)


if __name__ == "__main__":
    unittest.main()
