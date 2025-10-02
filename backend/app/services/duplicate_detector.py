"""
Duplicate detection service for identifying potential duplicate transactions.

This module compares new transactions against existing ledger transactions using
exact matches for date/amount/account and fuzzy matching for payees.
"""

import os
import logging
from typing import List, Optional, Tuple
from decimal import Decimal
from datetime import date

from rapidfuzz import fuzz
from beancount import loader
from beancount.core import data

from app.schemas.transaction_schemas import DuplicateInfo

logger = logging.getLogger(__name__)


class DuplicateDetectionError(Exception):
    """Exception raised when duplicate detection fails."""
    pass


class LedgerTransaction:
    """
    Internal representation of a ledger transaction for duplicate detection.

    This is a simplified structure used only within the duplicate detector.
    """
    def __init__(self, date: date, payee: str, narration: str, amount: Decimal,
                 account: str, transaction_id: Optional[str] = None, ofx_id: Optional[str] = None):
        self.date = date
        self.payee = payee
        self.narration = narration
        self.amount = amount
        self.account = account
        self.transaction_id = transaction_id
        self.ofx_id = ofx_id


def load_existing_transactions(ledger_file: str) -> List[LedgerTransaction]:
    """
    Load existing transactions from the Beancount ledger.

    Args:
        ledger_file: Path to Beancount ledger file

    Returns:
        List of LedgerTransaction objects

    Raises:
        FileNotFoundError: If ledger file doesn't exist
        DuplicateDetectionError: If ledger parsing fails
    """
    if not os.path.exists(ledger_file):
        raise FileNotFoundError(f"Ledger file not found: {ledger_file}")

    try:
        entries, errors, _ = loader.load_file(ledger_file)

        if errors:
            logger.warning(f"Beancount parsing warnings: {len(errors)} issues found")

        transactions = []

        for entry in entries:
            if not isinstance(entry, data.Transaction):
                continue

            # Extract payee and narration
            payee = entry.payee or ''
            narration = entry.narration or ''

            # Find source account (Assets or Liabilities posting)
            source_account = None
            transaction_amount = None

            for posting in entry.postings:
                if posting.units and posting.account.startswith(('Assets:', 'Liabilities:')):
                    source_account = posting.account
                    transaction_amount = posting.units.number
                    break

            # Skip if no source account found
            if not source_account or transaction_amount is None:
                continue

            # Extract metadata
            transaction_id = None
            ofx_id = None
            if hasattr(entry, 'meta') and entry.meta:
                transaction_id = entry.meta.get('transaction_id')
                ofx_id = entry.meta.get('ofx_id')

            # Create ledger transaction
            ledger_txn = LedgerTransaction(
                date=entry.date,
                payee=payee,
                narration=narration,
                amount=transaction_amount,
                account=source_account,
                transaction_id=transaction_id,
                ofx_id=ofx_id
            )

            transactions.append(ledger_txn)

        logger.info(f"Loaded {len(transactions)} existing transactions from ledger")
        return transactions

    except Exception as e:
        raise DuplicateDetectionError(f"Failed to load existing transactions: {e}")


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
                transaction_id=existing_txn.transaction_id,
                date=existing_txn.date,
                payee=existing_txn.payee,
                narration=existing_txn.narration,
                amount=existing_txn.amount,
                account=existing_txn.account
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
    - Payee: > 90% similarity

    Args:
        txn_date: Transaction date
        payee: Transaction payee
        amount: Transaction amount
        account: Source account
        existing_transactions: List of existing ledger transactions

    Returns:
        DuplicateInfo if match found, None otherwise
    """
    logger.debug(f"Checking fuzzy match for: date={txn_date}, payee='{payee}', amount={amount}, account={account}")

    for existing_txn in existing_transactions:
        # Check exact matches for date, amount, account
        if (existing_txn.date != txn_date or
            existing_txn.amount != amount or
            existing_txn.account != account):
            continue

        # Check payee similarity
        similarity = calculate_payee_similarity(payee, existing_txn.payee)
        logger.debug(f"  Comparing with existing: '{existing_txn.payee}' | similarity={similarity:.2f}")

        if similarity > 0.8:
            # Potential duplicate found
            logger.info(f"  ✓ Duplicate match! similarity={similarity:.2f}")
            return DuplicateInfo(
                transaction_id=existing_txn.transaction_id,
                date=existing_txn.date,
                payee=existing_txn.payee,
                narration=existing_txn.narration,
                amount=existing_txn.amount,
                account=existing_txn.account
            )

    return None


def find_duplicate(
    txn_date: date,
    payee: str,
    amount: Decimal,
    source_account: str,
    ofx_id: Optional[str],
    existing_transactions: List[LedgerTransaction]
) -> Tuple[bool, Optional[DuplicateInfo]]:
    """
    Find potential duplicate for a transaction.

    Uses two-step detection:
    1. Check for exact OFX ID match
    2. Check for fuzzy match on date/amount/account/payee

    Args:
        txn_date: Transaction date
        payee: Transaction payee
        amount: Transaction amount
        source_account: Source account
        ofx_id: Optional OFX transaction ID
        existing_transactions: List of existing ledger transactions

    Returns:
        Tuple of (is_duplicate, duplicate_info)
    """
    # Step 1: Check OFX ID match (if available)
    if ofx_id:
        ofx_match = check_ofx_id_match(ofx_id, existing_transactions)
        if ofx_match:
            return True, ofx_match

    # Step 2: Check fuzzy match
    fuzzy_match = check_fuzzy_match(txn_date, payee, amount, source_account, existing_transactions)
    if fuzzy_match:
        return True, fuzzy_match

    # No duplicate found
    return False, None
