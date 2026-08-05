#!/usr/bin/env python3
"""Locate ledger entries whose metadata cannot be JSON-serialized.

Diagnoses the mirror-export failure

    sub-export 'postings' failed: keys must be str, int, float, bool or None, not <T>

which comes from ``SQLiteExporter._metadata_to_json``: it sanitizes metadata
*values* but not metadata *keys*, so any dict-valued metadata whose inner keys
are not primitives aborts the whole export.

Self-contained on purpose — it imports only ``beancount``, no ``app.*`` — so it
can be handed to a user running the packaged app.

Usage:
    python scripts/find_bad_metadata.py /path/to/main.beancount
"""

import json
import sys
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from beancount import loader
from beancount.core import data


def convert(value: Any) -> Any:
    """Mirror of SQLiteExporter._convert_value_to_json_serializable."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, dict):
        return {k: convert(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [convert(item) for item in value]
    return str(value)


def bad_keys(value: Any, path: str = ""):
    """Yield (path, key, key_type) for every non-primitive dict key, recursively."""
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, (str, int, float, bool, type(None))):
                yield (path or "<root>", k, type(k).__name__)
            yield from bad_keys(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, (list, tuple, set, frozenset)):
        for i, item in enumerate(value):
            yield from bad_keys(item, f"{path}[{i}]")


def check(meta, label: str, where: str) -> bool:
    if not meta:
        return True
    try:
        json.dumps({k: convert(v) for k, v in meta.items()})
        return True
    except TypeError as e:
        print(f"\n{where}")
        print(f"  {label} metadata is not serializable: {e}")
        for path, key, ktype in bad_keys(meta):
            print(f"    offending key {key!r} (type {ktype}) at meta path {path}")
        for k, v in meta.items():
            print(f"    {k!r} = {type(v).__name__}: {v!r}")
        return False


def main(path: str) -> int:
    entries, errors, _options = loader.load_file(path)
    print(f"Loaded {len(entries)} entries, {len(errors)} parse errors from {path}")

    failures = 0
    for entry in entries:
        meta = entry.meta or {}
        where = f"{meta.get('filename', '?')}:{meta.get('lineno', '?')}  {entry.date} {type(entry).__name__}"
        if isinstance(entry, data.Transaction):
            where += f'  "{entry.payee or ""}" "{entry.narration or ""}"'
        if not check(meta, "entry", where):
            failures += 1
        if isinstance(entry, data.Transaction):
            for posting in entry.postings:
                pmeta = posting.meta or {}
                pwhere = (
                    f"{pmeta.get('filename', meta.get('filename', '?'))}:"
                    f"{pmeta.get('lineno', '?')}  posting {posting.account} "
                    f"(txn at {meta.get('filename', '?')}:{meta.get('lineno', '?')})"
                )
                if not check(pmeta, "posting", pwhere):
                    failures += 1

    print(f"\n{failures} unserializable metadata dict(s) found.")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
