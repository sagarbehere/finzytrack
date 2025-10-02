"""
Transaction ID Generator - Self-contained utility for generating unique transaction IDs.

This module provides a reusable service for generating SHA256-based transaction IDs
with collision handling and OFX ID validation. It's designed to be framework-agnostic
and easily portable to other projects.

Dependencies: Only standard library modules (hashlib, secrets, typing, datetime)
"""

import hashlib
import secrets
from typing import Dict, Set, Optional, Tuple, Union, List
from decimal import Decimal, InvalidOperation
from datetime import datetime


class TransactionIdValidationError(Exception):
    """
    Exception raised when transaction data fails validation for ID generation.
    
    This exception is raised when critical fields required for transaction_id
    generation are missing, empty, or have invalid formats in strict validation mode.
    """
    pass


class TransactionIdGenerator:
    """
    Generates unique transaction IDs using SHA256 hashes of immutable transaction fields.
    
    Features:
    - Deterministic SHA256 hashing based on date|payee|amount|account
    - Collision handling with -2, -3, etc. suffixes
    - Duplicate handling with -dup-1, -dup-2, etc. suffixes
    - OFX ID validation and cleaning
    - Fallback ID generation when mapped account unavailable
    - Self-contained with no external dependencies beyond standard library
    
    Usage:
        generator = TransactionIdGenerator()
        
        # Generate transaction ID
        txn_id = generator.generate_id("2024-01-15", "GROCERY STORE", "-85.50", "Liabilities:CreditCard")
        
        # Validate OFX ID
        clean_ofx_id = generator.validate_ofx_id("  20240115001234567890  ")
    """
    
    def __init__(self):
        """Initialize generator with empty state tracking."""
        self.used_ids: Set[str] = set()
        self.collision_counters: Dict[str, int] = {}
    
    def generate_id(self, 
                   date: str, 
                   payee: str, 
                   amount: Union[str, Decimal, float], 
                   mapped_account: str, 
                   narration: str = "",
                   is_kept_duplicate: bool = False,
                   strict_validation: bool = False) -> str:
        """
        Generate unique transaction ID using SHA256 hash of immutable fields.
        
        The hash input format is: "{date}|{payee}|{narration}|{amount}|{mapped_account}"
        
        Args:
            date: Transaction date in YYYY-MM-DD format
            payee: Transaction payee/merchant name
            amount: Transaction amount (can be string, Decimal, or float)
            mapped_account: Beancount account name (e.g., "Assets:Checking")
            narration: Transaction narration/description
            is_kept_duplicate: Whether this is a kept duplicate transaction
            strict_validation: If True, enforce strict validation of all fields
            
        Returns:
            64-character SHA256 hash string, potentially with suffix for collisions/duplicates
            
        Raises:
            TransactionIdValidationError: If strict_validation=True and any field is invalid
            
        Examples:
            # Normal transaction
            >>> gen = TransactionIdGenerator()
            >>> gen.generate_id("2024-01-15", "GROCERY STORE", "-85.50", "Liabilities:CreditCard")
            'a1b2c3d4e5f6789012345678901234567890123456789012345678901234567890'
            
            # Strict validation
            >>> gen.generate_id("2024-01-15", "", "-85.50", "Liabilities:CreditCard", strict_validation=True)
            TransactionIdValidationError: Payee field is empty or whitespace-only
            
            # Kept duplicate
            >>> gen.generate_id("2024-01-15", "GROCERY STORE", "-85.50", "Liabilities:CreditCard", True)
            'a1b2c3d4e5f6789012345678901234567890123456789012345678901234567890-dup-1'
            
            # Fallback when no account (disabled in strict mode)
            >>> gen.generate_id("2024-01-15", "GROCERY STORE", "-85.50", "")
            'fallback_a1b2c3d4'
        """
        # Perform strict validation if requested
        if strict_validation:
            self._validate_fields(date, payee, amount, mapped_account, narration)
        
        # Fallback if mapped account not available (disabled in strict mode)
        if not mapped_account or not str(mapped_account).strip():
            if strict_validation:
                raise TransactionIdValidationError("Mapped account field is empty or whitespace-only")
            random_suffix = secrets.token_hex(4)  # 8 char random string
            fallback_id = f"fallback_{random_suffix}"
            self.used_ids.add(fallback_id)
            return fallback_id
        
        # Normalize inputs
        clean_payee = str(payee) if payee else ""
        clean_narration = str(narration) if narration else ""
        clean_amount = str(amount) if amount else "0"
        clean_account = str(mapped_account).strip()
        
        # Create hash input
        hash_input = f"{date}|{clean_payee}|{clean_narration}|{clean_amount}|{clean_account}"
        base_hash = hashlib.sha256(hash_input.encode('utf-8')).hexdigest()
        
        # Handle kept duplicates
        if is_kept_duplicate:
            final_id = self._handle_kept_duplicate(base_hash)
        else:
            # Handle collisions
            final_id = self._handle_collision(base_hash)
        
        self.used_ids.add(final_id)
        return final_id
    
    def _validate_fields(self, date: str, payee: str, amount: Union[str, Decimal, float], mapped_account: str, narration: str = "") -> None:
        """
        Validate all critical fields required for transaction ID generation.
        
        Args:
            date: Transaction date string 
            payee: Transaction payee string
            amount: Transaction amount
            mapped_account: Beancount account name
            narration: Transaction narration string
            
        Raises:
            TransactionIdValidationError: If any field is invalid
        """
        # Validate date
        if not date or not str(date).strip():
            raise TransactionIdValidationError("Date field is empty or whitespace-only")
        
        date_str = str(date).strip()
        try:
            # Validate YYYY-MM-DD format and that it represents a valid date
            datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            raise TransactionIdValidationError(f"Date field '{date_str}' is not in valid YYYY-MM-DD format or represents an invalid date")
        
        # Validate payee OR narration (at least one must be non-empty)
        payee_str = str(payee).strip() if payee else ""
        narration_str = str(narration).strip() if narration else ""
        
        if not payee_str and not narration_str:
            raise TransactionIdValidationError("Both payee and narration fields are empty - at least one must contain meaningful content")
        
        # Validate amount
        if amount is None:
            raise TransactionIdValidationError("Amount field is None")
        
        amount_str = str(amount).strip()
        if not amount_str:
            raise TransactionIdValidationError("Amount field is empty or whitespace-only")
        
        try:
            # Handle amounts with currency (e.g., "-11.75 USD" or just "-11.75")
            # Split on whitespace and try to parse the first part as a number
            amount_parts = amount_str.split()
            if amount_parts:
                # Try to parse the numeric part (first part)
                Decimal(amount_parts[0])
            else:
                raise TransactionIdValidationError(f"Amount field '{amount_str}' is empty after splitting")
        except (InvalidOperation, ValueError):
            raise TransactionIdValidationError(f"Amount field '{amount_str}' does not contain a valid number")
        
        # Validate mapped account
        if not mapped_account or not str(mapped_account).strip():
            raise TransactionIdValidationError("Mapped account field is empty or whitespace-only")
    
    def _handle_kept_duplicate(self, base_hash: str) -> str:
        """Handle kept duplicate ID generation with -dup-N suffix."""
        dup_counter = 1
        while f"{base_hash}-dup-{dup_counter}" in self.used_ids:
            dup_counter += 1
        return f"{base_hash}-dup-{dup_counter}"
    
    def _handle_collision(self, base_hash: str) -> str:
        """Handle hash collision with -N suffix."""
        if base_hash not in self.used_ids:
            return base_hash
        
        # Initialize counter if first collision
        if base_hash not in self.collision_counters:
            self.collision_counters[base_hash] = 1
        
        self.collision_counters[base_hash] += 1
        return f"{base_hash}-{self.collision_counters[base_hash]}"
    
    def validate_ofx_id(self, ofx_id: Optional[str]) -> Optional[str]:
        """
        Validate and clean OFX transaction ID.
        
        Args:
            ofx_id: Original OFX transaction ID (may be None, empty, or whitespace)
            
        Returns:
            Cleaned OFX ID string, or None if invalid/empty
            
        Examples:
            >>> gen = TransactionIdGenerator()
            >>> gen.validate_ofx_id("  20240115001234567890  ")
            '20240115001234567890'
            >>> gen.validate_ofx_id("")
            None
            >>> gen.validate_ofx_id(None)
            None
        """
        if not ofx_id:
            return None
        
        cleaned = str(ofx_id).strip()
        if not cleaned or cleaned.isspace():
            return None
        
        return cleaned
    
    def generate_hash_components(self, date: str, payee: str, amount: Union[str, Decimal, float], mapped_account: str, narration: str = "") -> Tuple[str, str]:
        """
        Generate hash components for debugging/testing purposes.
        
        Args:
            date: Transaction date
            payee: Transaction payee
            amount: Transaction amount
            mapped_account: Beancount account
            narration: Transaction narration/description
            
        Returns:
            Tuple of (hash_input_string, sha256_hash)
        """
        clean_payee = str(payee) if payee else ""
        clean_narration = str(narration) if narration else ""
        clean_amount = str(amount) if amount else "0"
        clean_account = str(mapped_account).strip()
        
        hash_input = f"{date}|{clean_payee}|{clean_narration}|{clean_amount}|{clean_account}"
        hash_output = hashlib.sha256(hash_input.encode('utf-8')).hexdigest()
        
        return hash_input, hash_output
    
    def reset(self):
        """Reset generator state (used_ids and collision_counters)."""
        self.used_ids.clear()
        self.collision_counters.clear()
    
    def get_stats(self) -> Dict[str, int]:
        """
        Get generator statistics for debugging/monitoring.
        
        Returns:
            Dictionary with generator statistics
        """
        return {
            'total_ids_generated': len(self.used_ids),
            'collision_count': len(self.collision_counters),
            'max_collision_suffix': max(self.collision_counters.values()) if self.collision_counters else 0
        }


def generate_single_transaction_id(date: str, 
                                 payee: str, 
                                 amount: Union[str, Decimal, float], 
                                 mapped_account: str,
                                 narration: str = "",
                                 strict_validation: bool = False) -> str:
    """
    Convenience function to generate a single transaction ID without state tracking.
    
    Use this when you don't need collision handling or are generating IDs for 
    separate, unrelated transactions.
    
    Args:
        date: Transaction date in YYYY-MM-DD format
        payee: Transaction payee/merchant name
        amount: Transaction amount
        mapped_account: Beancount account name
        narration: Transaction narration/description
        strict_validation: If True, enforce strict validation of all fields
        
    Returns:
        64-character SHA256 hash string
        
    Raises:
        TransactionIdValidationError: If strict_validation=True and any field is invalid
        
    Examples:
        >>> generate_single_transaction_id("2024-01-15", "GROCERY STORE", "-85.50", "Liabilities:CreditCard", "Weekly shopping")
        'a1b2c3d4e5f6789012345678901234567890123456789012345678901234567890'
        
        >>> generate_single_transaction_id("2024-01-15", "", "-85.50", "Liabilities:CreditCard", "", strict_validation=True)
        TransactionIdValidationError: Both payee and narration fields are empty - at least one must contain meaningful content
    """
    generator = TransactionIdGenerator()
    return generator.generate_id(date, payee, amount, mapped_account, narration, strict_validation=strict_validation)


def validate_single_ofx_id(ofx_id: Optional[str]) -> Optional[str]:
    """
    Convenience function to validate a single OFX ID without instantiating generator.
    
    Args:
        ofx_id: OFX transaction ID to validate
        
    Returns:
        Cleaned OFX ID or None if invalid
        
    Example:
        >>> validate_single_ofx_id("  20240115001234567890  ")
        '20240115001234567890'
    """
    generator = TransactionIdGenerator()
    return generator.validate_ofx_id(ofx_id)


def select_account_for_transaction_id(postings: list, source_account_metadata: Optional[str] = None) -> Tuple[str, str]:
    """
    Select the appropriate account and amount for transaction ID generation.
    
    This function implements the standard account selection logic used across all 
    transaction ID generation to ensure consistency. It handles both OFX-originated 
    transactions (which have source_account metadata) and manually created transactions.
    
    Priority order:
    0. If source_account_metadata is provided, use that account
    1. Assets or Liabilities accounts (first found)
    2. Income accounts (first found)
    3. First posting account
    
    Args:
        postings: List of transaction postings (each must have 'account' and optionally 'units')
        source_account_metadata: Optional source account from metadata (for OFX-originated transactions)
        
    Returns:
        Tuple of (account_name, amount_with_currency)
        
    Raises:
        ValueError: If postings list is empty or invalid
        
    Example:
        >>> postings = [
        ...     {'account': 'Expenses:Food', 'units': {'number': '50.00', 'currency': 'USD'}},
        ...     {'account': 'Liabilities:CreditCard', 'units': {'number': '-50.00', 'currency': 'USD'}}
        ... ]
        >>> select_account_for_transaction_id(postings)
        ('Liabilities:CreditCard', '-50.00 USD')
        
        >>> select_account_for_transaction_id(postings, 'Liabilities:CreditCard')
        ('Liabilities:CreditCard', '-50.00 USD')
    """
    if not postings:
        raise ValueError("Transaction has no postings")
    
    # Priority 0: Use source_account metadata if provided (ensures consistency)
    if source_account_metadata:
        for posting in postings:
            account = posting.get('account') if isinstance(posting, dict) else getattr(posting, 'account', None)
            if account == source_account_metadata:
                # Extract amount
                if isinstance(posting, dict):
                    units = posting.get('units')
                    if units:
                        number = units.get('number', '0')
                        currency = units.get('currency', 'USD')
                        return source_account_metadata, f"{number} {currency}"
                else:
                    # Handle object-style postings (from Beancount parser)
                    units = getattr(posting, 'units', None)
                    if units:
                        number = getattr(units, 'number', '0')
                        currency = getattr(units, 'currency', 'USD')
                        return source_account_metadata, f"{number} {currency}"
    
    # Helper function to extract account and amount from a posting
    def extract_account_amount(posting):
        if isinstance(posting, dict):
            account = posting.get('account')
            units = posting.get('units')
            if units:
                number = units.get('number', '0')
                currency = units.get('currency', 'USD')
                amount_str = f"{number} {currency}"
            else:
                amount_str = "0 USD"
        else:
            # Handle object-style postings
            account = getattr(posting, 'account', None)
            units = getattr(posting, 'units', None)
            if units:
                number = getattr(units, 'number', '0')
                currency = getattr(units, 'currency', 'USD')
                amount_str = f"{number} {currency}"
            else:
                amount_str = "0 USD"
        return account, amount_str
    
    # Priority 1: Assets or Liabilities accounts
    for posting in postings:
        account, amount_str = extract_account_amount(posting)
        if account and (account.startswith('Assets:') or account.startswith('Liabilities:')):
            return account, amount_str
    
    # Priority 2: Income accounts
    for posting in postings:
        account, amount_str = extract_account_amount(posting)
        if account and account.startswith('Income:'):
            return account, amount_str
    
    # Priority 3: First posting with valid account
    for posting in postings:
        account, amount_str = extract_account_amount(posting)
        if account:
            return account, amount_str
    
    # This shouldn't happen if postings are valid
    raise ValueError("No valid account found in postings")


def add_transaction_id_to_beancount_transaction(transaction, 
                                               force_recalculate: bool = False,
                                               strict_validation: bool = True,
                                               id_generator: Optional[TransactionIdGenerator] = None):
    """
    Add transaction_id metadata to a Beancount transaction object.
    
    This is the main entry point for transaction ID generation. It accepts a standard
    beancount.core.data.Transaction object and returns a new transaction with 
    transaction_id metadata added.
    
    This design ensures consistency across all code that uses the same Beancount library
    version, as all transaction data is normalized to the standard Beancount format.
    
    Args:
        transaction: beancount.core.data.Transaction object
        force_recalculate: If True, recalculate even if transaction already has transaction_id
        strict_validation: If True, enforce strict field validation
        id_generator: Optional TransactionIdGenerator instance (for collision tracking)
                     If not provided, a new instance will be created
        
    Returns:
        New beancount.core.data.Transaction object with transaction_id metadata added
        
    Raises:
        TransactionIdValidationError: If strict_validation=True and fields are invalid
        ValueError: If transaction format is invalid
        
    Example:
        >>> from beancount.core import data
        >>> from beancount import loader
        >>> 
        >>> # Load transaction from Beancount file
        >>> entries, _, _ = loader.load_file('transactions.beancount')
        >>> txn = entries[0]  # First transaction
        >>> 
        >>> # Add transaction_id
        >>> txn_with_id = add_transaction_id_to_beancount_transaction(txn)
        >>> print(txn_with_id.meta['transaction_id'])
        'a1b2c3d4e5f6789012345678901234567890123456789012345678901234567890'
        
        # Force recalculation
        >>> txn_recalc = add_transaction_id_to_beancount_transaction(
        ...     txn_with_id, force_recalculate=True
        ... )
    """
    # Validate input
    if not hasattr(transaction, 'postings'):
        raise ValueError("Input must be a beancount.core.data.Transaction object")
    
    # Create generator if not provided
    if id_generator is None:
        id_generator = TransactionIdGenerator()
    
    # Check if already has transaction_id in metadata
    if (hasattr(transaction, 'meta') and transaction.meta and 
        'transaction_id' in transaction.meta and not force_recalculate):
        return transaction
    
    # Extract source_account from metadata if present
    source_account_metadata = None
    if hasattr(transaction, 'meta') and transaction.meta:
        source_account_metadata = transaction.meta.get('source_account')
    
    # Select account using centralized logic
    try:
        account, amount_str = select_account_for_transaction_id(
            postings=transaction.postings,
            source_account_metadata=source_account_metadata
        )
    except ValueError as e:
        if strict_validation:
            raise TransactionIdValidationError(f"Account selection failed: {e}")
        # Fallback for non-strict mode
        account = "Unknown"
        amount_str = "0 USD"
    
    # Extract date (Beancount date objects have strftime)
    date_str = transaction.date.strftime('%Y-%m-%d')
    
    # Generate transaction ID
    transaction_id = id_generator.generate_id(
        date=date_str,
        payee=transaction.payee or '',
        amount=amount_str,
        mapped_account=account,
        narration=transaction.narration or '',
        is_kept_duplicate=False,  # Could be parameterized in future
        strict_validation=strict_validation
    )
    
    # Prepare new metadata - only modify transaction_id, preserve everything else
    if not hasattr(transaction, 'meta') or transaction.meta is None:
        updated_meta = {'transaction_id': transaction_id}
    else:
        updated_meta = transaction.meta.copy()
        updated_meta['transaction_id'] = transaction_id
    
    # Create new transaction with updated metadata using Beancount's _replace method
    return transaction._replace(meta=updated_meta)


def create_beancount_transaction_with_id(date_str: str,
                                        payee: str,
                                        narration: str,
                                        postings: List,
                                        source_account: str,
                                        ofx_id: Optional[str] = None,
                                        id_generator: Optional[TransactionIdGenerator] = None):
    """
    Create a new Beancount transaction with transaction_id metadata from scratch.
    
    This is a helper function for creating transactions from OFX data that ensures
    the transaction_id is generated consistently with the standard process.
    
    Args:
        date_str: Date in YYYY-MM-DD format
        payee: Transaction payee
        narration: Transaction narration/description
        postings: List of Beancount Posting objects
        source_account: The account that originated this transaction (from OFX)
        ofx_id: Optional OFX transaction ID
        id_generator: Optional TransactionIdGenerator instance
        
    Returns:
        beancount.core.data.Transaction object with transaction_id metadata
        
    Example:
        >>> from beancount.core.data import Posting, Amount
        >>> from decimal import Decimal
        >>> 
        >>> postings = [
        ...     Posting('Expenses:Food', Amount(Decimal('50.00'), 'USD'), None, None, None, None),
        ...     Posting('Liabilities:CreditCard', Amount(Decimal('-50.00'), 'USD'), None, None, None, None)
        ... ]
        >>> 
        >>> txn = create_beancount_transaction_with_id(
        ...     date_str='2024-01-15',
        ...     payee='GROCERY STORE',
        ...     narration='Weekly shopping',
        ...     postings=postings,
        ...     source_account='Liabilities:CreditCard'
        ... )
        >>> print(txn.meta['transaction_id'])
    """
    # Import here to avoid circular dependencies
    try:
        from beancount.core.data import Transaction
        from datetime import datetime
    except ImportError:
        raise ImportError("This function requires the beancount library")
    
    # Create generator if not provided
    if id_generator is None:
        id_generator = TransactionIdGenerator()
    
    # Select account and amount from the postings for transaction_id generation
    account, amount_str = select_account_for_transaction_id(
        postings=postings,
        source_account_metadata=source_account
    )
    
    # Generate transaction ID
    transaction_id = id_generator.generate_id(
        date=date_str,
        payee=payee,
        amount=amount_str,
        mapped_account=account,
        narration=narration,
        strict_validation=True
    )
    
    # Prepare metadata
    meta = {'transaction_id': transaction_id}
    
    # Add source_account metadata for future consistency
    if source_account:
        meta['source_account'] = source_account
    
    # Add ofx_id if provided
    if ofx_id and str(ofx_id).strip():
        validated_ofx_id = TransactionIdGenerator().validate_ofx_id(ofx_id)
        if validated_ofx_id:
            meta['ofx_id'] = validated_ofx_id
    
    # Parse date
    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    # Create Beancount transaction
    return Transaction(
        meta=meta,
        date=date_obj,
        flag='*',  # Default flag
        payee=payee,
        narration=narration,
        tags=frozenset(),
        links=frozenset(),
        postings=postings
    )


# Module-level constants for external use
HASH_INPUT_FORMAT = "{date}|{payee}|{narration}|{amount}|{account}"
FALLBACK_PREFIX = "fallback_"
DUPLICATE_SUFFIX_FORMAT = "-dup-{counter}"
COLLISION_SUFFIX_FORMAT = "-{counter}"


if __name__ == "__main__":
    # Demo/test the generator
    generator = TransactionIdGenerator()
    
    print("Transaction ID Generator Demo")
    print("=" * 40)
    
    # Test normal transaction
    txn_id = generator.generate_id("2024-01-15", "GROCERY STORE", "-85.50", "Liabilities:CreditCard")
    print(f"Normal transaction ID: {txn_id}")
    
    # Test collision (same input)
    txn_id2 = generator.generate_id("2024-01-15", "GROCERY STORE", "-85.50", "Liabilities:CreditCard")
    print(f"Collision handling: {txn_id2}")
    
    # Test kept duplicate
    txn_id3 = generator.generate_id("2024-01-15", "GROCERY STORE", "-85.50", "Liabilities:CreditCard", is_kept_duplicate=True)
    print(f"Kept duplicate: {txn_id3}")
    
    # Test fallback
    txn_id4 = generator.generate_id("2024-01-15", "GROCERY STORE", "-85.50", "")
    print(f"Fallback ID: {txn_id4}")
    
    # Test OFX ID validation
    ofx_id = generator.validate_ofx_id("  20240115001234567890  ")
    print(f"Cleaned OFX ID: {ofx_id}")
    
    # Show stats
    stats = generator.get_stats()
    print(f"Generator stats: {stats}")
    
    # Show hash components
    hash_input, hash_output = generator.generate_hash_components("2024-01-15", "GROCERY STORE", "-85.50", "Liabilities:CreditCard", "Weekly shopping")
    print(f"Hash input: {hash_input}")
    print(f"Hash output: {hash_output}")