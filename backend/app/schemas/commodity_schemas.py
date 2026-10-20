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

class CommodityListData(BaseModel):
    """Response data for commodity listing."""
    commodities: List[CommodityDetails] = Field(..., description="List of all commodities")

class CommodityMetadataChange(BaseModel):
    """Set and/or remove metadata keys on one commodity's `commodity` directive.

    Keys not named are kept. The directive is created if the commodity has none
    and something is being set.
    """
    code: CommodityStr
    set: Dict[str, str] = Field(default_factory=dict, description="Keys to add or overwrite")
    remove: List[str] = Field(default_factory=list, description="Keys to delete")

class CommodityMetadataUpdateRequest(BaseModel):
    """A batch of commodity metadata changes, written in one ledger write."""
    changes: List[CommodityMetadataChange] = Field(..., min_length=1)

class CommodityMetadataUpdateData(BaseModel):
    """Result of a commodity metadata batch."""
    changed: List[CommodityStr] = Field(..., description="Codes whose directive was created or changed")
    commodities: List[CommodityDetails] = Field(..., description="Read-back of every commodity named in the request")

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