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
from typing import List, Optional, Tuple

from app.services.sqlite_reader import SqliteReader
from app.schemas.investment_schemas import (
    InvestmentEvent,
    BuyEvent,
    DripEvent,
    CashDividendEvent,
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
