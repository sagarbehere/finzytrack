"""Divergence classifiers (ADR-0014): each tags a planted divergence of its class and nothing else."""

from __future__ import annotations

import unittest

from helpers import OPENS, LedgerDir

import classify
import model
import semantic_diff as sd

T1 = """
2020-01-02 * "Shop" "lunch"
  id: "t-1"
  Expenses:Food  10.50 USD
  Assets:Cash   -10.50 USD
"""
T1_EDITED = T1.replace('"lunch"', '"dinner"')
ELIDED = """
2020-01-03 * "Shop" "snack"
  Expenses:Food  2.00 USD
  Assets:Cash
"""
ELIDED_BAKED = ELIDED.replace("  Assets:Cash\n", "  Assets:Cash  -2.00 USD\n")


class Case:
    """A before ledger, a simulated legacy 'after' ledger, and the run's comparison."""

    def __init__(self, before: dict[str, str], after: dict[str, str], expect, root: str = "main.beancount") -> None:
        self.d = LedgerDir(before)
        self.before = sd.parse_set(self.d.path, root)
        self.generated = sd.generated_keys(self.d.path, root)
        self.expected = expect(self.before)
        self.d.write(after)
        self.after = sd.parse_set(self.d.path, root)
        self.report = classify.compare(self.before, self.after, self.expected, self.generated)
        self.d.close()

    def classes(self) -> list[str]:
        return sorted(d.cls for d in self.report.divergences)


def update_t1(before):
    return model.update_transaction(before, "t-1", T1_EDITED)


class ClassifyTest(unittest.TestCase):
    def test_exact_intended_change_is_a_match(self) -> None:
        c = Case({"main.beancount": OPENS + T1 + ELIDED}, {"main.beancount": OPENS + T1_EDITED + ELIDED}, update_t1)
        self.assertEqual(c.classes(), [])
        self.assertEqual(c.report.verdict, "MATCH")

    def test_i2_glob_included_entries_copied_into_the_root(self) -> None:
        before = {"main.beancount": 'include "y/*.beancount"\n' + OPENS + T1, "y/2020.beancount": ELIDED}
        after = {"main.beancount": 'include "y/*.beancount"\n' + OPENS + T1_EDITED + ELIDED, "y/2020.beancount": ""}
        c = Case(before, after, update_t1)
        self.assertEqual(c.classes(), ["I2"])
        self.assertEqual(c.report.verdict, "EXPECTED_DIVERGENCE")

    def test_i2_glob_included_entries_duplicated_into_the_root(self) -> None:
        before = {"main.beancount": 'include "y/*.beancount"\n' + OPENS + T1, "y/2020.beancount": ELIDED}
        after = {"main.beancount": 'include "y/*.beancount"\n' + OPENS + T1_EDITED + ELIDED, "y/2020.beancount": ELIDED}
        self.assertEqual(Case(before, after, update_t1).classes(), ["I2"])

    def test_i2_with_i10_a_duplicated_glob_entry_with_its_elided_amount_baked(self) -> None:
        before = {"main.beancount": 'include "y/*.beancount"\n' + OPENS + T1, "y/2020.beancount": ELIDED}
        after = {"main.beancount": 'include "y/*.beancount"\n' + OPENS + T1_EDITED + ELIDED_BAKED, "y/2020.beancount": ELIDED}
        self.assertEqual(Case(before, after, update_t1).classes(), ["I10+I2"])

    def test_i2_an_edit_inside_a_glob_file_lands_in_the_root_and_the_file_is_not_rewritten(self) -> None:
        before = {"main.beancount": 'include "y/*.beancount"\n' + OPENS, "y/2020.beancount": T1}
        after = {"main.beancount": 'include "y/*.beancount"\n' + OPENS + T1_EDITED, "y/2020.beancount": T1}
        self.assertEqual(Case(before, after, update_t1).classes(), ["I2", "I2"])

    def test_an_edit_not_applied_in_a_rewritten_glob_file_is_not_i2(self) -> None:
        before = {"main.beancount": 'include "y/*.beancount"\n' + OPENS, "y/2020.beancount": T1 + ELIDED}
        after = {"main.beancount": 'include "y/*.beancount"\n' + OPENS, "y/2020.beancount": T1}
        self.assertEqual(Case(before, after, update_t1).classes(), ["NOT_APPLIED", "NOT_APPLIED", "UNEXPLAINED"])

    def test_a_move_out_of_a_plain_include_is_unexplained(self) -> None:
        before = {"main.beancount": 'include "sub.beancount"\n' + OPENS + T1, "sub.beancount": ELIDED}
        after = {"main.beancount": 'include "sub.beancount"\n' + OPENS + T1_EDITED + ELIDED, "sub.beancount": ""}
        c = Case(before, after, update_t1)
        self.assertEqual(c.classes(), ["UNEXPLAINED"])
        self.assertEqual(c.report.verdict, "UNEXPLAINED")

    def test_i3_plugin_generated_entries_persisted(self) -> None:
        fx = '2020-01-04 * "fx"\n  Assets:Cash  10 EUR @ 1.10 USD\n  Expenses:Food  -11.00 USD\n'
        head = 'plugin "beancount.plugins.implicit_prices"\n' + OPENS
        c = Case({"main.beancount": head + T1 + fx},
                 {"main.beancount": head + T1_EDITED + fx + "2020-01-04 price EUR 1.10 USD\n"}, update_t1)
        self.assertEqual(c.classes(), ["I3"])

    def test_i10_elided_amount_baked_into_an_untargeted_entry(self) -> None:
        c = Case({"main.beancount": OPENS + T1 + ELIDED}, {"main.beancount": OPENS + T1_EDITED + ELIDED_BAKED}, update_t1)
        self.assertEqual(c.classes(), ["I10"])

    def test_i10_comments_lost(self) -> None:
        c = Case({"main.beancount": "; my note\n" + OPENS + T1}, {"main.beancount": OPENS + T1_EDITED}, update_t1)
        self.assertEqual(c.classes(), ["I10"])

    def test_m28_legacy_machinery_keys_instead_of_import_key(self) -> None:
        intended = '2020-03-01 * "Cafe" "coffee"\n  Expenses:Food  3.00 USD\n  Assets:Cash  -3.00 USD\n    import-key: "v1:h:abc"\n'
        legacy = ('2020-03-01 * "Cafe" "coffee"\n  id: "0190-x"\n  content_hash: "h"\n  source_account: "Assets:Cash"\n'
                  "  Expenses:Food  3.00 USD\n  Assets:Cash  -3.00 USD\n")
        c = Case({"main.beancount": OPENS + T1}, {"main.beancount": OPENS + T1 + legacy},
                 lambda b: model.import_append(b, [intended]))
        self.assertEqual(c.classes(), ["M28"])

    def test_two_classes_on_one_entry_are_both_named(self) -> None:
        intended = '2020-03-01 * "Cafe" "coffee"\n  Expenses:Food  3.00 USD\n  Assets:Cash\n    import-key: "v1:h:abc"\n'
        legacy = '2020-03-01 * "Cafe" "coffee"\n  id: "0190-x"\n  Expenses:Food  3.00 USD\n  Assets:Cash  -3.00 USD\n'
        c = Case({"main.beancount": OPENS + T1}, {"main.beancount": OPENS + T1 + legacy},
                 lambda b: model.import_append(b, [intended]))
        self.assertEqual(c.classes(), ["I10+M28"])

    def test_fix_l3_15_typed_metadata_turned_into_strings(self) -> None:
        typed = T1.replace('  id: "t-1"\n', '  id: "t-1"\n  rate: 1.50\n')
        c = Case({"main.beancount": OPENS + typed}, {"main.beancount": OPENS + T1_EDITED.replace('  id: "t-1"\n', '  id: "t-1"\n  rate: "1.50"\n')},
                 lambda b: model.update_transaction(b, "t-1", typed.replace('"lunch"', '"dinner"')))
        self.assertEqual(c.classes(), ["FIX-L3-15"])

    def test_fix_l3_02_total_price_turned_into_a_rounded_unit_price(self) -> None:
        opens = OPENS + "2020-01-01 open Assets:Broker\n"
        src = '2020-01-06 * "Buy"\n  id: "t-9"\n  Assets:Broker  7 TOK @@ 10.00 USD\n  Assets:Cash  -10.00 USD\n'
        legacy = '2020-01-06 * "Buy" "x"\n  id: "t-9"\n  Assets:Broker  7 TOK @ 1.4286 USD\n  Assets:Cash  -10.00 USD\n'
        c = Case({"main.beancount": opens + src}, {"main.beancount": opens + legacy},
                 lambda b: model.update_transaction(b, "t-9", src.replace('"Buy"', '"Buy" "x"')))
        self.assertEqual(c.classes(), ["FIX-L3-02"])

    def test_fix_l3_05_written_scale_changed(self) -> None:
        src = T1.replace("10.50", "10").replace("-10.50", "-10")
        c = Case({"main.beancount": OPENS + src}, {"main.beancount": OPENS + T1_EDITED.replace("10.50", "10.00")},
                 lambda b: model.update_transaction(b, "t-1", src.replace('"lunch"', '"dinner"')))
        self.assertEqual(c.classes(), ["FIX-L3-05"])

    def test_fix_l3_02_cost_date_dropped(self) -> None:
        opens = OPENS + "2020-01-01 open Assets:Broker\n"
        src = '2020-01-06 * "Buy"\n  id: "t-9"\n  Assets:Broker  2 TOK {5.00 USD, 2020-01-01, "lot-a"}\n  Assets:Cash  -10.00 USD\n'
        legacy = '2020-01-06 * "Buy" "x"\n  id: "t-9"\n  Assets:Broker  2 TOK {5.00 USD}\n  Assets:Cash  -10.00 USD\n'
        c = Case({"main.beancount": opens + src}, {"main.beancount": opens + legacy},
                 lambda b: model.update_transaction(b, "t-9", src.replace('"Buy"', '"Buy" "x"')))
        self.assertEqual(c.classes(), ["FIX-L3-02"])

    def test_an_untargeted_amount_change_is_unexplained(self) -> None:
        c = Case({"main.beancount": OPENS + T1 + ELIDED},
                 {"main.beancount": OPENS + T1_EDITED + ELIDED.replace("2.00", "2.01")}, update_t1)
        self.assertEqual(c.classes(), ["UNEXPLAINED"])

    def test_an_intended_change_not_applied_is_reported(self) -> None:
        c = Case({"main.beancount": OPENS + T1}, {"main.beancount": OPENS + T1}, update_t1)
        self.assertEqual(c.classes(), ["NOT_APPLIED", "NOT_APPLIED"])
        self.assertEqual(c.report.verdict, "UNEXPLAINED")

    def test_a_refused_edit_of_a_priced_transaction_is_fix_l3_02(self) -> None:
        opens = OPENS + "2020-01-01 open Assets:Broker\n"
        src = '2020-01-06 * "Buy"\n  id: "t-9"\n  Assets:Broker  7 TOK @@ 10.00 USD\n  Assets:Cash  -10.00 USD\n'
        c = Case({"main.beancount": opens + src}, {}, lambda b: model.update_transaction(b, "t-9", src.replace('"Buy"', '"Buy" "x"')))
        r = classify.refused(c.before, c.after, c.expected, c.generated, "update_transaction", "TRANSACTION_INVALID")
        self.assertEqual([d.cls for d in r.divergences], ["FIX-L3-02"])

    def test_any_other_refusal_is_not_applied(self) -> None:
        c = Case({"main.beancount": OPENS + T1}, {}, update_t1)
        r = classify.refused(c.before, c.after, c.expected, c.generated, "update_transaction", "TRANSACTION_INVALID")
        self.assertEqual([d.cls for d in r.divergences], ["NOT_APPLIED"])

    def test_a_refusal_that_still_changed_the_ledger_is_unexplained(self) -> None:
        c = Case({"main.beancount": OPENS + T1}, {"main.beancount": OPENS}, update_t1)
        r = classify.refused(c.before, c.after, c.expected, c.generated, "update_transaction", "X")
        self.assertEqual(sorted(d.cls for d in r.divergences), ["NOT_APPLIED", "UNEXPLAINED"])

    def test_an_unexplained_header_change_is_reported(self) -> None:
        c = Case({"main.beancount": OPENS + T1}, {"main.beancount": 'option "title" "x"\n' + OPENS + T1_EDITED}, update_t1)
        self.assertEqual(c.classes(), ["UNEXPLAINED"])


if __name__ == "__main__":
    unittest.main()
