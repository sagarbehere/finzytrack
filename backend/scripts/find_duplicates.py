#!/usr/bin/env python3
"""
Find duplicate transactions in a beancount ledger.

This script identifies potential duplicate transactions based on metadata attributes
like id, content_hash, or ofx_id.

Usage:
    python scripts/find_duplicates.py -i /path/to/ledger.beancount
    python scripts/find_duplicates.py -i /path/to/ledger.beancount --key ofx_id
    python scripts/find_duplicates.py -i /path/to/ledger.beancount --key content_hash
"""

import argparse
import sys
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Tuple

from beancount import loader
from beancount.core.data import Transaction


def find_duplicates(
    ledger_file: str,
    duplicate_key: str = 'id'
) -> Tuple[Dict[str, List[Tuple[Transaction, int]]], int, int]:
    """
    Find duplicate transactions based on specified metadata key.

    Args:
        ledger_file: Path to beancount ledger file
        duplicate_key: Metadata attribute to use for duplicate detection (id, ofx_id, content_hash)

    Returns:
        Tuple of (duplicate_groups, total_transactions, transactions_with_key)
        - duplicate_groups: Dict mapping key value to list of (transaction, line_number) tuples
        - total_transactions: Total number of transactions in ledger
        - transactions_with_key: Number of transactions that have the specified key
    """
    print(f"Loading ledger from: {ledger_file}")
    print(f"Duplicate detection key: {duplicate_key}\n")

    # Load ledger
    entries, errors, options = loader.load_file(ledger_file)

    if errors:
        print(f"Warning: {len(errors)} parsing errors found:")
        for error in errors[:5]:
            print(f"  - {error}")
        if len(errors) > 5:
            print(f"  ... and {len(errors) - 5} more")
        print()

    # Group transactions by the specified key
    key_to_transactions: Dict[str, List[Tuple[Transaction, int]]] = defaultdict(list)
    total_transactions = 0
    transactions_with_key = 0

    for entry in entries:
        if isinstance(entry, Transaction):
            total_transactions += 1

            # Get the key value from transaction metadata
            meta = entry.meta or {}
            key_value = meta.get(duplicate_key)

            if key_value:
                transactions_with_key += 1
                # Get line number from metadata
                line_number = meta.get('lineno', 'unknown')
                key_to_transactions[key_value].append((entry, line_number))

    # Filter to only groups with duplicates (more than 1 transaction)
    duplicate_groups = {
        key: txns
        for key, txns in key_to_transactions.items()
        if len(txns) > 1
    }

    return duplicate_groups, total_transactions, transactions_with_key


def format_transaction_summary(txn: Transaction, line_number: int) -> str:
    """Format a transaction for display."""
    date = txn.date
    payee = txn.payee or '(no payee)'
    narration = txn.narration or '(no narration)'

    # Get first posting for amount info
    amount_str = ''
    if txn.postings:
        first_posting = txn.postings[0]
        if first_posting.units:
            amount_str = f"{first_posting.units.number} {first_posting.units.currency}"

    return f"  Line {line_number}: {date} | {payee} | {narration} | {amount_str}"


def print_duplicates(
    duplicate_groups: Dict[str, List[Tuple[Transaction, int]]],
    duplicate_key: str
):
    """Print duplicate transaction groups."""
    if not duplicate_groups:
        print("No duplicate transactions found!")
        return

    print("=" * 80)
    print("DUPLICATE TRANSACTIONS")
    print("=" * 80)
    print()

    # Sort by number of duplicates (descending) then by key value
    sorted_groups = sorted(
        duplicate_groups.items(),
        key=lambda x: (-len(x[1]), x[0])
    )

    for idx, (key_value, transactions) in enumerate(sorted_groups, 1):
        print(f"Duplicate Set #{idx}: {duplicate_key} = {key_value}")
        print(f"  ({len(transactions)} transactions)")
        print()

        for txn, line_number in sorted(transactions, key=lambda x: x[0].date):
            print(format_transaction_summary(txn, line_number))

        print()
        print("-" * 80)
        print()


def print_summary(
    duplicate_groups: Dict[str, List[Tuple[Transaction, int]]],
    total_transactions: int,
    transactions_with_key: int,
    duplicate_key: str
):
    """Print summary statistics."""
    total_duplicates = sum(len(txns) for txns in duplicate_groups.values())
    num_duplicate_sets = len(duplicate_groups)

    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total transactions in ledger:           {total_transactions}")
    print(f"Transactions with '{duplicate_key}' attribute:  {transactions_with_key}")
    print(f"Transactions without '{duplicate_key}':         {total_transactions - transactions_with_key}")
    print()
    print(f"Number of duplicate sets:               {num_duplicate_sets}")
    print(f"Total duplicate transactions:           {total_duplicates}")
    print()

    if num_duplicate_sets > 0:
        # Show breakdown by duplicate set size
        set_sizes = defaultdict(int)
        for txns in duplicate_groups.values():
            set_sizes[len(txns)] += 1

        print("Breakdown by duplicate set size:")
        for size in sorted(set_sizes.keys(), reverse=True):
            count = set_sizes[size]
            print(f"  {count} set(s) with {size} duplicates each ({count * size} transactions total)")

    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description='Find duplicate transactions in a beancount ledger',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Find duplicates by id (default)
  python scripts/find_duplicates.py -i ledger.beancount

  # Find duplicates by ofx_id
  python scripts/find_duplicates.py -i ledger.beancount --key ofx_id

  # Find duplicates by content_hash
  python scripts/find_duplicates.py -i ledger.beancount --key content_hash
        """
    )
    parser.add_argument(
        '-i',
        '--input',
        required=True,
        help='Path to beancount ledger file'
    )
    parser.add_argument(
        '--key',
        choices=['id', 'ofx_id', 'content_hash'],
        default='id',
        help='Metadata attribute to use for duplicate detection (default: id)'
    )

    args = parser.parse_args()

    # Validate input file
    ledger_path = Path(args.input)
    if not ledger_path.exists():
        print(f"Error: Ledger file not found: {args.input}")
        sys.exit(1)

    # Find duplicates
    duplicate_groups, total_transactions, transactions_with_key = find_duplicates(
        ledger_file=str(ledger_path),
        duplicate_key=args.key
    )

    # Print results
    print_duplicates(duplicate_groups, args.key)
    print_summary(duplicate_groups, total_transactions, transactions_with_key, args.key)


if __name__ == '__main__':
    main()
