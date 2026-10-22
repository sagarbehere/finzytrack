"""The ledger state a statement-import plan needs, read from a parsed ledger.

Both preview and commit build this from a fresh parse of the ledger (not the mirror),
so the commit's re-plan sees exactly what the preview saw unless the ledger really
changed — and the preview hash comparison is exact (dev-docs/cas-import.md §13.4).

Everything is derived from the ledger itself — no side state: what earlier imports
wrote (``stmt_id`` / ``stmt_import`` / ``stmt_match`` / ``stmt_opening`` metadata and
the ``custom "statement-import"`` record), hand-entered holdings (transactions in a
commodity without ``stmt_import``), lots, and the bank postings a purchase may match.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from beancount.core import data

META_STMT_ID = "stmt_id"            # space-separated statement row ids in this transaction
META_STMT_IMPORT = "stmt_import"    # the import this entry came from (undo as a unit)
META_STMT_MATCH = "stmt_match"      # the bank transaction this one was matched to
META_STMT_OPENING = "stmt_opening"  # opening holding (history start) for this security key
META_FOLIO = "folio"
META_ADVISOR = "advisor"
RECORD_TYPE = "statement-import"    # custom directive: one per import


def entry_ref(entry: Any) -> str:
    """A stable reference to a parsed entry within one parse: its ``id`` metadata, or
    its source location for hand-written entries that have none."""
    meta = entry.meta or {}
    if meta.get("id"):
        return str(meta["id"])
    return f"L:{meta.get('filename', '')}:{meta.get('lineno', 0)}"


@dataclass(frozen=True)
class AccountSnap:
    open_date: date
    close_date: Optional[date]
    currencies: Tuple[str, ...]
    meta: Dict[str, Any]


@dataclass(frozen=True)
class Lot:
    units: Decimal
    cost: Decimal
    currency: str
    date: date


@dataclass(frozen=True)
class BankPosting:
    ref: str
    date: date
    amount: Decimal
    currency: str
    narration: str


@dataclass(frozen=True)
class HandEntries:
    refs: Tuple[str, ...]
    latest: date
    count: int


@dataclass(frozen=True)
class ImportRecordSnap:
    import_id: str
    date: date
    meta: Dict[str, Any]
    file: str


@dataclass
class LedgerSnapshot:
    root_file: str
    files: List[str]                                        # include tree, root first
    operating_currencies: List[str]
    accounts: Dict[str, AccountSnap]
    commodities: Dict[str, Dict[str, Any]]                  # code → metadata
    currencies_in_use: Set[str]
    imported_rows: Set[str]
    folio_latest: Dict[str, date]                           # folio → latest imported date
    openings: Dict[Tuple[str, str], date]                   # (folio, security key) → opening date
    claimed_bank_refs: Set[str]
    lots: Dict[Tuple[str, str], List[Lot]]                  # (account, commodity) → open lots
    hand_entries: Dict[str, HandEntries]                    # commodity → hand-entered txns
    bank_postings: Dict[str, List[BankPosting]]             # account → non-import postings
    balances: Dict[Tuple[date, str, str], Decimal]          # (date, account, currency) → asserted units
    imports: List[ImportRecordSnap] = field(default_factory=list)

    def commodity_for(self, isin: Optional[str], amfi: Optional[str]) -> Optional[str]:
        """An existing commodity for a security — by ``isin`` metadata, else by
        ``fetch_symbol: "amfi:<code>"`` (§6.2). Never by name."""
        if isin:
            for code, meta in sorted(self.commodities.items()):
                if str(meta.get("isin", "")).strip().upper() == isin.upper():
                    return code
        if amfi:
            for code, meta in sorted(self.commodities.items()):
                if str(meta.get("fetch_symbol", "")).strip().lower() == f"amfi:{amfi}".lower():
                    return code
        return None


def _clean_meta(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {k: v for k, v in (meta or {}).items() if k not in ("filename", "lineno")}


def build_snapshot(entries: List[Any], options: Dict[str, Any], root_file: str, files: List[str]) -> LedgerSnapshot:
    root_abs = str(Path(root_file).resolve())
    ordered_files = [root_abs] + sorted(f for f in files if f != root_abs)

    accounts: Dict[str, AccountSnap] = {}
    closes: Dict[str, date] = {}
    commodities: Dict[str, Dict[str, Any]] = {}
    currencies_in_use: Set[str] = set()
    imported_rows: Set[str] = set()
    folio_latest: Dict[str, date] = {}
    openings: Dict[Tuple[str, str], date] = {}
    claimed: Set[str] = set()
    lot_units: Dict[Tuple[str, str, Decimal, str, date], Decimal] = defaultdict(Decimal)
    hand: Dict[str, List[Tuple[str, date]]] = defaultdict(list)
    bank: Dict[str, List[BankPosting]] = defaultdict(list)
    balances: Dict[Tuple[date, str, str], Decimal] = {}
    imports: List[ImportRecordSnap] = []

    for e in entries:
        if isinstance(e, data.Open):
            accounts[e.account] = AccountSnap(e.date, None, tuple(e.currencies or ()), _clean_meta(e.meta))
        elif isinstance(e, data.Close):
            closes[e.account] = e.date
        elif isinstance(e, data.Commodity):
            commodities[e.currency] = _clean_meta(e.meta)
        elif isinstance(e, data.Balance):
            balances[(e.date, e.account, e.amount.currency)] = e.amount.number
        elif isinstance(e, data.Custom) and e.type == RECORD_TYPE and e.values:
            imports.append(ImportRecordSnap(
                import_id=str(e.values[0].value), date=e.date, meta=_clean_meta(e.meta),
                file=str(Path((e.meta or {}).get("filename") or root_abs).resolve()),
            ))
        elif isinstance(e, data.Transaction):
            meta = e.meta or {}
            is_import = bool(meta.get(META_STMT_IMPORT))
            if is_import:
                for rid in str(meta.get(META_STMT_ID, "")).split():
                    imported_rows.add(rid)
                folio = meta.get(META_FOLIO)
                if folio:
                    folio = str(folio)
                    if folio not in folio_latest or e.date > folio_latest[folio]:
                        folio_latest[folio] = e.date
                    if meta.get(META_STMT_OPENING):
                        openings[(folio, str(meta[META_STMT_OPENING]))] = e.date
                for ref in str(meta.get(META_STMT_MATCH, "")).split():
                    claimed.add(ref)
            touched_commodities: Set[str] = set()
            for p in e.postings:
                if p.units is None:
                    continue
                currencies_in_use.add(p.units.currency)
                if p.cost is not None and p.cost.number is not None:
                    touched_commodities.add(p.units.currency)
                    key = (p.account, p.units.currency, p.cost.number, p.cost.currency, p.cost.date)
                    lot_units[key] += p.units.number
                elif not is_import:
                    bank[p.account].append(BankPosting(
                        ref=entry_ref(e), date=e.date, amount=p.units.number,
                        currency=p.units.currency, narration=e.narration or "",
                    ))
            if not is_import:
                for code in touched_commodities:
                    hand[code].append((entry_ref(e), e.date))

    for name, closed in closes.items():
        if name in accounts:
            a = accounts[name]
            accounts[name] = AccountSnap(a.open_date, closed, a.currencies, a.meta)

    lots: Dict[Tuple[str, str], List[Lot]] = defaultdict(list)
    for (acct, code, cost, ccy, when), units in lot_units.items():
        if units != 0:
            lots[(acct, code)].append(Lot(units, cost, ccy, when))
    for v in lots.values():
        v.sort(key=lambda lot: (lot.date, lot.cost))

    hand_entries = {
        code: HandEntries(refs=tuple(r for r, _ in items), latest=max(d for _, d in items), count=len(items))
        for code, items in hand.items()
    }

    return LedgerSnapshot(
        root_file=root_abs,
        files=ordered_files,
        operating_currencies=list(options.get("operating_currency") or []),
        accounts=accounts,
        commodities=commodities,
        currencies_in_use=currencies_in_use,
        imported_rows=imported_rows,
        folio_latest=folio_latest,
        openings=openings,
        claimed_bank_refs=claimed,
        lots=dict(lots),
        hand_entries=hand_entries,
        bank_postings=dict(bank),
        balances=balances,
        imports=sorted(imports, key=lambda r: (r.date, r.import_id)),
    )
