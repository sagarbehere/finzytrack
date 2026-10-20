"""The recognized metadata vocabulary — the single definition of the keys and
values Finzytrack's investment features read.

This is **recognition, never validation**: nothing here rejects an unknown key
or a custom value. It tells the UI which keys to offer as typed fields, which
values to suggest, when a key is relevant to an item, and what it adds. Matching
of values is case-insensitive everywhere they are read. See
dev-docs/metadata-conventions.md (the prose contract) and
dev-docs/investment-view.md §10.3 (the Setup & Metadata tab built on this).

Keys are listed only when some part of the app reads them. Documented-but-unread
keys (``compounding``, ``institution``, …) stay free-form custom metadata.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional, Tuple

# Scopes — where a key lives.
SCOPE_COMMODITY = "commodity"              # the `commodity` directive
SCOPE_ACCOUNT = "account"                  # an `Assets:` currency-balance account's `open`
SCOPE_INCOME_ACCOUNT = "income-account"    # an `Income:` account's `open`

SCOPES = (SCOPE_COMMODITY, SCOPE_ACCOUNT, SCOPE_INCOME_ACCOUNT)

# Field kinds — how the UI edits a value and how it is written to the ledger.
KIND_ENUM = "enum"            # recognized values offered, custom values allowed
KIND_TEXT = "text"
KIND_DATE = "date"            # written as a Beancount date when it parses as ISO
KIND_INTEGER = "integer"
KIND_PERCENT = "percent"
KIND_ACCOUNT = "account"      # an account name
KIND_COMMODITY = "commodity"  # a commodity code

# Commodity `asset-class` values that make a commodity a currency (unit of
# account) when the ledger declares no operating_currency.
CURRENCY_ASSET_CLASSES = frozenset({"cash", "currency"})

COMMODITY_ASSET_CLASSES = (
    "cash", "currency", "stock", "etf", "mutual-fund", "money-market",
    "bond", "cd", "i-bond", "crypto",
)
# Account-level `account-type` values — a different key from the commodity's
# `asset-class` (an account holds a balance; a commodity is a security).
ACCOUNT_TYPES = ("checking", "savings", "cd", "bond", "cash")
INCOME_TYPES = (
    "interest", "dividend", "capital-gains", "rental", "royalty",
    "business", "salary", "other",
)


@dataclass(frozen=True)
class MetadataField:
    key: str
    scope: str
    kind: str
    label: str
    adds: str
    # Recognized values (KIND_ENUM only). Custom values are always allowed.
    values: Tuple[str, ...] = ()
    # Whether an unset value is shown as "not set" in the review. Optional
    # refinements (e.g. `fetch_symbol`) are editable but never listed.
    review: bool = True
    # The key is relevant only when another key on the same item has one of
    # these values (case-insensitive), e.g. maturity_date needs account-type cd/bond.
    requires_key: Optional[str] = None
    requires_values: Tuple[str, ...] = ()


FIELDS: Tuple[MetadataField, ...] = (
    # ── commodity ──
    MetadataField(
        key="asset-class", scope=SCOPE_COMMODITY, kind=KIND_ENUM,
        label="Asset class", values=COMMODITY_ASSET_CLASSES,
        adds="Adds: allocation by asset class, and returns by asset class.",
    ),
    MetadataField(
        key="name", scope=SCOPE_COMMODITY, kind=KIND_TEXT,
        label="Name", review=False,
        adds="Adds: a readable name next to the code.",
    ),
    MetadataField(
        key="fetch_symbol", scope=SCOPE_COMMODITY, kind=KIND_TEXT,
        label="Price symbol", review=False,
        adds="Needed only when the price service uses a different symbol than this code.",
    ),
    MetadataField(
        key="issue_date", scope=SCOPE_COMMODITY, kind=KIND_TEXT,
        label="Issue month",
        requires_key="asset-class", requires_values=("i-bond",),
        adds="Adds: automatic I-Bond value. Use MM/YYYY.",
    ),
    MetadataField(
        key="denomination", scope=SCOPE_COMMODITY, kind=KIND_INTEGER,
        label="Face value",
        requires_key="asset-class", requires_values=("i-bond",),
        adds="Adds: automatic I-Bond value for this face value.",
    ),
    # ── account (Assets: currency-balance accounts) ──
    MetadataField(
        key="account-type", scope=SCOPE_ACCOUNT, kind=KIND_ENUM,
        label="Account type", values=ACCOUNT_TYPES,
        adds="Adds: this account's type in Cash & Deposits, and interest earned and yield for savings, CDs and bonds.",
    ),
    MetadataField(
        key="interest_rate", scope=SCOPE_ACCOUNT, kind=KIND_PERCENT,
        label="Interest rate",
        requires_key="account-type", requires_values=("checking", "savings", "cd", "bond"),
        adds="Adds: the stated rate in Cash & Deposits.",
    ),
    MetadataField(
        key="maturity_date", scope=SCOPE_ACCOUNT, kind=KIND_DATE,
        label="Maturity date",
        requires_key="account-type", requires_values=("cd", "bond"),
        adds="Adds: the maturity date in Cash & Deposits.",
    ),
    MetadataField(
        key="interest_account", scope=SCOPE_ACCOUNT, kind=KIND_ACCOUNT,
        label="Interest income account", review=False,
        requires_key="account-type", requires_values=("savings", "cd", "bond"),
        adds="Needed only when interest is paid to a different account. Links that interest to this account.",
    ),
    # ── income account ──
    MetadataField(
        key="income-type", scope=SCOPE_INCOME_ACCOUNT, kind=KIND_ENUM,
        label="Income type", values=INCOME_TYPES,
        adds="Adds: the split of interest and dividends, and interest earned per account.",
    ),
    MetadataField(
        key="dividend_of", scope=SCOPE_INCOME_ACCOUNT, kind=KIND_COMMODITY,
        label="Dividend from",
        requires_key="income-type", requires_values=("dividend",),
        adds="Adds: cash dividends in returns by asset class.",
    ),
)


def fields_for(scope: str) -> Tuple[MetadataField, ...]:
    return tuple(f for f in FIELDS if f.scope == scope)


def field_applies(field: MetadataField, metadata: dict) -> bool:
    """Whether ``field`` is relevant to an item carrying ``metadata``."""
    if field.requires_key is None:
        return True
    current = metadata.get(field.requires_key)
    if current is None:
        return False
    return str(current).strip().lower() in field.requires_values


# Beancount's metadata-key grammar: a lowercase letter, then letters, digits,
# '-' or '_'. `filename`/`lineno` are parser-stamped, never user keys.
METADATA_KEY_RE = re.compile(r"^[a-z][a-zA-Z0-9_-]*$")
RESERVED_KEYS = frozenset({"filename", "lineno"})


def is_valid_key(key: str) -> bool:
    return bool(METADATA_KEY_RE.match(key)) and key not in RESERVED_KEYS


def to_ledger_value(scopes: Tuple[str, ...], key: str, value: Any) -> Any:
    """Type a submitted metadata value for writing to the ledger.

    A recognized date field becomes a Beancount date when it parses as ISO
    (``maturity_date: 2027-01-14``, unquoted, as metadata-conventions.md shows);
    everything else is written as given. A value that doesn't parse stays a
    string — recognized, never enforced.
    """
    if not isinstance(value, str):
        return value
    for f in FIELDS:
        if f.key == key and f.scope in scopes and f.kind == KIND_DATE:
            try:
                return date.fromisoformat(value.strip())
            except ValueError:
                return value
    return value
