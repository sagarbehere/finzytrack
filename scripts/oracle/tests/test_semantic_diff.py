"""Semantic diff (review:D1 §3 step 2): per-file entry multisets keyed by printer output without location meta."""

from __future__ import annotations

import unittest

from helpers import OPENS, LedgerDir

import semantic_diff as sd

TXN = """
2020-01-02 * "Shop" "lunch"
  Expenses:Food  10.50 USD
  Assets:Cash
"""


class ParseSetTest(unittest.TestCase):
    def tearDown(self) -> None:
        self.d.close()

    def test_location_meta_is_not_part_of_the_key(self) -> None:
        self.d = LedgerDir({"a.beancount": OPENS + TXN, "b.beancount": "\n\n\n" + OPENS + "\n\n" + TXN})
        a = sd.parse_set(self.d.path, "a.beancount").files["a.beancount"]
        b = sd.parse_set(self.d.path, "b.beancount").files["b.beancount"]
        self.assertEqual(a.keys, b.keys)

    def test_duplicates_count_twice(self) -> None:
        self.d = LedgerDir({"main.beancount": OPENS + TXN + TXN})
        f = sd.parse_set(self.d.path, "main.beancount").files["main.beancount"]
        self.assertEqual(max(f.keys.values()), 2)

    def test_includes_and_globs_are_followed_per_file(self) -> None:
        self.d = LedgerDir({
            "main.beancount": 'include "accounts.beancount"\ninclude "years/*.beancount"\n',
            "accounts.beancount": OPENS,
            "years/2020.beancount": TXN,
            "years/2021.beancount": TXN.replace("2020-01-02", "2021-01-02"),
        })
        s = sd.parse_set(self.d.path, "main.beancount")
        self.assertEqual(set(s.files), {"main.beancount", "accounts.beancount", "years/2020.beancount", "years/2021.beancount"})
        self.assertEqual(s.glob_files, frozenset({"years/2020.beancount", "years/2021.beancount"}))
        self.assertEqual(sum(s.files["accounts.beancount"].keys.values()), 3)

    def test_parser_only_no_plugins_no_booking(self) -> None:
        self.d = LedgerDir({"main.beancount": 'plugin "beancount.plugins.implicit_prices"\n' + OPENS + """
2020-01-03 * "fx"
  Assets:Cash  -10 USD @ 2 EUR
  Expenses:Food
"""})
        f = sd.parse_set(self.d.path, "main.beancount").files["main.beancount"]
        self.assertFalse(any(" price " in k for k in f.keys))  # implicit_prices did not run
        self.assertTrue(any(k.rstrip().endswith("Expenses:Food") for k in f.keys))  # the elided amount stays elided

    def test_header_holds_options_plugins_includes(self) -> None:
        self.d = LedgerDir({"main.beancount": 'option "operating_currency" "USD"\nplugin "beancount.plugins.auto_accounts"\n' + OPENS})
        h = sd.parse_set(self.d.path, "main.beancount").files["main.beancount"].header
        self.assertIn(("option", "operating_currency", ("USD",)), h)
        self.assertIn(("plugin", "beancount.plugins.auto_accounts", None), h)

    def test_comment_lines_are_counted(self) -> None:
        self.d = LedgerDir({"main.beancount": "; one\n" + OPENS + "  ; two\n"})
        self.assertEqual(sd.parse_set(self.d.path, "main.beancount").files["main.beancount"].comment_lines, 2)


class DiffTest(unittest.TestCase):
    def setUp(self) -> None:
        self.d = LedgerDir({"main.beancount": 'include "sub.beancount"\n' + OPENS, "sub.beancount": TXN})

    def tearDown(self) -> None:
        self.d.close()

    def test_identical_sets_have_an_empty_diff(self) -> None:
        before = sd.parse_set(self.d.path, "main.beancount")
        self.assertTrue(sd.diff(before, sd.parse_set(self.d.path, "main.beancount")).is_empty())

    def test_reordering_and_reformatting_is_not_a_change(self) -> None:
        before = sd.parse_set(self.d.path, "main.beancount")
        self.d.write({"main.beancount": 'include "sub.beancount"\n' + "\n".join(reversed(OPENS.strip().splitlines())) + "\n"})
        self.assertTrue(sd.diff(before, sd.parse_set(self.d.path, "main.beancount")).is_empty())

    def test_a_move_between_files_is_a_remove_and_an_add(self) -> None:
        before = sd.parse_set(self.d.path, "main.beancount")
        self.d.write({"main.beancount": 'include "sub.beancount"\n' + OPENS + TXN, "sub.beancount": ""})
        d = sd.diff(before, sd.parse_set(self.d.path, "main.beancount"))
        self.assertEqual(sum(d.files["sub.beancount"].removed.values()), 1)
        self.assertEqual(sum(d.files["main.beancount"].added.values()), 1)
        self.assertEqual(d.files["sub.beancount"].removed, d.files["main.beancount"].added)

    def test_a_changed_amount_is_a_remove_and_an_add(self) -> None:
        before = sd.parse_set(self.d.path, "main.beancount")
        self.d.write({"sub.beancount": TXN.replace("10.50", "10.5")})  # the written scale is part of the entry
        d = sd.diff(before, sd.parse_set(self.d.path, "main.beancount"))
        self.assertEqual((sum(d.files["sub.beancount"].removed.values()), sum(d.files["sub.beancount"].added.values())), (1, 1))

    def test_header_change_and_lost_comments_are_reported(self) -> None:
        self.d.write({"main.beancount": "; keep me\n" + 'include "sub.beancount"\n' + OPENS})
        before = sd.parse_set(self.d.path, "main.beancount")
        self.d.write({"main.beancount": 'option "title" "x"\ninclude "sub.beancount"\n' + OPENS})
        d = sd.diff(before, sd.parse_set(self.d.path, "main.beancount"))
        self.assertIn("main.beancount", d.header_changed)
        self.assertEqual(d.comments_lost, {"main.beancount": 1})
        self.assertFalse(d.is_empty())

    def test_files_that_appear_or_vanish_count_wholesale(self) -> None:
        before = sd.parse_set(self.d.path, "main.beancount")
        self.d.write({"main.beancount": OPENS + TXN})
        d = sd.diff(before, sd.parse_set(self.d.path, "main.beancount"))
        self.assertEqual(sum(d.files["sub.beancount"].removed.values()), 1)


class GeneratedKeysTest(unittest.TestCase):
    def test_plugin_and_pad_entries_are_generated(self) -> None:
        d = LedgerDir({"main.beancount": 'plugin "beancount.plugins.implicit_prices"\n' + OPENS + """
2020-01-01 open Equity:Opening USD
2020-01-01 open Assets:Fx EUR
2020-01-02 pad Assets:Cash Equity:Opening
2020-01-03 balance Assets:Cash 100 USD
2020-01-04 * "fx"
  Assets:Fx  10 EUR @ 1.1 USD
  Assets:Cash
"""})
        try:
            gen = sd.generated_keys(d.path, "main.beancount")
        finally:
            d.close()
        self.assertTrue(any(" price EUR " in k for k in gen))
        self.assertTrue(any(k.startswith("2020-01-02 P ") for k in gen))
        self.assertFalse(any('"fx"' in k for k in gen))


if __name__ == "__main__":
    unittest.main()
