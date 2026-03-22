"""Pydantic models for email import API responses."""
from typing import Any, Dict, List, Optional
from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel


class ProfileInfo(BaseModel):
    """One entry in the GET /profiles response."""
    name: str                              # metadata.name from account YAML
    profile_id: str                        # filename without .yaml extension
    beancount_account: str                 # metadata.beancount_account
    default_currency: str                  # metadata.default_currency
    lookback_days: Optional[int] = None    # from account YAML; null = use global default
    file: str                              # filename including .yaml


class InvalidProfileInfo(BaseModel):
    """One entry for a profile file that failed to load."""
    filename: str                          # filename including .yaml
    error: str                             # human-readable reason for failure


class ProfilesListResponse(BaseModel):
    profiles: List[ProfileInfo]
    invalid_profiles: List[InvalidProfileInfo]


class TestConnectionRequest(BaseModel):
    profile_id: str


class TestConnectionResponse(BaseModel):
    success: bool
    email_count: Optional[int] = None
    message: Optional[str] = None
    error: Optional[str] = None


class ReloadResponse(BaseModel):
    profiles_loaded: int
    message: str


class FetchRequest(BaseModel):
    profile_id: str
    since_date: Optional[str] = None   # ISO format: "2026-03-01"
    until_date: Optional[str] = None   # ISO format: "2026-03-15"


class ParsedTransaction(BaseModel):
    date: date
    amount: Decimal                        # signed: negative=debit, positive=credit
    payee: str
    external_id: Optional[str] = None
    external_id_type: Optional[str] = None
    masked_account: Optional[str] = None
    source_rule: str                       # e.g. "axis-bank-savings/UPI"
    raw_email_from: str
    raw_email_subject: str
    raw_email_date: datetime
    message_id: str


class UnmatchedEmail(BaseModel):
    from_address: str
    subject: str
    date: datetime
    reason: str


class ExtractionErrorInfo(BaseModel):
    """An email that matched a rule but field extraction failed."""
    from_address: str
    subject: str
    date: datetime
    rule_matched: str
    reason: str


class FetchStats(BaseModel):
    emails_fetched: int
    transactions_parsed: int
    unmatched: int
    extraction_errors: int
    truncated: bool = False       # True if max_emails limit was hit


class FetchResult(BaseModel):
    stats: FetchStats
    transactions: List[ParsedTransaction]
    unmatched_emails: List[UnmatchedEmail]
    extraction_errors: List[ExtractionErrorInfo]


class ProgressEvent(BaseModel):
    """
    Payload for each Server-Sent Event emitted by POST /fetch.

    Phases (in order):
      connecting  — attempting IMAP login
      fetching    — body fetch in progress; current = index, total = total emails
      parsing     — rule matching + extraction; current = index, total = total emails
      complete    — all done; result = full FetchResult dict
      error       — unrecoverable error; stream ends after this event
    """
    phase: str
    message: str
    total: Optional[int] = None
    current: Optional[int] = None
    result: Optional[Dict[str, Any]] = None   # populated only on phase='complete'
