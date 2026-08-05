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
from beancount.core import account_types as bc_account_types
from beancount.core import data
from beancount.core.amount import Amount

INTERVALS = ("daily", "weekly", "monthly", "quarterly", "yearly")

# The bare root type names (Assets, Liabilities, …). A budget can target a root
# (e.g. a zero-based total on the whole `Expenses` tree, dev-docs/budget.md §13),
# but Beancount rejects a bare root as an account *token* — it only parses when
# written as a quoted *string* (`custom "budget" "Expenses" …`). So on the wire a
# root account is a plain string, not an account-typed value; see
# ``build_budget_custom`` (quotes roots) and ``is_budget_account`` (reads them).
_ROOT_TYPES = frozenset(bc_account_types.DEFAULT_ACCOUNT_TYPES)


def is_budget_account(value: str) -> bool:
    """Whether a bare string value in a budget directive is the *account* — a
    valid multi-segment account (`Expenses:Food`) or a root type name
    (`Expenses`). Excludes stray strings (notes, typos) so a string fallback in
    the parsers can't misread them as the account. Colon'd accounts normally
    arrive as account-typed values; roots (and any hand-quoted account) as
    strings."""
    return bc_account.is_valid(value) or value in _ROOT_TYPES

# Sentinel interval marking a "budget end" tombstone: from this date the account
# has *no* budget (distinct from a real budget of 0), without deleting the prior
# directives. Written as e.g. `2026-07-01 custom "budget" Expenses:Food "none" 0 USD`
# — the inert `0 CCY` amount exists only to scope the end to one (account, currency).
# Still a valid Beancount `custom` directive (the file loads everywhere); only
# budget-aware code interprets the sentinel. See dev-docs/budget.md.
BUDGET_END = "none"


def budget_fields_complete(
    account: str | None, interval: str | None, amount: Decimal | None, currency: str | None
) -> bool:
    """A directive is well-formed iff it carries an account, a known interval, an
    amount, and a currency. Shared by both parsers (mirror JSON + live entry) so
    the completeness rule has one home."""
    return bool(account and interval in INTERVALS and amount is not None and currency)


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
    """Construct a `custom "budget"` directive entry ready for the writer.

    A root account (no ``:`` — e.g. ``Expenses``) is emitted as a quoted *string*
    value, because Beancount rejects a bare root as an account token (`Invalid
    token: 'Expenses'`). Multi-segment accounts print bare via ``bc_account.TYPE``.
    Both forms read back through ``is_budget_account``. See dev-docs/budget.md §13.1."""
    account_value = (account, str) if ":" not in account else (account, bc_account.TYPE)
    values = [
        account_value,
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
    # `2021-01-01 custom "budget"` with no arguments is legal Beancount and
    # parses to values=None, not []. Iterating that raised TypeError and took
    # out every budget edit in the ledger, not just this directive — the same
    # shape as the export aborts: one odd line, whole feature down. The
    # exporter and engine already guard this; this path did not.
    for value, dtype in (entry.values or []):
        if isinstance(value, Amount):
            amount = value.number
            currency = value.currency
        elif dtype is bc_account.TYPE:
            account = value
        elif isinstance(value, str) and value in INTERVALS:
            interval = value
        elif isinstance(value, str) and value == BUDGET_END:
            interval = BUDGET_END
        # A quoted root (e.g. "Expenses") arrives as a plain string, not an
        # account-typed value. Accept it as the account only if it's a real
        # account/root (not a stray note) and none has been set yet (first-wins,
        # so a later stray string can't clobber a real account).
        elif isinstance(value, str) and account is None and is_budget_account(value):
            account = value

    if interval == BUDGET_END:
        # Tombstone: needs an account + a currency (carried by the inert amount);
        # the amount itself is ignored (dev-docs/budget.md end-budget).
        if not (account and currency):
            return None
    elif not budget_fields_complete(account, interval, amount, currency):
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
