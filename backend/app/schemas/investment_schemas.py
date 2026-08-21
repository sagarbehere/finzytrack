"""
Pydantic schemas for the investment-event assembler.

The assembler turns a **typed investment event** (Buy, DRIP, Cash Dividend,
Add-Holding-at-Cost, …) into a balanced **draft transaction** in the same shape
the commit path consumes (cost/price per posting, `source_account`, `meta` for
dedupe). It reads the SQLite mirror for lots/prices/commodities but **never
writes** — drafts stage into the shared review table and Register uses the
existing `/api/import/commit` path.

Design refs: dev-docs/investment-view.md §6 (event model), §6.3 (per-event
Beancount geometry), metadata-conventions.md (classify/attribute by metadata,
never by account name). Money is `Decimal` in Python and a JSON string on the
wire (dev-docs/money-types.md) — Pydantic serializes `Decimal`→str in JSON mode.
"""

from typing import List, Optional, Dict, Union, Literal
from datetime import date as DateType
from decimal import Decimal
from typing import Annotated
from pydantic import BaseModel, Field


# =====================================
# Event inputs (the caller supplies accounts — never invented by name)
# =====================================

class InvestmentEventBase(BaseModel):
    """Fields common to every event (dev-docs/investment-view.md §6.2)."""
    date: DateType = Field(..., description="Event date (YYYY-MM-DD)")
    currency: str = Field(..., description="Cash / quote currency (e.g. USD)")
    narration: Optional[str] = Field(default=None, description="Overrides the default narration")
    payee: Optional[str] = Field(default=None, description="Optional payee")
    memo: Optional[str] = Field(default=None, description="Optional memo (stored as memo: metadata)")
    tags: List[str] = Field(default_factory=list, description="Transaction tags")
    links: List[str] = Field(default_factory=list, description="Transaction links")
    external_id: Optional[str] = Field(default=None, description="Broker transaction id (dedupe)")
    external_id_type: Optional[str] = Field(default=None, description="Type of external_id")
    note: Optional[str] = Field(default=None, description="Free-text note (stored in meta)")


class BuyEvent(InvestmentEventBase):
    """Acquire a holding at cost."""
    type: Literal["buy"]
    holding_account: str = Field(..., description="Assets account receiving the units")
    security: str = Field(..., description="Commodity code (is_currency=0)")
    units: Decimal = Field(..., description="Units acquired (> 0)")
    unit_price: Decimal = Field(..., description="Cost per unit in `currency`")
    cash_account: str = Field(..., description="Assets/Liabilities account funding the buy")
    fee: Optional[Decimal] = Field(default=None, description="Optional fee amount")
    fee_account: Optional[str] = Field(default=None, description="Expenses account for the fee")


class DripEvent(InvestmentEventBase):
    """Reinvested dividend/income buys units (nets to zero cash)."""
    type: Literal["drip"]
    holding_account: str = Field(..., description="Assets account receiving the units")
    security: str = Field(..., description="Commodity code (is_currency=0)")
    units: Decimal = Field(..., description="Units reinvested (> 0)")
    unit_price: Decimal = Field(..., description="Cost per unit (≈1.00 for money-market)")
    income_account: str = Field(..., description="Income account (income-type: dividend|interest)")


class CashDividendEvent(InvestmentEventBase):
    """Dividend paid to cash (not reinvested)."""
    type: Literal["cash_dividend"]
    cash_account: str = Field(..., description="Assets account receiving the cash")
    security: str = Field(..., description="Security the dividend is for (attribution)")
    amount: Decimal = Field(..., description="Dividend amount (> 0)")
    income_account: str = Field(..., description="Income account (income-type: dividend)")


class LotSelection(BaseModel):
    """One chosen lot + how many units of it to sell (from GET /holdings)."""
    acquisition_date: DateType = Field(..., description="The lot's acquisition date")
    cost_number: Decimal = Field(..., description="The lot's per-unit cost")
    cost_currency: str = Field(..., description="The lot's cost currency")
    units: Decimal = Field(..., description="Units of this lot to sell (> 0)")


class SellEvent(InvestmentEventBase):
    """Reduce a holding and realize a gain — cost basis resolved from the mirror lots.

    Provide either an explicit ``lots`` selection (specific-lot) or a ``strategy``
    (fifo/lifo/hifo) to auto-select which lots to consume. Every reducing leg is
    written with an explicit ``{cost, date}`` so it books deterministically under any
    Beancount booking method (dev-docs/investment-view.md §6.3 event 4, §6.5).
    """
    type: Literal["sell"]
    holding_account: str = Field(..., description="Assets account the units are sold from")
    security: str = Field(..., description="Commodity code (is_currency=0)")
    units: Decimal = Field(..., description="Total units to sell (> 0)")
    sale_price: Decimal = Field(..., description="Sale price per unit in `currency`")
    cash_account: str = Field(..., description="Assets account receiving the proceeds")
    gains_account: str = Field(..., description="Income account for the realized gain (income-type: capital-gains)")
    fee: Optional[Decimal] = Field(default=None, description="Optional fee amount")
    fee_account: Optional[str] = Field(default=None, description="Expenses account for the fee")
    lots: Optional[List[LotSelection]] = Field(
        default=None, description="Specific lots to consume; if omitted, `strategy` selects them"
    )
    strategy: Literal["fifo", "lifo", "hifo"] = Field(
        default="fifo",
        description="Auto lot-selection when `lots` is omitted: fifo (oldest), lifo (newest), hifo (highest cost)",
    )


class CdMaturityEvent(InvestmentEventBase):
    """CD / term-deposit matures: principal (+ any final interest) paid to a destination."""
    type: Literal["cd_maturity"]
    cd_account: str = Field(..., description="The CD/term-deposit account (Assets, asset-class: cd)")
    principal: Decimal = Field(..., description="Principal returned (> 0)")
    interest: Decimal = Field(default=Decimal("0"), description="Final interest paid at maturity (>= 0)")
    destination_account: str = Field(..., description="Account receiving principal + interest (checking/savings)")
    interest_income_account: Optional[str] = Field(
        default=None, description="Income account for the interest (income-type: interest); required if interest > 0"
    )


class InterestEvent(InvestmentEventBase):
    """Periodic interest — credited to the instrument (compounding) or a separate account (paid out).

    The geometry is the same either way; which account receives the interest is the
    caller's choice, and that choice is what makes it compounding vs paid out
    (dev-docs/metadata-conventions.md "Interest attribution").
    """
    type: Literal["interest"]
    deposit_account: str = Field(
        ..., description="Account receiving the interest — the instrument itself (compounding) or a cash account (paid out)"
    )
    interest: Decimal = Field(..., description="Interest amount (> 0)")
    income_account: str = Field(..., description="Income account (income-type: interest)")


class AddHoldingEvent(InvestmentEventBase):
    """Bootstrap a currently-held position at an aggregate cost (positions-snapshot)."""
    type: Literal["add_holding"]
    holding_account: str = Field(..., description="Assets account holding the position")
    security: str = Field(..., description="Commodity code (is_currency=0)")
    units: Decimal = Field(..., description="Units currently held (> 0)")
    avg_cost: Decimal = Field(..., description="Aggregate cost per unit in `currency`")
    opening_account: str = Field(
        ...,
        description="Equity opening-balances account (caller-selected; never hardcoded)",
    )


InvestmentEvent = Annotated[
    Union[BuyEvent, DripEvent, CashDividendEvent, SellEvent, CdMaturityEvent, InterestEvent, AddHoldingEvent],
    Field(discriminator="type"),
]


class AssembleRequest(BaseModel):
    """Request body for POST /api/investments/assemble."""
    events: List[InvestmentEvent] = Field(..., description="One or more typed investment events")


# =====================================
# Draft output (maps 1:1 to the commit path / review table)
# =====================================

class DraftPosting(BaseModel):
    """A single posting of an assembled draft (mirrors CommitPosting)."""
    account: str
    amount: Decimal
    currency: str
    cost_amount: Optional[Decimal] = None
    cost_currency: Optional[str] = None
    cost_date: Optional[DateType] = None
    price_amount: Optional[Decimal] = None
    price_currency: Optional[str] = None
    price_type: Optional[str] = None
    posting_meta: Optional[Dict[str, str]] = None


class DraftTransaction(BaseModel):
    """A balanced draft transaction (maps 1:1 to CommitTransaction)."""
    date: DateType
    flag: str = "*"
    payee: str = ""
    memo: Optional[str] = None
    narration: str = ""
    tags: List[str] = Field(default_factory=list)
    links: List[str] = Field(default_factory=list)
    postings: List[DraftPosting]
    source_account: str
    meta: Dict[str, str] = Field(default_factory=dict)


class AssembleIssue(BaseModel):
    """A typed warning or error tied to a specific input event."""
    event_index: int = Field(..., description="Index of the event in the request")
    code: str = Field(..., description="Machine code, e.g. INSUFFICIENT_LOTS, UNKNOWN_COMMODITY")
    message: str = Field(..., description="Human-readable detail")


class AssembleData(BaseModel):
    """Response data for POST /api/investments/assemble.

    Partial success is allowed: some events become drafts while others produce
    typed errors (skipped) or warnings (draft still built).
    """
    drafts: List[DraftTransaction] = Field(default_factory=list)
    warnings: List[AssembleIssue] = Field(default_factory=list)
    errors: List[AssembleIssue] = Field(default_factory=list)


# =====================================
# Holdings (feeds the Sale lot picker + holding context)
# =====================================

class HoldingLot(BaseModel):
    """One open lot of a held commodity, read from the mirror `lots` table."""
    account: str
    units: Decimal
    cost_number: Decimal
    cost_currency: str
    acquisition_date: DateType
    book_value: Optional[Decimal] = None
    label: Optional[str] = None


class CommodityHoldings(BaseModel):
    """All open lots of one commodity, plus its latest price."""
    commodity: str
    is_currency: bool
    asset_class: Optional[str] = None
    name: Optional[str] = None
    total_units: Decimal
    last_price: Optional[Decimal] = None
    last_price_currency: Optional[str] = None
    last_price_date: Optional[DateType] = None
    lots: List[HoldingLot] = Field(default_factory=list)


class HoldingsData(BaseModel):
    """Response data for GET /api/investments/holdings."""
    holdings: List[CommodityHoldings] = Field(default_factory=list)
