"""
Transaction ID Generator - Shared Library

This module provides transaction ID generation functionality that can be shared 
across multiple projects using git subtree.

The transaction ID generator creates deterministic SHA256-based IDs for financial
transactions with collision handling and OFX ID validation capabilities.

Usage:
    from transaction_id_generator import TransactionIdGenerator
    
    generator = TransactionIdGenerator()
    txn_id = generator.generate_id(date, payee, amount, account)

For detailed documentation, see the transaction_id_generator module.
"""

# Import all public classes and functions for easy access
from .transaction_id_generator import (
    TransactionIdGenerator,
    TransactionIdValidationError,
    generate_single_transaction_id,
    validate_single_ofx_id,
    select_account_for_transaction_id,
    add_transaction_id_to_beancount_transaction,
    create_beancount_transaction_with_id,
    HASH_INPUT_FORMAT,
    FALLBACK_PREFIX,
    DUPLICATE_SUFFIX_FORMAT,
    COLLISION_SUFFIX_FORMAT,
)

__version__ = "1.0.0"
__author__ = "OFX-to-Beancount Project"

# Define what gets imported with "from transaction_id_generator import *"
__all__ = [
    "TransactionIdGenerator",
    "TransactionIdValidationError", 
    "generate_single_transaction_id",
    "validate_single_ofx_id",
    "select_account_for_transaction_id",
    "add_transaction_id_to_beancount_transaction",
    "create_beancount_transaction_with_id",
    "HASH_INPUT_FORMAT",
    "FALLBACK_PREFIX", 
    "DUPLICATE_SUFFIX_FORMAT",
    "COLLISION_SUFFIX_FORMAT",
]