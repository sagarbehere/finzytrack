"""Pydantic schemas for the price endpoints (/api/prices)."""

from datetime import date
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.commodity_schemas import CommodityStr


class PriceUpdateData(BaseModel):
    """Result of a price fetch → sidecar write."""
    added: int = Field(..., description="Number of new (date, base, quote) price points added.")
    updated: int = Field(0, description="Existing price points whose number changed (the newest stored day is re-fetched).")
    total: int = Field(..., description="Total price points in the sidecar after the update.")
    as_of: Optional[str] = Field(None, description="Date of the most recent price, YYYY-MM-DD, or null if none.")
    symbols: List[str] = Field(default_factory=list, description="Tickers that were fetched.")
    skipped: List[str] = Field(default_factory=list, description="Holdings not fetched (e.g. money-market funds).")
    failed: List[str] = Field(default_factory=list, description="Holdings the provider returned nothing for, as 'CODE (SYMBOL): reason'.")


# ── Prices tab (dev-docs/investment-view.md §10.2) ───────────────────────────

PriceSource = Literal["sidecar", "ledger"]
PriceCadence = Literal["daily", "monthly", "computed", "never"]


class LatestPrice(BaseModel):
    date: date
    number: Decimal = Field(..., description="Price of one unit, in the row's quote currency.")
    source: PriceSource = Field(..., description="'sidecar' (the price file) or 'ledger' (a price written in the ledger file).")


class PriceOverviewRow(BaseModel):
    """One (commodity, quote currency) pair that has prices or is held."""
    commodity: CommodityStr
    quote_currency: Optional[str] = Field(None, description="Quote currency; null for a held commodity with no price and no cost currency.")
    asset_class: Optional[str] = Field(None, description="The commodity's asset-class metadata, as written.")
    cadence: PriceCadence = Field(..., description="How the fetcher prices it: daily, monthly, computed (I-Bond) or never.")
    held: bool = Field(..., description="Net units across all accounts are above zero.")
    latest: Optional[LatestPrice] = None
    age_days: Optional[int] = Field(None, description="Days from the latest price to today.")
    count: int = Field(..., ge=0, description="Number of prices for this pair.")
    out_of_date: bool = Field(..., description="Held, priced by cadence, and the latest price is older than the cadence allows (or missing).")
    not_fetched_reason: Optional[str] = Field(None, description="Why the fetcher won't price it, when it won't.")


class PriceOverviewData(BaseModel):
    rows: List[PriceOverviewRow]
    today: date = Field(..., description="The date age and out-of-date are measured against.")


class PricePoint(BaseModel):
    date: date
    number: Decimal
    quote_currency: str
    source: PriceSource


class PriceHistoryData(BaseModel):
    prices: List[PricePoint] = Field(..., description="Newest first.")
    total: int = Field(..., ge=0)


class PriceSet(BaseModel):
    """Add a price, or replace the one at the same (date, commodity, quote currency)."""
    date: date
    commodity: CommodityStr
    number: Decimal = Field(..., gt=0, description="Price of one unit (> 0).")
    quote_currency: CommodityStr
    replace: bool = Field(True, description="False: fail with 409 PRICE_EXISTS if this date already has a price, instead of replacing it.")


class PriceKey(BaseModel):
    date: date
    commodity: CommodityStr
    quote_currency: CommodityStr


class PriceEditRequest(BaseModel):
    """A batch of price edits, saved as one write to the price file."""
    set: List[PriceSet] = Field(default_factory=list)
    remove: List[PriceKey] = Field(default_factory=list)


class PriceEditData(BaseModel):
    added: int = Field(..., ge=0)
    updated: int = Field(..., ge=0, description="Existing prices whose number changed.")
    removed: int = Field(..., ge=0)
    total: int = Field(..., ge=0, description="Prices in the price file after the edit.")
