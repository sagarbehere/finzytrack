from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Annotated
from datetime import date

# Reuse existing types from account_schemas.py
from app.schemas.account_schemas import DateStr

# Define CommodityStr for consistent commodity code validation
CommodityStr = Annotated[
    str,
    Field(
        description="Commodity/currency code (e.g., 'USD', 'AAPL')",
        pattern=r'^[A-Z0-9]+$',
        min_length=1,
        max_length=16
    )
]

class CommodityUsageData(BaseModel):
    """Transaction usage statistics for a commodity."""
    transaction_count: int = Field(..., ge=0, description="Number of transactions using this commodity")
    total_volume: float = Field(..., description="Total absolute volume transacted in this commodity")

class CommodityDetails(BaseModel):
    """Detailed commodity information including transaction usage data."""
    code: CommodityStr = Field(..., description="Commodity/currency code (e.g., 'USD', 'AAPL')")
    name: Optional[str] = Field(None, description="Full name from commodity directive")
    type: Optional[str] = Field(None, description="Commodity type (e.g., 'Currency', 'Stock', 'ETF')")
    first_seen: Optional[DateStr] = Field(None, description="Earliest date this commodity appeared")
    last_seen: Optional[DateStr] = Field(None, description="Latest date this commodity appeared")
    usage: CommodityUsageData = Field(..., description="Transaction usage statistics")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional commodity metadata")

class CommodityCreateRequest(BaseModel):
    """Request model for creating new Beancount commodities."""
    code: CommodityStr = Field(..., description="Commodity/currency code (uppercase alphanumeric)")
    name: Optional[str] = Field(None, max_length=100, description="Optional full name")
    type: Optional[str] = Field(None, max_length=50, description="Commodity type (defaults to 'Unknown')")
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
    type: Optional[str] = Field(None, max_length=50, description="Updated commodity type")
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