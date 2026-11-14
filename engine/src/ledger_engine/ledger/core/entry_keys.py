"""Canonical entry keys and error keys: what the write-safety contract compares (ADR-0008 steps 2 and 3; S0116).

- **An entry's key** (step 2) is Beancount's printer's text of the parsed entry (`beancount_port.format_entry`), which
  leaves out the entry's location (`filename`, `lineno`): an entry the splice moved down a line keeps its key, and one
  whose text changed (an amount, its decimals, a flag, a tag, a metadata line) gets another. Example: `10.50 USD`
  and `10.5 USD` are one number but two keys, since the text the user wrote differs.
- **An error's key** (step 3) is its type, its entry and its message, each without locations, so a splice that shifts
  lines does not make an old error new. It is read from a `ledger_errors` row (`mirror.core.rows.error_rows`), the
  form in which the baseline is published (the published mirror generation's `ledger_errors`): the candidate's
  errors are keyed from the same rows. The entry is the row's JSON (`rows.json_value`) with every `filename` and
  `lineno` key of its metadata dropped (the printer's text cannot be had from a published row; the JSON keeps every
  field and every number exactly). The message has the locations Beancount puts in a message dropped: the
  `'filename': ...` and `'lineno': N` of an entry or posting printed into it (a booking error prints the posting),
  and the error's own file followed by `:N`. Example: "No position matches ... 'lineno': 7 ..." and the same error
  one line lower ("... 'lineno': 8 ...") have one key.
- Two errors with one key are counted (`error_keys` is a multiset): a second copy of an old error is new.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable
from decimal import Decimal
from typing import Any, Final

from ledger_engine.ledger import beancount_port as port
from ledger_engine.mirror.core.rows import Row

LOCATION: Final = frozenset({"filename", "lineno"})
_PRINTED_LINENO: Final = re.compile(r"'lineno': \d+")  # an entry's or posting's metadata printed into a message
_PRINTED_FILENAME: Final = re.compile(r"'filename': '(?:[^'\\]|\\.)*'")


def entry_key(entry: port.Directive) -> str:
    """The entry's canonical key: the printer's text, without its location."""
    return port.format_entry(entry)


def key_of_text(text: str, path: str) -> str | None:
    """The key of the one entry `text` holds, parsed as the file `path` (its name decides a `document` entry's path);
    None when it holds no entry, several, or a parser error."""
    entries, errors, _ = port.parse_string(text.encode(), path)
    return entry_key(entries[0]) if len(entries) == 1 and not errors else None


def _without_location(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _without_location(v) for k, v in value.items() if k not in LOCATION}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list):
        return [_without_location(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


def message_without_locations(message: str, source_file: str | None) -> str:
    out = _PRINTED_LINENO.sub("'lineno': _", message)
    out = _PRINTED_FILENAME.sub("'filename': _", out)
    if source_file:
        out = re.sub(re.escape(source_file) + r":\d+", source_file + ":_", out)
    return out


def error_key(row: Row) -> str:
    """The key of one `ledger_errors` row (`id`, `source_file`, `line_number`, `message`, `entry_json`,
    `error_type`): its type, its entry and its message without locations, as one JSON text."""
    _, source_file, _, message, entry_json, error_type = row
    entry = None if entry_json is None else _without_location(json.loads(str(entry_json), parse_float=Decimal))
    text = message_without_locations(str(message), None if source_file is None else str(source_file))
    return json.dumps([str(error_type), entry, text], sort_keys=True, ensure_ascii=False, default=str)  # a Decimal


def error_keys(rows: Iterable[Row]) -> Counter[str]:
    """The multiset of the rows' keys."""
    return Counter(error_key(r) for r in rows)


def new_errors(rows: Iterable[Row], baseline: Counter[str]) -> list[tuple[str, Row]]:
    """The rows (with their keys) whose key the baseline lacks, as a multiset: of a key's rows, the first as many as
    the baseline has are old, the rest new."""
    left = Counter(baseline)
    out: list[tuple[str, Row]] = []
    for row in rows:
        key = error_key(row)
        if left[key] > 0:
            left[key] -= 1
        else:
            out.append((key, row))
    return out


def digest(key: str) -> str:
    """A key's SHA-256, as the preview names an error (its text holds ledger data)."""
    return hashlib.sha256(key.encode()).hexdigest()
