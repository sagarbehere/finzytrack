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
            after_facts = sd.load_facts(copy.path, "main.beancount", keep_parsed=False)
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
        self.assertEqual(c.booked(), ["balance", "booked"], c)  # the sale; Income:Gain's end balance
        notes = [d.note for d in c.report.divergences]
        self.assertTrue(any("took another lot" in n or "other amounts" in n for n in notes), c)

    def test_an_untargeted_sale_whose_lot_changes_and_no_balance_does(self) -> None:
        # Two same-date lots at one cost, labelled; the legacy writes them in the other order, FIFO takes the other.
        a, b = buy("a", '10 USD, "a"'), buy("b", '10 USD, "b"')
        sell = SELL.replace("30 USD\n  Income:Gain\n", "10 USD\n")
        before = {"main.beancount": OPENS + T1 + a + b + sell}
        c = Case(before, {"main.beancount": OPENS + T1_EDITED + b + a + sell}, update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        self.assertEqual(c.booked(), ["booked"], c)
        self.assertIn("took another lot", c.report.divergences[-1].note)
        self.assertEqual(Case(before, {"main.beancount": OPENS + T1_EDITED + a + b + sell}, update_t1).report.verdict,
                         "MATCH")

    def test_an_end_balance_that_changes_with_no_entry_the_parsed_comparison_flags(self) -> None:
        a, b = buy("a", "10 USD"), buy("b", "20 USD")
        before = {"main.beancount": OPENS + T1 + a + b + SELL}
        c = Case(before, {"main.beancount": OPENS + T1_EDITED + b + a + SELL}, update_t1)
        self.assertEqual([d.cls for d in c.report.divergences if d.check == "parsed"], [], c)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        balances = sorted(d.note for d in c.report.divergences if d.check == "balance")
        self.assertEqual(balances, ["balance: Income:Gain USD: -20 -> -10"], c)


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


class ExclusionTest(unittest.TestCase):
    """What a class explains in the booked result, and only that (classify.booked's docstring)."""

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

        # Must-not-pass: a plugin's entry persisted (I3); the plugin generates it again, so the money doubles.
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
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        self.assertIn("booked", c.booked(), c)

    def test_a_reduction_baked_as_booked_before_an_edit_that_changes_its_lot_is_not_only_i10(self) -> None:
        # The edit moves buy B before buy A, so FIFO now takes B; the legacy bakes the sale's lot as Beancount booked
        # it before the edit (I10 accepts that), and the baked sale still takes A.
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
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        self.assertEqual(c.booked(), ["balance", "booked"], c)

    def test_an_i2_duplicate_is_left_out_but_its_effect_on_another_entry_is_not(self) -> None:
        glob_sell = '2020-02-15 * "sell one"\n  Assets:Broker  -1 TOK {}\n  Assets:Cash  10 USD\n  Income:Gain\n'
        lots = buy("a", "10 USD") + buy("b", "20 USD") + buy("c", "30 USD")
        head = 'include "y/*.beancount"\n' + OPENS
        before = {"main.beancount": head + T1 + lots + SELL, "y/2020.beancount": glob_sell}
        # The legacy copies the glob file's entry into the root and leaves it in place (I2): it now sells twice,
        # so the later sale takes the third lot instead of the second.
        after = {"main.beancount": head + T1_EDITED + lots + "\n" + glob_sell + SELL, "y/2020.beancount": glob_sell}
        c = Case(before, after, update_t1)
        self.assertEqual(sorted(d.cls for d in c.report.divergences if d.check == "parsed"), ["I2"], c)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        self.assertIn("booked", c.booked(), c)
        self.assertEqual(len([d for d in c.report.divergences if d.check == "booked"]), 1, c)  # the sale, not the copy

    def test_a_fix_l3_02_label_dropped_is_that_class_and_its_effect_on_a_later_sale_is_not(self) -> None:
        lot = '\n2020-01-06 * "Buy"\n  id: "t-9"\n  Assets:Broker  2 TOK {5.00 USD, 2020-01-01, "lot-a"}\n  Assets:Cash  -10.00 USD\n'
        legacy = lot.replace('"Buy"', '"Buy" "x"').replace(', "lot-a"', "")

        def edit(before: sd.LedgerSet) -> model.Expected:
            return model.update_transaction(before, "t-9", lot.replace('"Buy"', '"Buy" "x"').strip("\n") + "\n")
        c = Case({"main.beancount": OPENS + lot}, {"main.beancount": OPENS + legacy}, edit)
        self.assertEqual(sorted(c.report.class_counts()), ["FIX-L3-02"], c)
        sell = '\n2020-03-01 * "sell"\n  Assets:Broker  -1 TOK {}\n  Assets:Cash  5.00 USD\n'
        c = Case({"main.beancount": OPENS + lot + sell}, {"main.beancount": OPENS + legacy + sell}, edit)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c)
        self.assertEqual(c.booked(), ["booked"], c)


class ReportTest(unittest.TestCase):
    def test_a_t_local_report_of_a_booked_divergence_carries_no_amount_account_or_lot(self) -> None:
        a, b = buy("a", "10 USD"), buy("b", "20 USD")
        c = Case({"main.beancount": OPENS + T1 + a + b + SELL},
                 {"main.beancount": OPENS + T1_EDITED + b + a + SELL}, update_t1)
        fields = harness.case_fields(c.report.divergences)
        self.assertEqual(fields["booked"], {"booked": 1, "balance": 1})
        shown = json.dumps(fields)
        self.assertIn("Income:Gain", shown)  # the unredacted report names it (a repo corpus may show entries)
        redacted = json.dumps(harness.redact({"id": "upd-000", "mutator": "update_transaction", **fields}))
        for secret in ("Income", "Broker", "Cash", "TOK", "USD", "10", "20", "2020", "lot"):
            self.assertNotIn(secret, redacted)
        summary = harness.summarize([harness.redact({"id": "upd-000", **fields})])
        self.assertEqual(summary["booked"], {"booked": 1, "balance": 1})


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
