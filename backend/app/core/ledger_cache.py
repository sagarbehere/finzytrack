"""
Ledger data types.

LedgerTransaction is the simplified transaction representation used by
the duplicate detector and SqliteReader.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass
class LedgerTransaction:
    """
    Simplified transaction representation for duplicate detection.

    Used by duplicate_detector.py and SqliteReader.get_transactions().
    """
    id: str  # UUIDv7 - stable identifier
    content_hash: str  # SHA256 - content fingerprint
    date: date
    payee: str
    narration: str
    amount: Decimal
    account: str
    external_id: Optional[str] = None       # replaces ofx_id
    external_id_type: Optional[str] = None  # new: OFX, UPI, NEFT, etc.
