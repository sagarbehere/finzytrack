"""Demo-ledger generator: build-time anchoring, budget dating, determinism, and
that it parses (dev-docs/seed-content-refresh.md §8, §12).

The generator is a standalone build/dev script (backend/scripts/), imported here
only to unit-test its re-anchoring + budget behaviour.
"""

import re
from datetime import date

import beancount.loader as loader
import pytest

import scripts.generate_fake as gen


def _dates(text):
    return [date.fromisoformat(l[:10]) for l in text.splitlines() if re.match(r"\d{4}-\d{2}-\d{2} ", l)]


def _budget_lines(text):
    return [l for l in text.splitlines() if re.match(r'\d{4}-\d{2}-\d{2} custom "budget"', l)]


def test_deterministic_output():
    a = gen.generate(anchor_month=date(2026, 5, 1), buffer_months=2)
    b = gen.generate(anchor_month=date(2026, 5, 1), buffer_months=2)
    assert a == b


@pytest.mark.parametrize("anchor,buffer", [
    (date(2026, 1, 1), 2),
    (date(2027, 9, 1), 2),
    (date(2025, 11, 1), 3),
])
def test_span_ends_at_anchor_plus_buffer(anchor, buffer):
    text = gen.generate(anchor_month=anchor, buffer_months=buffer)
    last = max(_dates(text))
    expected_end = gen._add_months(anchor, buffer)
    # The last transaction lands in the anchor+buffer month (clean calendar end).
    assert (last.year, last.month) == (expected_end.year, expected_end.month)


def test_parses_without_errors_and_currencies_hardcoded():
    text = gen.generate(anchor_month=date(2026, 7, 1), buffer_months=2)
    # Demo currencies are hardcoded (no seed-time substitution needed for the demo).
    assert "{default_currency}" not in text
    assert re.search(r"\bUSD\b", text) and re.search(r"\bINR\b", text)
    _, errors, _ = loader.load_string(text)
    assert errors == []


def test_budget_directives_present_and_dated_relative_to_anchor():
    anchor, buffer = date(2026, 6, 1), 2
    text = gen.generate(anchor_month=anchor, buffer_months=buffer)
    budgets = _budget_lines(text)
    # 13 directives: monthly + a mid-span raise + nested pair + yearly + INR line.
    assert len(budgets) == 13
    assert any(' "yearly" ' in l for l in budgets)               # non-monthly interval
    assert any(l.rstrip().endswith("INR   ; second currency (multi-currency)") or
               "1300 INR" in l for l in budgets)                 # second currency
    assert sum("Expenses:Groceries" in l for l in budgets) == 3  # base + raise + INR

    # Effective budgets precede the final (anchor+buffer) month, and that month has
    # actual transactions — so a calendar-aligned "this month" has both.
    end = gen._add_months(anchor, buffer)
    final_prefix = f"{end.year:04d}-{end.month:02d}"
    budget_dates = [date.fromisoformat(l[:10]) for l in budgets]
    assert min(budget_dates) < date(end.year, end.month, 1)
    assert any(l.startswith(final_prefix) and ' * "' in l for l in text.splitlines())


def test_shift_preserves_pad_before_balance():
    """The day-based shift must never collapse the pad(30th)/balance(31st) pair
    onto the same day (a month-shift-with-clamp bug). Proven by a clean parse
    across several anchors."""
    for anchor in (date(2026, 2, 1), date(2026, 8, 1), date(2027, 1, 1)):
        text = gen.generate(anchor_month=anchor, buffer_months=2)
        _, errors, _ = loader.load_string(text)
        assert errors == [], f"parse errors at anchor {anchor}: {errors}"
