"""
Pydantic schemas for transaction categorization and commit operations.

This module defines the request/response schemas for:
- POST /api/import/categorize - ML-based categorization and duplicate detection
- POST /api/import/commit - Committing transactions to the Beancount ledger
"""

from typing import List, Optional
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
    date: DateType = Field(..., description="Transaction date")
    payee: str = Field(..., description="Transaction payee")
    memo: Optional[str] = Field(default=None, description="OFX memo field")
    narration: str = Field(default="", description="User notes (usually empty at this stage)")
    amount: Decimal = Field(..., description="Transaction amount from source account posting")

    # Metadata for duplicate detection
    ofx_id: Optional[str] = Field(default=None, description="OFX transaction ID for duplicate detection")


class CategorizeRequest(BaseModel):
    """Request body for categorization endpoint."""
    transactions: List[RawTransactionForCategorization] = Field(..., description="List of transactions to categorize")
    source_account: str = Field(..., description="Source account for transactions (e.g., Assets:Bank:Checking)")
    currency: str = Field(..., description="Currency code (e.g., USD)")


class DuplicateInfo(BaseModel):
    """Information about a potential duplicate transaction in the ledger."""
    transaction_id: Optional[str] = Field(default=None, description="Ledger transaction ID of potential duplicate")
    date: DateType = Field(..., description="Date of the duplicate transaction")
    payee: str = Field(..., description="Payee of the duplicate transaction")
    narration: str = Field(..., description="Narration of the duplicate transaction")
    amount: Decimal = Field(..., description="Amount of the duplicate transaction")
    account: str = Field(..., description="Account from the matching posting")


class CategorizedTransactionResult(BaseModel):
    """
    Result of categorization for a single transaction.
    Includes safety validation fields and categorization/duplicate detection results.
    """
    # Safety validation fields (for verifying order matches request)
    date: DateType = Field(..., description="Transaction date (for order verification)")
    amount: Decimal = Field(..., description="Transaction amount (for order verification)")

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
    ml_training_info: Optional[str] = Field(default=None, description="ML training warnings or info messages")


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


class CommitTransaction(BaseModel):
    """
    Complete transaction data for committing to the ledger.
    Includes all Beancount fields needed to write a valid transaction.
    """
    # Core Beancount fields
    date: DateType = Field(..., description="Transaction date")
    flag: str = Field(..., min_length=1, max_length=1, description="Transaction flag (* or !)")
    payee: str = Field(..., description="Transaction payee")
    memo: Optional[str] = Field(default=None, description="OFX memo field")
    narration: str = Field(..., description="Transaction narration")
    tags: List[str] = Field(default_factory=list, description="Transaction tags")
    links: List[str] = Field(default_factory=list, description="Transaction links")
    postings: List[CommitPosting] = Field(..., description="Transaction postings (must balance)")

    # Metadata for ledger
    ofx_id: Optional[str] = Field(default=None, description="OFX transaction ID")
    source_account: str = Field(..., description="Source account (needed for transaction_id generation)")


class CommitRequest(BaseModel):
    """Request body for commit endpoint."""
    transactions: List[CommitTransaction] = Field(..., description="List of transactions to commit to ledger")


class CommitResponse(BaseModel):
    """Response body for commit endpoint."""
    success: bool = Field(..., description="Whether commit was successful")
    count: int = Field(..., description="Number of transactions committed")
    message: str = Field(..., description="Success or error message")
