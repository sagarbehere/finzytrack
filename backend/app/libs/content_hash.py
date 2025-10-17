"""
Content Hash Generator for Transaction Deduplication

This module provides content-based hashing for transaction duplicate detection.

IMPORTANT: This REPLACES the old transaction_id_generator library.
- Old system: Content hash WAS the identifier (needed collision handling)
- New system: Content hash is for deduplication (collisions are GOOD)

No collision handling needed - same hash = duplicate transaction.
"""

import hashlib
from typing import Union
from decimal import Decimal


def compute_content_hash(
    date: str,
    payee: str,
    amount: Union[str, Decimal, float],
    source_account: str,
    narration: str = ""
) -> str:
    """
    Compute SHA256 hash of transaction content.

    Args:
        date: Transaction date in YYYY-MM-DD format
        payee: Transaction payee/merchant name
        amount: Transaction amount (can be string, Decimal, or float)
        source_account: Beancount account name (e.g., "Assets:Checking")
        narration: Transaction narration/description

    Returns:
        64-character SHA256 hash string

    Note:
        Unlike the old transaction_id system, we do NOT handle hash collisions.
        If two transactions produce the same hash, they are exact duplicates
        (which is what we want to detect).

    Examples:
        >>> compute_content_hash("2024-01-15", "Starbucks", "-5.00", "Assets:Checking", "Coffee")
        'a1b2c3d4e5f6789012345678901234567890123456789012345678901234567890'
    """
    # Normalize inputs
    clean_payee = str(payee) if payee else ""
    clean_narration = str(narration) if narration else ""
    clean_amount = str(amount) if amount else "0"
    clean_account = str(source_account).strip()

    # Create hash input (same format as old transaction_id)
    hash_input = f"{date}|{clean_payee}|{clean_narration}|{clean_amount}|{clean_account}"

    # Compute SHA256
    return hashlib.sha256(hash_input.encode('utf-8')).hexdigest()


# Backward compatibility constant
HASH_INPUT_FORMAT = "{date}|{payee}|{narration}|{amount}|{account}"
