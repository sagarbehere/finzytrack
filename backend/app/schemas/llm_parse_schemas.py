"""Schemas for the LLM-based transaction parsing endpoint."""

from decimal import Decimal
from pydantic import BaseModel, Field
from typing import Optional


class LlmParsedTransaction(BaseModel):
    """A single transaction extracted by the LLM."""
    date: str = Field(..., description="Transaction date in YYYY-MM-DD format")
    payee: str = Field(default="", description="Merchant or counterparty name")
    narration: str = Field(default="", description="Transaction description")
    amount: Decimal = Field(..., description="Signed amount (negative=debit, positive=credit)")
    memo: str = Field(default="", description="Reference number or additional notes")


class LlmParseResult(BaseModel):
    """Response from the LLM parse endpoint."""
    transactions: list[LlmParsedTransaction] = Field(..., description="Parsed transactions")
    file_type: str = Field(..., description="Detected file type: csv, xls, pdf, or image")
    transaction_count: int = Field(..., description="Number of transactions parsed")
    warning: Optional[str] = Field(None, description="Validation warnings, if any")
