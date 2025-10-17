#!/usr/bin/env python3
"""
One-time migration script: transaction_id → id + content_hash

This script migrates existing ledger transactions from the old transaction_id
system to the new UUIDv7 + content_hash system.

Usage:
    python scripts/migrate_to_uuid_system.py --ledger-file /path/to/ledger.beancount --dry-run
    python scripts/migrate_to_uuid_system.py --ledger-file /path/to/ledger.beancount --execute
"""

import argparse
import sys
import uuid_utils as uuid
from pathlib import Path
from datetime import datetime
from typing import List, Tuple

from beancount import loader
from beancount.core.data import Transaction
from beancount.parser import printer

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.libs.content_hash import compute_content_hash


def migrate_transaction(txn: Transaction) -> Tuple[Transaction, bool]:
    """
    Migrate a single transaction to new ID system.

    Returns:
        Tuple of (migrated_transaction, was_changed)
    """
    if not isinstance(txn, Transaction):
        return txn, False

    old_meta = txn.meta or {}
    changed = False

    # Check if already has new IDs
    if 'id' in old_meta and 'content_hash' in old_meta:
        # Already migrated
        return txn, False

    # Generate new UUIDv7
    new_id = str(uuid.uuid7())
    changed = True

    # Compute content hash from transaction
    source_account = None
    if 'source_account' in old_meta:
        source_account = old_meta['source_account']
    else:
        # Infer from first Assets/Liabilities posting
        for posting in txn.postings:
            if posting.account.startswith(('Assets:', 'Liabilities:')):
                source_account = posting.account
                break

    if not source_account:
        # Fallback: use first posting account
        source_account = txn.postings[0].account if txn.postings else ""

    # Get amount from source account posting
    amount_str = "0"
    for posting in txn.postings:
        if posting.account == source_account and posting.units:
            amount_str = f"{posting.units.number} {posting.units.currency}"
            break

    # Compute content hash
    content_hash = compute_content_hash(
        date=txn.date.isoformat(),
        payee=txn.payee or "",
        amount=amount_str,
        source_account=source_account,
        narration=txn.narration or ""
    )

    # Build new metadata
    new_meta = {}

    # Add new IDs
    new_meta['id'] = new_id
    new_meta['content_hash'] = content_hash

    # Preserve source_account
    if source_account:
        new_meta['source_account'] = source_account

    # Preserve important fields
    preserve_fields = ['ofx_id', 'ofx_memo']
    for field in preserve_fields:
        if field in old_meta:
            new_meta[field] = old_meta[field]

    # Copy other metadata (excluding old transaction_id)
    for key, value in old_meta.items():
        if key not in new_meta and key != 'transaction_id':
            new_meta[key] = value

    # Create migrated transaction
    migrated_txn = txn._replace(meta=new_meta)

    return migrated_txn, changed


def migrate_ledger(
    ledger_file: str,
    output_file: str = None,
    dry_run: bool = True
) -> Tuple[int, int]:
    """
    Migrate entire ledger to new ID system.

    Args:
        ledger_file: Path to input ledger file
        output_file: Path to output ledger file (if None, overwrites input)
        dry_run: If True, don't write changes

    Returns:
        Tuple of (total_transactions, migrated_count)
    """
    print(f"Loading ledger from: {ledger_file}")

    # Load ledger
    entries, errors, options = loader.load_file(ledger_file)

    if errors:
        print(f"⚠️  Warning: {len(errors)} parsing errors found:")
        for error in errors[:5]:
            print(f"  - {error}")
        if len(errors) > 5:
            print(f"  ... and {len(errors) - 5} more")

        response = input("\nContinue anyway? (y/n): ")
        if response.lower() != 'y':
            print("Migration aborted.")
            return 0, 0

    # Migrate transactions
    migrated_entries = []
    total_transactions = 0
    migrated_count = 0

    for entry in entries:
        if isinstance(entry, Transaction):
            total_transactions += 1
            migrated_entry, was_changed = migrate_transaction(entry)
            migrated_entries.append(migrated_entry)

            if was_changed:
                migrated_count += 1

                # Show sample
                if migrated_count <= 3:
                    print(f"\nSample migration #{migrated_count}:")
                    print(f"  Date: {entry.date}")
                    print(f"  Payee: {entry.payee}")
                    print(f"  Old meta keys: {list(entry.meta.keys()) if entry.meta else []}")
                    print(f"  New meta keys: {list(migrated_entry.meta.keys()) if migrated_entry.meta else []}")
        else:
            migrated_entries.append(entry)

    print(f"\n📊 Migration Summary:")
    print(f"  Total transactions: {total_transactions}")
    print(f"  Migrated: {migrated_count}")
    print(f"  Unchanged: {total_transactions - migrated_count}")

    if dry_run:
        print(f"\n🔍 DRY RUN - No changes written")
        return total_transactions, migrated_count

    # Write migrated ledger
    output_path = output_file or ledger_file

    # Create backup
    if output_path == ledger_file:
        backup_path = f"{ledger_file}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        print(f"\n💾 Creating backup: {backup_path}")
        import shutil
        shutil.copy2(ledger_file, backup_path)

    # Write new ledger
    print(f"✍️  Writing migrated ledger to: {output_path}")

    with open(output_path, 'w', encoding='utf-8') as f:
        for entry in migrated_entries:
            f.write(printer.format_entry(entry))
            f.write('\n')

    print(f"✅ Migration complete!")

    return total_transactions, migrated_count


def main():
    parser = argparse.ArgumentParser(
        description='Migrate ledger from transaction_id to UUIDv7 + content_hash system'
    )
    parser.add_argument(
        '--ledger-file',
        required=True,
        help='Path to beancount ledger file'
    )
    parser.add_argument(
        '--output-file',
        help='Path to output file (default: overwrite input with backup)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        default=True,
        help='Show what would be changed without writing (default: true)'
    )
    parser.add_argument(
        '--execute',
        action='store_true',
        help='Actually perform the migration (creates backup first)'
    )

    args = parser.parse_args()

    # Validate input file
    ledger_path = Path(args.ledger_file)
    if not ledger_path.exists():
        print(f"❌ Error: Ledger file not found: {args.ledger_file}")
        sys.exit(1)

    # Determine dry run mode
    dry_run = not args.execute

    if dry_run:
        print("🔍 Running in DRY RUN mode (use --execute to apply changes)\n")
    else:
        print("⚡ Running in EXECUTE mode (changes will be written)\n")

    # Run migration
    total, migrated = migrate_ledger(
        ledger_file=str(ledger_path),
        output_file=args.output_file,
        dry_run=dry_run
    )

    if dry_run:
        print(f"\n💡 To apply these changes, run:")
        print(f"   python {sys.argv[0]} --ledger-file {args.ledger_file} --execute")


if __name__ == '__main__':
    main()
