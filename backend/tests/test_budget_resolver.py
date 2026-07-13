"""budget_for_range resolver — Fava parity & precision (§7.5, dev-docs/budget.md §6).

Asserts EXACT decimal equality (not float-approx) per money-types.md.
"""

import json
from datetime import date
from decimal import Decimal

from app.compute.budget_resolver import (
    BudgetDirective,
    parse_budget_directive,
    resolve_budgets,
)


def _d(account, interval, amount, currency="USD", d="2026-01-01", lineno=0, src="main.beancount"):
    return BudgetDirective(date.fromisoformat(d), account, interval, Decimal(amount), currency, src, lineno)


# ── Aligned-period exactness ─────────────────────────────────────────────────


def test_aligned_month_single_directive_is_exact():
    rows, _ = resolve_budgets([_d("Expenses:Food", "monthly", "500.00")],
                              date(2026, 6, 1), date(2026, 6, 30))
    assert rows == [{"account": "Expenses:Food", "currency": "USD", "budget": "500"}]


def test_aligned_year_single_directive_is_exact():
    rows, _ = resolve_budgets([_d("Expenses:Travel", "yearly", "3600")],
                              date(2026, 1, 1), date(2026, 12, 31))
    assert rows[0]["budget"] == "3600"


def test_aligned_quarter_single_directive_is_exact():
    rows, _ = resolve_budgets([_d("Expenses:Food", "quarterly", "1500")],
                              date(2026, 1, 1), date(2026, 3, 31))
    assert rows[0]["budget"] == "1500"


# ── Cross-interval (inherently fractional, but exact via rationals) ──────────


def test_yearly_summed_over_june_matches_daywalk():
    # 3650/365 = 10/day; June has 30 days → exactly 300.
    rows, _ = resolve_budgets([_d("Expenses:Travel", "yearly", "3650")],
                              date(2026, 6, 1), date(2026, 6, 30))
    assert rows[0]["budget"] == "300"


# ── Directive-crossing range → piecewise sum ─────────────────────────────────


def test_directive_crossing_is_piecewise_sum():
    directives = [
        _d("Expenses:Food", "monthly", "500", d="2026-01-01"),
        _d("Expenses:Food", "monthly", "600", d="2026-04-01"),
    ]
    # Jan–Mar at 500 (=1500) + Apr–Jun at 600 (=1800) = 3300.
    rows, _ = resolve_budgets(directives, date(2026, 1, 1), date(2026, 6, 30))
    assert rows[0]["budget"] == "3300"


def test_per_period_reflects_directive_change():
    directives = [
        _d("Expenses:Food", "monthly", "500", d="2026-01-01"),
        _d("Expenses:Food", "monthly", "600", d="2026-04-01"),
    ]
    rows, _ = resolve_budgets(directives, date(2026, 3, 1), date(2026, 4, 30), group_by="period")
    by_period = {r["period"]: r["budget"] for r in rows}
    assert by_period == {"2026-03": "500", "2026-04": "600"}


# ── from omitted (None) → each account from its own inception ─────────────────


def test_per_period_from_none_starts_at_inception_no_leading_zeros():
    # Budget starts 2026-03; asking with from=None → the series begins at 2026-03
    # (no empty pre-inception months), not at some caller-supplied floor.
    directives = [_d("Expenses:Food", "monthly", "500", d="2026-03-01")]
    rows, _ = resolve_budgets(directives, None, date(2026, 5, 31), group_by="period")
    by_period = {r["period"]: r["budget"] for r in rows}
    assert by_period == {"2026-03": "500", "2026-04": "500", "2026-05": "500"}


def test_range_from_none_totals_from_inception():
    directives = [_d("Expenses:Food", "monthly", "500", d="2026-03-01")]
    rows, _ = resolve_budgets(directives, None, date(2026, 5, 31))
    assert rows[0]["budget"] == "1500"  # Mar+Apr+May at 500


def test_from_none_uses_each_accounts_own_inception():
    directives = [
        _d("Expenses:Food", "monthly", "500", d="2026-01-01"),
        _d("Expenses:Rent", "monthly", "1000", d="2026-03-01"),
    ]
    rows, _ = resolve_budgets(directives, None, date(2026, 4, 30), group_by="period")
    food = sorted(r["period"] for r in rows if r["account"] == "Expenses:Food")
    rent = sorted(r["period"] for r in rows if r["account"] == "Expenses:Rent")
    assert food == ["2026-01", "2026-02", "2026-03", "2026-04"]
    assert rent == ["2026-03", "2026-04"]  # starts at its own, later, inception


# ── Multi-currency isolation ─────────────────────────────────────────────────


def test_multi_currency_isolation():
    directives = [
        _d("Expenses:Phone", "monthly", "50", currency="USD"),
        _d("Expenses:Phone", "monthly", "1500", currency="INR"),
    ]
    rows, _ = resolve_budgets(directives, date(2026, 6, 1), date(2026, 6, 30))
    by_curr = {r["currency"]: r["budget"] for r in rows}
    assert by_curr == {"USD": "50", "INR": "1500"}

    usd_only, _ = resolve_budgets(directives, date(2026, 6, 1), date(2026, 6, 30), currency="USD")
    assert usd_only == [{"account": "Expenses:Phone", "currency": "USD", "budget": "50"}]


# ── Last-wins ambiguity + surfaced warning (§4.3) ────────────────────────────


def test_same_day_ambiguity_last_wins_and_warns():
    directives = [
        _d("Expenses:Food", "monthly", "500", lineno=10),
        _d("Expenses:Food", "monthly", "600", lineno=20),  # later line wins
    ]
    rows, warnings = resolve_budgets(directives, date(2026, 6, 1), date(2026, 6, 30))
    assert rows[0]["budget"] == "600"
    assert len(warnings) == 1 and "last wins" in warnings[0]


# ── account filter ───────────────────────────────────────────────────────────


def test_account_filter_returns_only_that_account():
    directives = [
        _d("Expenses:Food", "monthly", "500"),
        _d("Expenses:Rent", "monthly", "2000"),
    ]
    rows, _ = resolve_budgets(directives, date(2026, 6, 1), date(2026, 6, 30), account="Expenses:Food")
    assert rows == [{"account": "Expenses:Food", "currency": "USD", "budget": "500"}]


def test_omitted_account_returns_all_budgeted_accounts():
    directives = [
        _d("Expenses:Food", "monthly", "500"),
        _d("Expenses:Rent", "monthly", "2000"),
    ]
    rows, _ = resolve_budgets(directives, date(2026, 6, 1), date(2026, 6, 30))
    assert {r["account"] for r in rows} == {"Expenses:Food", "Expenses:Rent"}


def test_empty_range_returns_nothing():
    rows, _ = resolve_budgets([_d("Expenses:Food", "monthly", "500")],
                              date(2026, 6, 30), date(2026, 6, 1))
    assert rows == []


# ── Directive parsing from the exported values_json shape ────────────────────


def test_parse_budget_directive_from_export_shape():
    row = {
        "date": "2026-01-01",
        "values_json": json.dumps([
            ["Expenses:Food", "<AccountDummy>"],
            ["monthly", "<class 'str'>"],
            [["500.00", "USD"], "<class 'beancount.core.amount.Amount'>"],
        ]),
        "source_file": "main.beancount",
        "source_lineno": 12,
    }
    parsed = parse_budget_directive(row)
    assert parsed is not None
    assert parsed.account == "Expenses:Food"
    assert parsed.interval == "monthly"
    assert parsed.amount == Decimal("500.00")
    assert parsed.currency == "USD"


def test_parse_rejects_non_budget_shape():
    row = {"date": "2026-01-01", "values_json": json.dumps([["just-a-string", "<class 'str'>"]])}
    assert parse_budget_directive(row) is None


# ── Budget end (tombstone) — dev-docs/budget.md end-budget ───────────────────


def test_tombstone_ends_budget_range_sums_only_active_days():
    # 600/month from Jan, ended from Apr 1. Jan–Mar = 1800; Apr–Jun contributes 0.
    directives = [
        _d("Expenses:Food", "monthly", "600", d="2026-01-01"),
        _d("Expenses:Food", "none", "0", d="2026-04-01"),  # tombstone
    ]
    rows, _ = resolve_budgets(directives, date(2026, 1, 1), date(2026, 6, 30))
    assert rows == [{"account": "Expenses:Food", "currency": "USD", "budget": "1800"}]


def test_fully_ended_group_is_omitted_from_range():
    directives = [
        _d("Expenses:Food", "monthly", "600", d="2026-01-01"),
        _d("Expenses:Food", "none", "0", d="2026-04-01"),
    ]
    # A range entirely after the end → no real budget → account drops out.
    rows, _ = resolve_budgets(directives, date(2026, 4, 1), date(2026, 6, 30))
    assert rows == []


def test_tombstone_is_per_currency():
    # End USD but keep INR: a range after the end shows only INR.
    directives = [
        _d("Expenses:Phone", "monthly", "50", currency="USD", d="2026-01-01"),
        _d("Expenses:Phone", "monthly", "1500", currency="INR", d="2026-01-01"),
        _d("Expenses:Phone", "none", "0", currency="USD", d="2026-04-01"),
    ]
    rows, _ = resolve_budgets(directives, date(2026, 4, 1), date(2026, 4, 30))
    assert rows == [{"account": "Expenses:Phone", "currency": "INR", "budget": "1500"}]


def test_un_end_by_superseding_with_a_new_budget():
    # 500 → end → 700. After the new budget the account is budgeted again.
    directives = [
        _d("Expenses:Food", "monthly", "500", d="2026-01-01"),
        _d("Expenses:Food", "none", "0", d="2026-04-01"),
        _d("Expenses:Food", "monthly", "700", d="2026-07-01"),
    ]
    ended, _ = resolve_budgets(directives, date(2026, 5, 1), date(2026, 5, 31))
    assert ended == []  # still ended in May
    active, _ = resolve_budgets(directives, date(2026, 7, 1), date(2026, 7, 31))
    assert active == [{"account": "Expenses:Food", "currency": "USD", "budget": "700"}]


def test_per_period_series_continues_through_an_end_with_zeros():
    directives = [
        _d("Expenses:Food", "monthly", "600", d="2026-01-01"),
        _d("Expenses:Food", "none", "0", d="2026-03-01"),
    ]
    rows, _ = resolve_budgets(directives, date(2026, 1, 1), date(2026, 4, 30), group_by="period")
    by_period = {r["period"]: r["budget"] for r in rows}
    assert by_period == {"2026-01": "600", "2026-02": "600", "2026-03": "0", "2026-04": "0"}


def test_parse_tombstone_from_export_shape():
    row = {
        "date": "2026-04-01",
        "values_json": json.dumps([
            ["Expenses:Food", "<AccountDummy>"],
            ["none", "<class 'str'>"],
            [["0", "USD"], "<class 'beancount.core.amount.Amount'>"],
        ]),
        "source_file": "main.beancount",
        "source_lineno": 20,
    }
    parsed = parse_budget_directive(row)
    assert parsed is not None
    assert parsed.is_end is True
    assert parsed.account == "Expenses:Food"
    assert parsed.currency == "USD"


def test_parse_tombstone_without_currency_is_rejected():
    # No amount pair → no currency to scope the end → not a valid tombstone.
    row = {
        "date": "2026-04-01",
        "values_json": json.dumps([
            ["Expenses:Food", "<AccountDummy>"],
            ["none", "<class 'str'>"],
        ]),
    }
    assert parse_budget_directive(row) is None
