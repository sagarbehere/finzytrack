"""The demo generator in Beancount 3.2.0 (S0027; ADR-0022 "Validity gates"; review:22 §1; review:27 §1(d)).

The generator lives in the main sidecar and writes ledger text only (ADR-0054); this oracle-environment test
imports it from `backend/src`, writes each size to a temp folder, loads it with Beancount and checks:
- 0 Beancount errors (ADR-0022), and the household's transaction count as Beancount counts it;
- one assertion per fragment, run on a Beancount-free view of Beancount's own booked output (EP-29: the
  expected facts come from each fragment's stated rule, not from the generator's output);
- the metadata contract: only keys of ADR-0026 (app-written) and review:28 §4.2 (user vocabulary), never a
  retired machinery key;
- plausibility: the checking account never ends a day below zero.
Each check sits beside the planted ledger it must refuse.

Every size runs in the pre-push hook and in T1 (`ci.yml` oracle-unit): 54k generates in about 0.6 s and loads in
about 1.5 s (S0027 journal).
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from beancount import loader
from beancount.core import data

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend" / "src"))

from app.content.demo import (  # noqa: E402
    BookedPosting,
    BookedTxn,
    LedgerView,
    Size,
    default_fragments,
    generate,
    span_for,
)

ANCHOR = date(2026, 10, 25)
SIZES = list(Size)

# ADR-0026 "App-written keys, the whole list" and review:28 §4.2 (the user vocabulary the app recognises).
APP_WRITTEN = {"import-key", "txn-date", "original", "statement-amount", "memo"}
VOCABULARY = {
    "name",
    "asset-class",
    "exposure",
    "fetch-symbol",
    "isin",
    "issue-date",
    "denomination",
    "account-type",
    "interest-rate",
    "maturity-date",
    "interest-account",
    "account_number",
    "ofx-bank-id",
    "ifsc_code",
    "swift_bic",
    "display-name",
    "description",
    "income-type",
    "dividend-of",
    "reason",
    "note",
}
# ADR-0026 "Not written any more" (checked by name as well, so widening the allowlist cannot readmit them).
RETIRED = {
    "id",
    "content_hash",
    "source_account",
    "external_id",
    "external_id_type",
    "source_rule",
    "status",
    "import-batch",
    "import-key-override",
    "advisor",
}
LOCATION = {"filename", "lineno"}


def load_text(text: str) -> tuple[list[data.Directive], list[object]]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "main.beancount"
        root.write_text(text, encoding="utf-8")
        entries, errors, _ = loader.load_file(str(root))
    return entries, errors


def view_of(entries: list[data.Directive]) -> LedgerView:
    txns = []
    for e in entries:
        if isinstance(e, data.Transaction):
            postings = tuple(BookedPosting(p.account, p.units.number, p.units.currency) for p in e.postings)
            txns.append(BookedTxn(e.date, e.payee or "", e.narration or "", postings))
    return LedgerView(tuple(txns))


def meta_problems(entries: list[data.Directive]) -> list[str]:
    problems = []
    for e in entries:
        metas = [e.meta] + ([p.meta or {} for p in e.postings] if isinstance(e, data.Transaction) else [])
        for meta in metas:
            for key in meta:
                if key in LOCATION or key.startswith("__"):
                    continue
                if key in RETIRED or key.startswith("stmt_") or key not in APP_WRITTEN | VOCABULARY:
                    problems.append(f"{e.date} {type(e).__name__}: metadata key {key!r} is outside ADR-0026")
        if isinstance(e, data.Custom) and e.type == "statement-import":
            problems.append(f'{e.date}: a retired custom "statement-import" record')
    return problems


def negative_days(entries: list[data.Directive], account: str) -> list[str]:
    by_day: dict[date, Decimal] = defaultdict(Decimal)
    for e in entries:
        if isinstance(e, data.Transaction):
            for p in e.postings:
                if p.account == account:
                    by_day[e.date] += p.units.number
    balance, bad = Decimal(0), []
    for d in sorted(by_day):
        balance += by_day[d]
        if balance < 0:
            bad.append(f"{d}: {account} ends the day at {balance}")
    return bad


def drop_blocks(text: str, pattern: str, keep_first: int = 0) -> str:
    """The text without every blank-line block that matches *pattern*, except the first *keep_first* of them."""
    out, seen = [], 0
    for block in text.split("\n\n"):
        if re.search(pattern, block):
            seen += 1
            if seen > keep_first:
                continue
        out.append(block)
    return "\n\n".join(out)


def duplicate_block(text: str, pattern: str) -> str:
    blocks = text.split("\n\n")
    i = next(i for i, b in enumerate(blocks) if re.search(pattern, b))
    return "\n\n".join(blocks[: i + 1] + [blocks[i]] + blocks[i + 1 :])


class Generated(unittest.TestCase):
    loaded: dict[Size, tuple[str, list[data.Directive], list[object]]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.loaded = {}
        for size in SIZES:
            text = generate(ANCHOR, size, default_fragments()).files["main.beancount"]
            entries, errors = load_text(text)
            cls.loaded[size] = (text, entries, errors)

    def test_every_size_loads_with_zero_errors(self) -> None:
        for size, (_, _, errors) in self.loaded.items():
            with self.subTest(size=size.value):
                self.assertEqual([str(e) for e in errors], [])

    def test_beancount_counts_the_size_transactions(self) -> None:
        for size, (_, entries, _) in self.loaded.items():
            with self.subTest(size=size.value):
                self.assertEqual(sum(isinstance(e, data.Transaction) for e in entries), size.transactions)

    def test_every_fragment_assertion_holds(self) -> None:
        for size, (_, entries, _) in self.loaded.items():
            for fragment in default_fragments():
                with self.subTest(size=size.value, fragment=fragment.name):
                    self.assertEqual(fragment.check(view_of(entries), span_for(ANCHOR, size), size), [])

    def test_the_demo_is_at_least_as_large_as_the_legacy_demo(self) -> None:
        # INV-20's "a write takes at most 1 s at demo size" was set on the legacy demo: 5,441 transactions
        # (review:28 §1) and 5,581 entries (review:D1 §9). A smaller demo would loosen that owner-only absolute.
        _, entries, _ = self.loaded[Size.DEMO]
        self.assertGreaterEqual(sum(isinstance(e, data.Transaction) for e in entries), 5_441)
        self.assertGreaterEqual(len(entries), 5_581)

    def test_only_contract_metadata_is_written(self) -> None:
        for size, (_, entries, _) in self.loaded.items():
            with self.subTest(size=size.value):
                self.assertEqual(meta_problems(entries), [])

    def test_the_checking_account_never_ends_a_day_below_zero(self) -> None:
        for size, (_, entries, _) in self.loaded.items():
            with self.subTest(size=size.value):
                self.assertEqual(negative_days(entries, "Assets:Bank:Checking"), [])


class MustNotPass(unittest.TestCase):
    """Each gate refuses a planted ledger (EP-29), so a green run is not vacuous."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = generate(ANCHOR, Size.DEMO, default_fragments()).files["main.beancount"]
        cls.span = span_for(ANCHOR, Size.DEMO)
        (cls.household,) = default_fragments()

    def household_problems(self, text: str) -> list[str]:
        entries, errors = load_text(text)
        self.assertEqual(errors, [])
        return self.household.check(view_of(entries), self.span, Size.DEMO)

    def test_an_unbalanced_transaction_is_a_beancount_error(self) -> None:
        planted = (
            self.text + '\n2026-10-20 * "Planted" "unbalanced"\n  Expenses:Food:Groceries  1.00 USD\n'
            "  Assets:Bank:Checking  -0.90 USD\n"
        )
        self.assertNotEqual(load_text(planted)[1], [])

    def test_a_missing_salary_month_fails_the_household_assertion(self) -> None:
        planted = drop_blocks(self.text, r"\n  Income:Salary", keep_first=5)
        self.assertNotEqual(self.household_problems(planted), [])

    def test_a_doubled_salary_fails_the_household_assertion(self) -> None:
        self.assertNotEqual(self.household_problems(duplicate_block(self.text, r"\n  Income:Salary")), [])

    def test_a_salary_that_is_not_income_fails_the_household_assertion(self) -> None:
        planted = re.sub(r"(Income:Salary\s+)-", r"\1", self.text, count=1)
        planted = re.sub(r"(Expenses:Taxes:Income\s+)(\d)", r"\1-\2", planted, count=1)
        planted = re.sub(r"(Assets:Bank:Checking\s+)(\d)", r"\1-\2", planted, count=1)
        entries, _ = load_text(planted)
        self.assertNotEqual(self.household.check(view_of(entries), self.span, Size.DEMO), [])

    def test_the_unchanged_ledger_passes_the_household_assertion(self) -> None:
        self.assertEqual(self.household_problems(self.text), [])

    def test_a_retired_key_is_refused(self) -> None:
        for key in ("id", "content_hash", "source_account", "status", "stmt_ref", "my-key"):
            with self.subTest(key=key):
                planted = re.sub(r"(\n\d{4}-\d{2}-\d{2} \* [^\n]*\n)", rf'\1  {key}: "x"\n', self.text, count=1)
                self.assertNotEqual(meta_problems(load_text(planted)[0]), [])

    def test_a_retired_posting_key_is_refused(self) -> None:
        planted = re.sub(r"(\n  Income:Salary[^\n]*\n)", r'\1    source_account: "x"\n', self.text, count=1)
        self.assertNotEqual(meta_problems(load_text(planted)[0]), [])

    def test_vocabulary_keys_are_accepted(self) -> None:
        planted = re.sub(r"(open Assets:Bank:Savings[^\n]*\n)", r'\1  interest-rate: "4.1%"\n', self.text, count=1)
        self.assertIn("interest-rate", planted)
        self.assertEqual(meta_problems(load_text(planted)[0]), [])

    def test_a_negative_day_is_found(self) -> None:
        planted = (
            self.text + '\n2026-10-25 * "Planted" "overdraft"\n  Expenses:Shopping  1000000.00 USD\n'
            "  Assets:Bank:Checking  -1000000.00 USD\n"
        )
        self.assertNotEqual(negative_days(load_text(planted)[0], "Assets:Bank:Checking"), [])


if __name__ == "__main__":
    unittest.main()
