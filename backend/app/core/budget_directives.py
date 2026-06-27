"""Construction, parsing, and identity of `custom "budget"` directives.

Shared by LedgerManager (write path) and the /api/budgets router (read path).
A budget directive is a Beancount Custom entry:

    2026-01-01 custom "budget" Expenses:Food "monthly" 500 USD

Its values are (value, dtype) pairs the printer renders by dtype — the account
must carry ``account.TYPE`` so it prints bare (not quoted). See dev-docs/budget.md
§3, §6.3.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import Any

from beancount.core import account as bc_account
from beancount.core import data
from beancount.core.amount import Amount

INTERVALS = ("daily", "weekly", "monthly", "quarterly", "yearly")


def budget_id(source_file: str | None, lineno: int | None) -> str:
    """A stable, URL-safe identifier for a budget directive at a source
    location. Consistent between the mirror-backed read and the parsed write
    path (both see the same source file + line)."""
    raw = f"{source_file or ''}:{lineno or 0}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def build_budget_custom(
    meta: dict[str, Any],
    date_obj: date,
    account: str,
    interval: str,
    amount: Decimal,
    currency: str,
) -> data.Custom:
    """Construct a `custom "budget"` directive entry ready for the writer."""
    values = [
        (account, bc_account.TYPE),
        (interval, str),
        (Amount(amount, currency), Amount),
    ]
    return data.Custom(meta, date_obj, "budget", values)


def parse_budget_entry(entry: Any) -> dict | None:
    """Extract budget fields from a live Custom entry, or None if it isn't a
    well-formed budget directive. Returns {id, date, account, interval, amount
    (Decimal), currency, source_file, source_lineno}."""
    if not isinstance(entry, data.Custom) or entry.type != "budget":
        return None

    account = interval = currency = None
    amount: Decimal | None = None
    for value, dtype in entry.values:
        if isinstance(value, Amount):
            amount = value.number
            currency = value.currency
        elif dtype is bc_account.TYPE:
            account = value
        elif isinstance(value, str) and value in INTERVALS:
            interval = value

    if not (account and interval and amount is not None and currency):
        return None

    meta = entry.meta or {}
    src = meta.get("filename")
    lineno = meta.get("lineno", 0)
    return {
        "id": budget_id(src, lineno),
        "date": entry.date,
        "account": account,
        "interval": interval,
        "amount": amount,
        "currency": currency,
        "source_file": src,
        "source_lineno": lineno,
    }
