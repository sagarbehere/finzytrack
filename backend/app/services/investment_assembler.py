"""investment_assembler — typed investment events → balanced draft transactions.

The single backend component that turns a typed **event** (e.g. `buy 10 VOO
@417.45`) into the correct, balanced Beancount **draft** (cost/price/income legs),
resolving lots/prices/commodities against the SQLite mirror. It **returns drafts;
it never writes** — drafts stage into the shared review table and Register uses the
existing commit path (dev-docs/investment-view.md §2, §6, §12).

Slice 1 events: Buy, DRIP (reinvest), Cash Dividend, Add-Holding-at-Cost. The Sale
event (stateful lot resolution) lands in Slice 2. Accounts are **supplied by the
caller** — the assembler never invents account paths by name; it classifies by
metadata (`is_currency`, `asset-class`) via the mirror
(metadata-conventions.md, feedback_no_hardcoded_account_names).

Money is exact `Decimal` end to end (dev-docs/money-types.md).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, List, Optional, Tuple

from pydantic import TypeAdapter, ValidationError

from app.services.sqlite_reader import SqliteReader
from app.schemas.investment_schemas import (
    InvestmentEvent,
    BuyEvent,
    DripEvent,
    CashDividendEvent,
    SellEvent,
    CdMaturityEvent,
    InterestEvent,
    AddHoldingEvent,
    DraftPosting,
    DraftTransaction,
    AssembleIssue,
    AssembleData,
    HoldingLot,
    CommodityHoldings,
    HoldingsData,
)

logger = logging.getLogger(__name__)

# Balance tolerance: all amounts are exact Decimals, but keep a small epsilon so a
# legitimately-rounded input never trips a false imbalance (mirrors the NL parser).
_BALANCE_TOLERANCE = Decimal("0.005")

_EVENT_ADAPTER = TypeAdapter(InvestmentEvent)


def parse_investment_events(raw_events: List[Any]) -> Tuple[List[InvestmentEvent], List[str]]:
    """Validate raw event dicts (from an AI parser) into typed InvestmentEvents.

    Salvages the good ones and warns on the rest, so an unusable event becomes a
    warning the user sees rather than a failed request. Shared by the NL and file
    AI parsers (dev-docs/investment-view.md §8).
    """
    events: List[InvestmentEvent] = []
    warnings: List[str] = []
    for i, raw in enumerate(raw_events):
        if not isinstance(raw, dict):
            warnings.append(f"Event {i + 1}: expected an object, got {type(raw).__name__}.")
            continue
        try:
            events.append(_EVENT_ADAPTER.validate_python(raw))
        except ValidationError as e:
            first = e.errors()[0] if e.errors() else {}
            loc = ".".join(str(x) for x in first.get("loc", [])) or "(root)"
            warnings.append(f"Event {i + 1}: could not be understood ({loc}: {first.get('msg', 'invalid')}).")
    return events, warnings


class _EventError(Exception):
    """A per-event build failure → becomes a typed error, the event is skipped."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _weight(p: DraftPosting) -> Tuple[Decimal, str]:
    """The balance weight of a posting and the currency it lands in.

    Cost-bearing legs weigh `units × cost` in the cost currency; `@` priced legs
    weigh `units × price`; `@@` (total) legs weigh the signed total; otherwise the
    plain amount in its own currency.
    """
    if p.cost_amount is not None:
        return (p.amount * p.cost_amount, p.cost_currency or p.currency)
    if p.price_amount is not None:
        if p.price_type == "@@":
            sign = Decimal(1) if p.amount >= 0 else Decimal(-1)
            return (sign * p.price_amount, p.price_currency or p.currency)
        return (p.amount * p.price_amount, p.price_currency or p.currency)
    return (p.amount, p.currency)


def _balance_error(postings: List[DraftPosting]) -> Optional[str]:
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for p in postings:
        w, ccy = _weight(p)
        totals[ccy] += w
    for ccy, tot in totals.items():
        if abs(tot) > _BALANCE_TOLERANCE:
            return f"does not balance: {tot} {ccy}"
    return None


class InvestmentAssembler:
    """Assembles draft transactions and reads holdings from the mirror."""

    def __init__(self, reader: SqliteReader):
        self.reader = reader

    # ── Holdings ─────────────────────────────────────────────────────────────

    def holdings(self, commodity: Optional[str] = None) -> HoldingsData:
        """Current positions and their open lots (the `lots` table), enriched with
        commodity metadata and the latest price. Feeds the Sale lot picker and the
        holding context in the forms.
        """
        lots = self.reader.get_lots()
        commodities = {c.code: c for c in self.reader.get_commodities()}

        by_comm: dict[str, list[dict]] = defaultdict(list)
        for lot in lots:
            comm = lot["units_currency"]
            if commodity and comm != commodity:
                continue
            by_comm[comm].append(lot)

        result: List[CommodityHoldings] = []
        for comm in sorted(by_comm):
            cdet = commodities.get(comm)
            price = self._latest_price(comm)
            hlots: List[HoldingLot] = []
            total = Decimal("0")
            for lot in by_comm[comm]:
                units = Decimal(lot["units_number"])
                total += units
                hlots.append(HoldingLot(
                    account=lot["account"],
                    units=units,
                    cost_number=Decimal(lot["cost_number"]),
                    cost_currency=lot["cost_currency"],
                    acquisition_date=date.fromisoformat(lot["acquisition_date"]),
                    book_value=Decimal(lot["book_value"]) if lot.get("book_value") else None,
                    label=lot.get("label"),
                ))
            hlots.sort(key=lambda l: l.acquisition_date)  # FIFO-friendly ordering
            result.append(CommodityHoldings(
                commodity=comm,
                is_currency=cdet.is_currency if cdet else False,
                asset_class=cdet.asset_class if cdet else None,
                name=cdet.name if cdet else None,
                total_units=total,
                last_price=price[0] if price else None,
                last_price_currency=price[1] if price else None,
                last_price_date=price[2] if price else None,
                lots=hlots,
            ))
        return HoldingsData(holdings=result)

    def _latest_price(self, commodity: str) -> Optional[Tuple[Decimal, str, date]]:
        rows = self.reader.get_prices(currency=commodity)  # base_currency == commodity, date asc
        if not rows:
            return None
        last = rows[-1]
        return (
            Decimal(last["quote_number"]),
            last["quote_currency"],
            date.fromisoformat(last["date"]),
        )

    # ── Assemble ─────────────────────────────────────────────────────────────

    def assemble(self, events: List[InvestmentEvent]) -> AssembleData:
        commodities = {c.code: c for c in self.reader.get_commodities()}
        drafts: List[DraftTransaction] = []
        warnings: List[AssembleIssue] = []
        errors: List[AssembleIssue] = []

        for i, ev in enumerate(events):
            try:
                draft, ev_warnings = self._build(i, ev, commodities)
                drafts.append(draft)
                warnings.extend(ev_warnings)
            except _EventError as e:
                errors.append(AssembleIssue(event_index=i, code=e.code, message=e.message))

        return AssembleData(drafts=drafts, warnings=warnings, errors=errors)

    def _build(
        self, index: int, ev: InvestmentEvent, commodities: dict
    ) -> Tuple[DraftTransaction, List[AssembleIssue]]:
        if isinstance(ev, BuyEvent):
            return self._build_buy(index, ev, commodities)
        if isinstance(ev, DripEvent):
            return self._build_drip(index, ev, commodities)
        if isinstance(ev, CashDividendEvent):
            return self._build_cash_dividend(index, ev, commodities)
        if isinstance(ev, SellEvent):
            return self._build_sell(index, ev, commodities)
        if isinstance(ev, CdMaturityEvent):
            return self._build_cd_maturity(index, ev)
        if isinstance(ev, InterestEvent):
            return self._build_interest(index, ev)
        if isinstance(ev, AddHoldingEvent):
            return self._build_add_holding(index, ev, commodities)
        raise _EventError("UNKNOWN_EVENT_TYPE", f"Unsupported event type: {getattr(ev, 'type', '?')}")

    # ── Event builders ───────────────────────────────────────────────────────

    def _build_buy(self, index, ev: BuyEvent, commodities):
        warnings = self._check_holding_security(index, ev.security, commodities)
        self._require_positive(ev.units, "units")
        self._require_non_negative(ev.unit_price, "unit_price")

        gross = ev.units * ev.unit_price
        postings = [DraftPosting(
            account=ev.holding_account, amount=ev.units, currency=ev.security,
            cost_amount=ev.unit_price, cost_currency=ev.currency,
        )]
        cash_out = gross
        if ev.fee is not None and ev.fee != 0:
            self._require_positive(ev.fee, "fee")
            if not ev.fee_account:
                raise _EventError("MISSING_FEE_ACCOUNT", "A fee amount was given without a fee_account")
            postings.append(DraftPosting(account=ev.fee_account, amount=ev.fee, currency=ev.currency))
            cash_out = gross + ev.fee
        postings.append(DraftPosting(account=ev.cash_account, amount=-cash_out, currency=ev.currency))

        return self._finalize(index, ev, ev.holding_account, f"Buy {ev.security}", postings, warnings)

    def _build_drip(self, index, ev: DripEvent, commodities):
        warnings = self._check_holding_security(index, ev.security, commodities)
        self._require_positive(ev.units, "units")
        self._require_non_negative(ev.unit_price, "unit_price")

        postings = [
            DraftPosting(
                account=ev.holding_account, amount=ev.units, currency=ev.security,
                cost_amount=ev.unit_price, cost_currency=ev.currency,
            ),
            DraftPosting(
                account=ev.income_account, amount=-(ev.units * ev.unit_price), currency=ev.currency,
            ),
        ]
        return self._finalize(
            index, ev, ev.holding_account, f"{ev.security} dividend reinvested", postings, warnings
        )

    def _build_cash_dividend(self, index, ev: CashDividendEvent, commodities):
        warnings: List[AssembleIssue] = []
        # Security is only for narration/attribution here — an unknown one warns, never blocks.
        if ev.security not in commodities:
            warnings.append(AssembleIssue(
                event_index=index, code="UNKNOWN_COMMODITY",
                message=f"Security {ev.security} is not a known commodity; dividend still recorded",
            ))
        self._require_positive(ev.amount, "amount")

        postings = [
            DraftPosting(account=ev.cash_account, amount=ev.amount, currency=ev.currency),
            DraftPosting(account=ev.income_account, amount=-ev.amount, currency=ev.currency),
        ]
        return self._finalize(index, ev, ev.cash_account, f"{ev.security} dividend", postings, warnings)

    def _build_sell(self, index, ev: SellEvent, commodities):
        """Reduce a holding, realizing a gain — cost basis resolved from the mirror lots.

        Emits one reducing leg per consumed lot, each stamped with that lot's explicit
        ``{cost, date}`` (so it books deterministically under any Beancount method) plus
        the sale price ``@``. Gross gain = proceeds − cost basis; a fee is a **separate**
        Expenses leg (it does not reduce the gain). A whole-position redemption legitimately
        expands into many legs — that is the correct mirror of Beancount's booking, not a bug.
        """
        warnings = self._check_holding_security(index, ev.security, commodities)
        self._require_positive(ev.units, "units")
        self._require_non_negative(ev.sale_price, "sale_price")

        available = [
            {
                "units": Decimal(l["units_number"]),
                "cost": Decimal(l["cost_number"]),
                "cost_currency": l["cost_currency"],
                "date": date.fromisoformat(l["acquisition_date"]),
            }
            for l in self.reader.get_lots(ev.holding_account)
            if l["units_currency"] == ev.security and Decimal(l["units_number"]) > 0
        ]
        consumed = self._select_lots(ev, available)  # [(units, cost, date, cost_currency)]

        postings: List[DraftPosting] = []
        cost_basis = Decimal("0")
        for units, cost, lot_date, cost_currency in consumed:
            if cost_currency != ev.currency:
                # Cross-currency cost basis (lot held in another currency than the sale
                # currency) is out of scope — refuse rather than emit an imbalanced draft.
                raise _EventError(
                    "COST_CURRENCY_MISMATCH",
                    f"Lot cost currency {cost_currency} differs from sale currency {ev.currency}",
                )
            postings.append(DraftPosting(
                account=ev.holding_account, amount=-units, currency=ev.security,
                cost_amount=cost, cost_currency=cost_currency, cost_date=lot_date,
                price_amount=ev.sale_price, price_currency=ev.currency, price_type="@",
            ))
            cost_basis += units * cost

        proceeds = ev.units * ev.sale_price
        gain = proceeds - cost_basis  # gross; a fee is a separate expense, never nets the gain

        cash_amount = proceeds
        fee_leg: Optional[DraftPosting] = None
        if ev.fee is not None and ev.fee != 0:
            self._require_positive(ev.fee, "fee")
            if not ev.fee_account:
                raise _EventError("MISSING_FEE_ACCOUNT", "A fee amount was given without a fee_account")
            cash_amount = proceeds - ev.fee
            fee_leg = DraftPosting(account=ev.fee_account, amount=ev.fee, currency=ev.currency)

        postings.append(DraftPosting(account=ev.cash_account, amount=cash_amount, currency=ev.currency))
        if fee_leg is not None:
            postings.append(fee_leg)
        postings.append(DraftPosting(account=ev.gains_account, amount=-gain, currency=ev.currency))

        return self._finalize(index, ev, ev.holding_account, f"Sell {ev.security}", postings, warnings)

    def _select_lots(self, ev: SellEvent, available: List[dict]) -> List[tuple]:
        """Resolve which lots (and how many units of each) the sale consumes.

        Specific-lot selection (``ev.lots``) matches each chosen lot to a held one by
        (date, cost, currency); otherwise ``ev.strategy`` orders the held lots
        (fifo/lifo/hifo) and consumes them greedily. Refuses — never guesses — when the
        held lots don't cover the sale (INSUFFICIENT_LOTS).
        """
        if not available:
            raise _EventError("INSUFFICIENT_LOTS", f"No lots held for {ev.security} in {ev.holding_account}")

        if ev.lots:
            avail_by_key: dict = {}
            for a in available:
                avail_by_key[(a["date"], a["cost"], a["cost_currency"])] = a
            remaining = {k: v["units"] for k, v in avail_by_key.items()}
            consumed: List[tuple] = []
            total = Decimal("0")
            for sel in ev.lots:
                self._require_positive(sel.units, "lot units")
                key = (sel.acquisition_date, sel.cost_number, sel.cost_currency)
                if key not in avail_by_key:
                    raise _EventError(
                        "LOT_NOT_FOUND",
                        f"No held lot matches {sel.units} @ {sel.cost_number} {sel.cost_currency} "
                        f"acquired {sel.acquisition_date}",
                    )
                if sel.units > remaining[key]:
                    raise _EventError(
                        "INSUFFICIENT_LOTS",
                        f"Lot acquired {sel.acquisition_date} has only {remaining[key]} units available",
                    )
                remaining[key] -= sel.units
                total += sel.units
                consumed.append((sel.units, sel.cost_number, sel.acquisition_date, sel.cost_currency))
            if total != ev.units:
                raise _EventError(
                    "LOT_UNITS_MISMATCH",
                    f"Selected lots total {total} units but the sale is for {ev.units}",
                )
            return consumed

        ordered = self._order_lots(available, ev.strategy)
        consumed = []
        remaining_units = ev.units
        for a in ordered:
            if remaining_units <= 0:
                break
            take = a["units"] if a["units"] < remaining_units else remaining_units
            consumed.append((take, a["cost"], a["date"], a["cost_currency"]))
            remaining_units -= take
        if remaining_units > 0:
            held = sum(a["units"] for a in available)
            raise _EventError(
                "INSUFFICIENT_LOTS",
                f"Only {held} units of {ev.security} held in {ev.holding_account}; cannot sell {ev.units}",
            )
        return consumed

    @staticmethod
    def _order_lots(available: List[dict], strategy: str) -> List[dict]:
        if strategy == "lifo":
            return sorted(available, key=lambda a: a["date"], reverse=True)
        if strategy == "hifo":
            return sorted(available, key=lambda a: a["cost"], reverse=True)
        return sorted(available, key=lambda a: a["date"])  # fifo (oldest first)

    def _build_cd_maturity(self, index, ev: CdMaturityEvent):
        """A CD/term-deposit matures: principal (+ final interest) paid to a destination.

        Draws down the CD by principal, credits the destination with principal+interest,
        and books the interest to income (omitted when zero — a principal-only return).
        The CD is the sole reduced investment leg → interest attributes structurally as a
        maturity (metadata-conventions.md), so no metadata is required.
        """
        self._require_positive(ev.principal, "principal")
        self._require_non_negative(ev.interest, "interest")

        total = ev.principal + ev.interest
        postings = [
            DraftPosting(account=ev.destination_account, amount=total, currency=ev.currency),
            DraftPosting(account=ev.cd_account, amount=-ev.principal, currency=ev.currency),
        ]
        if ev.interest > 0:
            if not ev.interest_income_account:
                raise _EventError("MISSING_INCOME_ACCOUNT", "Interest was given without an interest_income_account")
            postings.append(DraftPosting(account=ev.interest_income_account, amount=-ev.interest, currency=ev.currency))

        return self._finalize(index, ev, ev.cd_account, "CD matured", postings, [])

    def _build_interest(self, index, ev: InterestEvent):
        """Periodic interest — credited to ``deposit_account`` (the instrument itself when
        compounding, or a separate cash account when paid out) against an income leg."""
        self._require_positive(ev.interest, "interest")
        postings = [
            DraftPosting(account=ev.deposit_account, amount=ev.interest, currency=ev.currency),
            DraftPosting(account=ev.income_account, amount=-ev.interest, currency=ev.currency),
        ]
        return self._finalize(index, ev, ev.deposit_account, "Interest", postings, [])

    def _build_add_holding(self, index, ev: AddHoldingEvent, commodities):
        warnings = self._check_holding_security(index, ev.security, commodities)
        self._require_positive(ev.units, "units")
        self._require_non_negative(ev.avg_cost, "avg_cost")

        postings = [
            DraftPosting(
                account=ev.holding_account, amount=ev.units, currency=ev.security,
                cost_amount=ev.avg_cost, cost_currency=ev.currency,
            ),
            DraftPosting(
                account=ev.opening_account, amount=-(ev.units * ev.avg_cost), currency=ev.currency,
            ),
        ]
        return self._finalize(
            index, ev, ev.holding_account, f"Opening holding {ev.security}", postings, warnings
        )

    # ── Shared helpers ───────────────────────────────────────────────────────

    def _check_holding_security(self, index: int, security: str, commodities: dict) -> List[AssembleIssue]:
        """A holding event's security must not be a currency. An unknown security is
        allowed (first buy of a new commodity) but flagged so the UI can offer to
        declare it; a currency is a hard error (you don't hold a currency at cost here).
        """
        warnings: List[AssembleIssue] = []
        cdet = commodities.get(security)
        if cdet is None:
            warnings.append(AssembleIssue(
                event_index=index, code="NEW_COMMODITY",
                message=f"{security} is not yet a declared commodity; it will be created on use",
            ))
        elif cdet.is_currency:
            raise _EventError("CURRENCY_NOT_HOLDING", f"{security} is a currency, not a holding")
        return warnings

    @staticmethod
    def _require_positive(value: Decimal, name: str) -> None:
        if value is None or value <= 0:
            raise _EventError("INVALID_AMOUNT", f"{name} must be greater than zero")

    @staticmethod
    def _require_non_negative(value: Decimal, name: str) -> None:
        if value is None or value < 0:
            raise _EventError("INVALID_AMOUNT", f"{name} must not be negative")

    def _finalize(
        self, index: int, ev: InvestmentEvent, source_account: str,
        default_narration: str, postings: List[DraftPosting], warnings: List[AssembleIssue],
    ) -> Tuple[DraftTransaction, List[AssembleIssue]]:
        err = _balance_error(postings)
        if err:
            raise _EventError("ASSEMBLE_IMBALANCE", f"Assembled transaction {err}")

        meta: dict[str, str] = {}
        if ev.external_id:
            meta["external_id"] = ev.external_id
        if ev.external_id_type:
            meta["external_id_type"] = ev.external_id_type
        if ev.note:
            meta["note"] = ev.note

        draft = DraftTransaction(
            date=ev.date,
            flag="*",
            payee=ev.payee or "",
            memo=ev.memo,
            narration=ev.narration or default_narration,
            tags=ev.tags,
            links=ev.links,
            postings=postings,
            source_account=source_account,
            meta=meta,
        )
        return draft, warnings
