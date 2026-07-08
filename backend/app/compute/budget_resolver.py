"""Budget resolver — the single source of budget math (dev-docs/budget.md §6).

Fava-style full-precision daily-equivalent resolver over `custom "budget"`
directives. Used by both the `budget_for_range` compute function (recipe DAG)
and the `/api/budgets` CRUD read path (§6.3) — the same math, one home.

Exactness: the per-day equivalent `amount / days_in_period(day)` is accumulated
with exact rational arithmetic (`fractions.Fraction`), so a calendar-aligned
period with a single active directive sums to *exactly* the directive amount
(no fractional drift), while cross-interval views remain correctly fractional.
Money is converted to a Decimal string only at the boundary (money-types.md).
"""

from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

from app.core.budget_directives import (
    BUDGET_END, INTERVALS, budget_fields_complete, budget_id,
)

# Precision floor for non-terminating daily-equivalents (~10 fractional digits);
# the display formatter rounds further. Terminating fractions stay exact.
_QUANTIZE = Decimal("0.0000000001")


@dataclass(frozen=True)
class BudgetDirective:
    date: date
    account: str
    interval: str
    amount: Decimal
    currency: str
    source_file: str | None = None
    source_lineno: int = 0

    @property
    def id(self) -> str:
        """Stable id from the source location — the single hashing site
        (app.core.budget_directives.budget_id)."""
        return budget_id(self.source_file, self.source_lineno)

    @property
    def is_end(self) -> bool:
        """A tombstone: 'no budget from this date' (BUDGET_END sentinel). The
        amount is inert; it contributes nothing to range/period totals."""
        return self.interval == BUDGET_END


# ── Directive parsing (from custom_directives.values_json) ───────────────────


def parse_budget_directive(row: dict) -> BudgetDirective | None:
    """Parse one `custom "budget"` row into a BudgetDirective, or None if it
    doesn't look like a budget directive.

    values_json is a list of [value, dtype] pairs (Beancount Custom values),
    e.g. [["Expenses:Food", "<AccountDummy>"], ["monthly", "..."],
          [["500.00", "USD"], "<...Amount>"]]. We classify by structure so the
    directive's argument order doesn't matter.
    """
    try:
        values = json.loads(row["values_json"]) if row.get("values_json") else []
    except (ValueError, TypeError):
        return None

    account = interval = currency = None
    amount: Decimal | None = None
    for pair in values:
        if not isinstance(pair, (list, tuple)) or not pair:
            continue
        v = pair[0]
        if isinstance(v, (list, tuple)) and len(v) == 2:  # amount: [number, currency]
            try:
                amount = Decimal(str(v[0]))
            except (ValueError, ArithmeticError):
                return None
            currency = str(v[1])
        elif isinstance(v, str) and v in INTERVALS:
            interval = v
        elif isinstance(v, str) and v == BUDGET_END:  # tombstone, before account fallback
            interval = BUDGET_END
        elif isinstance(v, str):
            account = v

    if interval == BUDGET_END:
        # Tombstone: needs an account + currency (from the inert amount); the
        # amount is ignored. See dev-docs/budget.md end-budget.
        if not (account and currency):
            return None
        amount = Decimal(0)
    elif not budget_fields_complete(account, interval, amount, currency):
        return None
    try:
        d = date.fromisoformat(row["date"])
    except (ValueError, TypeError, KeyError):
        return None
    return BudgetDirective(d, account, interval, amount, currency,
                           row.get("source_file"), row.get("source_lineno", 0))


def parse_budget_directives(rows: list[dict]) -> list[BudgetDirective]:
    return [d for d in (parse_budget_directive(r) for r in rows) if d is not None]


# ── Daily-equivalent math ────────────────────────────────────────────────────


def _days_in_quarter(d: date) -> int:
    q_start_month = 3 * ((d.month - 1) // 3) + 1
    return sum(calendar.monthrange(d.year, q_start_month + i)[1] for i in range(3))


def _period_days(interval: str, d: date) -> int:
    if interval == "daily":
        return 1
    if interval == "weekly":
        return 7
    if interval == "monthly":
        return calendar.monthrange(d.year, d.month)[1]
    if interval == "quarterly":
        return _days_in_quarter(d)
    if interval == "yearly":
        return 366 if calendar.isleap(d.year) else 365
    raise ValueError(f"unknown interval '{interval}'")


def _daily_equivalent(directive: BudgetDirective, d: date) -> Fraction:
    return Fraction(directive.amount) / _period_days(directive.interval, d)


def _to_decimal_string(value: Fraction) -> str:
    """Exact when the fraction terminates (aligned periods → the directive
    amount verbatim); otherwise a high-precision decimal the display rounds."""
    if value.denominator == 1:
        return str(value.numerator)
    # Terminates iff the denominator (in lowest terms) has only factors 2 and 5.
    den = value.denominator
    d2 = den
    for p in (2, 5):
        while d2 % p == 0:
            d2 //= p
    if d2 == 1:
        # Exact terminating decimal.
        return str(Decimal(value.numerator) / Decimal(value.denominator))
    # Non-terminating: keep ~10 fractional digits of precision; display rounds.
    return str((Decimal(value.numerator) / Decimal(value.denominator)).quantize(_QUANTIZE))


# ── Effective-directive selection (last-wins) ────────────────────────────────


def _by_account_currency(directives: list[BudgetDirective]) -> dict[tuple[str, str], list[BudgetDirective]]:
    groups: dict[tuple[str, str], list[BudgetDirective]] = {}
    for d in directives:
        groups.setdefault((d.account, d.currency), []).append(d)
    for lst in groups.values():
        lst.sort(key=lambda x: (x.date, x.source_file or "", x.source_lineno))
    return groups


def _ambiguity_warnings(directives: list[BudgetDirective]) -> list[str]:
    """Same (date, account, currency) with different amounts → 'last wins' but
    surface a warning (§4.3)."""
    warnings: list[str] = []
    seen: dict[tuple, Decimal] = {}
    for d in sorted(directives, key=lambda x: (x.date, x.account, x.currency, x.source_file or "", x.source_lineno)):
        key = (d.date, d.account, d.currency)
        if key in seen and seen[key] != d.amount:
            warnings.append(
                f"Ambiguous budget for {d.account} {d.currency} on {d.date.isoformat()}: "
                f"{seen[key]} vs {d.amount} — last wins."
            )
        seen[key] = d.amount
    return warnings


def _effective_on(sorted_directives: list[BudgetDirective], d: date) -> BudgetDirective | None:
    """The last directive (by date, then source order) with date <= d."""
    active = None
    for directive in sorted_directives:
        if directive.date <= d:
            active = directive
        else:
            break
    return active


def effective_directives_as_of(
    directives: list[BudgetDirective], as_of: date
) -> list[BudgetDirective]:
    """One directive per (account, currency): the latest effective on/before
    ``as_of`` (last-wins). The single home of effective-directive selection —
    the /api/budgets CRUD read path calls this rather than reimplementing it."""
    out: list[BudgetDirective] = []
    for sorted_directives in _by_account_currency(directives).values():
        eff = _effective_on(sorted_directives, as_of)
        if eff is not None:
            out.append(eff)
    return out


def _range_total(sorted_directives: list[BudgetDirective], d_from: date, d_to: date) -> Fraction:
    total = Fraction(0)
    day = d_from
    while day <= d_to:
        directive = _effective_on(sorted_directives, day)
        if directive is not None and not directive.is_end:
            total += _daily_equivalent(directive, day)
        day += timedelta(days=1)
    return total


def _has_real_budget_in_range(
    sorted_directives: list[BudgetDirective], d_from: date, d_to: date
) -> bool:
    """True iff some day in [d_from, d_to] has a real (non-tombstone) active
    budget. A group that is tombstoned (or unbudgeted) across the whole range is
    omitted from budget results — 'no longer budgeted' drops out of dashboards."""
    day = d_from
    while day <= d_to:
        directive = _effective_on(sorted_directives, day)
        if directive is not None and not directive.is_end:
            return True
        day += timedelta(days=1)
    return False


def _iter_months(d_from: date, d_to: date):
    """Yield (year, month) for each calendar month from d_from's month to
    d_to's month inclusive."""
    y, m = d_from.year, d_from.month
    while (y, m) <= (d_to.year, d_to.month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


# ── Public resolver ──────────────────────────────────────────────────────────


def resolve_budgets(
    directives: list[BudgetDirective],
    date_from: date,
    date_to: date,
    *,
    currency: str | None = None,
    account: str | None = None,
    group_by: str | None = None,
) -> tuple[list[dict], list[str]]:
    """Resolve budgets over [date_from, date_to].

    Returns (rows, warnings):
      - range mode (group_by None): one row per budgeted (account, currency) in
        range — {account, currency, budget}.
      - per-period mode (group_by 'period'): one row per (account, currency,
        calendar-month) — {account, currency, period: 'YYYY-MM', budget}. Each
        period is the full calendar-month budget (for envelope rollover, §14).
    budget is a Decimal string (money-types.md).
    """
    if date_to < date_from:
        return [], []
    if group_by not in (None, "period"):
        raise ValueError("group_by must be None or 'period'")

    filtered = [d for d in directives if (currency is None or d.currency == currency)
                and (account is None or d.account == account)]
    warnings = _ambiguity_warnings(filtered)
    groups = _by_account_currency(filtered)

    rows: list[dict] = []
    for (acct, curr), sorted_directives in sorted(groups.items()):
        # A group with no real budget anywhere in range (fully ended, or only a
        # tombstone) is not a budgeted account here — omit it entirely (§ end-budget).
        if not _has_real_budget_in_range(sorted_directives, date_from, date_to):
            continue
        if group_by == "period":
            for (y, m) in _iter_months(date_from, date_to):
                m_start = date(y, m, 1)
                m_end = date(y, m, calendar.monthrange(y, m)[1])
                total = _range_total(sorted_directives, m_start, m_end)
                rows.append({
                    "account": acct,
                    "currency": curr,
                    "period": f"{y:04d}-{m:02d}",
                    "budget": _to_decimal_string(total),
                })
        else:
            total = _range_total(sorted_directives, date_from, date_to)
            rows.append({
                "account": acct,
                "currency": curr,
                "budget": _to_decimal_string(total),
            })

    return rows, warnings
