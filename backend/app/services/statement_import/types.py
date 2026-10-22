"""The neutral statement shape every statement parser produces.

A parser (``parsers/cas.py`` today; investment OFX / CSV rules later) turns a file
into ``StatementData``: the statement's accounts, securities, closing positions and
typed rows. Everything after that — the plan, the preview, re-import, the write — is
format-agnostic and works only on these types (dev-docs/cas-import.md §11, §13).

Personal data: an investor carries only a display ``name`` for the preview. PAN,
email, phone and address never enter these types, so they can never reach the
ledger (§9a.5).

Money and units are exact ``Decimal`` (dev-docs/money-types.md).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple


# Raw row kinds, as the statement labels them. A parser maps its own labels onto these
# (for a CAS they are casparser's TransactionType names); the plan's classifier then
# decides what each row *means* (an interpretation), which the user can correct.
KIND_PURCHASE = "PURCHASE"
KIND_PURCHASE_SIP = "PURCHASE_SIP"
KIND_REDEMPTION = "REDEMPTION"
KIND_DIVIDEND_PAYOUT = "DIVIDEND_PAYOUT"
KIND_DIVIDEND_REINVEST = "DIVIDEND_REINVEST"
KIND_SWITCH_IN = "SWITCH_IN"
KIND_SWITCH_IN_MERGER = "SWITCH_IN_MERGER"
KIND_SWITCH_OUT = "SWITCH_OUT"
KIND_SWITCH_OUT_MERGER = "SWITCH_OUT_MERGER"
KIND_STT_TAX = "STT_TAX"
KIND_STAMP_DUTY_TAX = "STAMP_DUTY_TAX"
KIND_TDS_TAX = "TDS_TAX"
KIND_SEGREGATION = "SEGREGATION"
KIND_GIFT_IN = "GIFT_IN"
KIND_GIFT_OUT = "GIFT_OUT"
KIND_REVERSAL = "REVERSAL"
KIND_MISC = "MISC"
KIND_UNKNOWN = "UNKNOWN"

TAX_KINDS = frozenset({KIND_STT_TAX, KIND_STAMP_DUTY_TAX, KIND_TDS_TAX})


@dataclass(frozen=True)
class StmtInvestor:
    """One investor (a CAS is per PAN; a joint folio shows under its first holder)."""
    key: str             # opaque, statement-local ("I1", "I2", …) — never derived from the PAN
    name: Optional[str]  # display only, for the preview; never written to the ledger


@dataclass(frozen=True)
class StmtAccount:
    """An account at an institution — a folio for a CAS."""
    id: str              # folio number (kept as `folio:` metadata — not personal data)
    institution: str     # the fund house (AMC)
    investor: str        # StmtInvestor.key


@dataclass(frozen=True)
class StmtSecurity:
    """A security — for a CAS, one scheme = one plan + option."""
    key: str                  # stable identity: ISIN, else "<rta>:<rta_code>"
    name: str                 # cleaned display name
    institution: str
    isin: Optional[str] = None
    amfi: Optional[str] = None
    category: Optional[str] = None   # EQUITY / DEBT / … as the statement says


@dataclass(frozen=True)
class StmtPosition:
    """A security held in a statement account, with the statement's own totals."""
    account_id: str
    security_key: str
    advisor: Optional[str]          # distributor (ARN code), or None for direct plans
    open_units: Decimal
    close_units: Decimal
    close_calculated: Optional[Decimal]  # parser's recomputation from the rows
    valuation_date: Optional[date]
    valuation_nav: Optional[Decimal]
    valuation_value: Optional[Decimal]
    valuation_cost: Optional[Decimal]

    @property
    def key(self) -> Tuple[str, str]:
        return (self.account_id, self.security_key)


@dataclass(frozen=True)
class StmtRow:
    """One statement row, exactly as printed (corrections are applied by the plan)."""
    id: str                      # stable across statements (see ``row_id``)
    index: int                   # order within the statement (ties on a date)
    account_id: str
    security_key: str
    date: date
    kind: str                    # one of the KIND_* labels
    description: str
    amount: Optional[Decimal]    # signed as printed (redemptions/switch-outs negative)
    units: Optional[Decimal]     # signed
    nav: Optional[Decimal]
    balance: Optional[Decimal]   # the statement's running unit balance after this row

    @property
    def position_key(self) -> Tuple[str, str]:
        return (self.account_id, self.security_key)


@dataclass
class StatementData:
    source: str                   # "cas"
    variant: str                  # "detailed" | "summary"
    issuer: str                   # "CAMS" / "KFINTECH"
    period_start: date
    period_end: date
    currency: str                 # every amount's currency (INR for a CAS)
    investors: List[StmtInvestor]
    accounts: List[StmtAccount]
    securities: List[StmtSecurity]
    positions: List[StmtPosition]
    rows: List[StmtRow]
    warnings: List[str] = field(default_factory=list)

    def security(self, key: str) -> StmtSecurity:
        return self._securities_by_key()[key]

    def account(self, account_id: str) -> StmtAccount:
        return {a.id: a for a in self.accounts}[account_id]

    def _securities_by_key(self) -> Dict[str, StmtSecurity]:
        return {s.key: s for s in self.securities}


def row_id(
    source: str, account_id: str, security_key: str, when: date, kind: str,
    units: Optional[Decimal], amount: Optional[Decimal], occurrence: int,
) -> str:
    """A row's stable id — the same row in an overlapping later statement gets the
    same id, so a re-import recognises it (§8). ``occurrence`` separates rows that
    are otherwise identical (two equal STP instalments on one day)."""
    raw = "|".join([
        source, account_id, security_key, when.isoformat(), kind,
        format(units, "f") if units is not None else "",
        format(amount, "f") if amount is not None else "",
        str(occurrence),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
