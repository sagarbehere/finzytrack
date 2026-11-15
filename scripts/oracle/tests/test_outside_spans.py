"""Step 2b in the oracle (BL-0045, S0122): every byte outside the intended change's spans is the old byte, or the
difference is reported as a step-2b divergence and classed (`classify.outside_spans`).

Each must-not-pass test plants one outside-span change that the entry-level compare cannot see (the canonical keys
stay equal), as the legacy app's full rewrite makes it, and checks the class the docstring names. The last tests pin
what must pass: an intended change exactly at its spans, and the inserted entry's place.
"""

from __future__ import annotations

import json
import unittest

from helpers import OPENS, LedgerDir
from test_classify import ELIDED, ELIDED_BAKED, T1, T1_EDITED, Case, update_t1

import classify
import harness
import model
import semantic_diff as sd

BROKER = OPENS + "2020-01-01 open Assets:Broker\n"
TOTAL = '\n2020-01-06 * "Buy"\n  Assets:Broker  7 TOK @@ 10.00 USD\n  Assets:Cash  -10.00 USD\n'
# The legacy re-render writes Beancount's own per-unit quotient, so the parsed entry (and its key) is unchanged.
TOTAL_AS_UNIT = TOTAL.replace("@@ 10.00 USD", "@ 1.428571428571428571428571429 USD")


def outside(c: Case) -> list[str]:
    return sorted(d.cls for d in c.report.divergences if d.step == "2b")


class MustNotPassTest(unittest.TestCase):
    """Each plants one outside-span change; none may come out as a MATCH."""

    def test_an_untargeted_total_price_rewritten_as_a_unit_price_is_fix_l3_02(self) -> None:
        c = Case({"main.beancount": BROKER + T1 + TOTAL}, {"main.beancount": BROKER + T1_EDITED + TOTAL_AS_UNIT}, update_t1)
        self.assertEqual(outside(c), ["FIX-L3-02"])
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE")

    def test_a_comment_inside_an_untargeted_entry_dropped_is_i10(self) -> None:
        commented = ELIDED.replace("  Expenses:Food  2.00 USD\n", "  Expenses:Food  2.00 USD ; paid in coins\n")
        c = Case({"main.beancount": OPENS + T1 + commented}, {"main.beancount": OPENS + T1_EDITED + ELIDED}, update_t1)
        self.assertEqual(outside(c), ["I10"])
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE")

    def test_a_reindented_untargeted_posting_is_format(self) -> None:
        reindented = ELIDED.replace("  Expenses:Food  2.00 USD\n", "    Expenses:Food      2.00 USD\n")
        c = Case({"main.beancount": OPENS + T1 + ELIDED}, {"main.beancount": OPENS + T1_EDITED + reindented}, update_t1)
        self.assertEqual(outside(c), ["FORMAT"])
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE")

    def test_a_byte_changed_between_two_entries_is_unexplained(self) -> None:
        note = "; checked\n"
        before = {"main.beancount": OPENS + T1 + note + ELIDED}
        for changed, cls in (("; checkeD\n", "UNEXPLAINED"), (";   checked\n", "FORMAT")):
            with self.subTest(changed=changed):
                c = Case(before, {"main.beancount": OPENS + T1_EDITED + changed + ELIDED}, update_t1)
                self.assertEqual(outside(c), [cls])

    def test_a_changed_line_outside_every_entry_is_unexplained(self) -> None:
        # An org-mode heading: Beancount skips it, so neither the entries nor the header see it.
        c = Case({"main.beancount": "* Groceries\n" + OPENS + T1}, {"main.beancount": "* Grocery\n" + OPENS + T1_EDITED}, update_t1)
        self.assertEqual(outside(c), ["UNEXPLAINED"])
        self.assertEqual(c.report.verdict, "UNEXPLAINED")

    def test_a_pushtag_block_folded_into_its_entries_is_unexplained(self) -> None:
        # The legacy rewrite drops `pushtag`/`poptag` and writes the tag on each entry: the keys stay equal.
        before = OPENS + T1 + "pushtag #trip\n" + ELIDED + "poptag #trip\n"
        after = OPENS + T1_EDITED + ELIDED.replace('"snack"', '"snack" #trip')
        c = Case({"main.beancount": before}, {"main.beancount": after}, update_t1)
        self.assertIn("UNEXPLAINED", outside(c))
        self.assertEqual(c.report.verdict, "UNEXPLAINED")

    def test_an_untargeted_amount_respelled_is_unexplained(self) -> None:
        # `1,000.00` and `1000.00` parse to one number; no class names a re-spelled number.
        src = '\n2020-01-07 * "Big"\n  Expenses:Rent  1,000.00 USD\n  Assets:Cash  -1,000.00 USD\n'
        c = Case({"main.beancount": OPENS + T1 + src}, {"main.beancount": OPENS + T1_EDITED + src.replace(",", "")}, update_t1)
        self.assertEqual(outside(c), ["UNEXPLAINED"])

    def test_a_line_ending_changed_is_format(self) -> None:
        c = Case({"main.beancount": OPENS + T1 + ELIDED}, {"main.beancount": OPENS + T1_EDITED + ELIDED.replace("\n", "\r\n")},
                 update_t1)
        self.assertEqual(outside(c), ["FORMAT"])

    def test_a_comment_added_in_the_intended_change_place_is_unexplained(self) -> None:
        c = Case({"main.beancount": OPENS + T1 + ELIDED}, {"main.beancount": OPENS + "; new\n" + T1_EDITED + ELIDED}, update_t1)
        self.assertEqual(outside(c), ["UNEXPLAINED"])

    def test_two_untargeted_entries_of_one_date_swapped_is_unexplained(self) -> None:
        # Beancount orders same-date, same-kind entries by their line: the swap changes the loaded order.
        a = '\n2020-01-03 * "A" "first"\n  Expenses:Food  1.00 USD\n  Assets:Cash  -1.00 USD\n'
        b = a.replace('"A" "first"', '"B" "second"')
        c = Case({"main.beancount": OPENS + T1 + a + b}, {"main.beancount": OPENS + T1_EDITED + b + a}, update_t1)
        self.assertEqual(outside(c), ["UNEXPLAINED", "UNEXPLAINED"])
        self.assertEqual(c.report.verdict, "UNEXPLAINED")

    def test_entries_reordered_as_beancount_sorts_them_are_format(self) -> None:
        # The legacy writes entries in Beancount's order (date, then kind): a file's sections are lost, its meaning
        # is not. The intended replacement's own place among them is the same layout.
        late = '\n2020-01-09 * "Late" "x"\n  Expenses:Food  1.00 USD\n  Assets:Cash  -1.00 USD\n'
        for after in (OPENS + T1_EDITED + ELIDED + late, OPENS + ELIDED + T1_EDITED + late):
            with self.subTest(after=after[-40:]):
                c = Case({"main.beancount": OPENS + late + T1 + ELIDED}, {"main.beancount": after}, update_t1)
                self.assertEqual(outside(c), ["FORMAT"])
        c = Case({"main.beancount": OPENS + late + T1 + ELIDED}, {"main.beancount": OPENS + T1_EDITED + ELIDED + late}, update_t1)
        self.assertIn("another order", next(d.note for d in c.report.divergences if d.step == "2b"))
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE")

    def test_the_replacement_moved_past_an_entry_needs_format(self) -> None:
        # Without a reprint the replacement must stand where its target stood; moved, it is FORMAT's reorder.
        x, y = T1.lstrip("\n"), ELIDED.lstrip("\n")
        c = Case({"main.beancount": OPENS + "\n" + x + y}, {"main.beancount": OPENS + "\n" + y + T1_EDITED.lstrip("\n")}, update_t1)
        self.assertEqual(outside(c), ["FORMAT"])
        self.assertIn("another order", next(d.note for d in c.report.divergences if d.step == "2b"))

    def test_a_file_the_change_does_not_touch_respaced_is_format(self) -> None:
        c = Case({"main.beancount": 'include "sub.beancount"\n' + OPENS + T1, "sub.beancount": ELIDED},
                 {"main.beancount": 'include "sub.beancount"\n' + OPENS + T1_EDITED,
                  "sub.beancount": ELIDED.replace("Food  2", "Food   2")}, update_t1)
        self.assertEqual([(d.cls, d.file) for d in c.report.divergences], [("FORMAT", "sub.beancount")])

    def test_a_respaced_header_line_is_format(self) -> None:
        c = Case({"main.beancount": 'include "sub.beancount"\n' + OPENS + T1, "sub.beancount": ELIDED},
                 {"main.beancount": 'include   "sub.beancount"\n' + OPENS + T1_EDITED, "sub.beancount": ELIDED}, update_t1)
        self.assertEqual(outside(c), ["FORMAT"])

    def test_header_lines_moved_in_their_own_order_are_format_and_swapped_are_not(self) -> None:
        # Beancount reads option, plugin and include lines in file order wherever they stand; plugins run in it.
        p1, p2 = 'plugin "beancount.plugins.implicit_prices"\n', 'plugin "beancount.plugins.auto_accounts"\n'
        before = {"main.beancount": p1 + OPENS + T1 + p2}
        c = Case(before, {"main.beancount": p1 + p2 + OPENS + T1_EDITED}, update_t1)
        self.assertEqual(outside(c), ["FORMAT"])
        c = Case(before, {"main.beancount": p2 + p1 + OPENS + T1_EDITED}, update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED")
        self.assertIn("UNEXPLAINED", outside(c))

    def test_an_untargeted_unit_price_respelled_is_not_fix_l3_02(self) -> None:
        # FIX-L3-02 is a `@@` total only; a `@` price written another way is no class.
        src = '\n2020-01-06 * "Buy"\n  Assets:Broker  7 TOK @ 1,000.00 USD\n  Assets:Cash  -7000.00 USD\n'
        c = Case({"main.beancount": BROKER + T1 + src}, {"main.beancount": BROKER + T1_EDITED + src.replace("1,000", "1000")},
                 update_t1)
        self.assertEqual(outside(c), ["UNEXPLAINED"])

    def test_an_inserted_entry_takes_one_blank_line_not_more(self) -> None:
        new = '2020-03-01 * "Cafe" "coffee"\n  Expenses:Food  3.00 USD\n  Assets:Cash  -3.00 USD\n'
        intent = lambda b: model.import_append(b, [new])  # noqa: E731
        for old, after in ((T1 + ELIDED, T1 + "\n" + new + "\n\n" + ELIDED.lstrip("\n")),
                           (T1 + ELIDED.lstrip("\n"), T1 + new + "\n\n" + ELIDED.lstrip("\n"))):
            with self.subTest(after=after):
                c = Case({"main.beancount": OPENS + old}, {"main.beancount": OPENS + after}, intent)
                self.assertEqual(outside(c), ["FORMAT"])

    def test_a_deleted_file_that_held_no_entry_is_reported(self) -> None:
        # Step 2 sees no entry and no header in it; only the bytes show that it went.
        src = LedgerDir({"main.beancount": 'include "notes.beancount"\n' + OPENS + T1, "notes.beancount": "* Plans\n"})
        copy = LedgerDir({"main.beancount": 'include "notes.beancount"\n' + OPENS + T1_EDITED})
        try:
            before, bf = sd.parse_set(src.path, "main.beancount"), sd.load_facts(src.path, "main.beancount")
            after, af = sd.parse_set(copy.path, "main.beancount"), sd.load_facts(copy.path, "main.beancount")
            r = classify.compare(before, after, update_t1(before), bf, af)
        finally:
            src.close()
            copy.close()
        self.assertEqual([(d.cls, d.file) for d in r.divergences if d.step == "2b"], [("I10", "notes.beancount")])

    def test_a_semicolon_inside_a_string_is_not_a_comment(self) -> None:
        src = '\n2020-01-07 * "Shop" "two ; items" ; real note\n  Expenses:Food  1.00 USD\n  Assets:Cash  -1.00 USD\n'
        c = Case({"main.beancount": OPENS + T1 + src}, {"main.beancount": OPENS + T1_EDITED + src.replace(" ; real note", "")},
                 update_t1)
        self.assertEqual(outside(c), ["I10"])

    def test_classes_combine_on_one_file(self) -> None:
        commented = ELIDED.replace("  Assets:Cash\n", "  Assets:Cash ; till\n")
        respaced = ELIDED.replace("  Expenses:Food  2.00 USD\n", "  Expenses:Food    2.00 USD\n")
        c = Case({"main.beancount": "; top\n" + OPENS + T1 + commented}, {"main.beancount": OPENS + T1_EDITED + "\n\n" + respaced},
                 update_t1)
        self.assertEqual(outside(c), ["FORMAT", "I10"])

    def test_a_paired_entry_that_also_lost_a_comment_needs_i10(self) -> None:
        commented = ELIDED.replace("  Expenses:Food  2.00 USD\n", "  Expenses:Food  2.00 USD ; coins\n")
        c = Case({"main.beancount": OPENS + T1 + commented}, {"main.beancount": OPENS + T1_EDITED + ELIDED_BAKED}, update_t1)
        self.assertEqual(sorted(d.cls for d in c.report.divergences), ["I10", "I10"])
        self.assertEqual(outside(c), ["I10"])


class MustPassTest(unittest.TestCase):
    def test_an_intended_change_exactly_at_its_span_passes_and_one_byte_past_it_does_not(self) -> None:
        # The target's own lines may change in any way (here: its comment and its layout); the next byte may not.
        src = T1.replace("  Expenses:Food  10.50 USD\n", "  Expenses:Food  10.50 USD ; old note\n")
        edited = '\n2020-01-02 * "Shop" "dinner"\n  id: "t-1"\n  Expenses:Food     10.50 USD\n  Assets:Cash  -10.50 USD\n'
        intent = lambda b: model.update_transaction(b, "t-1", edited)  # noqa: E731
        exact = Case({"main.beancount": OPENS + src + ELIDED}, {"main.beancount": OPENS + edited + ELIDED}, intent)
        self.assertEqual((exact.report.verdict, outside(exact)), ("MATCH", []))
        past = Case({"main.beancount": OPENS + src + ELIDED}, {"main.beancount": OPENS + edited + ELIDED.replace("Food  2", "Food   2")},
                    intent)
        self.assertNotEqual(past.report.verdict, "MATCH")
        self.assertEqual(outside(past), ["FORMAT"])

    def test_an_appended_entry_at_the_end_of_the_root_passes(self) -> None:
        new = '2020-03-01 * "Cafe" "coffee"\n  Expenses:Food  3.00 USD\n  Assets:Cash  -3.00 USD\n    import-key: "v1:h:abc"\n'
        for sep in ("\n", ""):
            with self.subTest(sep=repr(sep)):
                c = Case({"main.beancount": OPENS + T1}, {"main.beancount": OPENS + T1 + sep + new},
                         lambda b: model.import_append(b, [new]))
                self.assertEqual((c.report.verdict, outside(c)), ("MATCH", []))
                touched = Case({"main.beancount": OPENS + T1}, {"main.beancount": OPENS + T1.replace("Food  10", "Food   10") + sep + new},
                               lambda b: model.import_append(b, [new]))
                self.assertEqual(outside(touched), ["FORMAT"])

    def test_an_untouched_included_file_passes(self) -> None:
        c = Case({"main.beancount": 'include "sub.beancount"\n' + OPENS + T1, "sub.beancount": "; kept\n" + ELIDED},
                 {"main.beancount": 'include "sub.beancount"\n' + OPENS + T1_EDITED, "sub.beancount": "; kept\n" + ELIDED}, update_t1)
        self.assertEqual((c.report.verdict, outside(c)), ("MATCH", []))
        c = Case({"main.beancount": 'include "sub.beancount"\n' + OPENS + T1, "sub.beancount": "; kept\n" + ELIDED},
                 {"main.beancount": 'include "sub.beancount"\n' + OPENS + T1_EDITED, "sub.beancount": "; kepT\n" + ELIDED}, update_t1)
        self.assertEqual([(d.cls, d.file) for d in c.report.divergences], [("UNEXPLAINED", "sub.beancount")])


class ReportTest(unittest.TestCase):
    def test_a_t_local_report_of_an_outside_span_divergence_carries_no_text(self) -> None:
        commented = ELIDED.replace("  Expenses:Food  2.00 USD\n", "  Expenses:Food  2.00 USD ; paid in coins\n")
        big = '\n2020-01-07 * "Secret Payee"\n  Expenses:Rent  1,000.00 USD\n  Assets:Cash  -1,000.00 USD\n'
        c = Case({"main.beancount": OPENS + T1 + commented + big},
                 {"main.beancount": OPENS + T1_EDITED + ELIDED + big.replace(",", "")}, update_t1)
        result = {"id": "upd-000", "mutator": "update_transaction"} | harness.result_fields(c.report.divergences)
        self.assertEqual(result["classes_2b"], {"I10": 1, "UNEXPLAINED": 1})
        self.assertIn("Secret", json.dumps(result))  # a repo-corpus report shows the text...
        shown = json.dumps(harness.redact(result))
        for text in ("Secret", "coins", "Expenses", "Food", "main.beancount", "line"):
            self.assertNotIn(text, shown)  # ...a T-local one never does
        self.assertEqual(harness.redact(result)["classes_2b"], {"I10": 1, "UNEXPLAINED": 1})

    def test_the_summary_counts_step_2b_classes_apart(self) -> None:
        s = harness.summarize([{"verdict": "EXPECTED_DIVERGENCE", "classes": {"I10": 3, "FORMAT": 1},
                                "classes_2b": {"I10": 1, "FORMAT": 1}, "seconds": {"total": 1.0}}])
        self.assertEqual((s["classes"], s["classes_2b"]), ({"I10": 3, "FORMAT": 1}, {"I10": 1, "FORMAT": 1}))

    def test_every_step_2b_class_is_known(self) -> None:
        self.assertEqual(set(classify.STEP2B_ERASERS), {"I10", "FIX-L3-02", "FORMAT"})
        self.assertIn("FORMAT", classify.__doc__ or "")


if __name__ == "__main__":
    unittest.main()
