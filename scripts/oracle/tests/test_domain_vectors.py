"""The Beancount domain vectors (S0024; spec design/specs/kernel/vectors.md; INV-02, ADR-0024, BL-0002).

The committed vectors equal a fresh run of the generator in Beancount 3.2.0, and every structural check passes on
the real files. Each check has a must-not-pass test (EP-29): a corrupted copy of the inputs or the vectors that the
check refuses, naming its rule.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gen_domain_vectors  # noqa: E402
import vectors  # noqa: E402


# Which vectors show each behaviour (vectors.COVERAGE); reviewed by hand in S0024. A changed behaviour check or
# a changed corpus must update this table on purpose.
SHOWN_BY = {
    'tolerance': {
        'an elided leg: indeterminate, filled by Beancount': [
            'tolerance/elided-leg-quantized',
        ],
        'a {} reduction: indeterminate': [
            'tolerance/reduction-needs-booking',
        ],
        'a {USD} reduction: indeterminate': [
            'tolerance/partial-cost-currency-only',
        ],
        'a {date} reduction: indeterminate': [
            'tolerance/partial-cost-date-only',
        ],
        'a cost with no currency: indeterminate': [
            'tolerance/partial-cost-number-only',
        ],
        'a cost with only a total: indeterminate': [
            'tolerance/partial-cost-total-only',
        ],
        'a refused entry has no port verdict': [
            'tolerance/negative-price-refused',
            'tolerance/negative-total-price-refused',
        ],
        'a negative price is served as written': [
            'tolerance/negative-price-refused',
            'tolerance/negative-total-price-refused',
        ],
        'a residual equal to the tolerance balances': [
            'tolerance/boundary-equal-passes',
            'tolerance/multiplier-option',
            'tolerance/default-option-per-currency',
            'tolerance/default-star-applies-to-integers',
        ],
        'an unbalanced transaction': [
            'tolerance/written-scale-10.50',
            'tolerance/boundary-over-fails',
            'tolerance/default-option-absent',
            'tolerance/default-star-limit',
            'tolerance/default-star-does-not-raise-inferred',
            'tolerance/integer-cash-leg-zero-tolerance',
            'tolerance/sale-without-gain-leg',
            'tolerance/from-cost-option-off',
            'tolerance/two-currencies-no-price',
        ],
        'integers infer no tolerance': [
            'tolerance/integer-cash-leg-zero-tolerance',
        ],
        'tolerance_multiplier option': [
            'tolerance/multiplier-option',
        ],
        'inferred_tolerance_default for one currency': [
            'tolerance/default-option-per-currency',
        ],
        'inferred_tolerance_default *': [
            'tolerance/default-star-applies-to-integers',
            'tolerance/default-star-limit',
            'tolerance/default-star-does-not-raise-inferred',
        ],
        'infer_tolerance_from_cost': [
            'tolerance/from-cost-option-on',
        ],
        'a @@ total price': [
            'tolerance/total-price-division',
            'tolerance/negative-total-price-refused',
        ],
        'a {{}} total cost': [
            'tolerance/total-cost-division',
        ],
        'cost and price on one posting': [
            'tolerance/cost-not-price-weighs',
            'tolerance/sale-without-gain-leg',
            'tolerance/reduction-needs-booking',
        ],
        'Beancount drops the transaction': [
            'tolerance/two-elided-legs',
        ],
    },
    'balance': {
        'an explicit ~ tolerance': [
            'balance/explicit-tolerance',
        ],
        'an integer assertion has zero tolerance': [
            'balance/integer-zero-tolerance',
        ],
        'a difference equal to the tolerance passes': [
            'balance/inferred-boundary',
            'balance/multiplier-option',
        ],
        "a transaction on the assertion's date is not counted": [
            'balance/start-of-day',
            'balance/start-of-day-includes-fails',
        ],
        'a parent account sums its children': [
            'balance/parent-sums-children',
        ],
        'tolerance_multiplier option': [
            'balance/multiplier-option',
        ],
    },
    'account': {
        'Assets:Retirement:401k is valid': [
            'account/default-roots Assets:Retirement:401k',
        ],
        'Café is valid': [
            'account/default-roots Assets:Bank:Café',
        ],
        '日本 is valid': [
            'account/default-roots Assets:Bank:日本',
        ],
        'an underscore is invalid': [
            'account/default-roots Assets:My_Bank',
            'account/default-roots Assets:Bank:Café_Bar',
        ],
        'a custom root is valid with its option': [
            'account/custom-root Activos:Banco',
            'account/unicode-root Ägare:Bank',
        ],
        'a custom root is invalid without its option': [
            'account/default-roots Activos:Banco',
        ],
        'valid although account.is_valid says no': [
            'account/default-roots Assets:Bank:éclair',
            'account/default-roots Assets:Bank:日本',
            'account/default-roots Assets:Bank:🏦',
        ],
        'more text after a name is invalid even when it parses': [
            'account/default-roots Assets:Bank USD',
        ],
        'invalid although account.is_valid says yes': [
            'account/default-roots Activos:Banco',
            'account/custom-root Assets:Bank',
        ],
    },
    'currency': {
        'a single capital letter is valid': [
            'currency/grammar V',
        ],
        'a slash-prefixed future is valid': [
            'currency/grammar /6J',
            'currency/grammar /NQH21',
            'currency/grammar /NQH21_QNEG21C13100',
        ],
        'a slash code without a letter is invalid': [
            'currency/grammar /6.3',
        ],
        'a trailing special character is invalid': [
            'currency/grammar CAC_',
            'currency/grammar ABC.',
        ],
        'more text after a code is invalid even when it parses': [
            'currency/grammar USD ;x',
        ],
        'invalid although CURRENCY_RE matches': [
            'currency/grammar CAC_',
        ],
        'lower case is invalid': [
            'currency/grammar usd',
            'currency/grammar Usd',
        ],
    },
    'price': {
        'a price on the query date counts': [
            'price/as-of VTI USD 2020-01-10',
            'price/as-of USD VTI 2020-01-10',
            'price/same-day-last-wins VTI USD 2020-01-01',
            'price/both-directions USD INR 2020-01-02',
            'price/both-directions INR USD 2020-01-02',
            'price/both-directions INR USD 2020-01-01',
            'price/inverted-wins-anywhere USD INR 2020-01-02',
            'price/double-inversion USD INR 2020-01-01',
            'price/double-inversion INR USD 2020-01-01',
            'price/zero-not-inverted XYZ USD 2020-01-01',
            'price/implicit-prices-plugin VTI USD 2020-01-01',
            'price/implicit-prices-plugin USD INR 2020-02-01',
        ],
        'before the first price there is none': [
            'price/as-of VTI USD 2019-12-31',
        ],
        'a later price is excluded for an earlier date': [
            'price/as-of VTI USD 2020-01-10',
            'price/as-of VTI USD 2020-01-09',
            'price/as-of VTI USD 2025-01-01',
            'price/both-directions INR USD 2020-01-01',
            'price/double-inversion INR USD 2020-01-01',
        ],
        'the latest price includes future dates': [
            'price/as-of VTI USD latest',
        ],
        'an inverse lookup': [
            'price/as-of USD VTI 2020-01-10',
        ],
        'the inverted point wins wherever it is in the file': [
            'price/inverted-wins-anywhere USD INR 2020-01-02',
        ],
        'a point inverted twice': [
            'price/double-inversion USD INR 2020-01-01',
        ],
        'same-day duplicates': [
            'price/same-day-last-wins VTI USD 2020-01-01',
        ],
        'another quote currency gives none': [
            'price/as-of VTI EUR 2020-06-01',
            'price/no-triangulation VTI INR 2020-01-01',
        ],
        'base equals quote': [
            'price/as-of VTI VTI 2020-06-01',
        ],
        'implicit prices with the plugin': [
            'price/implicit-prices-plugin VTI USD 2020-01-01',
            'price/implicit-prices-plugin USD INR 2020-02-01',
        ],
        'no implicit prices without the plugin': [
            'price/no-implicit-without-plugin VTI USD 2020-01-01',
        ],
    },
    'precision': {
        'most common differs from maximum': [
            'precision/most-common-and-maximum USD',
        ],
        'integers count as 0 digits': [
            'precision/most-common-and-maximum JPY',
        ],
        'an unseen currency has none': [
            'precision/most-common-and-maximum CHF',
            'precision/tie USD',
        ],
    },
    'booking': {
        'method STRICT': [
            'booking/strict-ambiguous',
            'booking/strict-named-lot',
        ],
        'method STRICT_WITH_SIZE': [
            'booking/strict-with-size-exact',
            'booking/strict-with-size-no-match',
        ],
        'method NONE': [
            'booking/none',
        ],
        'method AVERAGE': [
            'booking/average-refused',
        ],
        'method FIFO': [
            'booking/fifo',
            'booking/fifo-not-enough',
            'booking/option-default',
        ],
        'method LIFO': [
            'booking/lifo',
            'booking/account-overrides-option',
            'booking/backdated-lifo',
        ],
        'method HIFO': [
            'booking/hifo',
        ],
        'STRICT refuses an ambiguous reduction': [
            'booking/strict-ambiguous',
        ],
        'a lot dated after the sale is not used': [
            'booking/backdated-lifo',
        ],
    },
}


def unit_label(family: str, case: dict, item: dict) -> str:
    return case["id"] + (" " + " ".join(str(v) for v in item["input"].values()) if family in vectors.ITEMS else "")


class RealVectors(unittest.TestCase):
    def test_every_check_passes_and_the_vectors_are_fresh(self) -> None:
        self.assertEqual(vectors.run_all(), [])

    def test_pinned_counts(self) -> None:
        # Sizes per family (cases, positive and negative vectors), so a silently shrunk set is noticed.
        doc = json.loads((vectors.CORPUS / "vectors.json").read_text(encoding="utf-8"))
        counts = {f: (len(doc["families"][f]),
                      sum(i["polarity"] == "positive" for _, i in vectors.units(doc, f)),
                      sum(i["polarity"] == "negative" for _, i in vectors.units(doc, f)))
                  for f in vectors.FAMILIES}
        self.assertEqual(counts, {
            "tolerance": (30, 18, 12), "balance": (10, 6, 4), "account": (4, 15, 16), "currency": (1, 12, 9),
            "price": (9, 16, 5), "precision": (2, 5, 2), "booking": (13, 9, 4),
        })

    def test_which_vectors_show_each_behaviour(self) -> None:
        doc = json.loads((vectors.CORPUS / "vectors.json").read_text(encoding="utf-8"))
        shown = {f: {name: [unit_label(f, c, i) for c, i in vectors.units(doc, f) if pred(c, i)]
                     for name, pred in preds.items()}
                 for f, preds in vectors.COVERAGE.items()}
        self.assertEqual(shown, SHOWN_BY)

    def test_the_generator_cli_prints_the_vectors(self) -> None:
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(gen_domain_vectors.main([]), 0)
        self.assertEqual(out.getvalue(), (vectors.CORPUS / "vectors.json").read_text(encoding="utf-8"))


class Corpus(unittest.TestCase):
    """A temp copy of corpora/vectors; edit the cases or the vectors, then run the checks."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.corpus = Path(self._tmp.name) / "vectors"
        shutil.copytree(vectors.CORPUS, self.corpus)
        self.doc = json.loads((self.corpus / "vectors.json").read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @property
    def cases_text(self) -> str:
        return (self.corpus / "cases.toml").read_text(encoding="utf-8")

    def edit_cases(self, old: str, new: str, count: int = 1) -> None:
        text = self.cases_text
        self.assertIn(old, text)
        (self.corpus / "cases.toml").write_text(text.replace(old, new, count), encoding="utf-8")

    def problems(self, fresh: bool = False) -> list[str]:
        (self.corpus / "vectors.json").write_text(json.dumps(self.doc, indent=1, ensure_ascii=False) + "\n",
                                                  encoding="utf-8")
        return vectors.run_all(self.corpus, fresh=fresh)

    def assertReports(self, fragment: str, fresh: bool = False) -> None:
        problems = self.problems(fresh)
        self.assertTrue(any(fragment in p for p in problems), f"{fragment!r} not in {problems}")

    def vec(self, vid: str) -> dict:
        family = vid.split("/")[0]
        return next(v for v in self.doc["families"][family] if v["id"] == vid)

    def test_the_copy_is_clean(self) -> None:
        self.assertEqual(self.problems(fresh=True), [])


class Inputs(Corpus):
    def test_a_wrong_beancount_pin(self) -> None:
        self.edit_cases('beancount = "3.2.0"', 'beancount = "3.2.3"')
        self.assertReports("beancount must be '3.2.0'")

    def test_an_unknown_family(self) -> None:
        self.edit_cases("[[currency]]", "[[currencies]]")
        self.assertReports("unknown family 'currencies'")

    def test_a_family_without_cases(self) -> None:
        self.edit_cases("[[currency]]", "[[currencies]]")
        self.assertReports("currency: the family has no cases")

    def test_a_missing_field(self) -> None:
        self.edit_cases('about = "Integer amounts infer no tolerance; an exact sum balances."\n', "")
        self.assertReports("tolerance tolerance/integers-exact: missing field(s) ['about']")

    def test_an_unknown_field(self) -> None:
        self.edit_cases('id = "tolerance/integers-exact"\n', 'id = "tolerance/integers-exact"\nexpect = "x"\n')
        self.assertReports("unknown field(s) ['expect']")

    def test_a_duplicate_id(self) -> None:
        self.edit_cases('id = "tolerance/written-scale-10.50"', 'id = "tolerance/integers-exact"')
        self.assertReports("tolerance tolerance/integers-exact: duplicate id")

    def test_an_id_outside_its_family(self) -> None:
        self.edit_cases('id = "balance/exact"', 'id = "tolerance/exact-balance"')
        self.assertReports("the id must start with 'balance/'")

    def test_a_case_citing_nothing(self) -> None:
        self.edit_cases('spec = ["beancount:beancount.ops.validation.validate_check_transaction_balances", "ADR-0024"]',
                        "spec = []")
        self.assertReports("tolerance/integers-exact: the case cites nothing")

    def test_a_case_citing_no_beancount_function(self) -> None:
        self.edit_cases('spec = ["beancount:beancount.ops.validation.validate_check_transaction_balances", "ADR-0024"]',
                        'spec = ["ADR-0024"]')
        self.assertReports("tolerance/integers-exact: the case cites no Beancount function")

    def test_an_unknown_beancount_symbol(self) -> None:
        self.edit_cases("beancount:beancount.ops.validation.validate_check_transaction_balances",
                        "beancount:beancount.ops.validation.validate_check_transaction_balance")
        self.assertReports("unknown Beancount symbol")

    def test_a_beancount_citation_outside_beancount(self) -> None:
        self.edit_cases("beancount:beancount.ops.validation.validate_check_transaction_balances",
                        "beancount:json.dumps")
        self.assertReports("unknown Beancount symbol 'beancount:json.dumps'")

    def test_a_beancount_module_that_does_not_exist(self) -> None:
        self.edit_cases("beancount:beancount.ops.validation.validate_check_transaction_balances",
                        "beancount:beancount.nosuchmodule")
        self.assertReports("unknown Beancount symbol 'beancount:beancount.nosuchmodule'")

    def test_an_unknown_adr(self) -> None:
        self.edit_cases('"beancount:beancount.ops.validation.validate_check_transaction_balances", "ADR-0024"',
                        '"beancount:beancount.ops.validation.validate_check_transaction_balances", "ADR-9999"')
        self.assertReports("unknown ADR 'ADR-9999'")

    def test_an_unrecognised_citation(self) -> None:
        self.edit_cases('"beancount:beancount.ops.validation.validate_check_transaction_balances", "ADR-0024"',
                        '"beancount:beancount.ops.validation.validate_check_transaction_balances", "L3-09"')
        self.assertReports("unrecognised citation 'L3-09'")

    def test_a_review_citation_without_section(self) -> None:
        self.edit_cases('"review:L3-verif §2.5", "ADR-0023"', '"review:L3-verif", "ADR-0023"')
        self.assertReports("unrecognised citation 'review:L3-verif'")

    def test_a_case_without_a_valid_polarity(self) -> None:
        self.edit_cases('id = "tolerance/integers-exact"\npolarity = "positive"',
                        'id = "tolerance/integers-exact"\npolarity = "yes"')
        self.assertReports("tolerance tolerance/integers-exact: polarity must be one of")

    def test_an_item_without_a_valid_polarity(self) -> None:
        self.edit_cases('{ code = "USD", polarity = "positive" }', '{ code = "USD", polarity = "ok" }')
        self.assertReports("currency currency/grammar item 0: polarity must be one of")

    def test_an_item_with_a_wrong_field(self) -> None:
        self.edit_cases('{ code = "USD", polarity = "positive" }', '{ name = "USD", polarity = "positive" }')
        self.assertReports("currency currency/grammar item 0: fields must be")

    def test_a_case_with_no_items(self) -> None:
        self.edit_cases('  { currency = "EUR", polarity = "positive" },\n  { currency = "USD", polarity = "negative" },\n',
                        "")
        self.assertReports("precision precision/tie: no currencies")

    def test_a_posting_with_a_wrong_field(self) -> None:
        self.edit_cases('{ account = "Assets:Bank", units = "-10 USD" }', '{ account = "Assets:Bank", amount = "-10 USD" }')
        self.assertReports("tolerance tolerance/integers-exact posting 0: fields must be")

    def test_a_posting_without_an_account(self) -> None:
        self.edit_cases('{ account = "Assets:Bank", units = "-10 USD" }', '{ units = "-10 USD" }')
        self.assertReports("tolerance tolerance/integers-exact posting 0: fields must be")


class Vectors(Corpus):
    def test_vectors_from_another_beancount(self) -> None:
        self.doc["beancount"] = "3.2.3"
        self.assertReports("vectors.json: written by Beancount '3.2.3', the pin is '3.2.0'")

    def test_an_environment_running_another_beancount(self) -> None:
        with mock.patch.object(vectors.beancount, "__version__", "3.2.3"):
            self.assertReports("the oracle environment runs Beancount 3.2.3")

    def test_a_missing_family(self) -> None:
        del self.doc["families"]["booking"]
        self.assertReports("vectors.json: families must be")

    def test_vectors_out_of_order(self) -> None:
        fam = self.doc["families"]["balance"]
        fam[0], fam[1] = fam[1], fam[0]
        self.assertReports("balance: the vectors do not match the cases")

    def test_a_dropped_vector(self) -> None:
        self.doc["families"]["balance"].pop()
        self.assertReports("balance: the vectors do not match the cases")

    def test_a_dropped_item(self) -> None:
        self.vec("currency/grammar")["items"].pop(0)
        self.assertReports("currency currency/grammar: the vector's items do not match the case's")

    def test_a_case_polarity_beancount_disagrees_with(self) -> None:
        self.vec("tolerance/written-scale-10.50")["polarity"] = "positive"
        self.assertReports("tolerance tolerance/written-scale-10.50: declared positive but Beancount says negative")

    def test_an_item_polarity_beancount_disagrees_with(self) -> None:
        self.vec("account/default-roots")["items"][0]["polarity"] = "negative"
        self.assertReports("declared negative but Beancount says positive")

    def test_a_declared_polarity_flipped_in_the_cases(self) -> None:
        # Regenerated (fresh): the vectors carry the declared polarity, the verdict refuses it.
        self.edit_cases('{ name = "Assets:My_Bank", polarity = "negative" }',
                        '{ name = "Assets:My_Bank", polarity = "positive" }')
        self.doc = json.loads(gen_domain_vectors.generate(self.corpus / "cases.toml"))
        self.assertReports("declared positive but Beancount says negative")

    def test_each_verdict_rule(self) -> None:
        # Flipping the output that decides each family's verdict must flip the verdict.
        flips = {
            "tolerance/integers-exact": lambda o: o.update(balanced=False),
            "balance/exact": lambda o: o.update(errors=["Balance failed"]),
            "booking/fifo": lambda o: o.update(errors=["Ambiguous"]),
        }
        for vid, flip in flips.items():
            with self.subTest(vid):
                self.doc = json.loads((vectors.CORPUS / "vectors.json").read_text(encoding="utf-8"))
                flip(self.vec(vid)["output"])
                self.assertReports(f"{vid}: declared positive but Beancount says negative")
        item_flips = {
            "account/default-roots": ("valid", False),
            "currency/grammar": ("valid", False),
            "price/same-day-last-wins": ("number", None),
            "precision/tie": ("most_common", None),
        }
        for vid, (key, value) in item_flips.items():
            with self.subTest(vid):
                self.doc = json.loads((vectors.CORPUS / "vectors.json").read_text(encoding="utf-8"))
                self.vec(vid)["items"][0]["output"][key] = value
                self.assertReports("declared positive but Beancount says negative")

    def test_a_tolerance_error_alone_makes_it_negative(self) -> None:
        self.vec("tolerance/integers-exact")["output"]["errors"] = ["Invalid reference to unknown account"]
        self.assertReports("tolerance/integers-exact: declared positive but Beancount says negative")

    def test_a_family_without_a_negative_vector(self) -> None:
        for item in self.vec("currency/grammar")["items"]:
            item["polarity"] = "positive"
            item["output"]["valid"] = True
        self.assertReports("currency: the family has no negative vector")

    def test_a_family_without_a_positive_vector(self) -> None:
        for item in self.vec("currency/grammar")["items"]:
            item["polarity"] = "negative"
            item["output"]["valid"] = False
        self.assertReports("currency: the family has no positive vector")


class Port(Corpus):
    def test_a_wrong_port_verdict(self) -> None:
        self.vec("tolerance/written-scale-10.50")["output"]["port"] = "balanced"
        self.assertReports("tolerance tolerance/written-scale-10.50: port verdict 'balanced', the rule gives 'unbalanced'")

    def test_indeterminate_is_not_balanced(self) -> None:
        self.vec("tolerance/elided-leg-quantized")["output"]["port"] = "balanced"
        self.assertReports("port verdict 'balanced', the rule gives 'indeterminate'")

    def test_needs_booking_is_the_or_of_the_postings(self) -> None:
        self.vec("tolerance/elided-leg-quantized")["output"]["needs_booking"] = False
        self.assertReports("tolerance/elided-leg-quantized: needs_booking is not the OR")

    def test_a_left_out_part_must_be_flagged(self) -> None:
        cost = {"per": "1", "total": None, "currency": "USD", "date": None, "label": None, "merge": False}
        cases = {
            "units": lambda p: p.update(units="MISSING"),
            "units number": lambda p: p["units"].update(number="MISSING"),
            "units currency": lambda p: p["units"].update(currency="MISSING"),
            "cost per": lambda p: p.update(cost={**cost, "per": "MISSING"}),
            "cost total": lambda p: p.update(cost={**cost, "total": "MISSING"}),
            "cost currency": lambda p: p.update(cost={**cost, "currency": "MISSING"}),
            "price number": lambda p: p.update(price={"number": "MISSING", "currency": "USD", "total": False}),
            "price currency": lambda p: p.update(price={"number": "1", "currency": "MISSING", "total": False}),
        }
        for name, change in cases.items():
            with self.subTest(name):
                self.doc = json.loads((vectors.CORPUS / "vectors.json").read_text(encoding="utf-8"))
                change(self.vec("tolerance/integers-exact")["input"]["postings"][0])
                self.assertReports("tolerance/integers-exact posting 0: needs_booking is False, "
                                   "the written posting says True")

    def test_a_flag_with_nothing_left_out(self) -> None:
        # The other direction (F5): a posting flagged although every part is written.
        self.vec("tolerance/integers-exact")["input"]["postings"][0]["needs_booking"] = True
        self.vec("tolerance/integers-exact")["output"]["needs_booking"] = True
        self.vec("tolerance/integers-exact")["output"]["port"] = "indeterminate"
        self.assertReports("tolerance/integers-exact posting 0: needs_booking is True, the written posting says False")

    def test_a_refused_entry_has_no_port_verdict(self) -> None:
        self.vec("tolerance/negative-price-refused")["output"]["port"] = "balanced"
        self.assertReports("tolerance/negative-price-refused: port verdict 'balanced', the rule gives 'not-pinned'")

    def test_not_pinned_only_for_refused_entries(self) -> None:
        self.vec("tolerance/written-scale-10.50")["output"]["port"] = "not-pinned"
        self.assertReports("port verdict 'not-pinned', the rule gives 'unbalanced'")
        self.doc = json.loads((vectors.CORPUS / "vectors.json").read_text(encoding="utf-8"))
        self.vec("tolerance/two-elided-legs")["output"]["port"] = "not-pinned"
        self.assertReports("port verdict 'not-pinned', the rule gives 'indeterminate'")

    def test_balanced_must_agree_with_beancounts_validation(self) -> None:
        self.vec("tolerance/written-scale-10.50")["output"]["balanced"] = True
        self.vec("tolerance/written-scale-10.50")["output"]["port"] = "balanced"
        self.assertReports("tolerance/written-scale-10.50: balanced=True disagrees with Beancount's own validation")
        self.doc = json.loads((vectors.CORPUS / "vectors.json").read_text(encoding="utf-8"))
        self.vec("tolerance/integers-exact")["output"]["errors"] = ["Transaction does not balance: (1 USD)"]
        self.assertReports("tolerance/integers-exact: balanced=True disagrees with Beancount's own validation")

    def test_a_complete_cost_is_not_flagged_by_the_written_rule(self) -> None:
        p = {"units": {"number": "3", "currency": "VTI"}, "cost": {"per": "0", "total": "1000.00", "currency": "USD"},
             "price": None}
        self.assertFalse(vectors.written_needs_booking(p))
        p["cost"]["total"] = None
        self.assertFalse(vectors.written_needs_booking(p))
        p["cost"]["total"] = "MISSING"  # {100 # USD} is not {100 USD}
        self.assertTrue(vectors.written_needs_booking(p))


class Balance(Corpus):
    def test_a_tolerance_that_does_not_explain_a_pass(self) -> None:
        self.vec("balance/inferred-boundary")["output"]["tolerance"] = "0.005"
        self.assertReports("balance balance/inferred-boundary: difference 0.01 and tolerance 0.005 do not explain")

    def test_a_tolerance_that_does_not_explain_a_failure(self) -> None:
        self.vec("balance/inferred-over")["output"]["tolerance"] = "0.1"
        self.assertReports("balance balance/inferred-over: difference 0.05 and tolerance 0.1 do not explain")

    def test_another_error_is_not_judged_by_the_numbers(self) -> None:
        # An assertion refused for another reason (its currency) is not judged by its numbers.
        self.vec("balance/currency-not-allowed")["output"]["difference"] = "-5"
        self.assertEqual([p for p in self.problems() if "currency-not-allowed" in p], [])

    def test_a_dropped_transaction_with_known_weights(self) -> None:
        o = self.vec("tolerance/two-elided-legs")["output"]
        o["needs_booking"] = False
        for p in self.vec("tolerance/two-elided-legs")["input"]["postings"]:
            p["needs_booking"] = False
            p["units"] = {"number": "1", "currency": "USD"}
        self.assertReports("tolerance tolerance/two-elided-legs: Beancount dropped a transaction whose weights are known")


class Coverage(Corpus):
    def test_every_named_behaviour_has_a_vector(self) -> None:
        # Remove every vector that shows one behaviour; the check must name that behaviour.
        pristine = json.loads((vectors.CORPUS / "vectors.json").read_text(encoding="utf-8"))
        for family, preds in vectors.COVERAGE.items():
            for name, pred in preds.items():
                with self.subTest(f"{family}: {name}"):
                    self.doc = copy.deepcopy(pristine)
                    cases = self.doc["families"][family]
                    if family in vectors.ITEMS:
                        for case in cases:
                            case["items"] = [i for i in case["items"] if not pred(case, i)]
                    else:
                        cases[:] = [c for c in cases if not pred(c, c)]
                    self.assertReports(f"{family}: coverage: no vector for {name!r}")


    def test_every_behaviour_check_discriminates(self) -> None:
        # A behaviour check that every vector of its family satisfies proves nothing.
        for family, preds in vectors.COVERAGE.items():
            for name, pred in preds.items():
                with self.subTest(f"{family}: {name}"):
                    self.assertFalse(all(pred(c, i) for c, i in vectors.units(self.doc, family)))


class Fresh(Corpus):
    def test_a_hand_edited_vector(self) -> None:
        self.vec("tolerance/written-scale-10.50")["output"]["residual"]["USD"] = "-0.040"
        self.assertReports("vectors.json differs from a fresh run", fresh=True)

    def test_a_case_changed_without_regenerating(self) -> None:
        self.edit_cases('{ account = "Expenses:Rent", units = "5.21 USD" }', '{ account = "Expenses:Rent", units = "5.20 USD" }')
        self.assertReports("vectors.json differs from a fresh run", fresh=True)

    def test_a_setup_that_does_not_load(self) -> None:
        self.edit_cases('2020-01-01 * "deposit"\n  Assets:Bank  100.00 USD\n  Income:Salary  -100.00 USD\n',
                        '2020-01-01 * "deposit"\n  Assets:Bank  100.00 USD\n  Income:Salary  -99.00 USD\n')
        self.assertReports("generator: balance/exact: setup does not load cleanly", fresh=True)

    def test_a_price_ledger_that_does_not_load(self) -> None:
        self.edit_cases("2020-01-01 price XYZ 0 USD", "2020-01-01 price XYZ USD")
        self.assertReports("generator: price/zero-not-inverted: ledger does not load cleanly", fresh=True)

    def test_a_precision_ledger_that_does_not_load(self) -> None:
        self.edit_cases('  Assets:Bank  1.5 EUR\n  Income:Gift  -1.5 EUR\n', '  Assets:Bank  1.5 EUR\n  Income:Gift  -1.4 EUR\n')
        self.assertReports("generator: precision/tie: ledger does not load cleanly", fresh=True)

    def test_a_second_vector_transaction(self) -> None:
        self.edit_cases('setup = """\n2020-01-01 * "buy"\n  Assets:Broker  10 VTI {100 USD}\n  Assets:Bank  -1000 USD\n"""\n'
                        'postings = [\n  { account = "Assets:Broker", units = "-5 VTI", cost = "{100 USD}", price = "@ 120 USD" },\n'
                        '  { account = "Assets:Bank", units = "600.00 USD" },\n  { account = "Income:Gains"',
                        'setup = """\n2020-01-01 * "vector"\n  Assets:Broker  10 VTI {100 USD}\n  Assets:Bank  -1000 USD\n"""\n'
                        'postings = [\n  { account = "Assets:Broker", units = "-5 VTI", cost = "{100 USD}", price = "@ 120 USD" },\n'
                        '  { account = "Assets:Bank", units = "600.00 USD" },\n  { account = "Income:Gains"')
        self.assertReports("generator: more than one vector transaction", fresh=True)

    def test_a_transaction_that_does_not_parse(self) -> None:
        self.edit_cases('{ account = "Assets:Bank", units = "-10 USD" }', '{ account = "assets:Bank", units = "-10 USD" }')
        self.assertReports("generator: tolerance/integers-exact: the transaction does not parse", fresh=True)

    def test_a_second_balance_entry(self) -> None:
        self.edit_cases('  Income:Salary  -100.00 USD\n"""\naccount = "Assets:Bank"\namount = "100.00 USD"\n',
                        '  Income:Salary  -100.00 USD\n2020-06-01 balance Assets:Bank  100.00 USD\n"""\n'
                        'account = "Assets:Bank"\namount = "100.00 USD"\n')
        self.assertReports("generator: balance/exact: expected one balance entry", fresh=True)

    def test_a_dropped_transaction_with_known_weights_is_refused_by_the_generator(self) -> None:
        case = tomllib.loads(self.cases_text)["tolerance"][0]
        real = gen_domain_vectors.loader.load_string

        def drop(text: str):  # Beancount loads, but without the vector transaction
            entries, errors, options = real(text)
            return [e for e in entries if getattr(e, "narration", None) != gen_domain_vectors.NARRATION], errors, options

        with mock.patch.object(gen_domain_vectors.loader, "load_string", drop):
            with self.assertRaisesRegex(gen_domain_vectors.VectorError, "dropped a transaction whose weights are known"):
                gen_domain_vectors.gen_tolerance(case)


class Generator(unittest.TestCase):
    """The generator's own small rules."""

    def test_numbers_are_plain_and_keep_their_exponent(self) -> None:
        from decimal import Decimal

        self.assertEqual(gen_domain_vectors.num(Decimal("10.50")), "10.50")
        self.assertEqual(gen_domain_vectors.num(Decimal("1E-25")), "0.0000000000000000000000001")
        self.assertIsNone(gen_domain_vectors.num(None))
        self.assertIsNone(gen_domain_vectors.num(gen_domain_vectors.MISSING))

    def test_every_account_used_is_opened(self) -> None:
        text = gen_domain_vectors.with_opens([], '2020-01-01 * "x"\n  Assets:A  1 USD\n  Expenses:B  -1 USD\n')
        self.assertIn("2000-01-01 open Assets:A\n", text)
        self.assertIn("2000-01-01 open Expenses:B\n", text)

    def test_an_account_opened_in_the_setup_is_not_opened_twice(self) -> None:
        text = gen_domain_vectors.with_opens([], '2000-01-01 open Assets:A "FIFO"\n2020-01-01 * "x"\n  Assets:A  1 USD\n'
                                                 '  Expenses:B  -1 USD\n')
        self.assertEqual(len(re.findall(r"open Assets:A", text)), 1)

    def test_a_negative_price_is_served_as_written(self) -> None:
        doc = json.loads((vectors.CORPUS / "vectors.json").read_text(encoding="utf-8"))
        v = next(v for v in doc["families"]["tolerance"] if v["id"] == "tolerance/negative-price-refused")
        self.assertEqual(v["input"]["postings"][0]["price"], {"number": "-100", "currency": "USD", "total": False})

    def test_left_out_parts_are_marked_not_null(self) -> None:
        doc = json.loads((vectors.CORPUS / "vectors.json").read_text(encoding="utf-8"))
        v = next(v for v in doc["families"]["tolerance"] if v["id"] == "tolerance/elided-leg-quantized")
        self.assertEqual(v["input"]["postings"][2]["units"], "MISSING")
        v = next(v for v in doc["families"]["tolerance"] if v["id"] == "tolerance/partial-cost-total-only")
        self.assertEqual(v["input"]["postings"][0]["cost"]["per"], "MISSING")

    def test_a_total_price_keeps_the_written_total(self) -> None:
        doc = json.loads((vectors.CORPUS / "vectors.json").read_text(encoding="utf-8"))
        v = next(v for v in doc["families"]["tolerance"] if v["id"] == "tolerance/total-price-division")
        self.assertEqual(v["input"]["postings"][0]["price"], {"number": "0.01", "currency": "BTC", "total": True})

    def test_needs_booking_per_part(self) -> None:
        from beancount.parser import parser

        def flag(line: str) -> bool:
            entries, _, _ = parser.parse_string(f'2020-01-01 * "x"\n  Assets:A  {line}\n  Assets:B  -1 USD\n')
            return gen_domain_vectors.posting_needs_booking(entries[0].postings[0])

        self.assertFalse(flag("1 VTI {100 USD}"))
        self.assertFalse(flag("3 VTI {{1000 USD}}"))
        self.assertFalse(flag("1 VTI @ 100 USD"))
        self.assertTrue(flag("1 VTI {}"))
        self.assertTrue(flag("1 VTI {100 # USD}"))
        self.assertTrue(flag("1 VTI {# 5 USD}"))
        self.assertTrue(flag("1 VTI {100}"))
        self.assertTrue(flag("VTI {100 USD}"))
        self.assertTrue(flag("1 VTI @ USD"))


if __name__ == "__main__":
    unittest.main()
