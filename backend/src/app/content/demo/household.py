"""The background household fragment (ADR-0022; review:22 §1 "salary, spending, transfers, cards").

One salaried household in USD, with invented names and amounts (INV-23):
- the 1st: rent; the 5th, 12th and 15th: electricity, internet and phone on the card; the 8th: water;
- the 20th: the card bill, paid in full from checking (every card charge dated before the 20th since the last
  payment);
- the 28th: the salary (gross to income, income tax withheld, net into checking), then a transfer to savings;
- everyday card purchases spread over every day of the span, as many as the size needs: the household writes
  exactly `size.transactions` transactions, the opening balances included.
Amounts are set relative to the anchor year (pay rises 3% a year), so recent months look the same at every size.
It writes no metadata (ADR-0026: the demo writes only the metadata contract).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from app.content.demo.ledger_text import Directive, Posting, commodity, open_account, transaction
from app.content.demo.model import FragmentContext, LedgerView, Size, Span
from app.content.demo.rng import SeededRng

USD = "USD"
CHECKING = "Assets:Bank:Checking"
SAVINGS = "Assets:Bank:Savings"
CARD = "Liabilities:Card:Everyday"
SALARY = "Income:Salary"
SALARY_DAY = 28
_ACCOUNTS = (
    CHECKING,
    SAVINGS,
    CARD,
    "Equity:Opening-Balances",
    SALARY,
    "Expenses:Taxes:Income",
    "Expenses:Housing:Rent",
    "Expenses:Utilities:Electricity",
    "Expenses:Utilities:Water",
    "Expenses:Utilities:Internet",
    "Expenses:Utilities:Phone",
    "Expenses:Food:Groceries",
    "Expenses:Food:Dining",
    "Expenses:Transport:Fuel",
    "Expenses:Shopping",
    "Expenses:Leisure",
    "Expenses:Health",
)
# (account, narration, payees), weight: everyday card purchases.
_PURCHASES: tuple[tuple[tuple[str, str, tuple[str, ...]], int], ...] = (
    (("Expenses:Food:Groceries", "Groceries", ("Corner Grocer", "Green Basket Market", "Hillside Foods")), 35),
    (("Expenses:Food:Dining", "Meal", ("Bluebird Cafe", "Lantern Noodle House", "Pine Street Pizza")), 20),
    (("Expenses:Transport:Fuel", "Fuel", ("Roadside Fuel", "Northgate Petrol")), 12),
    (("Expenses:Shopping", "Household items", ("Bright Home Store", "Riverside Books", "Paper and Thread")), 15),
    (("Expenses:Leisure", "Tickets", ("Starlight Cinema", "City Pool")), 10),
    (("Expenses:Health", "Health", ("Willow Pharmacy", "Family Clinic")), 8),
)
# Fixed monthly items: (day, order among same-day items).
_RENT, _ELECTRICITY, _WATER, _INTERNET, _PHONE, _CARD_BILL, _SALARY, _SAVE = (
    (1, 0),
    (5, 0),
    (8, 0),
    (12, 0),
    (15, 0),
    (20, 0),
    (SALARY_DAY, 0),
    (SALARY_DAY, 1),
)
_PURCHASE_ORDER = 9


def _cents(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def _round_to(x: Decimal, step: int) -> Decimal:
    return _cents((x / step).quantize(Decimal(1), rounding=ROUND_HALF_EVEN) * step)


def _months(span: Span) -> list[date]:
    first = span.start.year * 12 + span.start.month - 1
    last = span.end.year * 12 + span.end.month - 1
    return [date(i // 12, i % 12 + 1, 1) for i in range(first, last + 1)]


@dataclass(frozen=True, slots=True)
class _Pay:
    gross: Decimal
    tax: Decimal
    net: Decimal
    rent: Decimal
    save: Decimal
    spend: Decimal  # the month's budget for everyday card purchases


def _pay(year: int, anchor_year: int) -> _Pay:
    gross = _cents(Decimal(6000) / Decimal("1.03") ** (anchor_year - year))
    tax = _cents(gross * Decimal("0.22"))
    net = gross - tax
    return _Pay(
        gross,
        tax,
        net,
        _round_to(net * Decimal("0.31"), 5),
        _round_to(net * Decimal("0.10"), 10),
        net * Decimal("0.38"),
    )


@dataclass(frozen=True, slots=True)
class _Event:
    date: date
    order: int  # among same-day events
    payee: str
    narration: str
    postings: tuple[Posting, ...]


def _two(d: date, key: tuple[int, int], payee: str, narration: str, to: str, frm: str, amount: Decimal) -> _Event:
    """A two-leg event: *amount* into *to*, out of *frm*."""
    return _Event(d, key[1], payee, narration, (Posting(to, amount, USD), Posting(frm, -amount, USD)))


def _fixed_events(span: Span, rng: SeededRng) -> tuple[list[_Event], list[date]]:
    """The opening balances and the monthly items, and the card bill days (their amounts come later)."""
    anchor_year = span.end.year
    opening = _cents(_pay(span.start.year, anchor_year).net * Decimal("1.5"))
    events = [
        _Event(
            span.start,
            -1,
            "Opening balances",
            "Balances when the books start",
            (
                Posting(CHECKING, opening, USD),
                Posting(SAVINGS, Decimal("5000.00"), USD),
                Posting("Equity:Opening-Balances", -(opening + Decimal("5000.00")), USD),
            ),
        )
    ]
    bill_days: list[date] = []
    for m in _months(span):
        pay = _pay(m.year, anchor_year)
        keys = (_RENT, _ELECTRICITY, _WATER, _INTERNET, _PHONE, _CARD_BILL, _SALARY, _SAVE)
        on = {key: d for key in keys if (d := date(m.year, m.month, key[0])) <= span.end}
        if d := on.get(_RENT):
            events.append(_two(d, _RENT, "Oakview Apartments", "Rent", "Expenses:Housing:Rent", CHECKING, pay.rent))
        if d := on.get(_ELECTRICITY):
            amount = rng.cents(Decimal(55), Decimal(140))
            events.append(
                _two(d, _ELECTRICITY, "City Power", "Electricity", "Expenses:Utilities:Electricity", CARD, amount)
            )
        if d := on.get(_WATER):
            amount = rng.cents(Decimal(28), Decimal(62))
            events.append(_two(d, _WATER, "City Water", "Water", "Expenses:Utilities:Water", CHECKING, amount))
        if d := on.get(_INTERNET):
            amount = Decimal("59.99")
            events.append(_two(d, _INTERNET, "FiberNet", "Internet", "Expenses:Utilities:Internet", CARD, amount))
        if d := on.get(_PHONE):
            amount = Decimal("45.00")
            events.append(_two(d, _PHONE, "Mobile Co", "Phone plan", "Expenses:Utilities:Phone", CARD, amount))
        if d := on.get(_CARD_BILL):
            bill_days.append(d)
        if d := on.get(_SALARY):
            legs = (
                Posting(SALARY, -pay.gross, USD),
                Posting("Expenses:Taxes:Income", pay.tax, USD),
                Posting(CHECKING, pay.net, USD),
            )
            events.append(_Event(d, _SALARY[1], "Acme Widgets Ltd", "Salary", legs))
        if d := on.get(_SAVE):
            events.append(_two(d, _SAVE, "Transfer", "Monthly saving", SAVINGS, CHECKING, pay.save))
    return events, bill_days


def _purchases(span: Span, rng: SeededRng, count: int) -> list[_Event]:
    """*count* everyday card purchases on uniformly drawn days; the year's budget sets the typical amount."""
    days = (span.end - span.start).days + 1
    per_month = Decimal(count) / len(_months(span))
    out: list[_Event] = []
    for _ in range(count):
        d = date.fromordinal(span.start.toordinal() + rng.below(days))
        account, narration, payees = rng.weighted(_PURCHASES)
        mean = _pay(d.year, span.end.year).spend / per_month
        amount = max(Decimal("1.00"), rng.cents(mean * Decimal("0.3"), mean * Decimal("1.7")))
        out.append(_two(d, (0, _PURCHASE_ORDER), rng.choice(payees), narration, account, CARD, amount))
    return out


def _card_bills(events: Sequence[_Event], bill_days: Sequence[date]) -> list[_Event]:
    """Each bill pays every card charge dated before it and on or after the previous bill."""
    charges = sorted((e.date, -p.amount) for e in events for p in e.postings if p.account == CARD)
    bills: list[_Event] = []
    i = 0
    for d in bill_days:
        owed = Decimal("0.00")
        while i < len(charges) and charges[i][0] < d:
            owed += charges[i][1]
            i += 1
        if owed <= 0:
            msg = f"no card charges before the bill on {d}"
            raise ValueError(msg)
        bills.append(_two(d, _CARD_BILL, "Card payment", "Card bill", CARD, CHECKING, owed))
    return bills


@dataclass(frozen=True, slots=True)
class Household:
    name: str = "household"
    assertion: str = (
        "Income:Salary is credited (a negative posting) exactly once on day 28 of every month of the span whose "
        "day 28 is on or before the anchor, and in no other month"
    )

    def build(self, ctx: FragmentContext) -> Sequence[Directive]:
        events, bill_days = _fixed_events(ctx.span, ctx.rng)
        count = ctx.size.transactions - len(events) - len(bill_days)
        if count <= 0:
            msg = f"size {ctx.size.value} leaves no room for purchases ({len(events) + len(bill_days)} fixed)"
            raise ValueError(msg)
        events += _purchases(ctx.span, ctx.rng, count)
        events += _card_bills(events, bill_days)
        ordered = sorted(enumerate(events), key=lambda ie: (ie[1].date, ie[1].order, ie[0]))
        directives = [commodity(ctx.span.start, USD)]
        directives += [open_account(ctx.span.start, a, [USD]) for a in _ACCOUNTS]
        directives += [transaction(e.date, e.payee, e.narration, e.postings) for _, e in ordered]
        return directives

    def check(self, view: LedgerView, span: Span, size: Size) -> list[str]:
        expected = {(m.year, m.month) for m in _months(span) if date(m.year, m.month, SALARY_DAY) <= span.end}
        seen: dict[tuple[int, int], int] = {}
        problems: list[str] = []
        for t in view.transactions:
            for p in t.postings:
                if p.account != SALARY:
                    continue
                seen[(t.date.year, t.date.month)] = seen.get((t.date.year, t.date.month), 0) + 1
                if p.number >= 0:
                    problems.append(f"{t.date}: {SALARY} posting {p.number} is not a credit")
                if t.date.day != SALARY_DAY:
                    problems.append(f"{t.date}: salary not on day {SALARY_DAY}")
        for ym in sorted(expected | set(seen)):
            want = 1 if ym in expected else 0
            if seen.get(ym, 0) != want:
                problems.append(f"{ym[0]}-{ym[1]:02d}: {seen.get(ym, 0)} salary credits, expected {want}")
        return problems
