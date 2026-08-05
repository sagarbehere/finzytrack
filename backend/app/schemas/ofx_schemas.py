from pydantic import BaseModel, Field
from typing import Optional, Annotated

from app.core.constants import COMMODITY_CODE_MAX_LENGTH, COMMODITY_CODE_PATTERN


# Reusable custom type for currency validation. Used for account currencies too
# (app.schemas.account_schemas), so it must accept everything Beancount does:
# the previous alphanumeric-only rule rejected valid codes like 'ELEC-DAYS' on
# the account-create path. Derived from Beancount's lexer — see
# app.core.constants.COMMODITY_CODE_PATTERN.
CurrencyStr = Annotated[
    str,
    Field(
        description="Currency/commodity code (e.g., 'USD', 'BRK.B')",
        max_length=COMMODITY_CODE_MAX_LENGTH,
        pattern=COMMODITY_CODE_PATTERN
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

