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
        self.before_facts = sd.load_facts(self.d.path, root)
        self.expected = expect(self.before)
        self.d.write(after)
        self.after = sd.parse_set(self.d.path, root)
        self.after_facts = sd.load_facts(self.d.path, root)
        self.report = classify.compare(self.before, self.after, self.expected, self.before_facts, self.after_facts)
        self.d.close()

    def refused(self, mutator: str, code: str):
        return classify.refused(self.before, self.after, self.expected, self.before_facts, self.after_facts, mutator, code)

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

    def test_i2_a_changed_directive_duplicated_from_a_glob_file(self) -> None:
        budget = '2020-01-01 custom "budget" Expenses:Rent "monthly" 900 USD\n'
        before = {"main.beancount": 'include "y/*.beancount"\n' + OPENS + T1, "y/2020.beancount": budget}
        after = {"main.beancount": 'include "y/*.beancount"\n' + OPENS + T1_EDITED + budget.replace("900", "901"),
                 "y/2020.beancount": budget}
        self.assertEqual(Case(before, after, update_t1).classes(), ["UNEXPLAINED"])

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
        r = c.refused("update_transaction", "TRANSACTION_INVALID")
        self.assertEqual([d.cls for d in r.divergences], ["FIX-L3-02"])

    def test_any_other_refusal_is_not_applied(self) -> None:
        c = Case({"main.beancount": OPENS + T1}, {}, update_t1)
        r = c.refused("update_transaction", "TRANSACTION_INVALID")
        self.assertEqual([d.cls for d in r.divergences], ["NOT_APPLIED"])

    def test_a_refusal_that_still_changed_the_ledger_is_unexplained(self) -> None:
        c = Case({"main.beancount": OPENS + T1}, {"main.beancount": OPENS}, update_t1)
        r = c.refused("update_transaction", "X")
        self.assertEqual(sorted(d.cls for d in r.divergences), ["NOT_APPLIED", "UNEXPLAINED"])

    def test_an_unexplained_header_change_is_reported(self) -> None:
        c = Case({"main.beancount": OPENS + T1}, {"main.beancount": 'option "title" "x"\n' + OPENS + T1_EDITED}, update_t1)
        self.assertEqual(c.classes(), ["UNEXPLAINED"])


T2 = '\n2020-01-04 * "Bank" "deposit"\n  id: "t-2"\n  Assets:Cash  100.00 USD\n    statement-amount: 100.00 USD\n    import-key: "v1:h:dep"\n  Expenses:Rent  -100.00 USD\n'
BROKER = OPENS + "2020-01-01 open Assets:Broker\n"


class OverMatchTest(unittest.TestCase):
    """The verifier's attacks on S0002 (findings F2-F6): a real divergence must never be tagged as expected."""

    def test_m28_does_not_mask_a_changed_legacy_id_on_an_untargeted_entry(self) -> None:
        c = Case({"main.beancount": OPENS + T1 + T2}, {"main.beancount": OPENS + T1_EDITED + T2.replace('"t-2"', '"t-99"')}, update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c.classes())

    def test_m28_does_not_mask_a_changed_statement_amount(self) -> None:
        c = Case({"main.beancount": OPENS + T1 + T2},
                 {"main.beancount": OPENS + T1_EDITED + T2.replace("statement-amount: 100.00", "statement-amount: 999.00")}, update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c.classes())

    def test_m28_does_not_mask_a_dropped_import_key(self) -> None:
        c = Case({"main.beancount": OPENS + T1 + T2},
                 {"main.beancount": OPENS + T1_EDITED + T2.replace('    import-key: "v1:h:dep"\n', "")}, update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c.classes())

    def test_m28_does_not_mask_a_changed_import_key_on_a_created_entry(self) -> None:
        intended = '2020-03-01 * "Cafe" "coffee"\n  Expenses:Food  3.00 USD\n  Assets:Cash  -3.00 USD\n    import-key: "v1:h:abc"\n'
        legacy = intended.replace("v1:h:abc", "v1:h:zzz")
        c = Case({"main.beancount": OPENS + T1}, {"main.beancount": OPENS + T1 + legacy}, lambda b: model.import_append(b, [intended]))
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c.classes())

    def test_i10_does_not_mask_a_wrong_baked_amount(self) -> None:
        wrong = ELIDED.replace("  Assets:Cash\n", "  Assets:Cash  -99.00 USD\n")
        c = Case({"main.beancount": OPENS + T1 + ELIDED}, {"main.beancount": OPENS + T1_EDITED + wrong}, update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c.classes())

    def test_i10_does_not_mask_a_wrong_baked_lot(self) -> None:
        lots = BROKER + '2020-01-02 * "a"\n  Assets:Broker  1 TOK {5 USD}\n  Assets:Cash  -5 USD\n' \
            + '2020-01-03 * "b"\n  Assets:Broker  1 TOK {7 USD}\n  Assets:Cash  -7 USD\n'
        sell = '2020-01-05 * "sell"\n  Assets:Broker  -1 TOK {7 USD}\n  Assets:Cash  7 USD\n'
        baked_wrong = sell.replace("{7 USD}", "{5 USD, 2020-01-02}").replace("  7 USD", "  5 USD")
        c = Case({"main.beancount": lots + T1 + sell}, {"main.beancount": lots + T1_EDITED + baked_wrong}, update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c.classes())

    def test_i10_accepts_the_lot_beancount_books(self) -> None:
        lots = BROKER + '2020-01-02 * "a"\n  Assets:Broker  1 TOK {5 USD}\n  Assets:Cash  -5 USD\n'
        sell = '2020-01-05 * "sell"\n  Assets:Broker  -1 TOK {}\n  Assets:Cash  5 USD\n'
        baked = sell.replace("{}", "{5 USD, 2020-01-02}")
        c = Case({"main.beancount": lots + T1 + sell}, {"main.beancount": lots + T1_EDITED + baked}, update_t1)
        self.assertEqual(c.classes(), ["I10"])

    def test_l3_02_does_not_mask_a_changed_unit_price(self) -> None:
        src = '2020-01-06 * "Buy"\n  Assets:Broker  0.001 BTC @ 30000 USD\n  Assets:Cash  -30.00 USD\n'
        c = Case({"main.beancount": BROKER + T1 + src}, {"main.beancount": BROKER + T1_EDITED + src.replace("30000", "30004")}, update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c.classes())

    def test_l3_02_does_not_mask_a_dropped_dated_cost(self) -> None:
        src = '2020-01-06 * "Buy"\n  Assets:Broker  2 TOK {5.00 USD, 2020-01-01}\n  Assets:Cash  -10.00 USD\n'
        c = Case({"main.beancount": BROKER + T1 + src},
                 {"main.beancount": BROKER + T1_EDITED + src.replace(" {5.00 USD, 2020-01-01}", "")}, update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c.classes())

    def test_l3_02_does_not_mask_a_dropped_cost_number(self) -> None:
        src = '2020-01-06 * "Buy"\n  Assets:Broker  2 TOK {5.00 USD, 2020-01-01}\n  Assets:Cash  -10.00 USD\n'
        c = Case({"main.beancount": BROKER + T1 + src},
                 {"main.beancount": BROKER + T1_EDITED + src.replace("{5.00 USD, 2020-01-01}", "{2020-01-01}")}, update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c.classes())

    def test_a_new_parse_error_is_not_a_match(self) -> None:
        c = Case({"main.beancount": OPENS + T1}, {"main.beancount": OPENS + T1_EDITED + '\n2020-01-09 * "x"\n  Assets:Cash  1 USD {{{\n'}, update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c.classes())

    def test_a_new_load_error_is_not_a_match(self) -> None:
        c = Case({"main.beancount": OPENS + T1}, {"main.beancount": OPENS + T1_EDITED + "2020-01-09 balance Assets:Cash 5 USD\n"},
                 update_t1)
        self.assertEqual(c.report.verdict, "UNEXPLAINED", c.classes())

    def test_refusal_of_an_unbalanced_priced_intent_is_not_expected(self) -> None:
        src = '2020-01-06 * "Buy"\n  id: "t-9"\n  Assets:Broker  7 TOK @@ 10.00 USD\n  Assets:Cash  -10.00 USD\n'
        bad = src.replace("-10.00 USD", "-55.00 USD")
        c = Case({"main.beancount": BROKER + src}, {}, lambda b: model.update_transaction(b, "t-9", bad))
        r = c.refused("update_transaction", "TRANSACTION_INVALID")
        self.assertEqual(r.verdict, "UNEXPLAINED", [d.cls for d in r.divergences])


if __name__ == "__main__":
    unittest.main()
