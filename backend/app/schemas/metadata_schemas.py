"""Schemas for the recognized metadata vocabulary and the metadata review
(Investments → Setup & Metadata; dev-docs/investment-view.md §10.3)."""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class MetadataFieldSpec(BaseModel):
    """One recognized metadata key in one scope."""
    key: str = Field(..., description="Metadata key as written in the ledger (e.g. 'asset-class')")
    scope: str = Field(..., description="'commodity', 'account' or 'income-account'")
    kind: str = Field(..., description="enum, text, date, integer, percent, account or commodity")
    label: str = Field(..., description="Short UI label")
    adds: str = Field(..., description="One line on what setting this key adds")
    values: List[str] = Field(default_factory=list, description="Recognized values (enum only); custom values are allowed")
    review: bool = Field(..., description="Whether an unset value is listed in the review")
    requires_key: Optional[str] = Field(None, description="Relevant only when this other key on the same item...")
    requires_values: List[str] = Field(default_factory=list, description="...has one of these values (case-insensitive)")


class MetadataVocabularyData(BaseModel):
    fields: List[MetadataFieldSpec]
    currency_asset_classes: List[str] = Field(
        ..., description="Commodity asset-class values that make a commodity a currency when no operating_currency is declared",
    )


class MetadataReviewItem(BaseModel):
    """A commodity or account that recognized metadata can describe."""
    scope: str = Field(..., description="'commodity', 'account' or 'income-account'")
    item: str = Field(..., description="Commodity code or account name")
    metadata: Dict[str, str] = Field(default_factory=dict, description="Current metadata, values as text")
    unset: List[str] = Field(default_factory=list, description="Recognized keys that are relevant here and not set")
    is_currency: Optional[bool] = Field(None, description="Commodities only: current currency-vs-holding role")
    closed: bool = Field(False, description="Accounts only: the account is closed")


class MetadataReviewData(BaseModel):
    items: List[MetadataReviewItem]
    summary: Dict[str, int] = Field(..., description="Per scope: open, in-use items whose main key (asset-class for commodities, account-type for accounts, income-type for income accounts) is not set. Drives the dashboard banners.")
    has_operating_currencies: bool = Field(
        ..., description="When false, a commodity's asset-class decides whether it counts as a currency",
    )
