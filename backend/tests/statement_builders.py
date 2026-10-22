"""Synthetic statements for statement-import tests (no real CAS, no personal data).

``Stmt`` builds a ``StatementData`` row by row (running balances and closing units are
computed unless given); ``snapshot_of`` reads a ledger text the way the import does;
``render`` turns a plan into the ledger text it would produce, so a test can load it
and prove it books cleanly.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from beancount.core import data

from app.core.beancount_engine import BeancountEngine
from app.core.ledger_loader import load_string_checked
from app.services.statement_import import types as T
from app.services.statement_import.entries import build_entries, record_choices
from app.services.statement_import.snapshot import build_snapshot

ROOT = "/virtual/main.beancount"

BASE = """\
option "operating_currency" "INR"
2000-01-01 open Assets:Broker
2000-01-01 open Assets:Broker2
2000-01-01 open Assets:Bank
2000-01-01 open Assets:Transfer
2000-01-01 open Equity:Funding
2000-01-01 open Equity:Old
2000-01-01 open Income:Dividends
  income-type: "dividend"
2000-01-01 open Income:Gains
2000-01-01 open Expenses:Taxes
"""


def D(x) -> Decimal:
    return Decimal(str(x))


@dataclass
class Fund:
    key: str
    folio: str


class Stmt:
    def __init__(self, start: date = date(2019, 1, 1), end: date = date(2024, 12, 31)):
        self.start, self.end = start, end
        self.securities: Dict[str, T.StmtSecurity] = {}
        self.accounts: Dict[str, T.StmtAccount] = {}
        self.investors: Dict[str, Optional[str]] = {}
        self.pos_meta: Dict[Tuple[str, str], dict] = {}
        self.rows: List[T.StmtRow] = []
        self._occ: Counter = Counter()
        self._bal: Dict[Tuple[str, str], Decimal] = defaultdict(Decimal)

    def fund(self, key: str, name: Optional[str] = None, *, institution: str = "DSP Mutual Fund", folio: str = "F1",
             advisor: Optional[str] = "ARN-1", isin: Optional[str] = None, amfi: Optional[str] = None,
             investor: str = "I1", investor_name: Optional[str] = "Investor One", open_units=0,
             close_units=None, nav=None, nav_date=None) -> Fund:
        self.investors.setdefault(investor, investor_name)
        self.accounts.setdefault(folio, T.StmtAccount(id=folio, institution=institution, investor=investor))
        self.securities.setdefault(key, T.StmtSecurity(
            key=key, name=name or f"{institution.split()[0]} {key} Fund - Regular Plan - Growth",
            institution=institution, isin=isin, amfi=amfi, category="EQUITY",
        ))
        self.pos_meta[(folio, key)] = dict(advisor=advisor, open_units=D(open_units), close_units=close_units,
                                          nav=nav, nav_date=nav_date)
        self._bal[(folio, key)] = D(open_units)
        return Fund(key, folio)

    def row(self, f: Fund, when: date, kind: str, *, amount=None, units=None, nav=None, desc: str = "",
            balance="auto") -> str:
        units_d = D(units) if units is not None else None
        amount_d = D(amount) if amount is not None else None
        if units_d is not None:
            self._bal[(f.folio, f.key)] += units_d
        bal = self._bal[(f.folio, f.key)] if balance == "auto" and units_d is not None else (
            D(balance) if balance not in ("auto", None) else None)
        occ_key = (f.folio, f.key, when, kind, units_d, amount_d)
        self._occ[occ_key] += 1
        rid = T.row_id("cas", f.folio, f.key, when, kind, units_d, amount_d, self._occ[occ_key])
        self.rows.append(T.StmtRow(
            id=rid, index=len(self.rows), account_id=f.folio, security_key=f.key, date=when, kind=kind,
            description=desc or kind.title(), amount=amount_d, units=units_d,
            nav=D(nav) if nav is not None else None, balance=bal,
        ))
        return rid

    def build(self) -> T.StatementData:
        positions = []
        for (folio, key), m in self.pos_meta.items():
            close = D(m["close_units"]) if m["close_units"] is not None else self._bal[(folio, key)]
            positions.append(T.StmtPosition(
                account_id=folio, security_key=key, advisor=m["advisor"], open_units=m["open_units"],
                close_units=close, close_calculated=self._bal[(folio, key)],
                valuation_date=m["nav_date"] or self.end, valuation_nav=D(m["nav"]) if m["nav"] else D("10"),
                valuation_value=None, valuation_cost=None,
            ))
        return T.StatementData(
            source="cas", variant="detailed", issuer="CAMS", period_start=self.start, period_end=self.end,
            currency="INR",
            investors=[T.StmtInvestor(k, n) for k, n in self.investors.items()],
            accounts=list(self.accounts.values()), securities=list(self.securities.values()),
            positions=positions, rows=list(self.rows),
        )


def snapshot_of(text: str, files: Optional[List[str]] = None):
    entries, errors, options = load_string_checked(text)
    assert errors == [], [str(e.message) for e in errors]
    for i, e in enumerate(entries):  # load_string stamps "<string>"; make them look like the root file
        if getattr(e, "meta", None) is not None and e.meta.get("filename") == "<string>":
            entries[i] = e._replace(meta={**e.meta, "filename": ROOT})
    return build_snapshot(entries, options, ROOT, files or [ROOT])


def render(plan, base: str, stmt: T.StatementData, mapping, *, import_id: str = "cas-test") -> str:
    """The ledger text after writing ``plan`` on top of ``base`` (commodities included)."""
    eng = BeancountEngine()

    def make(d, payee, narration, postings, source, meta):
        return eng.create_transaction(d, payee, narration, postings, source, additional_meta=meta, ledger_file=ROOT)

    new = build_entries(plan, import_id=import_id, today=date(2025, 1, 15), make_transaction=make,
                        source=stmt.source, period_start=stmt.period_start, period_end=stmt.period_end,
                        choices=record_choices(mapping))
    coms = [data.Commodity({"filename": ROOT, "lineno": 0, **c.set_metadata}, date(2000, 1, 1), c.code)
            for c in plan.commodities if c.status == "new"]
    return base + "\n" + eng.format_entries(coms + new)


def load_errors(text: str) -> List[str]:
    _e, errors, _o = load_string_checked(text)
    return [str(e.message) for e in errors]
