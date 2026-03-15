#!/usr/bin/env python3
"""
One-time migration script to replace legacy ofx_id metadata with external_id + external_id_type.

Usage:
    python migrate_ofx_id.py --ledger ~/finances/main.beancount
    python migrate_ofx_id.py --ledger ~/finances/main.beancount --dry-run

What it does:
    1. Reads the Beancount ledger file line by line.
    2. When it finds a line matching `  ofx_id: "..."`, it:
       - Replaces `ofx_id:` with `external_id:`
       - Inserts a new line immediately after with `  external_id_type: "OFX"`
    3. Writes the result to a new file, then replaces the original (after taking a backup).

The script is idempotent — running it twice has no effect (it skips lines
that already have external_id:).
"""
import argparse
import re
import shutil
import sys
from pathlib import Path


def migrate(ledger_path: Path, dry_run: bool = False) -> int:
    """Migrate ofx_id to external_id + external_id_type. Returns count of lines changed."""
    with open(ledger_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    output_lines = []
    changed = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        # Match lines like:   ofx_id: "some-value"
        m = re.match(r'^(\s+)ofx_id:\s*"([^"]*)"', line)
        if m:
            indent = m.group(1)
            value = m.group(2)
            output_lines.append(f'{indent}external_id: "{value}"\n')
            output_lines.append(f'{indent}external_id_type: "OFX"\n')
            changed += 1
        else:
            output_lines.append(line)
        i += 1

    if dry_run:
        print(f"DRY RUN: would change {changed} line(s) in {ledger_path}")
        if changed > 0:
            print("Sample of changes (first 5):")
            shown = 0
            for j, line in enumerate(lines):
                if re.match(r'^\s+ofx_id:', line) and shown < 5:
                    print(f"  Line {j+1}: {line.rstrip()}")
                    shown += 1
        return changed

    if changed == 0:
        print("No ofx_id lines found — nothing to migrate.")
        return 0

    # Take backup
    backup_path = ledger_path.with_suffix(ledger_path.suffix + '.pre-migration-backup')
    shutil.copy2(ledger_path, backup_path)
    print(f"Backup created: {backup_path}")

    # Write migrated content
    with open(ledger_path, 'w', encoding='utf-8') as f:
        f.writelines(output_lines)

    print(f"Migration complete: {changed} ofx_id line(s) updated in {ledger_path}")
    return changed


def main():
    p = argparse.ArgumentParser(description='Migrate ofx_id to external_id + external_id_type')
    p.add_argument('--ledger', required=True, help='Path to Beancount ledger file')
    p.add_argument('--dry-run', action='store_true', help='Show what would be changed without writing')
    args = p.parse_args()

    ledger_path = Path(args.ledger).expanduser().resolve()
    if not ledger_path.exists():
        print(f"Error: ledger file not found: {ledger_path}", file=sys.stderr)
        sys.exit(1)

    migrate(ledger_path, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
