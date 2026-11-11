"""Ledger text builders for the generator: each directive's shape is checked before it becomes text.

The builder checks only what keeps its own text well formed: two-decimal amounts, at least two postings, a `*` or
`!` flag, strings without quotes, backslashes or newlines. Whether an account name or a currency is valid and
whether a transaction balances is Beancount's to say (INV-02, EP-23; S0104): the oracle test loads every size
with Beancount and fails on any error (`scripts/oracle/tests/test_demo_generator.py`).
TODO(BL-0063): use the printer-free text builders of `ledger.core` once they exist (ARCHITECTURE.md §2.3).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

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


def _check_text(value: str) -> None:
    if _UNSAFE_TEXT.search(value):
        msg = f"text {value!r} holds a quote, backslash or line break"
        raise ValueError(msg)


def commodity(d: date, currency: str) -> Directive:
    return Directive(d, RANK_COMMODITY, f"{d.isoformat()} commodity {currency}")


def open_account(d: date, account: str, currencies: Sequence[str]) -> Directive:
    return Directive(d, RANK_OPEN, f"{d.isoformat()} open {account} {','.join(currencies)}".rstrip())


def transaction(d: date, payee: str, narration: str, postings: Sequence[Posting], flag: str = "*") -> Directive:
    """A transaction with explicit two-decimal amounts on every posting (Beancount checks that it balances)."""
    if flag not in ("*", "!"):
        msg = f"transaction flag {flag!r} must be * or !"
        raise ValueError(msg)
    _check_text(payee)
    _check_text(narration)
    if len(postings) < 2:
        msg = "a transaction needs at least two postings"
        raise ValueError(msg)
    for p in postings:
        if p.amount.as_tuple().exponent != -2:
            msg = f"posting amount {p.amount} must have exactly two decimals"
            raise ValueError(msg)
    lines = [f'{d.isoformat()} {flag} "{payee}" "{narration}"']
    # At least two spaces between account and amount whatever their lengths (Beancount needs a separator).
    lines += [f"  {p.account:<{_ACCOUNT_WIDTH}}  {p.amount:>{_AMOUNT_WIDTH}} {p.currency}" for p in postings]
    return Directive(d, RANK_TRANSACTION, "\n".join(lines))
