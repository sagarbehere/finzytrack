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
    currencies: List[CurrencyStr] = Field(..., description="List of currencies for this account (at least one required)")
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
    transactions_deleted: Optional[int] = Field(None, description="Number of transactions deleted if delete_transactions was true")

class AccountReopenData(BaseModel):
    """Response data for account reopen results."""
    account_reopened: bool = Field(..., description="Whether account was reopened")
    message: str = Field(..., description="Reopen result message")

# =====================================
# Balance & Pad Directive Schemas
# =====================================

class BalanceDirectiveData(BaseModel):
    """A balance assertion directive with optional associated pad."""
    date: DateStr
    currency: str
    expected_balance: float
    has_pad: bool
    pad_source_account: Optional[str] = None
    has_error: bool = False
    error_message: Optional[str] = None

class BalanceDirectiveListData(BaseModel):
    """Response for listing balance directives."""
    account: str
    directives: List[BalanceDirectiveData]

class BalanceDirectiveCreateRequest(BaseModel):
    """Request to create a balance assertion (optionally with pad)."""
    date: DateStr
    currency: str
    amount: float
    include_pad: bool = False
    pad_source_account: Optional[str] = None  # Required when include_pad=True

class BalanceDirectiveUpdateRequest(BaseModel):
    """Request to update an existing balance directive."""
    original_date: DateStr          # To identify the existing directive
    original_currency: str          # To identify the existing directive
    original_amount: float          # To identify the existing directive
    new_date: Optional[DateStr] = None
    new_currency: Optional[str] = None
    new_amount: Optional[float] = None
    include_pad: Optional[bool] = None
    pad_source_account: Optional[str] = None

class BalanceDirectiveCreateData(BaseModel):
    """Response for balance directive creation."""
    created: bool
    message: str

class BalanceDirectiveUpdateData(BaseModel):
    """Response for balance directive update."""
    updated: bool
    message: str

class BalanceDirectiveDeleteData(BaseModel):
    """Response for balance directive deletion."""
    deleted: bool
    message: str