#!/usr/bin/env python3
"""
Test case demonstrating Beancount's auto-generation of padding transactions.

This script shows that:
1. loader.load_file() automatically generates padding transactions for pad directives
2. Writing all entries back with printer.format_entry() materializes these transactions
3. This causes bean-check to warn about "Unused Pad entry"

Question for Beancount maintainers:
- Is this intended behavior?
- Is there a way to prevent auto-generation during load?
- Should auto-generated entries be marked in metadata to distinguish them?
"""

import os
from beancount import loader
from beancount.parser import printer

# Get the script directory
script_dir = os.path.dirname(os.path.abspath(__file__))
test_file = os.path.join(script_dir, 'test_pad_autogeneration.beancount')

print("=" * 70)
print("BEANCOUNT PAD AUTO-GENERATION TEST")
print("=" * 70)

# Step 1: Show original file contents
print("\n1. ORIGINAL FILE CONTENTS:")
print("-" * 70)
with open(test_file, 'r') as f:
    original_content = f.read()
    print(original_content)

# Step 2: Load file and inspect entries
print("\n2. ENTRIES RETURNED BY loader.load_file():")
print("-" * 70)
entries, errors, options = loader.load_file(test_file)

print(f"Total entries: {len(entries)}")
print(f"Total errors: {len(errors)}\n")

for i, entry in enumerate(entries):
    entry_type = entry.__class__.__name__
    print(f"Entry {i}: {entry_type}")

    if entry_type == 'Pad':
        print(f"  Account: {entry.account}")
        print(f"  Source: {entry.source_account}")
        print(f"  Date: {entry.date}")

    elif entry_type == 'Transaction':
        print(f"  Date: {entry.date}")
        print(f"  Flag: {entry.flag}")
        print(f"  Narration: {entry.narration}")
        print(f"  Postings: {len(entry.postings)}")

        # Check if this has transaction IDs (user-created) or not (auto-generated)
        has_id = entry.meta and ('id' in entry.meta or 'transaction_id' in entry.meta)
        print(f"  Has transaction ID: {has_id}")

        if entry.flag == 'P' and not has_id:
            print("  ⚠️  This appears to be an AUTO-GENERATED padding transaction!")

    elif entry_type == 'Balance':
        print(f"  Account: {entry.account}")
        print(f"  Amount: {entry.amount}")

    print()

# Step 3: Simulate what happens when we write all entries back
print("\n3. SIMULATING LEDGER UPDATE (write all entries back):")
print("-" * 70)

output_file = os.path.join(script_dir, 'test_pad_autogeneration_rewritten.beancount')

with open(output_file, 'w') as f:
    for entry in entries:
        f.write(printer.format_entry(entry))
        f.write('\n\n')

print(f"Wrote all entries to: {output_file}\n")

# Step 4: Show what was written
print("\n4. REWRITTEN FILE CONTENTS:")
print("-" * 70)
with open(output_file, 'r') as f:
    rewritten_content = f.read()
    print(rewritten_content)

# Step 5: Compare
print("\n5. ANALYSIS:")
print("-" * 70)
print(f"Original file size: {len(original_content)} bytes")
print(f"Rewritten file size: {len(rewritten_content)} bytes")

if len(rewritten_content) > len(original_content):
    print("\n⚠️  The rewritten file is LARGER!")
    print("This is because the auto-generated padding transaction was written to disk.")
    print("\nIf you run bean-check on the rewritten file, you'll see:")
    print('  "Unused Pad entry" warning')
    print("\nThis happens because:")
    print("  1. Beancount sees: pad directive + balance assertion")
    print("  2. Beancount auto-generates a padding transaction in memory")
    print("  3. We write ALL entries (including auto-generated) back to file")
    print("  4. Now the file has: pad directive + explicit padding + balance")
    print("  5. The pad directive is now 'unused' (padding already exists)")

# Step 6: Check for loader options
print("\n6. CHECKING FOR LOADER OPTIONS:")
print("-" * 70)
print("loader.load_file() signature:")
import inspect
sig = inspect.signature(loader.load_file)
print(f"  {sig}")

print("\nAvailable parameters:")
for param_name, param in sig.parameters.items():
    print(f"  - {param_name}: {param.annotation if param.annotation != inspect.Parameter.empty else 'any'}")
    if param.default != inspect.Parameter.empty:
        print(f"    Default: {param.default}")

print("\n⚠️  There doesn't appear to be an option to disable pad auto-generation.")
print("The padding is done internally by the Beancount loader.")

# Step 7: Recommendations
print("\n7. RECOMMENDATIONS:")
print("-" * 70)
print("To avoid materializing auto-generated padding transactions:")
print("  Option 1: Skip entries with flag='P' and no transaction ID when writing")
print("  Option 2: Read original file and only replace specific transactions")
print("  Option 3: Don't use pad directives (use explicit transactions instead)")
print("\nOur implementation uses Option 1 (skip auto-generated entries).")

print("\n" + "=" * 70)
print("TEST COMPLETE")
print("=" * 70)
