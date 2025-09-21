from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Annotated
from datetime import date

# Reuse existing types from ofx_schemas.py
from app.schemas.ofx_schemas import CurrencyStr

# Define DateStr for consistent date validation pattern
DateStr = Annotated[
    str,
    Field(
        description="Date in YYYY-MM-DD format",
        pattern=r'^\d{4}-\d{2}-\d{2}$'
    )
]

class AccountCurrencyData(BaseModel):
    """Per-currency transaction data for an account."""
    currency: CurrencyStr = Field(..., description="Currency code (e.g., 'USD')")
    transaction_count: int = Field(..., ge=0, description="Number of transactions in this currency")
    last_transaction_date: Optional[DateStr] = Field(None, description="Date of last transaction (YYYY-MM-DD)")
    balance: float = Field(..., description="Current balance in this currency")

class AccountDetails(BaseModel):
    """Detailed account information including transaction data."""
    name: str = Field(..., description="Beancount account name (e.g., 'Assets:Bank:Checking')")
    open_date: DateStr = Field(..., description="Date account was opened")
    close_date: Optional[DateStr] = Field(None, description="Date account was closed (null if open)")
    currencies: List[AccountCurrencyData] = Field(..., description="Per-currency transaction data")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary account metadata")

class AccountCreateRequest(BaseModel):
    """Request model for creating new Beancount accounts."""
    name: str = Field(..., min_length=1, description="Full Beancount account name with format validation")
    open_date: DateStr = Field(..., description="Opening date")
    primary_currency: CurrencyStr = Field(..., description="Primary account currency")
    additional_currencies: Optional[List[CurrencyStr]] = Field(default_factory=list, description="Optional list of additional currencies")
    description: Optional[str] = Field(None, description="Optional account description or notes")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Optional account metadata")

class AccountCreateData(BaseModel):
    """Response data for account creation results."""
    account_created: bool = Field(..., description="Whether account was created")
    account_details: Optional[AccountDetails] = Field(None, description="Created account details if successful")
    message: str = Field(..., description="Creation result message")

class AccountListData(BaseModel):
    """Response data for account listing."""
    accounts: List[AccountDetails] = Field(..., description="List of all accounts")

class AccountUpdateRequest(BaseModel):
    """Request model for updating account details."""
    new_name: Optional[str] = Field(None, min_length=1, description="New account name (must be unique if changed)")
    currencies: Optional[List[CurrencyStr]] = Field(None, description="Updated list of currencies for this account")
    open_date: Optional[DateStr] = Field(None, description="Updated opening date")
    close_date: Optional[DateStr] = Field(None, description="Updated closing date (null to reopen)")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Updated metadata (merges with existing)")

class AccountUpdateData(BaseModel):
    """Response data for account update results."""
    account_updated: bool = Field(..., description="Whether account was updated")
    account_details: Optional[AccountDetails] = Field(None, description="Updated account details if successful")
    message: str = Field(..., description="Update result message")

class AccountCloseRequest(BaseModel):
    """Request model for closing an account. Target account comes from URL path."""
    close_date: DateStr = Field(..., description="Closing date")
    reason: Optional[str] = Field(None, description="Optional reason for closing")

class AccountCloseData(BaseModel):
    """Response data for account close results."""
    account_closed: bool = Field(..., description="Whether account was closed")
    message: str = Field(..., description="Close result message")

class AccountDeleteData(BaseModel):
    """Response data for account delete results."""
    account_deleted: bool = Field(..., description="Whether account was deleted")
    message: str = Field(..., description="Delete result message")
    warnings: Optional[List[str]] = Field(None, description="Any warnings about the deletion")