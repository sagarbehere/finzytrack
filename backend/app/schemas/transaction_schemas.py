"""
Pydantic schemas for transaction categorization and commit operations.

This module defines the request/response schemas for:
- POST /api/import/categorize - ML-based categorization and duplicate detection
- POST /api/import/commit - Committing transactions to the Beancount ledger
"""

from typing import List, Optional, Dict
from datetime import date as DateType
from decimal import Decimal
from pydantic import BaseModel, Field


# =====================================
# Categorization Schemas
# =====================================

class RawTransactionForCategorization(BaseModel):
    """
    Simplified transaction data needed for categorization.
    Only includes fields required for ML classification and duplicate detection.
    """
    id: str = Field(..., description="Frontend-generated temporary ID for matching request to response")
    date: DateType = Field(..., description="Transaction date")
    payee: str = Field(..., description="Transaction payee")
    memo: Optional[str] = Field(default=None, description="OFX memo field")
    narration: str = Field(default="", description="User notes (usually empty at this stage)")
    amount: Decimal = Field(..., description="Transaction amount from source account posting")

    # Metadata for duplicate detection
    external_id: Optional[str] = Field(default=None, description="External transaction ID for duplicate detection (e.g. OFX FITID, UPI reference)")
    external_id_type: Optional[str] = Field(default=None, description="Type of external_id: OFX, UPI, NEFT, IMPS, EMAIL_MESSAGE_ID, CSV")


class CategorizeRequest(BaseModel):
    """Request body for categorization endpoint."""
    transactions: List[RawTransactionForCategorization] = Field(..., description="List of transactions to categorize")
    source_account: str = Field(..., description="Source account for transactions (e.g., Assets:Bank:Checking)")
    currency: str = Field(..., description="Currency code (e.g., USD)")
    force_engine: Optional[str] = Field(default=None, description="Override engine for this request only: 'ai' or 'classifier'. If unset, uses config.")


class DuplicateInfo(BaseModel):
    """Information about a potential duplicate transaction in the ledger."""
    id: str = Field(..., description="Stable transaction UUID (UUIDv7)")
    content_hash: str = Field(..., description="Content-based SHA256 hash")
    date: DateType = Field(..., description="Date of the duplicate transaction")
    payee: str = Field(..., description="Payee of the duplicate transaction")
    narration: str = Field(..., description="Narration of the duplicate transaction")
    amount: Decimal = Field(..., description="Amount of the duplicate transaction")
    account: str = Field(..., description="Account from the matching posting")
    match_type: str = Field(..., description="How duplicate was detected: 'external_id', 'exact_content', or 'fuzzy'")


class CategorizedTransactionResult(BaseModel):
    """
    Result of categorization for a single transaction.
    Uses ID-based matching to correlate request and response.
    """
    # ID for matching request to response (replaces order-based matching)
    id: str = Field(..., description="Frontend-generated temporary ID (matches request)")

    # Categorization results
    suggested_category: Optional[str] = Field(default=None, description="ML-suggested expense category (e.g., Expenses:Groceries)")
    confidence: Optional[float] = Field(default=None, description="ML confidence score (0.0 to 1.0)")

    # Duplicate detection results
    is_duplicate: bool = Field(default=False, description="Whether this is a potential duplicate")
    duplicate_info: Optional[DuplicateInfo] = Field(default=None, description="Details about the duplicate match")


class CategorizationStats(BaseModel):
    """Statistics about the categorization batch."""
    total_count: int = Field(..., description="Total number of transactions processed")
    categorized_count: int = Field(..., description="Number of transactions with ML categories")
    duplicate_count: int = Field(..., description="Number of potential duplicates detected")
    engine_used: str = Field(..., description="Engine used for categorization: 'classifier', 'ai', or 'default'")
    ml_training_info: Optional[str] = Field(default=None, description="ML training warnings or info messages (deprecated, use warnings)")
    warnings: List[str] = Field(default_factory=list, description="Warnings from categorization (e.g., AI validation failures, insufficient training data)")


class CategorizeResponse(BaseModel):
    """Response body for categorization endpoint."""
    results: List[CategorizedTransactionResult] = Field(..., description="Categorization results (same order as request)")
    stats: CategorizationStats = Field(..., description="Batch statistics")


# =====================================
# Commit Schemas
# =====================================

class CommitPosting(BaseModel):
    """A single posting in a transaction to be committed."""
    account: str = Field(..., description="Beancount account name")
    amount: Decimal = Field(..., description="Posting amount")
    currency: str = Field(..., description="Currency code")

    # Cost fields
    cost_amount: Optional[Decimal] = Field(None, description="Cost amount per unit")
    cost_currency: Optional[str] = Field(None, description="Cost currency")
    cost_date: Optional[DateType] = Field(None, description="Cost date (optional)")

    # Price fields
    price_amount: Optional[Decimal] = Field(None, description="Price amount (per-unit or total)")
    price_currency: Optional[str] = Field(None, description="Price currency")
    price_type: Optional[str] = Field(None, description="Price type: '@' (per-unit) or '@@' (total)")

    # Posting metadata
    posting_meta: Optional[Dict[str, str]] = Field(None, description="Arbitrary posting metadata")


class CommitTransaction(BaseModel):
    """
    Complete transaction data for committing to the ledger.

    Note: source_account is REQUIRED and provided as a top-level field.
    The backend automatically copies it into meta dict before writing to ledger.
    """
    # Core Beancount fields
    date: DateType = Field(..., description="Transaction date")
    flag: str = Field(..., min_length=1, max_length=1, description="Transaction flag (* or !)")
    payee: str = Field(..., description="Transaction payee")
    memo: Optional[str] = Field(default=None, description="Optional memo/reference field (stored as memo: metadata in the ledger)")
    narration: str = Field(..., description="Transaction narration")
    tags: List[str] = Field(default_factory=list, description="Transaction tags")
    links: List[str] = Field(default_factory=list, description="Transaction links")
    postings: List[CommitPosting] = Field(..., description="Transaction postings (must balance)")

    # REQUIRED: Source account (critical for transaction ID generation and duplicate detection)
    source_account: str = Field(
        ...,
        description="REQUIRED: Source account that originated this transaction. Backend copies this into meta."
    )

    # OPTIONAL: Additional arbitrary metadata
    meta: Dict[str, str] = Field(
        default_factory=dict,
        description="Additional arbitrary metadata (external_id, external_id_type, custom fields, etc.). Backend adds source_account automatically."
    )


class CommitRequest(BaseModel):
    """Request body for commit endpoint."""
    transactions: List[CommitTransaction] = Field(..., description="List of transactions to commit to ledger")


class CommitResponse(BaseModel):
    """Response body for commit endpoint."""
    success: bool = Field(..., description="Whether commit was successful")
    count: int = Field(..., description="Number of transactions committed")
    message: str = Field(..., description="Success or error message")
