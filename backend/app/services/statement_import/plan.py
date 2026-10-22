"""Statement → import plan. A pure function of (statement, mapping, corrections,
ledger snapshot): no I/O, so preview and commit compute the same plan and tests need
no PDF (dev-docs/cas-import.md §6–§8, §9, §13).

Steps:
1. **Interpret** every row (buy, sell, switch, merge, reinvest, …) from its type and
   wording, then apply the user's corrections (§13.2, §13.3).
2. **Attach** stamp duty / STT / TDS rows to the row they belong to; read TDS that a
   reinvestment states only in its description.
3. **Pair** switch-outs with switch-ins (and merger halves) within one fund house.
4. **Simulate** every fund's lots in date order — FIFO for sells, carried cost for
   mergers — and plan one balanced transaction per event. Rows already in the ledger
   (``stmt_id``), before the history start, or covered by kept hand entries are
   simulated or skipped but not written.
5. **Resolve the cash side** of every cash movement: before the cutoff → that
   account; else a bank posting in the matching account (same amount incl. stamp
   duty, ±window days, unclaimed) → the matching account; else the fallback (§6.3).
6. **Reconcile** each fund per year (against the statement's running balance) and at
   the end (against its closing units); plan commodities, balance assertions, prices.

Money is exact ``Decimal`` (dev-docs/money-types.md); accounts are only ever the
user's own choices (never proposed).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Dict, Iterable, List, Optional, Set, Tuple

from app.core.constants import COMMODITY_CODE_PATTERN
from app.schemas.statement_import_schemas import (
    FundPlan, PlannedBalance, PlannedCommodity, PlannedPosting, PlannedPrice, PlannedRow,
    PlannedTransaction, RowCorrection, StatementIssue, StatementMapping, StatementPlanData,
    StatementPlanSummary, UnclaimedBankTransaction, YearCheck,
)
from app.services.lot_math import InsufficientUnits, LotPiece, consume_fifo, convert_lots, unit_cost
from app.services.statement_import import types as T
from app.services.statement_import.snapshot import (
    META_ADVISOR, META_FOLIO, META_STMT_ID, META_STMT_MATCH, META_STMT_OPENING, LedgerSnapshot,
)

CENT = Decimal("0.01")
ZERO = Decimal("0")
UNIT_QUANTUM = Decimal("0.001")       # CAS units have 3 decimals
BALANCE_TOLERANCE = Decimal("0.001")  # §9a.3: rounding of the statement's own units
PAIR_DAYS = 7                          # a switch-in can be allotted days after its switch-out (seen: 5)
DIRECT = "DIRECT"                      # distributor key for direct plans (no ARN)

_CODE_RE = re.compile(COMMODITY_CODE_PATTERN)


def _q2(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_EVEN)


def _gross_out(r: "_Row") -> Decimal:
    """Gross proceeds of a row that removes units. Statements differ: some print the
    amount net of STT/TDS ("Redemption less TDS", the tax on its own line), some print
    it gross with the tax deducted after. Units × NAV decides which (verified on real
    statements, dev-docs/cas-import.md §13.1)."""
    taxes = r.stt + r.tds
    if taxes == 0:
        return r.a
    if r.nav and r.u:
        estimate = r.u * r.nav
        return r.a if abs(r.a - estimate) <= abs(r.a + taxes - estimate) else r.a + taxes
    return r.a + taxes


def _net_out(r: "_Row") -> Decimal:
    return _gross_out(r) - r.stt - r.tds


def _rounding_tolerance(*rows: "_Row") -> Decimal:
    """How far two halves of a switch may differ: units are rounded to 3 decimals, so
    an amount recomputed from them moves by up to NAV × 0.001 (₹0.4 at a ₹425 NAV)."""
    navs = [r.nav for r in rows if r.nav]
    tol = max([CENT] + [n * UNIT_QUANTUM for n in navs])
    # TDS is printed rounded to whole rupees (s. 288B) while the fund moves the exact
    # net, so a half with TDS can differ from its partner by up to ₹1 (seen: ₹0.74).
    if any(r.tds for r in rows):
        tol += Decimal("1")
    return tol


# ── Internal working types ───────────────────────────────────────────────────

@dataclass
class _Row:
    src: T.StmtRow
    date: date
    units: Optional[Decimal]
    amount: Optional[Decimal]
    interp: str
    proposed: str
    reason: Optional[str]
    corrected: bool = False
    excluded: bool = False
    pair: Optional[str] = None
    no_pair: bool = False            # the user said "never pair this row"
    forced_pair: Optional[str] = None
    parent: Optional[str] = None
    taxes: List["_Row"] = field(default_factory=list)
    tds_in_description: Decimal = ZERO
    status: str = "import"
    txn: Optional[int] = None
    partner_not_imported: bool = False  # its switch half is in a fund the user left out

    @property
    def id(self) -> str:
        return self.src.id

    @property
    def pos(self) -> Tuple[str, str]:
        return self.src.position_key

    def tax(self, kind: str) -> Decimal:
        return sum((abs(t.amount or ZERO) for t in self.taxes if t.src.kind == kind), ZERO)

    @property
    def stamp(self) -> Decimal:
        return self.tax(T.KIND_STAMP_DUTY_TAX)

    @property
    def stt(self) -> Decimal:
        return self.tax(T.KIND_STT_TAX)

    @property
    def tds(self) -> Decimal:
        return self.tax(T.KIND_TDS_TAX) + self.tds_in_description

    @property
    def nav(self) -> Optional[Decimal]:
        return self.src.nav

    @property
    def a(self) -> Decimal:
        return abs(self.amount or ZERO)

    @property
    def u(self) -> Decimal:
        return abs(self.units or ZERO)


@dataclass
class _Txn:
    date: date
    narration: str
    payee: str
    kind: str
    rows: List[_Row]
    postings: List[PlannedPosting]
    meta: Dict[str, str]
    cash_matched: Optional[bool] = None


@dataclass
class PlanResult:
    """The API view (``data``) plus what the writer needs."""
    data: StatementPlanData
    transactions: List[_Txn]
    commodities: List[PlannedCommodity]
    balances: List[PlannedBalance]
    prices: List[PlannedPrice]
    removed_refs: List[str]
    account_metadata: Dict[str, Dict[str, str]]  # account → keys to set
    target_file: str
    roles: Set[str] = field(default_factory=set)  # account roles this import needs

    @property
    def ready(self) -> bool:
        return not self.data.blocking


# ── 1. Interpretation ────────────────────────────────────────────────────────

# A dividend moved into this fund from another ("IDCW Sweep In", "IDCW Transfer …").
# Specific phrases only: a switch *from an IDCW fund* also names "IDCW".
_DIVIDEND_WORDS = re.compile(r"\b(idcw|dividend)\s+(sweep|transfer|reinvest)", re.I)
_SWITCH_WORDS = re.compile(r"lateral shift|switch|s\s*t\s*p|systematic transfer", re.I)
_MERGE_WORDS = re.compile(r"merge|face value change", re.I)
_TDS_IN_DESC = re.compile(r"TDS\s*:\s*([\d,]+(?:\.\d+)?)", re.I)


def classify(row: T.StmtRow) -> Tuple[str, Optional[str]]:
    """What a statement row means → (interpretation, reason). The reason is set when
    the reading differs from the printed type, so the drill-down can say why."""
    kind, desc = row.kind, row.description
    has_units = row.units is not None and row.units != 0

    if kind in T.TAX_KINDS:
        return "tax", None
    if not has_units:
        if kind == T.KIND_DIVIDEND_PAYOUT and row.amount:
            return "dividend", None
        if kind == T.KIND_REVERSAL and row.amount:
            return "unsupported", "A reversal without units can't be placed."
        if row.amount and kind not in (T.KIND_MISC, T.KIND_DIVIDEND_REINVEST, T.KIND_DIVIDEND_PAYOUT):
            return "unsupported", "An amount without units can't be placed."
        return "info", None

    out = row.units < 0
    if kind in (T.KIND_SWITCH_OUT_MERGER, T.KIND_SWITCH_IN_MERGER) or (
        kind in (T.KIND_SWITCH_OUT, T.KIND_SWITCH_IN) and _MERGE_WORDS.search(desc)
    ):
        reason = None if "MERGER" in kind else "Described as a merger or face-value change: cost and dates carry over."
        return ("merge_out" if out else "merge_in"), reason
    if kind == T.KIND_SWITCH_OUT:
        return "switch_out", None
    if kind == T.KIND_SWITCH_IN:
        return "switch_in", None
    if kind in (T.KIND_PURCHASE, T.KIND_PURCHASE_SIP):
        if _SWITCH_WORDS.search(desc) and "purchase" not in desc.lower():
            return "switch_in", "Described as a transfer from another fund."
        if _DIVIDEND_WORDS.search(desc):
            return "reinvest", "Described as a dividend moved in from another fund, so it is income, not new money."
        return "buy", None
    if kind == T.KIND_REDEMPTION:
        if _SWITCH_WORDS.search(desc):
            return "switch_out", "Described as a transfer to another fund."
        return "sell", None
    if kind in (T.KIND_DIVIDEND_REINVEST, T.KIND_DIVIDEND_PAYOUT):
        if out:
            return "unsupported", "A dividend that removes units can't be placed."
        reason = "A dividend paid as units (transferred in), so it is reinvested." if kind == T.KIND_DIVIDEND_PAYOUT else None
        return "reinvest", reason
    if kind == T.KIND_SEGREGATION:
        return ("segregation", None) if not out else ("unsupported", "A segregation that removes units can't be placed.")
    if kind == T.KIND_REVERSAL:
        return "reversal", None
    if kind in (T.KIND_GIFT_IN, T.KIND_GIFT_OUT):
        return "unsupported", "Gifted units aren't supported yet."
    return "unsupported", "The statement doesn't say what this row is."


def _apply_corrections(rows: Dict[str, _Row], corrections: Iterable[RowCorrection], issues: List[StatementIssue]) -> None:
    for c in corrections:
        r = rows.get(c.row_id)
        if r is None:
            issues.append(StatementIssue(code="CORRECTION_UNKNOWN_ROW", row_id=c.row_id,
                                         message="A correction refers to a row that isn't in this statement."))
            continue
        changed = False
        if c.exclude:
            r.excluded, changed = True, True
        if c.interpretation and c.interpretation != r.interp:
            r.interp, changed = c.interpretation, True
        if c.date and c.date != r.date:
            r.date, changed = c.date, True
        if c.units is not None and c.units != r.units:
            r.units, changed = c.units, True
        if c.amount is not None and c.amount != r.amount:
            r.amount, changed = c.amount, True
        if c.pair_with is not None:
            if c.pair_with == "":
                r.no_pair = True
            else:
                r.forced_pair = c.pair_with
            changed = True
        r.corrected = r.corrected or changed


# ── 2. Taxes ─────────────────────────────────────────────────────────────────

_PARENT_INTERPS = {"buy", "sell", "reinvest", "dividend", "switch_out", "switch_in", "merge_out", "merge_in", "reversal"}


def _attach_taxes(ordered: List[_Row], issues: List[StatementIssue]) -> None:
    by_pos_date: Dict[Tuple[Tuple[str, str], date], List[_Row]] = defaultdict(list)
    for r in ordered:
        if not r.excluded:
            by_pos_date[(r.pos, r.date)].append(r)
    for group in by_pos_date.values():
        for t in group:
            if t.interp != "tax":
                continue
            before = [p for p in group if p.interp in _PARENT_INTERPS and p.src.index < t.src.index]
            after = [p for p in group if p.interp in _PARENT_INTERPS and p.src.index > t.src.index]
            parent = before[-1] if before else (after[0] if after else None)
            if parent is None:
                t.status = "unsupported"
                issues.append(StatementIssue(
                    code="TAX_WITHOUT_TRANSACTION", row_id=t.id, security_key=t.src.security_key,
                    message="A tax or charge with no transaction it belongs to on that date; not imported.",
                ))
                continue
            t.parent = parent.id
            parent.taxes.append(t)
    for r in ordered:
        if r.interp == "reinvest" and not r.excluded and r.tax(T.KIND_TDS_TAX) == 0:
            m = _TDS_IN_DESC.search(r.src.description)
            if m:
                r.tds_in_description = Decimal(m.group(1).replace(",", ""))


# ── 3. Pairing ───────────────────────────────────────────────────────────────

_PAIRS = {"switch_out": "switch_in", "merge_out": "merge_in"}


def _pair_rows(ordered: List[_Row], institution_of, issues: List[StatementIssue]) -> None:
    rows = {r.id: r for r in ordered}
    for r in ordered:  # forced pairs first
        if r.forced_pair and not r.pair:
            other = rows.get(r.forced_pair)
            if other is None or other.excluded or other.id == r.id:
                issues.append(StatementIssue(code="PAIR_UNKNOWN_ROW", row_id=r.id,
                                             message="The row chosen to pair with isn't available."))
                continue
            out, inn = (r, other) if (r.units or ZERO) < 0 else (other, r)
            if (out.units or ZERO) >= 0 or (inn.units or ZERO) <= 0:
                issues.append(StatementIssue(code="PAIR_INVALID", row_id=r.id,
                                             message="A pair needs one row that removes units and one that adds them."))
                continue
            if other.pair:
                rows[other.pair].pair = None
            out.pair, inn.pair = inn.id, out.id
            if out.interp not in _PAIRS:
                out.interp = "merge_out" if inn.interp == "merge_in" else "switch_out"
            if inn.interp not in _PAIRS.values():
                inn.interp = "merge_in" if out.interp == "merge_out" else "switch_in"

    for out in ordered:
        if out.interp not in _PAIRS or out.pair or out.no_pair or out.excluded:
            continue
        want = _PAIRS[out.interp]
        best, best_key = None, None
        for inn in ordered:
            if inn.interp != want or inn.pair or inn.no_pair or inn.excluded:
                continue
            if institution_of(inn) != institution_of(out):
                continue
            days = (inn.date - out.date).days
            if abs(days) > PAIR_DAYS:
                continue
            gap = abs(_net_out(out) - (inn.a + inn.stamp + inn.tds))
            tol = _rounding_tolerance(out, inn)
            if want == "switch_in" and gap > tol:
                continue
            key = (gap > tol, abs(days), days < 0, gap, inn.src.index)
            if best_key is None or key < best_key:
                best, best_key = inn, key
        if best is not None:
            out.pair, best.pair = best.id, out.id


# ── Commodity codes ──────────────────────────────────────────────────────────

_CODE_STOP = {
    "FUND", "PLAN", "OPTION", "SCHEME", "THE", "OF", "AND", "REGULAR", "DIRECT", "GROWTH", "IDCW",
    "DIVIDEND", "DIV", "REINVEST", "REINVESTMENT", "PAYOUT", "CUMULATIVE", "NON", "DEMAT", "RETAIL",
    "INSTITUTIONAL", "WEEKLY", "MONTHLY", "DAILY", "FORTNIGHTLY", "QUARTERLY", "ANNUAL", "MUTUAL",
    "RP", "REG", "PORTFOLIO", "SERIES", "OPEN", "ENDED", "INDIA",
}
_FREQ = (("WEEKLY", "W"), ("MONTHLY", "M"), ("DAILY", "D"), ("FORTNIGHTLY", "F"), ("QUARTERLY", "Q"))


def _amc_short(institution: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z&]+", institution) if w.upper() not in ("MUTUAL", "FUND", "MF", "ASSET", "MANAGEMENT", "COMPANY", "LTD", "LIMITED")]
    if not words:
        return "MF"
    if words[0].isupper() and len(words[0]) <= 6:
        return words[0]
    if len(words) >= 3:
        return "".join(w[0] for w in words).upper()[:6]
    return words[0].upper()[:8]


def generate_code(sec: T.StmtSecurity, taken: Set[str]) -> str:
    """A short, readable, valid, unique commodity code from the scheme name, e.g.
    'HDFC Balanced Advantage Fund - Regular Plan - Growth' → 'HDFC-BALANCEDADV-RG'
    (§6.2; editable in the preview)."""
    amc = _amc_short(sec.institution)
    name = sec.name.split("(")[0]
    upper = name.upper()
    amc_words = {w.upper() for w in re.findall(r"[A-Za-z]+", sec.institution)}
    core_words = [w for w in re.findall(r"[A-Z0-9]+", upper) if w not in _CODE_STOP and w not in amc_words]
    if len(core_words) >= 2:
        first, second = core_words[0], core_words[1]
        core = (first if len(first) <= 8 else first[:4]) + (second if len(second) <= 6 else second[:3])
    else:
        core = core_words[0][:10] if core_words else "FUND"
    plan = "D" if "DIRECT" in upper else "R"
    if "GROWTH" in upper or "CUMULATIVE" in upper:
        opt = "G"
    elif "IDCW" in upper or "DIVIDEND" in upper:
        opt = "I" + next((f for w, f in _FREQ if w in upper), "")
    else:
        opt = ""
    suffix = plan + opt
    room = 24 - len(amc) - len(suffix) - 2
    core = core[:max(room, 3)]

    def make(n: int) -> str:
        tag = str(n) if n > 1 else ""
        return re.sub(r"[^A-Z0-9\-]", "", f"{amc}-{core[:max(room - len(tag), 3)]}{tag}-{suffix}".upper().strip("-"))

    n = 1
    code = make(n)
    while code in taken or not _CODE_RE.match(code):
        n += 1
        code = make(n)
    return code


# ── The plan ─────────────────────────────────────────────────────────────────

@dataclass
class _Ctx:
    stmt: T.StatementData
    mapping: StatementMapping
    snap: LedgerSnapshot
    blocking: List[StatementIssue]
    warnings: List[StatementIssue]
    codes: Dict[str, str]
    skipped: Set[Tuple[str, str]] = field(default_factory=set)
    claimed: Set[str] = field(default_factory=set)
    used_roles: Set[str] = field(default_factory=set)

    def advisor_key(self, pos: T.StmtPosition) -> str:
        return pos.advisor or DIRECT


def _distributor_key(advisor: Optional[str]) -> str:
    return advisor or DIRECT


def build_plan(
    stmt: T.StatementData, mapping: StatementMapping, corrections: List[RowCorrection], snap: LedgerSnapshot,
) -> PlanResult:
    blocking: List[StatementIssue] = []
    warnings: List[StatementIssue] = [StatementIssue(code="PARSER_WARNING", message=w) for w in stmt.warnings]
    ccy = stmt.currency
    sec_by_key = {s.key: s for s in stmt.securities}
    acct_by_id = {a.id: a for a in stmt.accounts}
    pos_by_key = {p.key: p for p in stmt.positions}
    investor_name = {i.key: i.name for i in stmt.investors}

    # ── rows: interpret + correct ──
    rows: Dict[str, _Row] = {}
    for r in stmt.rows:
        interp, reason = classify(r)
        rows[r.id] = _Row(src=r, date=r.date, units=r.units, amount=r.amount, interp=interp, proposed=interp, reason=reason)
    _apply_corrections(rows, corrections, warnings)
    ordered = sorted(rows.values(), key=lambda r: (r.date, r.src.index))
    _attach_taxes(ordered, warnings)
    _pair_rows(ordered, lambda r: acct_by_id[r.src.account_id].institution, warnings)

    # Funds the user leaves out (e.g. someone else's): every row excluded. A switch half
    # in an imported fund loses its partner and falls back to the cash side, so money
    # never appears or vanishes — said per row, naming the fund.
    skipped: Set[Tuple[str, str]] = {(f.folio, f.security_key) for f in mapping.excluded_funds}
    for r in ordered:
        if r.pos not in skipped:
            continue
        partner = rows.get(r.pair) if r.pair else None
        if partner is not None and partner.pos not in skipped:
            partner.pair = None
            partner.partner_not_imported = True
            warnings.append(StatementIssue(
                code="SWITCH_WITH_EXCLUDED_FUND", row_id=partner.id, security_key=partner.src.security_key,
                message=(f"The other half of this switch is in {sec_by_key[r.src.security_key].name}, which you "
                         "chose not to import, so this half is recorded against the cash side."),
            ))
        r.pair = None
        r.excluded = True
        r.reason = "You chose not to import this fund."

    # ── commodity codes (match existing by isin / amfi:, else generate; user override wins) ──
    codes: Dict[str, str] = {}
    code_status: Dict[str, str] = {}
    taken: Set[str] = set(snap.commodities) | set(snap.currencies_in_use)
    for sec in stmt.securities:
        existing = snap.commodity_for(sec.isin, sec.amfi)
        override = (mapping.commodity_codes.get(sec.key) or "").strip().upper()
        if override:
            if not _CODE_RE.match(override):
                blocking.append(StatementIssue(code="COMMODITY_CODE_INVALID", security_key=sec.key,
                                               message=f"'{override}' isn't a valid commodity code: use capital letters, digits, '-', '.' or '_', starting with a letter."))
            codes[sec.key] = override
        elif existing:
            codes[sec.key] = existing
        else:
            codes[sec.key] = generate_code(sec, taken)
        taken.add(codes[sec.key])
        code_status[sec.key] = "existing" if codes[sec.key] in snap.commodities or codes[sec.key] in snap.currencies_in_use else "new"
        if codes[sec.key] in snap.operating_currencies or codes[sec.key] == ccy:
            blocking.append(StatementIssue(code="COMMODITY_CODE_IS_CURRENCY", security_key=sec.key,
                                           message=f"{codes[sec.key]} is a currency; pick another code for {sec.name}."))
    by_code: Dict[str, List[str]] = defaultdict(list)
    for k, c in codes.items():
        by_code[c].append(k)
    for c, keys in by_code.items():
        isins = {sec_by_key[k].isin for k in keys}
        if len(keys) > 1 and len(isins) > 1:
            blocking.append(StatementIssue(code="COMMODITY_CODE_DUPLICATE", security_key=keys[1],
                                           message=f"Two different funds would use the code {c}; give one of them another code."))

    ctx = _Ctx(stmt=stmt, mapping=mapping, snap=snap, blocking=blocking, warnings=warnings, codes=codes,
               skipped=skipped)

    # ── per-position starting state ──
    def holding_account(pos_key) -> Optional[str]:
        return mapping.holding_accounts.get(_distributor_key(pos_by_key[pos_key].advisor)) if pos_key in pos_by_key else None

    removed_refs: List[str] = []
    lots: Dict[Tuple[str, str], List[LotPiece]] = defaultdict(list)
    start_units: Dict[Tuple[str, str], Decimal] = defaultdict(Decimal)
    mode: Dict[Tuple[str, str], str] = {}          # zero | ledger | hand
    hand_cutoff: Dict[Tuple[str, str], date] = {}
    ledger_lots_given: Set[Tuple[str, str]] = set()
    hand_info: Dict[str, Tuple[int, date, Optional[str]]] = {}

    for pos in stmt.positions:
        if pos.key in skipped:
            mode[pos.key] = "zero"
            continue
        sec = sec_by_key[pos.security_key]
        code = codes[sec.key]
        hold = holding_account(pos.key)
        hand = snap.hand_entries.get(code) if code_status[sec.key] == "existing" else None
        choice = mapping.hand_entries.get(sec.key) if hand else None
        if hand:
            hand_info[sec.key] = (hand.count, hand.latest, choice)
        if hand and choice is None:
            if sec.key not in {i.security_key for i in blocking if i.code == "HAND_ENTRIES"}:
                blocking.append(StatementIssue(
                    code="HAND_ENTRIES", security_key=sec.key,
                    message=(f"{sec.name}: the ledger already has {hand.count} hand-entered transaction(s) in {code} "
                             f"(latest {hand.latest.isoformat()}). Choose: replace them with the statement's history, "
                             "or import only what comes after them."),
                ))
            mode[pos.key] = "zero"
            continue
        if choice == "replace":
            for ref in hand.refs:
                if ref not in removed_refs:
                    removed_refs.append(ref)
            mode[pos.key] = "zero"
            continue
        if choice == "after_latest":
            mode[pos.key] = "hand"
            hand_cutoff[pos.key] = hand.latest
        elif pos.open_units != 0:
            mode[pos.key] = "ledger"
        else:
            mode[pos.key] = "zero"
            continue
        # start from the ledger's lots for this account + commodity (given to one position only)
        lk = (hold or "", code)
        if hold and lk not in ledger_lots_given:
            ledger_lots_given.add(lk)
            for lot in snap.lots.get(lk, []):
                lots[pos.key].append(LotPiece(lot.units, lot.cost, lot.currency, lot.date))
        start_units[pos.key] = sum((lot.units for lot in lots[pos.key]), ZERO)
        if mode[pos.key] == "ledger" and start_units[pos.key] != pos.open_units:
            warnings.append(StatementIssue(
                code="OPENING_MISMATCH", security_key=sec.key,
                message=(f"{sec.name}: the statement starts with {pos.open_units} units but the ledger holds "
                         f"{start_units[pos.key]} in {hold or 'the holding account'}."),
            ))

    # ── statuses that don't depend on simulation ──
    hist = mapping.history_start
    for r in ordered:
        if r.excluded:
            r.status = "excluded"
        elif r.interp == "info":
            r.status = "info"
        elif r.interp == "unsupported":
            r.status = "unsupported"
        elif r.interp == "tax":
            continue  # follows its parent (or stays unsupported when it has none)
        elif r.id in snap.imported_rows:
            r.status = "already"
        elif (r.src.account_id, r.src.security_key) in snap.openings and r.date < snap.openings[(r.src.account_id, r.src.security_key)]:
            r.status = "already"
        elif mode.get(r.pos) == "hand" and r.date <= hand_cutoff[r.pos]:
            r.status = "before_hand"
        elif hist and r.date < hist:
            r.status = "before_start"
        else:
            r.status = "import"

    # reversals: cancel a reversal against its original in this statement
    for r in ordered:
        if r.interp != "reversal" or r.status in ("excluded",):
            continue
        orig = next((o for o in reversed(ordered)
                     if o.src.index < r.src.index and o.pos == r.pos and o.status not in ("excluded", "reversed", "info", "unsupported")
                     and o.interp not in ("tax", "reversal") and o.units is not None and r.units is not None
                     and o.units == -r.units and abs(o.a - r.a) <= CENT), None)
        if orig is None:
            continue
        if orig.status == "already" and r.status == "import":
            r.reason = "Reverses a transaction imported earlier: recorded as a reversing entry."
            continue
        for x in (orig, r, *orig.taxes, *r.taxes):
            x.status = "reversed"
        if orig.pair and orig.pair in rows:
            p = rows[orig.pair]
            p.pair = None
            orig.pair = None

    # ── events in date order ──
    events: List[Tuple[date, int, int, _Row, Optional[_Row]]] = []
    seen: Set[str] = set()
    for r in ordered:
        if r.id in seen or r.interp in ("tax", "info", "unsupported") or r.status in ("excluded", "reversed", "info", "unsupported"):
            continue
        partner = rows.get(r.pair) if r.pair else None
        if partner is not None and partner.status in ("excluded", "reversed"):
            partner = None
        seen.add(r.id)
        if partner is not None:
            seen.add(partner.id)
            out, inn = (r, partner) if (r.units or ZERO) < 0 else (partner, r)
            events.append((max(out.date, inn.date), 1, out.src.index, out, inn))
        else:
            removes = 1 if (r.units or ZERO) < 0 else 0
            events.append((r.date, removes, r.src.index, r, None))
    # On a day, events that add units go before those that remove them: a statement
    # may print a same-day sell above the reinvestment that funds it.
    events.sort(key=lambda e: (e[0], e[1], e[2]))

    rows_by_pos_pre: Dict[Tuple[str, str], List[_Row]] = defaultdict(list)
    for r in ordered:
        rows_by_pos_pre[r.pos].append(r)
    txns: List[_Txn] = []
    matched_count: Dict[Tuple[str, str], int] = Counter()
    fallback_count: Dict[Tuple[str, str], int] = Counter()
    opened = False

    def emit_openings(on: date) -> None:
        for pk in sorted(lots, key=lambda k: (k[0], k[1])):
            if mode.get(pk) != "zero" or not lots[pk]:
                continue
            # The ledger already holds these lots: an earlier import wrote this opening,
            # or wrote the rows before the start date one by one.
            if pk in snap.openings or any(r.status == "already" and r.date < on for r in rows_by_pos_pre.get(pk, [])):
                continue
            pos = pos_by_key.get(pk)
            sec = sec_by_key[pk[1]]
            code = codes[sec.key]
            basis = sum((lot.units * lot.cost for lot in lots[pk]), ZERO)
            cash = _opening_cash(ctx, on)
            postings = [PlannedPosting(account=holding_account(pk) or "", units=lot.units, currency=code,
                                       cost=lot.cost, cost_currency=lot.currency, cost_date=lot.date)
                        for lot in sorted(lots[pk], key=lambda lot: (lot.date, lot.cost))]
            postings.append(PlannedPosting(account=cash or "", units=-_q2(basis), currency=ccy))
            txns.append(_Txn(on, f"Opening holding {code}", sec.institution, "opening", [], postings,
                             _meta(pk, pos, opening=sec.key)))

    for when, _removes, _idx, row, partner in events:
        if hist and not opened and when >= hist:
            emit_openings(hist)
            opened = True
        emit = row.status == "import" and (partner is None or partner.status == "import")
        if partner is not None and row.status != partner.status:
            # The two halves fall on different sides of a boundary: the pair goes together.
            if "already" in (row.status, partner.status):
                both = "already"
            elif "before_start" in (row.status, partner.status):
                both = "before_start" if when < hist else "import"
            else:
                both = "before_hand"
            for x in (row, partner):
                x.status = both
            emit = both == "import"
        if mode.get(row.pos) in ("ledger", "hand") and row.status in ("already", "before_hand", "before_start"):
            continue  # the ledger's lots already reflect it
        if row.status == "before_hand":
            continue
        try:
            txn = _build_event(ctx, row, partner, lots, holding_account, emit)
        except _EventFailed as e:
            for x in (row, partner) if partner else (row,):
                x.status = "failed"
            warnings.append(StatementIssue(code=e.code, row_id=row.id, security_key=row.src.security_key, message=e.message))
            continue
        if txn is not None and emit:
            idx = len(txns)
            txns.append(txn)
            for x in txn.rows:
                x.txn = idx
                for t in x.taxes:
                    t.txn = idx
            if txn.cash_matched is True:
                matched_count[row.pos] += 1
            elif txn.cash_matched is False:
                fallback_count[row.pos] += 1
    if hist and not opened:
        emit_openings(hist)

    for r in ordered:  # taxes follow their parent
        if r.interp == "tax" and r.parent and r.status != "excluded":
            r.status = rows[r.parent].status

    # ── reconciliation ──
    ledger_units: Dict[Tuple[str, str], Decimal] = {}
    year_checks: Dict[Tuple[str, str], List[YearCheck]] = {}
    rows_by_pos: Dict[Tuple[str, str], List[_Row]] = defaultdict(list)
    for r in ordered:
        rows_by_pos[r.pos].append(r)
    for pos in stmt.positions:
        pk = pos.key
        units = start_units[pk]
        checks: List[YearCheck] = []
        prs = rows_by_pos.get(pk, [])
        by_year: Dict[int, List[_Row]] = defaultdict(list)
        for r in prs:
            by_year[r.src.date.year].append(r)
        for year in sorted(by_year):
            yrows = by_year[year]
            counted = False
            for r in sorted(yrows, key=lambda r: (r.date, r.src.index)):
                if r.interp in ("tax", "info"):
                    continue
                in_ledger = r.status in ("import", "before_start") or (r.status == "already" and mode.get(pk) == "zero")
                if in_ledger and r.units is not None:
                    units += r.units
                    counted = True
            stmt_bal = next((r.src.balance for r in sorted(yrows, key=lambda r: r.src.index, reverse=True) if r.src.balance is not None), None)
            comparable = mode.get(pk) == "zero" or all(r.status not in ("before_hand", "already") for r in yrows)
            checks.append(YearCheck(
                year=year,
                counts=dict(Counter(r.interp for r in yrows if r.status != "excluded")),
                end_units=units,
                statement_units=stmt_bal,
                ok=(units == stmt_bal) if (stmt_bal is not None and comparable and (counted or mode.get(pk) == "zero")) else None,
            ))
        ledger_units[pk] = units
        year_checks[pk] = checks

    # ── balances, commodities, prices ──
    period_end = stmt.period_end
    bal_date = period_end + timedelta(days=1)
    bal_totals: Dict[Tuple[str, str], Decimal] = defaultdict(Decimal)
    for pos in stmt.positions:
        hold = holding_account(pos.key)
        if hold and pos.key not in skipped:
            bal_totals[(hold, codes[pos.security_key])] += pos.close_units
    balances = []
    for (a, c), u in sorted(bal_totals.items()):
        existing = snap.balances.get((bal_date, a, c))
        if existing is None:
            balances.append(PlannedBalance(date=bal_date, account=a, units=u, commodity=c, tolerance=BALANCE_TOLERANCE))
        elif abs(existing - u) > BALANCE_TOLERANCE:
            # Additive only: the earlier assertion stays; say why it will now disagree.
            warnings.append(StatementIssue(
                code="BALANCE_DIFFERS",
                message=(f"{a} already asserts {existing} {c} on {bal_date.isoformat()}; this statement says {u}. "
                         "The import doesn't change existing assertions — check that one."),
            ))

    used_codes = {p.currency for t in txns for p in t.postings if p.cost is not None} | {b.commodity for b in balances}
    commodities: List[PlannedCommodity] = []
    for sec in stmt.securities:
        code = codes[sec.key]
        if code not in used_codes:
            continue
        want = {"name": sec.name, "asset-class": "mutual-fund", "amc": sec.institution}
        if sec.isin:
            want["isin"] = sec.isin
        if sec.amfi:
            want["fetch_symbol"] = f"amfi:{sec.amfi}"
        have = snap.commodities.get(code, {})
        set_meta = {k: v for k, v in want.items() if k not in have}
        if code_status[sec.key] == "new" or set_meta:
            commodities.append(PlannedCommodity(security_key=sec.key, code=code,
                                                status="new" if code not in snap.commodities else "existing",
                                                set_metadata=set_meta))
        if not sec.amfi and any(p.close_units for p in stmt.positions if p.security_key == sec.key):
            warnings.append(StatementIssue(code="NO_AMFI_CODE", security_key=sec.key,
                                           message=f"{sec.name}: the statement has no AMFI code, so its price history can't be fetched. Set its price symbol in Setup & Metadata."))

    prices: List[PlannedPrice] = []
    for pos in stmt.positions:
        code = codes[pos.security_key]
        if code in used_codes and pos.valuation_nav and pos.valuation_nav > 0 and pos.valuation_date:
            p = PlannedPrice(date=pos.valuation_date, commodity=code, price=pos.valuation_nav, currency=ccy)
            if p not in prices:
                prices.append(p)

    # ── account checks ──
    _check_accounts(ctx, txns, balances)
    account_metadata: Dict[str, Dict[str, str]] = {}
    if mapping.dividend_account and mapping.dividend_account in snap.accounts:
        if not snap.accounts[mapping.dividend_account].meta.get("income-type"):
            if mapping.tag_dividend_account:
                account_metadata[mapping.dividend_account] = {"income-type": "dividend"}
            else:
                warnings.append(StatementIssue(code="DIVIDEND_ACCOUNT_UNTAGGED",
                                               message=f"{mapping.dividend_account} has no income type. Mark it as dividend income so dashboards show these dividends as dividends."))
    if snap.operating_currencies and ccy not in snap.operating_currencies:
        blocking.append(StatementIssue(code="CURRENCY_NOT_OPERATING",
                                       message=f"{ccy} isn't one of your operating currencies. Add it in Settings → Operating currencies, then preview again."))

    target = mapping.target_file or snap.root_file
    if target not in snap.files:
        blocking.append(StatementIssue(code="TARGET_FILE_UNKNOWN", message="The chosen file isn't part of this ledger."))
        target = snap.root_file

    # ── period gap per folio (§8) ──
    for acct in stmt.accounts:
        last = snap.folio_latest.get(acct.id)
        if last and stmt.period_start > last + timedelta(days=1):
            warnings.append(StatementIssue(
                code="PERIOD_GAP",
                message=(f"Folio {acct.id} ({acct.institution}): this statement starts {stmt.period_start.isoformat()}, "
                         f"but your last import for it ends {last.isoformat()}. Transactions in between would be missing — "
                         f"download a statement that starts on {last.isoformat()} or earlier."),
            ))

    for r in ordered:
        if (r.interp in ("switch_out", "switch_in", "merge_out", "merge_in") and not r.pair
                and r.status == "import" and not r.partner_not_imported):
            warnings.append(StatementIssue(
                code="UNPAIRED_SWITCH", row_id=r.id, security_key=r.src.security_key,
                message="No matching other half was found in the statement, so it is recorded against the cash side.",
            ))
        if r.status == "unsupported" and r.interp != "tax":
            warnings.append(StatementIssue(code="ROW_NOT_IMPORTED", row_id=r.id, security_key=r.src.security_key,
                                           message=r.reason or "This row can't be imported."))

    # ── funds ──
    funds: List[FundPlan] = []
    for pos in stmt.positions:
        pk = pos.key
        sec = sec_by_key[pos.security_key]
        acct = acct_by_id[pos.account_id]
        prs = rows_by_pos.get(pk, [])
        excluded = pk in skipped
        reconciled = excluded or ledger_units[pk] == pos.close_units
        if not reconciled:
            warnings.append(StatementIssue(
                code="NOT_RECONCILED", security_key=sec.key,
                message=f"{sec.name}: the ledger would hold {ledger_units[pk]} units, the statement says {pos.close_units}.",
            ))
        hand = hand_info.get(sec.key)
        funds.append(FundPlan(
            security_key=sec.key, folio=acct.id, name=sec.name, institution=acct.institution,
            investor=investor_name.get(acct.investor),
            distributor=_distributor_key(pos.advisor),
            holding_account=holding_account(pk),
            commodity=codes[sec.key], commodity_status=code_status[sec.key],
            isin=sec.isin, amfi=sec.amfi,
            close_units=pos.close_units, ledger_units=ledger_units[pk], reconciled=reconciled,
            statement_consistent=(pos.close_calculated is None or pos.close_calculated == pos.close_units),
            held=pos.close_units != 0,
            excluded=excluded,
            counts=dict(Counter(r.status for r in prs)),
            matched=matched_count[pk], fallback=fallback_count[pk],
            hand_entries=hand[0] if hand else None, hand_entries_latest=hand[1] if hand else None,
            hand_entries_choice=hand[2] if hand else None,
            years=year_checks[pk],
            rows=[_row_view(r, rows, sec_by_key) for r in sorted(prs, key=lambda r: (r.src.date, r.src.index))],
        ))
    funds.sort(key=lambda f: (not f.held, f.institution, f.name))

    unclaimed = []
    if mapping.cash_matching_account:
        for bp in snap.bank_postings.get(mapping.cash_matching_account, []):
            if bp.ref in snap.claimed_bank_refs or bp.ref in ctx.claimed or bp.currency != ccy:
                continue
            if stmt.period_start <= bp.date <= period_end:
                unclaimed.append(UnclaimedBankTransaction(date=bp.date, amount=bp.amount, narration=bp.narration))

    if removed_refs and any(f.hand_entries_choice == "replace" for f in funds):
        warnings.append(StatementIssue(code="HAND_ENTRIES_REPLACED",
                                       message=f"{len(removed_refs)} hand-entered transaction(s) will be removed and replaced by the statement's history (a backup is kept)."))

    # Say where a row-level message points: fund, date and amount.
    for w in warnings:
        r = rows.get(w.row_id) if w.row_id else None
        if r is not None:
            amount = f", {r.amount}" if r.amount is not None else ""
            w.message = f"{sec_by_key[r.src.security_key].name}, {r.date.isoformat()}{amount}: {w.message}"

    # ── summary + hash ──
    dated = [r.src.date for r in ordered if r.interp not in ("info",)]
    first, last = (min(dated), max(dated)) if dated else (None, None)
    view_txns = [PlannedTransaction(index=i, date=t.date, narration=t.narration, payee=t.payee, kind=t.kind,
                                    row_ids=[x.id for x in t.rows] + [tx.id for x in t.rows for tx in x.taxes],
                                    postings=t.postings, cash_matched=t.cash_matched)
                 for i, t in enumerate(txns)]
    summary = StatementPlanSummary(
        period_start=stmt.period_start, period_end=period_end, first_date=first, last_date=last,
        years=(last.year - first.year + 1) if first else 0,
        rows=len(ordered), funds=len(stmt.positions), funds_held=sum(1 for p in stmt.positions if p.close_units),
        transactions=len(txns),
        new_commodities=sum(1 for c in commodities if c.status == "new"),
        already_imported=sum(1 for r in ordered if r.status == "already"),
        matched=sum(matched_count.values()), fallback=sum(fallback_count.values()),
        investors=[i.name for i in stmt.investors if i.name],
    )
    digest = _hash(txns, commodities, balances, prices, removed_refs, account_metadata, target)
    data = StatementPlanData(
        summary=summary, funds=funds, transactions=view_txns, commodities=commodities,
        balances=balances, prices=prices, removed_hand_entries=len(removed_refs),
        unclaimed_bank_transactions=unclaimed,
        blocking=blocking, warnings=warnings, target_file=target, preview_hash=digest,
    )
    return PlanResult(data=data, transactions=txns, commodities=commodities, balances=balances,
                      prices=prices, removed_refs=removed_refs, account_metadata=account_metadata,
                      target_file=target, roles=set(ctx.used_roles))


# ── Event builders ───────────────────────────────────────────────────────────

class _EventFailed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _meta(pk: Tuple[str, str], pos: Optional[T.StmtPosition], *, rows: Iterable[_Row] = (), opening: Optional[str] = None) -> Dict[str, str]:
    meta: Dict[str, str] = {}
    ids = []
    for r in rows:
        ids.append(r.id)
        ids.extend(t.id for t in r.taxes)
    if ids:
        meta[META_STMT_ID] = " ".join(ids)
    if opening:
        meta[META_STMT_OPENING] = opening
    meta[META_FOLIO] = pk[0]
    if pos is not None and pos.advisor:
        meta[META_ADVISOR] = pos.advisor
    return meta


def _opening_cash(ctx: _Ctx, on: date) -> Optional[str]:
    m = ctx.mapping
    ctx.used_roles.add("cash_opening")
    if m.cutoff_date and on < m.cutoff_date:
        return m.cash_before_cutoff_account
    return m.cash_fallback_account


def _cash_side(ctx: _Ctx, on: date, delta: Decimal) -> Tuple[Optional[str], Optional[bool], Optional[str]]:
    """(account, matched?, bank ref) for a cash movement of ``delta`` on the import side
    (negative = money paid into the fund)."""
    m = ctx.mapping
    ctx.used_roles.add("cash")
    if m.cutoff_date and on < m.cutoff_date:
        ctx.used_roles.add("cash_before_cutoff")
        return m.cash_before_cutoff_account, None, None
    if m.cash_matching_account:
        best = None
        for bp in ctx.snap.bank_postings.get(m.cash_matching_account, []):
            if bp.ref in ctx.claimed or bp.ref in ctx.snap.claimed_bank_refs or bp.currency != ctx.stmt.currency:
                continue
            if bp.amount != -delta or abs((bp.date - on).days) > m.match_window_days:
                continue
            key = (abs((bp.date - on).days), bp.date, bp.ref)
            if best is None or key < best[0]:
                best = (key, bp)
        if best is not None:
            ctx.claimed.add(best[1].ref)
            return m.cash_matching_account, True, best[1].ref
        if not m.cash_fallback_account:
            return m.cash_matching_account, False, None
    return m.cash_fallback_account, False, None


def _taxes_leg(ctx: _Ctx, amount: Decimal) -> List[PlannedPosting]:
    if amount <= 0:
        return []
    ctx.used_roles.add("taxes")
    return [PlannedPosting(account=ctx.mapping.taxes_account or "", units=_q2(amount), currency=ctx.stmt.currency)]


def _consume(lots, pk, units: Decimal, row: _Row) -> List[LotPiece]:
    try:
        return consume_fifo(lots[pk], units)
    except InsufficientUnits as e:
        raise _EventFailed("INSUFFICIENT_UNITS",
                           f"{row.date.isoformat()}: {units} units go out, but only {e.held} are held at that point.")


def _build_event(ctx: _Ctx, row: _Row, partner: Optional[_Row], lots, holding_account, emit: bool) -> Optional[_Txn]:
    """Plan one event's transaction and update the lot book. Returns None for events
    that leave no transaction (simulated history)."""
    ccy = ctx.stmt.currency
    m = ctx.mapping
    pk = row.pos
    pos = next((p for p in ctx.stmt.positions if p.key == pk), None)
    sec = ctx.stmt.security(pk[1])
    code = ctx.codes[sec.key]
    hold = holding_account(pk) or ""
    if emit:
        ctx.used_roles.add("holding")
    interp = row.interp
    if partner is None and interp in ("switch_out", "merge_out"):
        interp = "sell"
    if partner is None and interp in ("switch_in", "merge_in"):
        interp = "buy"

    if partner is not None:
        out, inn = row, partner
        in_pk = inn.pos
        in_sec = ctx.stmt.security(in_pk[1])
        in_code = ctx.codes[in_sec.key]
        in_hold = holding_account(in_pk) or ""
        consumed = _consume(lots, out.pos, out.u, out)
        if out.interp == "merge_out":
            new = convert_lots(consumed, inn.u, Decimal("0.001"))
            lots[in_pk].extend(new)
            if not emit:
                return None
            ctx.used_roles.add("gains")
            postings = [PlannedPosting(account=holding_account(out.pos) or "", units=-p.units, currency=ctx.codes[out.src.security_key],
                                       cost=p.cost, cost_currency=p.currency, cost_date=p.date) for p in consumed]
            postings += [PlannedPosting(account=in_hold, units=p.units, currency=in_code, cost=p.cost,
                                        cost_currency=p.currency, cost_date=p.date) for p in new]
            # A zero leg gives Beancount the currency's tolerance for the carried costs' rounding.
            postings.append(PlannedPosting(account=m.gains_account or "", units=Decimal("0.00"), currency=ccy))
            out_code = ctx.codes[out.src.security_key]
            narration = f"Face value change {in_code}" if out_code == in_code else f"Merge {out_code} into {in_code}"
            return _Txn(inn.date, narration, in_sec.institution, "merge",
                        [out, inn], postings, _meta(in_pk, _pos(ctx, in_pk), rows=[out, inn]))
        basis = sum((p.units * p.cost for p in consumed), ZERO)
        gross = _gross_out(out)
        taxes = out.stt + out.tds + inn.stamp + inn.tds
        transfer = gross - taxes   # what left A for B, by the statement's own figures
        # B's cost is always the registrar's purchase amount (as the broker and the CAS show it).
        # When the halves agree up to rounding, the remainder is the statement's rounding: TDS is
        # printed rounded (₹4.00 while ₹3.26 was withheld), so it corrects the TDS; with no TDS
        # (units rounded to 3 decimals) it adjusts the gain. A user-made pair that doesn't add up
        # puts its gap on the cash side.
        rounding = cash_diff = ZERO
        if abs(transfer - inn.a) <= _rounding_tolerance(out, inn):
            rounding = transfer - inn.a
            if out.tds and taxes + rounding >= 0:
                taxes, rounding = taxes + rounding, ZERO
        else:
            cash_diff = transfer - inn.a
        in_cost = unit_cost(inn.a, inn.u) if inn.u else ZERO
        lots[in_pk].append(LotPiece(inn.u, in_cost, ccy, inn.date))
        if not emit:
            return None
        ctx.used_roles.add("gains")
        out_code = ctx.codes[out.src.security_key]
        price = out.nav if out.nav else (out.a / out.u)
        postings = [PlannedPosting(account=holding_account(out.pos) or "", units=-p.units, currency=out_code,
                                   cost=p.cost, cost_currency=p.currency, cost_date=p.date,
                                   price=price, price_currency=ccy) for p in consumed]
        postings.append(PlannedPosting(account=in_hold, units=inn.u, currency=in_code, cost=in_cost, cost_currency=ccy,
                                       cost_date=inn.date if inn.date != max(out.date, inn.date) else None))
        postings += _taxes_leg(ctx, taxes)
        matched = None
        txn_meta = _meta(out.pos, _pos(ctx, out.pos), rows=[out, inn])
        if cash_diff != 0:
            acct, matched, ref = _cash_side(ctx, out.date, cash_diff)
            postings.append(PlannedPosting(account=acct or "", units=cash_diff, currency=ccy))
            if ref:
                txn_meta[META_STMT_MATCH] = ref
        postings.append(PlannedPosting(account=m.gains_account or "", units=-_q2(gross - basis - rounding), currency=ccy))
        return _Txn(max(out.date, inn.date), f"Switch {out_code} to {in_code}", sec.institution, "switch",
                    [out, inn], postings, txn_meta, matched)

    if interp in ("buy", "reinvest", "segregation") or (interp == "reversal" and (row.units or ZERO) > 0):
        if not row.u:
            raise _EventFailed("NO_UNITS", "A purchase without units can't be placed.")
        if interp == "segregation":
            cost = ZERO
        else:
            if not row.a:
                raise _EventFailed("NO_AMOUNT", "A purchase without an amount can't be placed.")
            cost = unit_cost(row.a, row.u)
        lots[pk].append(LotPiece(row.u, cost, ccy, row.date))
        if not emit:
            return None
        postings = [PlannedPosting(account=hold, units=row.u, currency=code, cost=cost, cost_currency=ccy)]
        meta = _meta(pk, pos, rows=[row])
        if interp == "segregation":
            return _Txn(row.date, f"Segregated portfolio {code}", sec.institution, "segregation", [row], postings, meta)
        if interp == "reinvest":
            ctx.used_roles.add("dividend")
            postings += _taxes_leg(ctx, row.stamp + row.tds)
            postings.append(PlannedPosting(account=m.dividend_account or "", units=-(row.a + _q2(row.stamp + row.tds)), currency=ccy))
            return _Txn(row.date, f"{code} dividend reinvested", sec.institution, "reinvest", [row], postings, meta)
        postings += _taxes_leg(ctx, row.stamp + row.tds)
        cash = -(row.a + row.stamp + row.tds)
        acct, matched, ref = _cash_side(ctx, row.date, cash)
        if ref:
            meta[META_STMT_MATCH] = ref
        postings.append(PlannedPosting(account=acct or "", units=cash, currency=ccy))
        kind = "reversal" if interp == "reversal" else "buy"
        narration = f"Reversal {code}" if kind == "reversal" else f"Buy {code}"
        return _Txn(row.date, narration, sec.institution, kind, [row], postings, meta, matched)

    if interp in ("sell", "reversal"):
        if not row.u:
            raise _EventFailed("NO_UNITS", "A redemption without units can't be placed.")
        consumed = _consume(lots, pk, row.u, row)
        if not emit:
            return None
        ctx.used_roles.add("gains")
        basis = sum((p.units * p.cost for p in consumed), ZERO)
        price = row.nav if row.nav else (row.a / row.u)
        postings = [PlannedPosting(account=hold, units=-p.units, currency=code, cost=p.cost, cost_currency=p.currency,
                                   cost_date=p.date, price=price, price_currency=ccy) for p in consumed]
        meta = _meta(pk, pos, rows=[row])
        gross = _gross_out(row)
        net = gross - row.stt - row.tds
        acct, matched, ref = _cash_side(ctx, row.date, net)
        if ref:
            meta[META_STMT_MATCH] = ref
        postings.append(PlannedPosting(account=acct or "", units=net, currency=ccy))
        postings += _taxes_leg(ctx, row.stt + row.tds)
        postings.append(PlannedPosting(account=m.gains_account or "", units=-_q2(gross - basis), currency=ccy))
        kind = "reversal" if interp == "reversal" else "sell"
        narration = f"Reversal {code}" if kind == "reversal" else f"Sell {code}"
        return _Txn(row.date, narration, sec.institution, kind, [row], postings, meta, matched)

    if interp == "dividend":
        if not row.a:
            raise _EventFailed("NO_AMOUNT", "A dividend without an amount can't be placed.")
        if not emit:
            return None
        ctx.used_roles.add("dividend")
        meta = _meta(pk, pos, rows=[row])
        acct, matched, ref = _cash_side(ctx, row.date, row.a)
        if ref:
            meta[META_STMT_MATCH] = ref
        postings = [PlannedPosting(account=acct or "", units=row.a, currency=ccy)]
        postings += _taxes_leg(ctx, row.tds)
        postings.append(PlannedPosting(account=m.dividend_account or "", units=-(row.a + _q2(row.tds)), currency=ccy))
        return _Txn(row.date, f"{code} dividend", sec.institution, "dividend", [row], postings, meta, matched)

    raise _EventFailed("UNSUPPORTED", f"Rows read as '{interp}' can't be imported.")


def _pos(ctx: _Ctx, pk) -> Optional[T.StmtPosition]:
    return next((p for p in ctx.stmt.positions if p.key == pk), None)


# ── Checks, views, hash ──────────────────────────────────────────────────────

_ROLE_LABEL = {
    "holding": "a holding account for every distributor",
    "cash": "a cash-side account (fallback, or matching)",
    "cash_opening": "a fallback account for the opening holdings",
    "cash_before_cutoff": "an account for before the cutoff date",
    "dividend": "a dividend income account",
    "taxes": "a taxes & charges account",
    "gains": "a capital gains account",
}


def _check_accounts(ctx: _Ctx, txns: List[_Txn], balances: List[PlannedBalance]) -> None:
    m, snap = ctx.mapping, ctx.snap
    missing_roles = []
    if "holding" in ctx.used_roles or balances:
        needed = {_distributor_key(p.advisor) for p in ctx.stmt.positions if p.key not in ctx.skipped}
        if any(not m.holding_accounts.get(k) for k in needed):
            missing_roles.append("holding")
    role_value = {
        "cash": m.cash_fallback_account or m.cash_matching_account,
        "cash_opening": m.cash_fallback_account if not (m.cutoff_date and m.history_start and m.history_start < m.cutoff_date) else m.cash_before_cutoff_account,
        "cash_before_cutoff": m.cash_before_cutoff_account,
        "dividend": m.dividend_account, "taxes": m.taxes_account, "gains": m.gains_account,
    }
    for role, value in role_value.items():
        if role in ctx.used_roles and not value:
            missing_roles.append(role)
    for role in missing_roles:
        ctx.blocking.append(StatementIssue(code="ACCOUNT_REQUIRED", message=f"Choose {_ROLE_LABEL[role]}."))

    uses: Dict[str, Tuple[date, date, Set[str]]] = {}
    for t in txns:
        for p in t.postings:
            if not p.account:
                continue
            first, last, ccys = uses.get(p.account, (t.date, t.date, set()))
            uses[p.account] = (min(first, t.date), max(last, t.date), ccys | {p.currency})
    for b in balances:
        first, last, ccys = uses.get(b.account, (b.date, b.date, set()))
        uses[b.account] = (min(first, b.date), max(last, b.date), ccys | {b.commodity})
    for acct, (first, last, ccys) in sorted(uses.items()):
        snap_acct = snap.accounts.get(acct)
        if snap_acct is None:
            ctx.blocking.append(StatementIssue(code="ACCOUNT_NOT_FOUND", message=f"{acct} doesn't exist. Create it in the Accounts view."))
            continue
        if snap_acct.open_date > first:
            ctx.blocking.append(StatementIssue(
                code="ACCOUNT_OPENS_TOO_LATE",
                message=(f"{acct} opens on {snap_acct.open_date.isoformat()}, but this import uses it from "
                         f"{first.isoformat()}. Change its open date in the Accounts view, or set a history start date."),
            ))
        if snap_acct.close_date and snap_acct.close_date < last:
            ctx.blocking.append(StatementIssue(code="ACCOUNT_CLOSED",
                                               message=f"{acct} is closed on {snap_acct.close_date.isoformat()}, before this import's last entry ({last.isoformat()})."))
        if snap_acct.currencies:
            bad = sorted(c for c in ccys if c not in snap_acct.currencies)
            if bad:
                ctx.blocking.append(StatementIssue(code="ACCOUNT_CURRENCY",
                                                   message=f"{acct} only allows {', '.join(snap_acct.currencies)}, but this import puts {', '.join(bad)} in it."))


def _row_view(r: _Row, rows: Dict[str, _Row], sec_by_key) -> PlannedRow:
    partner = rows.get(r.pair) if r.pair else None
    return PlannedRow(
        id=r.id, date=r.date, kind=r.src.kind, description=r.src.description, amount=r.amount, units=r.units,
        nav=r.src.nav, balance=r.src.balance, interpretation=r.interp, proposed=r.proposed, reason=r.reason,
        status=r.status, corrected=r.corrected, pair_id=r.pair,
        pair_fund=sec_by_key[partner.src.security_key].name if partner else None,
        parent_id=r.parent, transaction=r.txn,
    )


def _hash(txns, commodities, balances, prices, removed, account_metadata, target) -> str:
    def ser(x):
        if hasattr(x, "model_dump"):
            return x.model_dump(mode="json")
        raise TypeError
    payload = {
        "t": [[t.date.isoformat(), t.narration, t.payee, t.kind, [p.model_dump(mode="json") for p in t.postings], sorted(t.meta.items())] for t in txns],
        "c": commodities, "b": balances, "p": prices, "r": removed, "a": account_metadata, "f": target,
    }
    return hashlib.sha256(json.dumps(payload, default=ser, sort_keys=True).encode()).hexdigest()
