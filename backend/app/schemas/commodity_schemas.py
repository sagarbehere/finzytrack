from datetime import date
from decimal import Decimal
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Annotated

from app.core.constants import COMMODITY_CODE_MAX_LENGTH, COMMODITY_CODE_PATTERN

# Define CommodityStr for consistent commodity code validation. The rule comes
# from Beancount's own lexer (app.core.constants) — a stricter hand-written
# pattern here once rejected valid codes like 'ELEC-DAYS' and 'BRK.B', which
# made the whole ledger unreadable rather than just the one commodity.
CommodityStr = Annotated[
    str,
    Field(
        description="Commodity/currency code (e.g., 'USD', 'AAPL', 'BRK.B')",
        pattern=COMMODITY_CODE_PATTERN,
        min_length=1,
        max_length=COMMODITY_CODE_MAX_LENGTH
    )
]

class CommodityUsageData(BaseModel):
    """Transaction usage statistics for a commodity."""
    transaction_count: int = Field(..., ge=0, description="Number of transactions using this commodity")
    total_volume: Decimal = Field(..., description="Total absolute volume transacted in this commodity")

class CommodityDetails(BaseModel):
    """Detailed commodity information including transaction usage data."""
    code: CommodityStr = Field(..., description="Commodity/currency code (e.g., 'USD', 'AAPL')")
    name: Optional[str] = Field(None, description="Full name from commodity directive")
    asset_class: Optional[str] = Field(None, description="Beancount 'asset-class' metadata (e.g., 'cash', 'stock', 'etf')")
    is_currency: bool = Field(True, description="Whether this commodity plays a currency (unit-of-account) role. Derived from operating_currency (primary) then asset-class (fallback).")
    first_seen: Optional[date] = Field(None, description="Earliest date this commodity appeared")
    last_seen: Optional[date] = Field(None, description="Latest date this commodity appeared")
    usage: CommodityUsageData = Field(..., description="Transaction usage statistics")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional commodity metadata")

class CommodityCreateRequest(BaseModel):
    """Request model for creating new Beancount commodities."""
    code: CommodityStr = Field(..., description="Commodity/currency code (uppercase alphanumeric)")
    name: Optional[str] = Field(None, max_length=100, description="Optional full name")
    asset_class: Optional[str] = Field(None, max_length=50, description="Beancount 'asset-class' metadata (e.g., 'cash', 'stock', 'etf')")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Optional commodity metadata")

class CommodityCreateData(BaseModel):
    """Response data for commodity creation results."""
    commodity_created: bool = Field(..., description="Whether commodity was created")
    commodity_details: Optional[CommodityDetails] = Field(None, description="Created commodity details if successful")
    message: str = Field(..., description="Creation result message")

class CommodityListData(BaseModel):
    """Response data for commodity listing."""
    commodities: List[CommodityDetails] = Field(..., description="List of all commodities")

class CommodityUpdateRequest(BaseModel):
    """Request model for updating commodity details."""
    name: Optional[str] = Field(None, max_length=100, description="Updated full name")
    asset_class: Optional[str] = Field(None, max_length=50, description="Updated 'asset-class' metadata")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Updated metadata (merges with existing)")

class CommodityUpdateData(BaseModel):
    """Response data for commodity update results."""
    commodity_updated: bool = Field(..., description="Whether commodity was updated")
    commodity_details: Optional[CommodityDetails] = Field(None, description="Updated commodity details if successful")
    message: str = Field(..., description="Update result message")

class CommodityDeleteData(BaseModel):
    """Response data for commodity delete results."""
    commodity_deleted: bool = Field(..., description="Whether commodity was deleted")
    message: str = Field(..., description="Delete result message")
    warnings: Optional[List[str]] = Field(None, description="Any warnings about the deletion")

class OperatingCurrenciesData(BaseModel):
    """The ledger's operating currencies — the authoritative currency whitelist."""
    currencies: List[CommodityStr] = Field(
        ..., description="Commodity codes declared as operating currencies (may be empty)"
    )

class OperatingCurrenciesUpdateRequest(BaseModel):
    """Request to replace the ledger's operating_currency option."""
    currencies: List[CommodityStr] = Field(
        ..., description="Full replacement list of operating currency codes (empty clears the whitelist)"
    )