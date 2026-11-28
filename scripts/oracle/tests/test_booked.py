"""The oracle's booked comparison (BL-0275, ADR-0014, INV-02): the intended ledger booked by Beancount against the
legacy's result booked by Beancount. Each must-not-pass shape is one the parsed comparison cannot see (every
parsed entry stays equal); each must-pass shape has a must-not-pass twin, so every test fails without the check."""

from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

from helpers import LedgerDir

from beancount import loader
from beancount.parser import printer

import classify
import harness
import model
import semantic_diff as sd

REPO = Path(__file__).resolve().parents[3]

OPENS = ('2020-01-01 open Assets:Cash USD\n2020-01-01 open Expenses:Food USD\n'
         '2020-01-01 open Assets:Broker "FIFO"\n2020-01-01 open Income:Gain USD\n')
T1 = '\n2020-01-02 * "Shop" "lunch"\n  id: "t-1"\n  Expenses:Food  10.50 USD\n  Assets:Cash   -10.50 USD\n'
T1_EDITED = T1.replace('"lunch"', '"dinner"')
SELL = '\n2020-03-01 * "sell"\n  Assets:Broker  -1 TOK {}\n  Assets:Cash  30 USD\n  Income:Gain\n'


def buy(label: str, cost: str, extra: str = "") -> str:
    return f'\n2020-02-01 * "buy {label}"\n{extra}  Assets:Broker  1 TOK {{{cost}}}\n  Assets:Cash  -{cost.split()[0]} USD\n'


def update_t1(before: sd.LedgerSet) -> model.Expected:
    return model.update_transaction(before, "t-1", T1_EDITED.strip("\n") + "\n")


class Case:
    """A source ledger, the legacy's result in its own folder (as the harness has it: the source is only read), and
    the comparison."""

    def __init__(self, before: dict[str, str], after: dict[str, str], expect) -> None:
        src, copy = LedgerDir(before), LedgerDir(before)
        try:
            copy.write(after)
            self.before = sd.parse_set(src.path, "main.beancount")
            self.before_facts = sd.load_facts(src.path, "main.beancount")
            self.expected = expect(self.before)
            after_set = sd.parse_set(copy.path, "main.beancount")
            after_facts = sd.load_facts(copy.path, "main.beancount")
            self.report = classify.compare(self.before, after_set, self.expected, self.before_facts, after_facts)
        finally:
            src.close()
            copy.close()

    def booked(self) -> list[str]:
        return sorted(d.check for d in self.report.divergences if d.check != "parsed")

    def __repr__(self) -> str:
        return repr([(d.cls, d.check, d.note) for d in self.report.divergences])


def gain(files: dict[str, str]) -> str:
    d = LedgerDir(files)
    try:
        entries, _, _ = loader.load_file(str(d.path / "main.beancount"))
    finally:
        d.close()
    sell = next(e for e in entries if getattr(e, "narration", "") == "sell")
    return str(next(p.units for p in sell.postings if p.account == "Income:Gain"))


class MustNotPassTest(unittest.TestCase):
    def test_the_s0122_tie_repro_a_layout_reprint_flips_a_cross_file_fifo_tie(self) -> None:
        # S0122 verifier F1: blank lines removed in main.beancount move "buy A" above "buy B" (same date, other file)
        # in Beancount's order, so FIFO reduces the other lot and the gain goes from -10 to -20 USD.
        sub = "; pad\n" * 14 + '2020-02-01 * "buy B"\n  Assets:Broker  1 TOK {20 USD}\n  Assets:Cash  -20 USD\n'
        pad = "\n" * 8
        main = ('include "sub.beancount"\n' + OPENS + T1 + pad
                + '2020-02-01 * "buy A"\n  Assets:Broker  1 TOK {10 USD}\n  Assets:Cash  -10 USD\n' + SELL)
        intended = {"main.beancount": main.replace(T1, T1_EDITED), "sub.beancount": sub}
        reprint = {"main.beancount": main.replace(T1, T1_EDITED).replace(pad, "\n"), "sub.beancount": sub}
        self.assertEqual((gain(intended), gain(reprint)), ("-10 USD", "-20 USD"))  # the repro still holds
        before = {"main.beancount": main, "sub.beancount": sub}
        self.assertEqual(Case(before, intended, update_t1).report.verdict, "MATCH")
        c = Case(before, reprint, update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        self.assertEqual(c.booked(), ["balance", "balance", "booked"], c)  # the sale; the lot held, the gain
        notes = [d.note for d in c.report.divergences]
        self.assertTrue(any("took another lot" in n or "other amounts" in n for n in notes), c)

    def test_an_untargeted_sale_whose_lot_changes_with_the_same_money(self) -> None:
        # Two same-date lots at one cost, labelled; the legacy writes them in the other order, FIFO takes the other.
        a, b = buy("a", '10 USD, "a"'), buy("b", '10 USD, "b"')
        sell = SELL.replace("30 USD\n  Income:Gain\n", "10 USD\n")
        before = {"main.beancount": OPENS + T1 + a + b + sell}
        c = Case(before, {"main.beancount": OPENS + T1_EDITED + b + a + sell}, update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        self.assertEqual(c.booked(), ["balance", "booked"], c)  # the sale, and the lot left (label "a", not "b")
        self.assertIn("took another lot", next(d.note for d in c.report.divergences if d.check == "booked"))
        self.assertIn('"a"', next(d.note for d in c.report.divergences if d.check == "balance"))
        self.assertEqual(Case(before, {"main.beancount": OPENS + T1_EDITED + a + b + sell}, update_t1).report.verdict,
                         "MATCH")

    def test_an_end_balance_that_changes_with_no_entry_the_parsed_comparison_flags(self) -> None:
        a, b = buy("a", "10 USD"), buy("b", "20 USD")
        before = {"main.beancount": OPENS + T1 + a + b + SELL}
        c = Case(before, {"main.beancount": OPENS + T1_EDITED + b + a + SELL}, update_t1)
        self.assertEqual([d.cls for d in c.report.divergences if d.check == "parsed"], [], c)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        balances = sorted(d.note for d in c.report.divergences if d.check == "balance")
        self.assertEqual(balances, ["balance: Assets:Broker TOK: 1 {20 USD, 2020-02-01} -> 1 {10 USD, 2020-02-01}",
                                    "balance: Income:Gain USD: -20 -> -10"], c)
        note = next(d.note for d in c.report.divergences if d.check == "booked")
        self.assertIn("took another lot and booked other amounts", note)  # the gain is elided, the lot named


class MustPassTest(unittest.TestCase):
    def test_an_edit_of_an_earlier_buys_cost_changes_a_later_sales_booked_cost(self) -> None:
        a = buy("a", "10 USD", '  id: "t-a"\n')
        b = buy("b", "20 USD")
        a12 = a.replace("{10 USD}", "{12 USD}").replace("-10 USD", "-12 USD")

        def edit(before: sd.LedgerSet) -> model.Expected:
            return model.update_transaction(before, "t-a", a12.strip("\n") + "\n")
        before = {"main.beancount": OPENS + a + b + SELL}
        self.assertEqual((gain(before), gain({"main.beancount": OPENS + a12 + b + SELL})), ("-20 USD", "-18 USD"))
        c = Case(before, {"main.beancount": OPENS + a12 + b + SELL}, edit)
        self.assertEqual(c.report.verdict, "MATCH", c)
        # Its twin: the edit written, and the two buys in the other order, so the sale takes the other lot.
        c = Case(before, {"main.beancount": OPENS + b + a12 + SELL}, edit)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)

    def test_a_rename_maps_the_accounts(self) -> None:
        a, b = buy("a", "10 USD"), buy("b", "20 USD")
        before = {"main.beancount": OPENS + T1 + a + b + SELL}
        new = OPENS + T1 + a + b + SELL

        def rename(before: sd.LedgerSet) -> model.Expected:
            return model.rename_account(before, "Assets:Broker", "Assets:Stocks")
        c = Case(before, {"main.beancount": new.replace("Assets:Broker", "Assets:Stocks")}, rename)
        self.assertEqual(c.report.verdict, "MATCH", c)
        swapped = (OPENS + T1 + b + a + SELL).replace("Assets:Broker", "Assets:Stocks")
        self.assertEqual(Case(before, {"main.beancount": swapped}, rename).report.verdict, "UNEXPLAINED")

    def test_an_import_append_goes_after_the_last_line_of_its_file(self) -> None:
        # The imported buy has the existing buy's date: appended, it is the later lot, and FIFO takes the older.
        a = buy("a", "10 USD")
        imported = buy("i", "20 USD").strip("\n") + "\n"
        before = {"main.beancount": OPENS + T1 + a + SELL}

        def imp(before: sd.LedgerSet) -> model.Expected:
            return model.import_append(before, [imported])
        c = Case(before, {"main.beancount": OPENS + T1 + a + SELL + "\n" + imported}, imp)
        self.assertEqual(c.report.verdict, "MATCH", c)
        # Its twin: the legacy writes the imported buy above the existing one, and the sale takes the new lot.
        c = Case(before, {"main.beancount": OPENS + T1 + "\n" + imported + a + SELL}, imp)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)

    def test_a_baked_reduction_that_books_the_same_lot_is_only_i10(self) -> None:
        a, b = buy("a", "10 USD"), buy("b", "20 USD")
        before = {"main.beancount": OPENS + T1 + a + b + SELL}
        baked = SELL.replace("{}", "{10 USD, 2020-02-01}").replace("  Income:Gain\n", "  Income:Gain  -20 USD\n")
        c = Case(before, {"main.beancount": OPENS + T1_EDITED + a + b + baked}, update_t1)
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE", c)
        self.assertEqual(c.booked(), [], c)
        # Its twin: the sale left elided and the two buys swapped, so the sale takes the other lot.
        c = Case(before, {"main.beancount": OPENS + T1_EDITED + b + a + SELL}, update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)

    def test_a_bake_that_swaps_two_lots_between_postings_is_not_i10(self) -> None:
        # S0161 (M1b finance red team F1): Beancount's booking regroups a sale's postings by currency, so the booked
        # posting at index i is not always the entry's posting i. A legacy bake that gives each reduction the other
        # lot (NONE booking, so no error) must stay UNEXPLAINED; the booked postings are matched by account.
        opens = ('2020-01-01 open Assets:Cash\n2020-01-01 open Expenses:Food USD\n2020-01-01 open Equity:O\n'
                 '2020-01-01 open Assets:X "NONE"\n2020-01-01 open Assets:Y "NONE"\n')
        buys = ('\n2020-01-02 * "buy"\n  Assets:X  1 XX {30 USD}\n  Assets:Y  1 YY {50 EUR}\n'
                '  Equity:O  -30 USD\n  Equity:O  -50 EUR\n')
        sell = ('\n2020-02-01 * "sell"\n  Assets:Cash  30 USD\n  Assets:Y  -1 YY {}\n  Assets:X  -1 XX {}\n'
                '  Assets:Cash  50 EUR\n')
        before = {"main.beancount": opens + buys + T1 + sell}
        swapped = sell.replace("-1 YY {}", "-1 YY {30 USD, 2020-02-01}").replace("-1 XX {}", "-1 XX {50 EUR, 2020-02-01}")
        c = Case(before, {"main.beancount": opens + buys + T1_EDITED + swapped}, update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        # Must-pass twin: each reduction baked with the lot Beancount books for it (under NONE, at the sale's date)
        # is only I10; with the booked postings paired by index it was a false alarm.
        baked = sell.replace("-1 YY {}", "-1 YY {50 EUR, 2020-02-01}").replace("-1 XX {}", "-1 XX {30 USD, 2020-02-01}")
        c = Case(before, {"main.beancount": opens + buys + T1_EDITED + baked}, update_t1)
        self.assertEqual((c.report.verdict, sorted(c.report.class_counts())), ("EXPECTED_DIVERGENCE", ["I10"]), c)

    def test_a_bake_that_swaps_two_lots_within_one_account_is_not_i10(self) -> None:
        # S0161: booking regroups by currency inside one account too (here XX's USD group goes before YY's EUR one),
        # so the account's second booked posting is the entry's first. A bake that swaps the two lots must not be
        # accepted by taking another posting's booked lot: the booked units must be the written ones.
        opens = ('2020-01-01 open Assets:Cash\n2020-01-01 open Expenses:Food USD\n2020-01-01 open Equity:O\n'
                 '2020-01-01 open Assets:X "NONE"\n')
        buys = ('\n2020-01-02 * "buy"\n  Assets:X  1 XX {30 USD}\n  Assets:X  1 YY {50 EUR}\n'
                '  Equity:O  -30 USD\n  Equity:O  -50 EUR\n')
        sell = ('\n2020-02-01 * "sell"\n  Assets:Cash  30 USD\n  Assets:X  -1 YY {EUR}\n  Assets:X  -1 XX {USD}\n'
                '  Assets:Cash  50 EUR\n')
        before = {"main.beancount": opens + buys + T1 + sell}
        swapped = sell.replace("-1 YY {EUR}", "-1 YY {30 USD, 2020-02-01}").replace(
            "-1 XX {USD}", "-1 XX {50 EUR, 2020-02-01}")
        c = Case(before, {"main.beancount": opens + buys + T1_EDITED + swapped}, update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)


class ExclusionTest(unittest.TestCase):
    """What a class explains in the booked result, and only that (classify.booked's docstring): since S0124, a
    difference its own cause makes, undone (ADR-0014 amendment of 2026-11-16)."""

    def test_a_persisted_generated_entry_stands_in_only_when_the_load_no_longer_generates_it(self) -> None:
        # Must-pass: a pad's entry persisted (I3); the pad has nothing left to fill, the persisted entry counts.
        pad = "2020-01-01 open Equity:Opening\n2020-01-01 pad Assets:Cash Equity:Opening\n"
        bal = "\n2020-01-03 balance Assets:Cash 100 USD\n"
        before = {"main.beancount": OPENS + pad + T1 + bal}
        d = LedgerDir(before)
        try:
            generated = [e for e in loader.load_file(str(d.path / "main.beancount"))[0] if getattr(e, "flag", "") == "P"]
        finally:
            d.close()
        persisted = "\n" + printer.format_entry(sd.strip_location(generated[0]))
        c = Case(before, {"main.beancount": OPENS + pad + persisted + T1_EDITED + bal}, update_t1)
        self.assertIn("I3", c.report.class_counts(), c)
        self.assertEqual(c.booked(), [], c)  # the pad no longer generates; the persisted entry stands in

        # A plugin's entry persisted (I3); the plugin generates it again, so the money doubles. Since S0124: taking
        # out the persisted entry (I3's cause) gives the intended result, so I3 explains it.
        plugin = ('from beancount.core import data\nfrom beancount.core.amount import Amount\n'
                  'from decimal import Decimal\n__plugins__ = ["fee"]\n'
                  'def fee(entries, options_map):\n'
                  '    meta = data.new_metadata("<fee>", 0)\n'
                  '    p = [data.Posting(a, Amount(Decimal(n), "USD"), None, None, None, None)\n'
                  '         for a, n in (("Expenses:Food", "1"), ("Assets:Cash", "-1"))]\n'
                  '    import datetime\n'
                  '    t = data.Transaction(meta, datetime.date(2020, 1, 31), "*", None, "fee", frozenset(), frozenset(), p)\n'
                  '    return entries + [t], []\n')
        head = 'option "insert_pythonpath" "TRUE"\nplugin "oracle_fee_plugin"\n' + OPENS
        persisted = '\n2020-01-31 * "fee"\n  Expenses:Food  1 USD\n  Assets:Cash  -1 USD\n'
        before = {"main.beancount": head + T1, "oracle_fee_plugin.py": plugin}
        c = Case(before, {"main.beancount": head + T1_EDITED + persisted}, update_t1)
        self.assertIn("I3", c.report.class_counts(), c)
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE", c)
        self.assertEqual((c.booked(), dict(c.report.undone)), ([], {"I3": 1}), c)
        # Must-not-pass: the same, and the lunch's amount changed as well: the undo does not touch it.
        c = Case(before, {"main.beancount": head + T1_EDITED.replace("10.50", "10.60") + persisted}, update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        self.assertIn("I3", c.report.class_counts(), c)

    def test_a_reduction_baked_as_booked_before_an_edit_that_changes_its_lot_is_explained_by_undoing_i10(self) -> None:
        # The edit moves buy B before buy A, so FIFO now takes B; the legacy bakes the sale's lot as Beancount booked
        # it before the edit (I10 accepts that), and the baked sale still takes A. Since S0124: undoing I10 (the
        # sale's `{}` back) lets FIFO take B, as intended, so I10 explains it.
        a = buy("a", "10 USD")
        b = '\n2020-02-05 * "buy b"\n  id: "t-b"\n  Assets:Broker  1 TOK {20 USD}\n  Assets:Cash  -20 USD\n'
        b_early = b.replace("2020-02-05", "2020-01-20")

        def edit(before: sd.LedgerSet) -> model.Expected:
            return model.update_transaction(before, "t-b", b_early.strip("\n") + "\n")
        before = {"main.beancount": OPENS + a + b + SELL}
        c = Case(before, {"main.beancount": OPENS + a + b_early + SELL}, edit)
        self.assertEqual(c.report.verdict, "MATCH", c)
        baked = SELL.replace("{}", "{10 USD, 2020-02-01}").replace("  Income:Gain\n", "  Income:Gain  -20 USD\n")
        c = Case(before, {"main.beancount": OPENS + a + b_early + baked}, edit)
        self.assertEqual(sorted(d.cls for d in c.report.divergences if d.check == "parsed"), ["I10"], c)
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE", c)
        self.assertEqual((c.booked(), dict(c.report.undone)), ([], {"I10": 1}), c)

    def test_an_i2_duplicate_and_its_effect_on_another_entry_are_explained_once_removed(self) -> None:
        glob_sell = '2020-02-15 * "sell one"\n  Assets:Broker  -1 TOK {}\n  Assets:Cash  10 USD\n  Income:Gain\n'
        lots = buy("a", "10 USD") + buy("b", "20 USD") + buy("c", "30 USD")
        head = 'include "y/*.beancount"\n' + OPENS
        before = {"main.beancount": head + T1 + lots + SELL, "y/2020.beancount": glob_sell}
        # The legacy copies the glob file's entry into the root and leaves it in place (I2): it now sells twice,
        # so the later sale takes the third lot instead of the second. Since S0124: taking the copy out gives the
        # intended sale, so I2 explains it (its must-not-pass twin: UndoTest's duplicate plus an unrelated change).
        after = {"main.beancount": head + T1_EDITED + lots + "\n" + glob_sell + SELL, "y/2020.beancount": glob_sell}
        c = Case(before, after, update_t1)
        self.assertEqual(sorted(d.cls for d in c.report.divergences if d.check == "parsed"), ["I2"], c)
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE", c)
        self.assertEqual((c.booked(), dict(c.report.undone)), ([], {"I2": 1}), c)

    def test_a_fix_l3_02_label_dropped_on_a_lot_a_later_sale_reduces_is_explained_by_the_undo(self) -> None:
        lot = '\n2020-01-06 * "Buy"\n  id: "t-9"\n  Assets:Broker  2 TOK {5.00 USD, 2020-01-01, "lot-a"}\n  Assets:Cash  -10.00 USD\n'
        legacy = lot.replace('"Buy"', '"Buy" "x"').replace(', "lot-a"', "")

        def edit(before: sd.LedgerSet) -> model.Expected:
            return model.update_transaction(before, "t-9", lot.replace('"Buy"', '"Buy" "x"').strip("\n") + "\n")
        c = Case({"main.beancount": OPENS + lot}, {"main.beancount": OPENS + legacy}, edit)
        self.assertEqual(sorted(c.report.class_counts()), ["FIX-L3-02"], c)
        # The brief's must-pass (S0123's exception, now by the rule): a later sale reduces the lot whose label the
        # legacy dropped, so it books another lot label; putting the label back gives the intended sale.
        sell = '\n2020-03-01 * "sell"\n  Assets:Broker  -1 TOK {}\n  Assets:Cash  5.00 USD\n'
        c = Case({"main.beancount": OPENS + lot + sell}, {"main.beancount": OPENS + legacy + sell}, edit)
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE", c)
        self.assertEqual((c.booked(), dict(c.report.undone)), ([], {"FIX-L3-02": 1}), c)


class WholeResultTest(unittest.TestCase):
    """The end inventories come from the two full booked results: no class's leaving-out hides money (verifier F1)."""

    def test_a_reduction_whose_lot_label_the_legacy_drops_takes_another_lot(self) -> None:
        # Verifier A1: the sale names lot "lot-b"; the legacy drops the label (FIX-L3-02's cause), so FIFO takes
        # lot A: the gain is -20 USD instead of -10 USD and the lot left is B, not A. Since S0124 (the brief's
        # question): the undo puts the label back, so the sale takes "lot-b" as intended: explained.
        a, b = buy("a", '10 USD, "lot-a"'), buy("b", '20 USD, "lot-b"')
        sell = '\n2020-03-01 * "sell"\n  id: "t-s"\n  Assets:Broker  -1 TOK {"lot-b"}\n  Assets:Cash  30 USD\n  Income:Gain\n'
        intended = sell.replace('"sell"', '"sell" "memo"')

        def edit(before: sd.LedgerSet) -> model.Expected:
            return model.update_transaction(before, "t-s", intended.strip("\n") + "\n")
        before = {"main.beancount": OPENS + a + b + sell}
        self.assertEqual(Case(before, {"main.beancount": OPENS + a + b + intended}, edit).report.verdict, "MATCH")
        legacy = intended.replace('{"lot-b"}', "{}")
        c = Case(before, {"main.beancount": OPENS + a + b + legacy}, edit)
        self.assertIn("FIX-L3-02", c.report.class_counts(), c)
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE", c)
        self.assertEqual((c.booked(), dict(c.report.undone)), ([], {"FIX-L3-02": 1}), c)
        # Must-not-pass: the same label drop, and buy A's cost changed as well: the undo does not touch buy A, so
        # its lot is left as a booked difference.
        c = Case(before, {"main.beancount": OPENS + a.replace("{10 USD", "{11 USD").replace("-10 USD", "-11 USD") + b + legacy}, edit)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        self.assertTrue(any(d.note.startswith("after undoing FIX-L3-02: balance: Assets:Broker")
                            for d in c.report.divergences), c)

    def test_a_label_dropped_on_a_lot_still_held_is_only_fix_l3_02(self) -> None:
        lot = '\n2020-01-06 * "Buy"\n  id: "t-9"\n  Assets:Broker  2 TOK {5.00 USD, 2020-01-01, "lot-a"}\n  Assets:Cash  -10.00 USD\n'

        def edit(before: sd.LedgerSet) -> model.Expected:
            return model.update_transaction(before, "t-9", lot.replace('"Buy"', '"Buy" "x"').strip("\n") + "\n")
        legacy = lot.replace('"Buy"', '"Buy" "x"').replace(', "lot-a"', "")
        c = Case({"main.beancount": OPENS + lot}, {"main.beancount": OPENS + legacy}, edit)
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE", c)  # the same units at the same cost
        # Its twin: the cost written differently as well is money, whatever the tag.
        c = Case({"main.beancount": OPENS + lot}, {"main.beancount": OPENS + legacy.replace("5.00 USD", "5.01 USD")}, edit)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)

    def test_an_edit_the_legacy_writes_into_the_root_beside_the_unchanged_glob_entry_is_explained_by_the_undo(self) -> None:
        # The legacy writes the edit into the root and leaves the old entry in the glob file: the money doubles.
        # Since S0124: the edit goes back to the old entry's place and the old entry is taken out (I2's cause).
        before = {"main.beancount": 'include "y/*.beancount"\n' + OPENS, "y/2020.beancount": T1}
        after = {"main.beancount": 'include "y/*.beancount"\n' + OPENS + T1_EDITED, "y/2020.beancount": T1}
        c = Case(before, after, update_t1)
        self.assertEqual(sorted(d.cls for d in c.report.divergences if d.check == "parsed"), ["I2", "I2"], c)
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE", c)
        self.assertEqual((c.booked(), dict(c.report.undone)), ([], {"I2": 2}), c)
        # Must-not-pass: the same, and an untargeted entry of the root also written with another amount.
        extra = '\n2020-01-03 * "Shop" "snack"\n  Expenses:Food  2.00 USD\n  Assets:Cash  -2.00 USD\n'
        c = Case({**before, "main.beancount": before["main.beancount"] + extra},
                 {**after, "main.beancount": after["main.beancount"] + extra.replace("2.00", "3.00")}, update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        self.assertEqual(sorted(d.note for d in c.report.divergences if d.check == "balance"),
                         ["after undoing I2: balance: Assets:Cash USD: -12.5 -> -13.5",
                          "after undoing I2: balance: Expenses:Food USD: 12.5 -> 13.5"], c)


COFFEE = ('2020-01-05 * "Cafe" "flat white"\n  Expenses:Coffee  4.20 USD\n  Assets:Cash\n\n'
          '2020-01-06 * "Cafe" "beans"\n  Expenses:Coffee  12.00 USD\n  Assets:Cash  -12.00 USD\n')
PAD_HEAD = ('include "y/*.beancount"\n' + OPENS + '2020-01-01 open Equity:Opening\n2020-01-01 open Expenses:Coffee USD\n'
            '\n2020-01-10 pad Assets:Cash Equity:Opening\n2020-01-11 balance Assets:Cash 1000 USD\n')


def padded(files: dict[str, str]) -> str:
    """What the pad fills from Equity:Opening, as Beancount books it."""
    d = LedgerDir(files)
    try:
        entries, _, _ = loader.load_file(str(d.path / "main.beancount"))
    finally:
        d.close()
    pad = next(e for e in entries if getattr(e, "flag", "") == "P")
    return str(next(p.units for p in pad.postings if p.account == "Equity:Opening"))


class UndoTest(unittest.TestCase):
    """ADR-0014's amendment of 2026-11-16 (S0124): a class explains a booked difference only when undoing its own
    cause on the legacy's result, booked again by Beancount, gives exactly the intended booked result."""

    def test_nastys_i2_shape_duplicates_before_a_pad_are_explained_once_removed(self) -> None:
        # Must-pass: the legacy copies two glob entries into the root and leaves them in their file; they fall
        # before a pad, so the pad fills 16.20 USD more. Removing the copies gives the intended pad.
        before = {"main.beancount": PAD_HEAD + T1, "y/2020.beancount": COFFEE}
        after = {"main.beancount": PAD_HEAD + T1_EDITED + "\n" + COFFEE, "y/2020.beancount": COFFEE}
        self.assertNotEqual(padded(after), padded({"main.beancount": PAD_HEAD + T1_EDITED, "y/2020.beancount": COFFEE}))
        c = Case(before, after, update_t1)
        self.assertEqual(sorted(d.cls for d in c.report.divergences if d.check == "parsed"), ["I2", "I2"], c)
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE", c)
        self.assertEqual(c.booked(), [], c)
        self.assertEqual(dict(c.report.undone), {"I2": 2}, c)

    def test_an_i2_duplicate_plus_an_unrelated_money_change_leaves_the_change(self) -> None:
        # Must-not-pass: the same duplicates, and the legacy also writes two same-date buys in the other order, so
        # FIFO sells the other lot. The undo removes the copies; the sale's change is left, and only it is reported.
        a, b = buy("a", "10 USD"), buy("b", "20 USD")
        before = {"main.beancount": PAD_HEAD + T1 + a + b + SELL, "y/2020.beancount": COFFEE}
        after = {"main.beancount": PAD_HEAD + T1_EDITED + b + a + SELL + "\n" + COFFEE, "y/2020.beancount": COFFEE}
        c = Case(before, after, update_t1)
        self.assertEqual(sorted(d.cls for d in c.report.divergences if d.check == "parsed"), ["I2", "I2"], c)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        self.assertEqual(c.booked(), ["balance", "balance", "booked"], c)
        self.assertEqual(sorted(d.note for d in c.report.divergences if d.check == "balance"),
                         ["after undoing I2: balance: Assets:Broker TOK: 1 {20 USD, 2020-02-01} -> 1 {10 USD, 2020-02-01}",
                          "after undoing I2: balance: Income:Gain USD: -20 -> -10"], c)

    def test_a_class_on_one_entry_does_not_explain_a_booked_difference_on_another(self) -> None:
        # Must-not-pass: FIX-L3-02 on the edited buy (its label dropped); the legacy also writes two same-cost,
        # same-date lots of another buy in the other order, so the sale takes lot "b": the undo does not touch them.
        lot = '\n2020-01-06 * "Buy"\n  id: "t-9"\n  Assets:Broker  2 FUND {5.00 USD, 2020-01-01, "lot-a"}\n  Assets:Cash  -10.00 USD\n'
        legacy = lot.replace('"Buy"', '"Buy" "x"').replace(', "lot-a"', "")
        a, b = buy("a", '10 USD, "a"'), buy("b", '10 USD, "b"')
        sell = SELL.replace("30 USD\n  Income:Gain\n", "10 USD\n")

        def edit(before: sd.LedgerSet) -> model.Expected:
            return model.update_transaction(before, "t-9", lot.replace('"Buy"', '"Buy" "x"').strip("\n") + "\n")
        before = {"main.beancount": OPENS + lot + a + b + sell}
        c = Case(before, {"main.beancount": OPENS + legacy + a + b + sell}, edit)
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE", c)
        c = Case(before, {"main.beancount": OPENS + legacy + b + a + sell}, edit)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        self.assertEqual(c.booked(), ["balance", "booked"], c)  # the sale, and the lot left ("a", not "b")
        self.assertIn('"a"', next(d.note for d in c.report.divergences if d.check == "balance"))

    def test_the_undo_keeps_the_place_the_legacy_wrote_an_entry_at(self) -> None:
        # Must-not-pass: the edited buy is FIX-L3-02 (its label dropped) and the legacy's reprint moves it above a
        # same-date buy in another file (S0122's tie). Undoing the label drop does not move it back: its place is
        # not the class's cause, so FIFO's other lot is left.
        sub = "; pad\n" * 14 + '2020-02-01 * "buy B"\n  Assets:Broker  1 TOK {20 USD}\n  Assets:Cash  -20 USD\n'
        pad = "\n" * 8
        buy_a = '2020-02-01 * "buy A"\n  id: "t-a"\n  Assets:Broker  1 TOK {10 USD, "a"}\n  Assets:Cash  -10 USD\n'
        edited = buy_a.replace('"buy A"', '"buy A" "x"')
        main = 'include "sub.beancount"\n' + OPENS + T1 + pad + buy_a + SELL

        def edit(before: sd.LedgerSet) -> model.Expected:
            return model.update_transaction(before, "t-a", edited)
        before = {"main.beancount": main, "sub.beancount": sub}
        written = main.replace(buy_a, edited.replace(', "a"', ""))
        c = Case(before, {"main.beancount": written, "sub.beancount": sub}, edit)
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE", c)  # the label drop alone: undone, explained
        c = Case(before, {"main.beancount": written.replace(pad, "\n"), "sub.beancount": sub}, edit)
        self.assertEqual(sorted(d.cls for d in c.report.divergences if d.check == "parsed"), ["FIX-L3-02"], c)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        self.assertTrue(all(d.note.startswith("after undoing FIX-L3-02: ")
                            for d in c.report.divergences if d.check != "parsed"), c)
        self.assertEqual(c.booked(), ["balance", "balance", "booked"], c)

    def test_an_i2_move_out_of_a_glob_file_is_undone_by_moving_it_back(self) -> None:
        # Must-pass: the legacy moves a glob file's buy into the root (I2), below a same-date buy, so FIFO sells
        # the other lot. Moving it back to its line in its file gives the intended sale.
        buy_b = '2020-02-01 * "buy B"\n  Assets:Broker  1 TOK {20 USD}\n  Assets:Cash  -20 USD\n'
        head = 'include "y/*.beancount"\n' + OPENS
        a = buy("a", "10 USD")
        before = {"main.beancount": head + T1 + a + SELL, "y/2020.beancount": buy_b}
        after = {"main.beancount": head + T1_EDITED + a + SELL + "\n" + buy_b, "y/2020.beancount": ""}
        self.assertNotEqual(gain(after), gain(before))
        c = Case(before, after, update_t1)
        self.assertEqual(sorted(d.cls for d in c.report.divergences if d.check == "parsed"), ["I2"], c)
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE", c)
        self.assertEqual(dict(c.report.undone), {"I2": 1}, c)

    def test_only_the_copy_is_taken_out_when_the_root_holds_the_same_entry_too(self) -> None:
        # Must-pass: the root already holds the same coffee as the glob file (the user's own); the legacy adds one
        # more copy. Taking out that one copy, not every instance, gives the intended result.
        coffee = COFFEE.split("\n\n")[1]
        before = {"main.beancount": PAD_HEAD + T1 + "\n" + coffee, "y/2020.beancount": COFFEE}
        after = {"main.beancount": PAD_HEAD + T1_EDITED + "\n" + coffee + "\n" + coffee, "y/2020.beancount": COFFEE}
        c = Case(before, after, update_t1)
        self.assertEqual(sorted(d.cls for d in c.report.divergences if d.check == "parsed"), ["I2"], c)
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE", c)
        self.assertEqual(dict(c.report.undone), {"I2": 1}, c)

    def test_a_copy_from_a_file_below_a_glob_match_is_undone_like_the_others(self) -> None:
        # S0160 (ADR-0014 amendment of 2026-11-26), the `includes` corpus's shape: the lunch lives in a file a
        # glob-matched file includes; the legacy copies it into the root and leaves it there, so it is held twice.
        lunch = '\n2020-01-04 * "Shop" "lunch two"\n  Expenses:Food  8.00 USD\n  Assets:Cash  -8.00 USD\n'
        before = {"main.beancount": 'include "y/*.beancount"\n' + OPENS + T1, "y/2020.beancount": 'include "x.bean"\n',
                  "y/x.bean": lunch}
        after = {**before, "main.beancount": 'include "y/*.beancount"\n' + OPENS + T1_EDITED + lunch}
        c = Case(before, after, update_t1)
        self.assertEqual(sorted(d.cls for d in c.report.divergences if d.check == "parsed"), ["I2"], c)
        self.assertEqual((c.report.verdict, c.booked(), dict(c.report.undone)), ("EXPECTED_DIVERGENCE", [], {"I2": 1}), c)
        # Must-not-pass: the copy written with another amount is no copy; it stays UNEXPLAINED with its money.
        c = Case(before, {**after, "main.beancount": after["main.beancount"].replace("8.00", "9.00")}, update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        self.assertEqual(c.booked(), ["balance", "balance"], c)

    def test_a_t_local_report_names_the_classes_undone_and_no_amount(self) -> None:
        a, b = buy("a", "10 USD"), buy("b", "20 USD")
        before = {"main.beancount": PAD_HEAD + T1 + a + b + SELL, "y/2020.beancount": COFFEE}
        after = {"main.beancount": PAD_HEAD + T1_EDITED + b + a + SELL + "\n" + COFFEE, "y/2020.beancount": COFFEE}
        c = Case(before, after, update_t1)
        fields = harness.case_fields(c.report.divergences, c.report.undone)
        self.assertEqual((fields["undone"], fields["booked"]), ({"I2": 2}, {"booked": 1, "balance": 2}))
        redacted = json.dumps(harness.redact({"id": "upd-000", **fields}))
        for secret in ("Income", "Broker", "Cash", "Coffee", "Opening", "TOK", "USD", "10", "20", "2020", "lot"):
            self.assertNotIn(secret, redacted)
        summary = harness.summarize([harness.redact({"id": "upd-000", **fields})])
        self.assertEqual(summary["undone"], {"I2": 2})


CAFE_HEAD = 'include "y/*.beancount"\n' + OPENS + "2020-01-01 open Expenses:Coffee USD\n"
TYPO = '\n2020-01-07 * "Shop" "typo"\n  Expenses:Typo  1.00 USD\n  Assets:Cash  -1.00 USD\n'


def to_cafe(before: sd.LedgerSet) -> model.Expected:
    return model.rename_account(before, "Expenses:Coffee", "Expenses:Cafe")


def cafe(text: str) -> str:
    return text.replace("Expenses:Coffee", "Expenses:Cafe")


def error_classes(c: Case) -> list[str]:
    return sorted(d.cls for d in c.report.divergences if "new Beancount error" in d.note)


class ErrorUndoTest(unittest.TestCase):
    """BL-0047 (S0164; ADR-0014 amendment of 2026-11-28): a new Beancount error is explained only when undoing the
    tagged causes and booking again leaves exactly the reference's errors, the undo itself having removed it. Each
    must-pass shape is followed by its must-not-pass twin, so every test fails on the rule it pins."""

    GLOB = {"main.beancount": CAFE_HEAD + T1, "y/2020.beancount": COFFEE}
    # The legacy writes the renamed copies into the root and leaves the glob file as it was.
    LEGACY = {"main.beancount": cafe(CAFE_HEAD + T1 + "\n" + COFFEE), "y/2020.beancount": COFFEE}

    def test_a_rename_leaves_the_old_name_in_the_glob_file_the_legacy_never_rewrites(self) -> None:
        # Must-pass, nasty ren-coffee's shape: the glob file's entries name an account no longer opened ("Invalid
        # reference to unknown account"). Undoing I2 (each copy back in its file, the old entry taken out) leaves
        # no error, exactly as the reference.
        c = Case(self.GLOB, self.LEGACY, to_cafe)
        self.assertEqual(error_classes(c), ["I2", "I2"], c)
        self.assertTrue(all(d.note.endswith(", gone after undoing I2")
                            for d in c.report.divergences if "new Beancount error" in d.note), c)
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE", c)
        self.assertEqual(c.report.undone["I2"], 4, c)
        # Must-not-pass: the same, and the legacy also writes an entry on an account that is not the old name. The
        # undo leaves that error, so the errors are not the reference's, and no new error is explained.
        c = Case(self.GLOB, {**self.LEGACY, "main.beancount": self.LEGACY["main.beancount"] + TYPO}, to_cafe)
        self.assertEqual(error_classes(c), ["UNEXPLAINED"] * 3, c)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)

    def test_the_error_is_tagged_with_the_smallest_set_of_classes_whose_undo_removes_it(self) -> None:
        # Must-pass: the same rename, and the legacy also bakes an untargeted entry's elided amount (I10). The errors
        # are I2's alone: undoing I2 by itself removes them, as the erasers name the smallest set for an entry.
        snack = '\n2020-01-03 * "Shop" "snack"\n  Expenses:Food  2.00 USD\n  Assets:Cash\n'
        before = {**self.GLOB, "main.beancount": CAFE_HEAD + T1 + snack}
        baked = snack.replace("Assets:Cash\n", "Assets:Cash  -2.00 USD\n")
        after = {**self.LEGACY, "main.beancount": cafe(CAFE_HEAD + T1 + baked + "\n" + COFFEE)}
        c = Case(before, after, to_cafe)
        self.assertIn("I10", [d.cls for d in c.report.divergences if "new Beancount error" not in d.note], c)
        self.assertEqual((error_classes(c), c.report.verdict), (["I2", "I2"], "EXPECTED_DIVERGENCE"), c)

    def test_the_old_name_left_in_a_file_below_a_glob_match_but_not_in_one_the_root_includes_by_name(self) -> None:
        # Must-pass, the `includes` corpus's shape: the coffee lives in a file a glob-matched file includes.
        before = {"main.beancount": CAFE_HEAD + T1, "y/2020.beancount": 'include "x.bean"\n', "y/x.bean": COFFEE}
        c = Case(before, {**before, "main.beancount": cafe(CAFE_HEAD + T1 + "\n" + COFFEE)}, to_cafe)
        self.assertEqual((error_classes(c), c.report.verdict), (["I2", "I2"], "EXPECTED_DIVERGENCE"), c)
        # Must-not-pass: the root includes the coffee's file by name, so the legacy knows it and rewrites it; the old
        # name left there is no I2 cause, though an I2 copy of the glob file's lunch is undone in the same case.
        lunch = '\n2020-01-04 * "Shop" "lunch two"\n  Expenses:Food  8.00 USD\n  Assets:Cash  -8.00 USD\n'
        head = CAFE_HEAD.replace('include "y/*.beancount"\n', 'include "y/*.beancount"\ninclude "c.bean"\n')
        before = {"main.beancount": head + T1, "y/2020.beancount": lunch, "c.bean": COFFEE}
        c = Case(before, {**before, "main.beancount": cafe(head + T1) + lunch}, to_cafe)
        self.assertIn("I2", [d.cls for d in c.report.divergences if "new Beancount error" not in d.note], c)
        self.assertEqual(error_classes(c), ["UNEXPLAINED", "UNEXPLAINED"], c)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)

    def test_an_error_the_undo_did_not_remove_is_not_explained_even_when_the_reference_has_it(self) -> None:
        # Must-pass: the glob coffee copied into the root (I2) and the old name left behind (the plain rename case).
        c = Case(self.GLOB, self.LEGACY, to_cafe)
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE", c)
        # Must-not-pass: an edit that itself posts to an account the ledger never opens, so the reference has the
        # error too; the legacy writes the edit and copies the glob file's coffee into the root (I2). Undoing the
        # copies leaves exactly the reference's errors, but the undo did not remove the new one: UNEXPLAINED.
        bad = T1_EDITED.replace("Expenses:Food", "Expenses:Never")
        after = {"main.beancount": CAFE_HEAD + bad + "\n" + COFFEE, "y/2020.beancount": COFFEE}
        c = Case(self.GLOB, after, lambda b: model.update_transaction(b, "t-1", bad.strip("\n") + "\n"))
        self.assertEqual(sorted(d.cls for d in c.report.divergences if "new Beancount error" not in d.note),
                         ["I2", "I2"], c)
        self.assertEqual(error_classes(c), ["UNEXPLAINED"], c)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)

    def test_the_errors_left_after_the_undo_must_be_exactly_the_references(self) -> None:
        # Must-pass: the legacy copies the glob file's `open` into the root (I2), a "duplicate open" error; taking
        # the copy out leaves no error, as the reference.
        opened = "2020-01-01 open Expenses:Coffee USD\n" + COFFEE
        head = 'include "y/*.beancount"\n' + OPENS
        before = {"main.beancount": head + T1, "y/2020.beancount": opened}
        c = Case(before, {**before, "main.beancount": head + T1_EDITED + opened.split("\n")[0] + "\n"}, update_t1)
        self.assertEqual((error_classes(c), c.report.verdict), (["I2"], "EXPECTED_DIVERGENCE"), c)
        # Must-not-pass: an error the reference does not have is left (the source's unbalanced entry, which the
        # intended edit fixes and the legacy leaves as it was), though the undo took the new error away.
        unbalanced = T1.replace("-10.50 USD", "-10.40 USD")
        before = {"main.beancount": head + unbalanced, "y/2020.beancount": opened}
        c = Case(before, {**before, "main.beancount": head + unbalanced + opened.split("\n")[0] + "\n"}, update_t1)
        self.assertEqual(error_classes(c), ["UNEXPLAINED"], c)
        # Must-not-pass: an error the reference has is missing (the intended edit posts to an account never opened;
        # the legacy leaves the entry as it was), though the undo took the new error away.
        bad = T1_EDITED.replace("Expenses:Food", "Expenses:Never")
        before = {"main.beancount": head + T1, "y/2020.beancount": opened}
        c = Case(before, {**before, "main.beancount": head + T1 + opened.split("\n")[0] + "\n"},
                 lambda b: model.update_transaction(b, "t-1", bad.strip("\n") + "\n"))
        self.assertEqual(error_classes(c), ["UNEXPLAINED"], c)


class FilledNumberTest(unittest.TestCase):
    """S0124 verifier F1: an intended entry's elided posting, filled by the legacy, is I10 only when the number is
    exactly what Beancount books for the intended entry (the reference); a number within Beancount's tolerance
    loads with no error, so nothing else would see it."""

    def edit_with_cash_elided(self, written: str, t: str = T1) -> Case:
        edited = t.replace('"lunch"', '"dinner"')
        edited = edited.replace(next(line for line in edited.splitlines() if line.startswith("  Assets:Cash")), "  Assets:Cash")

        def edit(before: sd.LedgerSet) -> model.Expected:
            return model.update_transaction(before, "t-1", edited.strip("\n") + "\n")
        return Case({"main.beancount": OPENS + t},
                    {"main.beancount": OPENS + t.replace('"lunch"', '"dinner"').replace("-10.50 USD", written)
                     .replace("-10.5 USD", written)}, edit)

    def test_an_edit_whose_elided_posting_is_filled_with_a_number_off_within_tolerance(self) -> None:
        c = self.edit_with_cash_elided("-10.504 USD")
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        self.assertNotIn("I10", c.report.undone, c)
        t = '\n2020-01-02 * "Shop" "lunch"\n  id: "t-1"\n  Expenses:Food  10.5 USD\n  Assets:Cash   -10.5 USD\n'
        c = self.edit_with_cash_elided("-10.54 USD", t)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        # Must-pass twin: the number Beancount books for the intended entry, written at another scale.
        c = self.edit_with_cash_elided("-10.50 USD")
        self.assertEqual((c.report.verdict, sorted(c.report.class_counts())), ("EXPECTED_DIVERGENCE", ["I10"]), c)

    def test_a_filled_number_is_refused_where_booking_split_the_entry(self) -> None:
        # The edited sale reduces two lots, so its booked form has a posting more than the entry: no booked posting
        # pins the elided gain, and the legacy's -30.04 (within the tolerance `60.0` gives) is not accepted.
        sale = '\n2020-03-01 * "sell"\n  id: "t-s"\n  Assets:Broker  -2 TOK {}\n  Assets:Cash  60.0 USD\n  Income:Gain\n'
        edited = sale.replace('"sell"', '"sell" "both"')

        def edit(before: sd.LedgerSet) -> model.Expected:
            return model.update_transaction(before, "t-s", edited.strip("\n") + "\n")
        src = OPENS + buy("a", "10 USD") + buy("b", "20 USD")
        c = Case({"main.beancount": src + sale},
                 {"main.beancount": src + edited.replace("  Income:Gain\n", "  Income:Gain  -30.04 USD\n")}, edit)
        self.assertEqual([d.cls for d in c.report.divergences if d.note.startswith("new Beancount error")], [], c)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        self.assertNotIn("I10", c.report.class_counts(), c)

    def test_an_import_whose_elided_posting_is_filled_with_a_number_off_within_tolerance(self) -> None:
        new = '2020-04-01 * "Shop" "tea"\n  Expenses:Food  3.5 USD\n  Assets:Cash\n'

        def imp(before: sd.LedgerSet) -> model.Expected:
            return model.import_append(before, [new])
        written = '\n2020-04-01 * "Shop" "tea"\n  Expenses:Food  3.5 USD\n  Assets:Cash  -3.54 USD\n'
        c = Case({"main.beancount": OPENS + T1}, {"main.beancount": OPENS + T1 + written}, imp)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        c = Case({"main.beancount": OPENS + T1}, {"main.beancount": OPENS + T1 + written.replace("-3.54", "-3.5")}, imp)
        self.assertEqual((c.report.verdict, sorted(c.report.class_counts())), ("EXPECTED_DIVERGENCE", ["I10"]), c)


class ImportOrderTest(unittest.TestCase):
    def test_imports_at_one_point_take_v04s_order_not_the_requests(self) -> None:
        # Verifier A4: v0.4 orders inserts at one point by date, kind, then text (`source_edit.insert_key`).
        zz = '2020-02-01 * "zz buy"\n  Assets:Broker  1 TOK {10 USD}\n  Assets:Cash  -10 USD\n'
        aa = '2020-02-01 * "aa buy"\n  Assets:Broker  1 TOK {20 USD}\n  Assets:Cash  -20 USD\n'
        sell = SELL.replace("2020-03-01", "2020-03-02")
        src = OPENS + T1 + sell
        before = {"main.beancount": src}

        def imp(before: sd.LedgerSet) -> model.Expected:
            return model.import_append(before, [zz, aa])
        self.assertEqual(Case(before, {"main.beancount": src + "\n" + aa + "\n" + zz}, imp).report.verdict, "MATCH")
        self.assertEqual(Case(before, {"main.beancount": src + "\n" + zz + "\n" + aa}, imp).report.verdict,
                         "UNEXPLAINED")


class DocumentTest(unittest.TestCase):
    def test_document_paths_compare_in_another_folder_and_a_persisted_folder_scan_is_i3(self) -> None:
        # S0160, the `documents` corpus's shape: Beancount makes a document's path absolute from its file's folder,
        # and the legacy's result lives in another folder than the source. The legacy also writes the documents the
        # `documents` option's folder scan generates (I3). Must-not-pass twin: a document whose path changed.
        doc = '2020-01-03 document Assets:Cash "docs/Assets/Cash/2020-01-03.a.txt"\n'
        scanned = '2020-01-04 document Assets:Cash "docs/Assets/Cash/2020-01-04.b.txt"\n'
        head = 'option "documents" "docs"\n' + OPENS
        files = {"docs/Assets/Cash/2020-01-03.a.txt": "a", "docs/Assets/Cash/2020-01-04.b.txt": "b",
                 "other/2020-01-03.a.txt": "a"}
        before = {**files, "main.beancount": head + T1 + doc}
        c = Case(before, {"main.beancount": head + T1_EDITED + doc + scanned}, update_t1)
        self.assertEqual(sorted(d.cls for d in c.report.divergences), ["I3"], c)
        for moved in (doc.replace("a.txt", "b.txt"), doc.replace("docs/Assets/Cash/", "other/")):
            with self.subTest(moved):
                c = Case(before, {"main.beancount": head + T1_EDITED + moved}, update_t1)
                self.assertIn(("UNEXPLAINED", "an entry that was not targeted changed"),
                              [(d.cls, d.note) for d in c.report.divergences], c)


class ReportTest(unittest.TestCase):
    def test_a_t_local_report_of_a_booked_divergence_carries_no_amount_account_or_lot(self) -> None:
        a, b = buy("a", "10 USD"), buy("b", "20 USD")
        c = Case({"main.beancount": OPENS + T1 + a + b + SELL},
                 {"main.beancount": OPENS + T1_EDITED + b + a + SELL}, update_t1)
        fields = harness.case_fields(c.report.divergences)
        self.assertEqual(fields["booked"], {"booked": 1, "balance": 2})
        shown = json.dumps(fields)
        self.assertIn("Income:Gain", shown)  # the unredacted report names it (a repo corpus may show entries)
        redacted = json.dumps(harness.redact({"id": "upd-000", "mutator": "update_transaction", **fields}))
        for secret in ("Income", "Broker", "Cash", "TOK", "USD", "10", "20", "2020", "lot"):
            self.assertNotIn(secret, redacted)
        summary = harness.summarize([harness.redact({"id": "upd-000", **fields})])
        self.assertEqual(summary["booked"], {"booked": 1, "balance": 2})


class FidelityTest(unittest.TestCase):
    """The reference's booking is Beancount's: with no change, `book(intended_entries(...))` gives exactly what
    `loader.load_file` gives (entries with their places, errors), on every loadable ledger of the repo's corpora."""

    def same_as_the_loader(self, base: Path, root: str) -> None:
        facts = sd.load_facts(base, root)
        before = sd.parse_set(base, root)
        booked, errors = sd.book(model.intended_entries(facts.parsed, before, model.Expected()), facts.parsed)
        loaded, loaded_errors, _ = loader.load_file(str(base.resolve() / root))

        def show(entries: list) -> list:
            return [(type(e).__name__, sd.location(e), printer.format_entry(e)) for e in entries]
        self.assertEqual(show(booked), show(loaded))
        self.assertEqual([(type(e).__name__, e.message) for e in errors],
                         [(type(e).__name__, e.message) for e in loaded_errors])

    def test_the_write_corpus_and_the_nasty_corpus(self) -> None:
        import shutil
        import tempfile
        write = REPO / "corpora/ledgers/write"
        cases = tomllib.loads((write / "cases.toml").read_text(encoding="utf-8"))
        folders = [(write / name, "main.beancount") for name, c in cases.items() if c["expect"] == "load"]
        folders.append((REPO / "corpora/ledgers/oracle/nasty", "main.beancount"))
        self.assertGreaterEqual(len(folders), 18)
        for folder, root in folders:
            with self.subTest(folder.name), tempfile.TemporaryDirectory() as tmp:
                copy = Path(tmp) / folder.name
                shutil.copytree(folder, copy)  # no bytecode or pickle cache in the repo
                self.same_as_the_loader(copy, root)

    def test_a_change_moves_no_other_entry(self) -> None:
        a = buy("a", "10 USD", '  id: "t-a"\n')
        d = LedgerDir({"main.beancount": OPENS + T1 + a + SELL})
        try:
            facts = sd.load_facts(d.path, "main.beancount")
            before = sd.parse_set(d.path, "main.beancount")
            longer = a.replace('  id: "t-a"\n', '  id: "t-a"\n  note: "one"\n  more: "two"\n').strip("\n") + "\n"
            exp = model.update_transaction(before, "t-a", longer)
            entries = model.intended_entries(facts.parsed, before, exp)
            lines = {getattr(e, "narration", None) or type(e).__name__: e.meta["lineno"] for e in entries}
            original = {getattr(e, "narration", None) or type(e).__name__: e.meta["lineno"] for e in facts.parsed.entries}
            self.assertEqual(lines, original)  # the replacement at its target's line; the sale below it unmoved
            imp = model.import_append(before, ['2020-04-01 * "x"\n  Assets:Cash  1 USD\n  Expenses:Food  -1 USD\n',
                                               '2020-04-02 * "y"\n  Assets:Cash  1 USD\n  Expenses:Food  -1 USD\n'])
            entries = model.intended_entries(facts.parsed, before, imp)
            n = (d.path / "main.beancount").read_text(encoding="utf-8").count("\n")
            self.assertEqual([e.meta["lineno"] for e in entries[-2:]], [n + 2, n + 6])  # v0.4's append places
        finally:
            d.close()


if __name__ == "__main__":
    unittest.main()
