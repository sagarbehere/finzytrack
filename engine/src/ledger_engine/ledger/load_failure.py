"""What a load tells when Beancount's own load raises on a ledger (S0049's contract: the load returns, never raises;
BL-0123; S0098).

Beancount 3.2.0 raises out of its load, and bean-check fails the same way, when its decimal arithmetic cannot compute
an amount of a transaction (its quantize of a filled-in amount that needs more than 28 digits, a total cost or price
for a quantity of 0: `decimal.InvalidOperation`, `DivisionByZero`), when an entry a plugin made is one its own checks
cannot read (a `document` whose file name is not text: `TypeError` in `validate_documents_paths`), or when a plugin
fails while it is imported. The overlay load and the rebuild's real load turn such an exception into a refusal (`of`):
`number-failed` when a decimal error is raised while Beancount works on a transaction it names (so the text can say
"the transaction on line N"), `beancount-failed` for any other, with the file and line when known.

**Not Beancount's** (`beancounts`): a `MemoryError`, and an exception whose innermost frame is the engine's own code
(a fault of a port wrapper), stay the engine's (`engine.internal`): a refusal tells the user to fix the ledger, and
the rebuild does not try a refused ledger again (S0098 round 1, finance F8).

**Which entry.** Beancount's loops hold the entry they work on in a local named `entry` (booking's `_book`, each
validator's loop). The innermost frame of the exception's traceback that holds one whose metadata names an absolute
file and a line from 1 is named; else the root, with no line (a check written as one expression over every entry
holds none, and a plugin's own entry may name no ledger file). The frames are read only after the load failed; the
traceback is dropped with the exception.

A disk error (`OSError`) and an encrypted file met (`beancount_port.EncryptedFileMet`) are not Beancount failing on the
ledger: the callers keep their own paths for them and never pass them here.
"""

from __future__ import annotations

import decimal
from collections.abc import Mapping
from dataclasses import dataclass
from os import path
from types import FrameType, TracebackType
from typing import Final, Literal

type Reason = Literal["number-failed", "beancount-failed"]

_OURS: Final = path.dirname(path.dirname(path.abspath(__file__)))
"""The engine's own package folder (`ledger_engine/`)."""


@dataclass(frozen=True, slots=True)
class Failure:
    """Beancount's load failed: why, and the file (and line, when known) to name."""

    reason: Reason
    path: str
    line: int | None


def _place(tb: TracebackType | None) -> tuple[str, int] | None:
    frames: list[FrameType] = []
    while tb is not None:
        frames.append(tb.tb_frame)
        tb = tb.tb_next
    for frame in reversed(frames):
        held: object = frame.f_locals.get("entry")
        meta: object = getattr(held, "meta", None)
        if not isinstance(meta, Mapping):
            continue
        named: Mapping[object, object] = meta  # pyright: ignore[reportUnknownVariableType] (a plugin may set anything)
        filename, lineno = named.get("filename"), named.get("lineno")
        if isinstance(filename, str) and path.isabs(filename) and type(lineno) is int and lineno >= 1:
            return filename, lineno
    return None


def beancounts(error: BaseException) -> bool:
    """Whether `error`, raised out of one of Beancount's load steps, is Beancount's or a plugin's (a refusal), not the
    engine's own fault: not a `MemoryError`, and not raised in a frame of the engine's own code."""
    if isinstance(error, MemoryError):
        return False
    tb = error.__traceback__
    if tb is None:
        return True
    while tb.tb_next is not None:
        tb = tb.tb_next
    return not path.abspath(tb.tb_frame.f_code.co_filename).startswith(_OURS + path.sep)


def of(error: BaseException, root: str) -> Failure:
    """The refusal for `error`, raised by one of Beancount's load steps on the ledger rooted at `root`."""
    place = _place(error.__traceback__)
    if place is None:
        return Failure("beancount-failed", root, None)
    number = isinstance(error, decimal.DecimalException)
    return Failure("number-failed" if number else "beancount-failed", place[0], place[1])
