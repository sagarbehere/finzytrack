"""
Transaction update schemas for PUT /api/ledger/transactions endpoint.
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import date as DateType
from decimal import Decimal


class UpdatePosting(BaseModel):
    """A posting in a transaction update request."""
    account: str = Field(..., description="Beancount account name")
    amount: Optional[Decimal] = Field(None, description="Posting amount")
    currency: str = Field(..., description="Currency code")

    # Cost fields
    cost_amount: Optional[Decimal] = Field(None, description="Cost per unit")
    cost_currency: Optional[str] = Field(None, description="Cost currency")
    cost_date: Optional[str] = Field(None, description="Cost date (ISO format)")

    # Price fields
    price_amount: Optional[Decimal] = Field(None, description="Price amount")
    price_currency: Optional[str] = Field(None, description="Price currency")
    price_type: Optional[str] = Field(None, description="Price type: '@' or '@@'")

    # Posting metadata
    posting_meta: Optional[Dict[str, str]] = Field(None, description="Posting metadata")


class UpdateTransaction(BaseModel):
    """A transaction to be updated in the ledger."""
    id: str = Field(..., description="Transaction ID (UUIDv7)")
    date: str = Field(..., description="Transaction date (ISO format YYYY-MM-DD)")
    flag: str = Field(..., description="Transaction flag (* or !)")
    payee: str = Field(..., description="Transaction payee")
    narration: str = Field(..., description="Transaction narration/description")
    memo: Optional[str] = Field(None, description="Transaction memo (stored as ofx_memo metadata)")
    tags: List[str] = Field(default_factory=list, description="Transaction tags")
    links: List[str] = Field(default_factory=list, description="Transaction links")
    postings: List[UpdatePosting] = Field(..., description="Transaction postings")
    meta: Dict[str, str] = Field(default_factory=dict, description="Transaction metadata")


class UpdateTransactionRequest(BaseModel):
    """Request body for updating transactions."""
    transactions: List[UpdateTransaction] = Field(..., description="List of transactions to update")


class UpdateTransactionResponse(BaseModel):
    """Response after updating transactions."""
    updated_count: int = Field(..., description="Number of transactions successfully updated")
    message: str = Field(..., description="Success message")
