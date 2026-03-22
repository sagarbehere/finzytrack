"""Pydantic models for email import rule YAML files."""
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class ExtractionFieldDef(BaseModel):
    pattern: Optional[str] = None          # regex with one capture group
    type: str = "string"                   # string | float | integer | datetime
    source: str = "body"                   # body | subject | email_header_date
    cleanup: Optional[str] = None          # remove_commas | strip_whitespace
    multiline: bool = False
    optional: bool = False
    format: Optional[str] = None           # strptime format for datetime
    timezone: Optional[str] = None         # e.g. "+05:30"


class AmountSignDef(BaseModel):
    field: str                             # extracted field name, or "fixed"
    positive_values: List[str] = Field(default_factory=list)
    negative_values: List[str] = Field(default_factory=list)
    value: Optional[str] = None            # "negative" or "positive" when field=="fixed"


class ErrorHandlingDef(BaseModel):
    required_fields: List[str] = Field(default_factory=lambda: ["amount", "timestamp"])
    partial_match_allowed: bool = True


class EmailFilterDef(BaseModel):
    subject_regex: Optional[str] = None
    body_regex: Optional[str] = None          # optional body filter; must match to accept email


class IMAPServerDef(BaseModel):
    """IMAP connection credentials — stored in the per-account YAML, never in global config."""
    server: str
    port: int = 993
    username: str                          # already expanded from env var at load time
    password: str                          # already expanded from env var at load time
    folder: str = "INBOX"


class TransactionTypeDef(BaseModel):
    name: str
    description: str = ""
    version: str = "1.0"
    parsing_mode: Optional[str] = None    # overrides account/global
    email_filter: EmailFilterDef = Field(default_factory=EmailFilterDef)
    extraction: Dict[str, ExtractionFieldDef] = Field(default_factory=dict)
    mapping: Dict[str, str] = Field(default_factory=dict)
    amount_sign: Optional[AmountSignDef] = None
    error_handling: ErrorHandlingDef = Field(default_factory=ErrorHandlingDef)


class RuleFileMetadata(BaseModel):
    name: str                              # display name for the UI dropdown
    beancount_account: str                 # target Beancount account
    default_currency: str = "USD"
    institution: str = ""                  # informational only
    description: str = ""
    version: str = "1.0"


class RuleFile(BaseModel):
    """Top-level model for a per-account YAML file."""
    metadata: RuleFileMetadata
    imap_server: IMAPServerDef             # IMAP credentials for this account
    lookback_days: Optional[int] = None    # optional; overrides global default
    bank_emails: List[str] = Field(default_factory=list)
    parsing_mode: Optional[str] = None
    transaction_types: List[TransactionTypeDef] = Field(default_factory=list)
