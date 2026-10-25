"""Ledger text builders for the generator: each directive is checked before it becomes text.

The generator's own guard, so a fragment bug fails here with a located message instead of as a Beancount error
in CI. The checks are deliberately narrower than Beancount's grammar (two-decimal amounts, plain account
components, upper-case currencies, strings without quotes, backslashes or newlines).
TODO(BL-0063): use the printer-free text builders of `ledger.core` once they exist (ARCHITECTURE.md §2.3).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

_ACCOUNT = re.compile(r"(Assets|Liabilities|Equity|Income|Expenses)(:[A-Z][A-Za-z0-9-]*)+")
_CURRENCY = re.compile(r"[A-Z]{3}")
_UNSAFE_TEXT = re.compile(r'["\\\n\r]')
_ACCOUNT_WIDTH = 38
_AMOUNT_WIDTH = 12

# Ranks order directives that share a date: commodities, then opens, then transactions.
RANK_COMMODITY = 0
RANK_OPEN = 1
RANK_TRANSACTION = 2


@dataclass(frozen=True, slots=True)
class Posting:
    account: str
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class Directive:
    """One dated directive as ledger text (no trailing newline) with its rank among same-date directives."""

    date: date
    rank: int
    text: str


def _check_account(account: str) -> None:
    if not _ACCOUNT.fullmatch(account):
        msg = f"posting account {account!r} is not a plain Beancount account name"
        raise ValueError(msg)


def _check_currency(currency: str) -> None:
    if not _CURRENCY.fullmatch(currency):
        msg = f"posting currency {currency!r} is not a three-letter upper-case code"
        raise ValueError(msg)


def _check_text(value: str) -> None:
    if _UNSAFE_TEXT.search(value):
        msg = f"text {value!r} holds a quote, backslash or line break"
        raise ValueError(msg)


def commodity(d: date, currency: str) -> Directive:
    _check_currency(currency)
    return Directive(d, RANK_COMMODITY, f"{d.isoformat()} commodity {currency}")


def open_account(d: date, account: str, currencies: Sequence[str]) -> Directive:
    _check_account(account)
    for c in currencies:
        _check_currency(c)
    return Directive(d, RANK_OPEN, f"{d.isoformat()} open {account} {','.join(currencies)}".rstrip())


def transaction(d: date, payee: str, narration: str, postings: Sequence[Posting], flag: str = "*") -> Directive:
    """A balanced transaction with explicit two-decimal amounts on every posting."""
    if flag not in ("*", "!"):
        msg = f"transaction flag {flag!r} must be * or !"
        raise ValueError(msg)
    _check_text(payee)
    _check_text(narration)
    if len(postings) < 2:
        msg = "a transaction needs at least two postings"
        raise ValueError(msg)
    sums: defaultdict[str, Decimal] = defaultdict(Decimal)
    for p in postings:
        _check_account(p.account)
        _check_currency(p.currency)
        if p.amount.as_tuple().exponent != -2:
            msg = f"posting amount {p.amount} must have exactly two decimals"
            raise ValueError(msg)
        sums[p.currency] += p.amount
    off = {c: s for c, s in sums.items() if s != 0}
    if off:
        msg = f"transaction {d} {payee!r} does not balance: {off}"
        raise ValueError(msg)
    lines = [f'{d.isoformat()} {flag} "{payee}" "{narration}"']
    # At least two spaces between account and amount whatever their lengths (Beancount needs a separator).
    lines += [f"  {p.account:<{_ACCOUNT_WIDTH}}  {p.amount:>{_AMOUNT_WIDTH}} {p.currency}" for p in postings]
    return Directive(d, RANK_TRANSACTION, "\n".join(lines))
