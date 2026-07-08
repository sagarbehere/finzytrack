"""Pydantic schemas for budget directive CRUD (/api/budgets).

Money is a decimal string on the wire (money-types.md).
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class BudgetItem(BaseModel):
    """A single budget directive."""
    id: str = Field(..., description="Stable identifier (source location hash)")
    date: str = Field(..., description="Effective-from date, YYYY-MM-DD")
    account: str = Field(..., description="Account the budget applies to")
    interval: str = Field(..., description="daily | weekly | monthly | quarterly | yearly (or 'none' for an end)")
    amount: str = Field(..., description="Budget amount (decimal string); '0' and ignored when ended")
    currency: str = Field(..., description="Currency code")
    ended: bool = Field(False, description="True if this is a 'budget end' tombstone (no budget from here)")
    source_file: Optional[str] = Field(None, description="File the directive lives in")
    source_lineno: int = Field(0, description="Line number in the source file")


class BudgetListData(BaseModel):
    """Response payload for GET /api/budgets."""
    budgets: List[BudgetItem]


class BudgetWriteRequest(BaseModel):
    """Body for POST/PUT — create or replace a budget directive.

    To end a budget (tombstone), send ``interval='none'``; ``amount`` is then
    optional and ignored (a ``0`` is written to carry the currency).
    """
    date: str = Field(..., description="Effective-from date, YYYY-MM-DD")
    account: str = Field(..., min_length=1, description="Account the budget applies to")
    interval: str = Field(..., description="daily | weekly | monthly | quarterly | yearly | none (end)")
    amount: Optional[str] = Field(None, description="Budget amount (decimal string); omit/ignored when ending")
    currency: str = Field(..., min_length=1, description="Currency code")


class BudgetWriteData(BaseModel):
    """Response payload for POST/PUT/DELETE."""
    budget: Optional[BudgetItem] = Field(None, description="The written directive (null for delete)")
    message: str
