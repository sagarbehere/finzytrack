"""Pydantic schemas for the price endpoints (/api/prices)."""

from typing import List, Optional
from pydantic import BaseModel, Field


class PriceUpdateData(BaseModel):
    """Result of a price fetch → sidecar write."""
    added: int = Field(..., description="Number of new (date, base, quote) price points added.")
    total: int = Field(..., description="Total price points in the sidecar after the update.")
    as_of: Optional[str] = Field(None, description="Date of the most recent price, YYYY-MM-DD, or null if none.")
    symbols: List[str] = Field(default_factory=list, description="Tickers that were fetched.")
    skipped: List[str] = Field(default_factory=list, description="Holdings not fetched (e.g. money-market funds).")
