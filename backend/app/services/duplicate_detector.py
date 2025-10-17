"""
Duplicate detection service for identifying potential duplicate transactions.

This module compares new transactions against existing ledger transactions using
exact matches for date/amount/account and fuzzy matching for payees.
Uses pre-computed transaction data from LedgerCache.
"""

import logging
from typing import List, Optional, Tuple
from decimal import Decimal
from datetime import date

from rapidfuzz import fuzz

from app.schemas.transaction_schemas import DuplicateInfo
from app.core.ledger_cache import LedgerTransaction

logger = logging.getLogger(__name__)


def calculate_payee_similarity(payee1: str, payee2: str) -> float:
    """
    Calculate similarity between two payee names using fuzzy matching.

    Uses multiple algorithms and returns the highest score.

    Args:
        payee1: First payee name
        payee2: Second payee name

    Returns:
        Similarity score between 0.0 and 1.0
    """
    if not payee1 or not payee2:
        return 0.0

    if payee1 == payee2:
        return 1.0

    # Use rapidfuzz for fuzzy string matching
    # Try multiple algorithms and take the best score
    ratio_score = fuzz.ratio(payee1.lower(), payee2.lower()) / 100.0
    partial_ratio_score = fuzz.partial_ratio(payee1.lower(), payee2.lower()) / 100.0
    token_sort_score = fuzz.token_sort_ratio(payee1.lower(), payee2.lower()) / 100.0
    token_set_score = fuzz.token_set_ratio(payee1.lower(), payee2.lower()) / 100.0

    # Return the highest score
    return max(ratio_score, partial_ratio_score, token_sort_score, token_set_score)


def check_ofx_id_match(ofx_id: str, existing_transactions: List[LedgerTransaction]) -> Optional[DuplicateInfo]:
    """
    Check if OFX ID matches any existing transaction.

    Args:
        ofx_id: OFX transaction ID to check
        existing_transactions: List of existing ledger transactions

    Returns:
        DuplicateInfo if match found, None otherwise
    """
    if not ofx_id:
        return None

    for existing_txn in existing_transactions:
        if existing_txn.ofx_id and existing_txn.ofx_id == ofx_id:
            # Exact OFX ID match found
            return DuplicateInfo(
                id=existing_txn.id,
                content_hash=existing_txn.content_hash,
                date=existing_txn.date,
                payee=existing_txn.payee,
                narration=existing_txn.narration,
                amount=existing_txn.amount,
                account=existing_txn.account,
                match_type='ofx_id'
            )

    return None


def check_fuzzy_match(
    txn_date: date,
    payee: str,
    amount: Decimal,
    account: str,
    existing_transactions: List[LedgerTransaction]
) -> Optional[DuplicateInfo]:
    """
    Check for fuzzy match based on date, amount, account, and payee similarity.

    Matching criteria:
    - Date: exact match
    - Amount: exact match
    - Account: exact match
    - Payee: > 80% similarity

    Args:
        txn_date: Transaction date
        payee: Transaction payee
        amount: Transaction amount
        account: Source account
        existing_transactions: List of existing ledger transactions

    Returns:
        DuplicateInfo if match found, None otherwise
    """
    for existing_txn in existing_transactions:
        # Check exact matches for date, amount, account
        if (existing_txn.date != txn_date or
            existing_txn.amount != amount or
            existing_txn.account != account):
            continue

        # Check payee similarity
        similarity = calculate_payee_similarity(payee, existing_txn.payee)

        if similarity > 0.8:
            # Potential duplicate found
            logger.info(f"Duplicate detected: {txn_date} | {payee} | {amount} (similarity: {similarity:.0%})")
            return DuplicateInfo(
                id=existing_txn.id,
                content_hash=existing_txn.content_hash,
                date=existing_txn.date,
                payee=existing_txn.payee,
                narration=existing_txn.narration,
                amount=existing_txn.amount,
                account=existing_txn.account,
                match_type='fuzzy'
            )

    return None


def check_content_hash_match(
    content_hash: str,
    existing_transactions: List[LedgerTransaction]
) -> Optional[DuplicateInfo]:
    """
    Check if content hash matches any existing transaction.

    Args:
        content_hash: SHA256 hash of transaction content
        existing_transactions: List of existing ledger transactions

    Returns:
        DuplicateInfo if match found, None otherwise
    """
    if not content_hash:
        return None

    for existing_txn in existing_transactions:
        if existing_txn.content_hash and existing_txn.content_hash == content_hash:
            # Exact content hash match found
            return DuplicateInfo(
                id=existing_txn.id,
                content_hash=existing_txn.content_hash,
                date=existing_txn.date,
                payee=existing_txn.payee,
                narration=existing_txn.narration,
                amount=existing_txn.amount,
                account=existing_txn.account,
                match_type='exact_content'
            )

    return None


def find_duplicate(
    txn_date: date,
    payee: str,
    amount: Decimal,
    source_account: str,
    narration: str,
    ofx_id: Optional[str],
    existing_transactions: List[LedgerTransaction]
) -> Tuple[bool, Optional[DuplicateInfo]]:
    """
    Find potential duplicate for a transaction.

    Uses three-tier detection:
    1. Check for exact OFX ID match
    2. Check for exact content hash match
    3. Check for fuzzy match on date/amount/account/payee

    Args:
        txn_date: Transaction date
        payee: Transaction payee
        amount: Transaction amount
        source_account: Source account
        narration: Transaction narration
        ofx_id: Optional OFX transaction ID
        existing_transactions: List of existing ledger transactions

    Returns:
        Tuple of (is_duplicate, duplicate_info)
    """
    # Tier 1: Check OFX ID match (if available)
    if ofx_id:
        ofx_match = check_ofx_id_match(ofx_id, existing_transactions)
        if ofx_match:
            return True, ofx_match

    # Tier 2: Check content hash match
    from app.libs.content_hash import compute_content_hash
    content_hash = compute_content_hash(
        date=str(txn_date),
        payee=payee,
        amount=str(amount),
        source_account=source_account,
        narration=narration
    )
    hash_match = check_content_hash_match(content_hash, existing_transactions)
    if hash_match:
        return True, hash_match

    # Tier 3: Check fuzzy match
    fuzzy_match = check_fuzzy_match(txn_date, payee, amount, source_account, existing_transactions)
    if fuzzy_match:
        return True, fuzzy_match

    # No duplicate found
    return False, None
