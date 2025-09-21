from pydantic import BaseModel, Field
from typing import Optional, Annotated


# Reusable custom type for currency validation
CurrencyStr = Annotated[
    str,
    Field(
        description="Alphanumeric currency code",
        max_length=16,
        pattern=r'^[a-zA-Z0-9]+$'
    )
]


class OFXDetectionRequest(BaseModel):
    """Request model for OFX account detection."""
    institution: str = Field(..., description="Institution name from OFX")
    institution_fid: Optional[str] = Field(None, description="Financial Institution ID")
    account_type: str = Field(..., description="Account type")
    account_id: str = Field(..., description="Full account ID")

class OFXDetectionData(BaseModel):
    """Data model for account detection results."""
    detected: bool = Field(..., description="Whether account was detected")
    beancount_account: str = Field(..., description="Detected or default Beancount account")
    currency: str = Field(..., description="Detected or default currency")
    message: str = Field(..., description="Detection result message")

class LearnOFXAccountRequest(BaseModel):
    """Request model for learning account mappings."""
    institution: str = Field(..., description="Institution name from OFX")
    institution_fid: Optional[str] = Field(None, description="Financial Institution ID")
    account_type: str = Field(..., description="Account type (empty string for credit cards)")
    account_id: str = Field(..., description="Full account ID")
    beancount_account: str = Field(..., description="User-specified Beancount account")
    currency: CurrencyStr

class LearnOFXAccountData(BaseModel):
    """Data model for learning account mapping results."""
    mapping_saved: bool = Field(..., description="Whether the mapping was saved to config")
    account_creation_needed: bool = Field(default=False, description="Whether account creation is needed")

class CreateAccountRequest(BaseModel):
    """Request model for creating Beancount accounts."""
    account_name: str = Field(..., min_length=1, description="Full Beancount account name")
    currency: CurrencyStr

class CreateAccountData(BaseModel):
    """Data model for account creation results."""
    account_created: bool = Field(..., description="Whether account was added to ledger")