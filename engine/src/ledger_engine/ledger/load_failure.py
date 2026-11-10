"""What a load tells when Beancount's own load raises on a ledger (S0049's contract: the load returns, never raises;
BL-0123; S0098).

Beancount 3.2.0 raises out of its load, and bean-check fails the same way, when its 28-digit arithmetic cannot go on
(an amount of more than 28 digits whose other leg it must fill in: `decimal.InvalidOperation` from its quantize), and
when an entry a plugin made is one its own checks cannot read (a `document` whose file name is not text: `TypeError`
in `validate_documents_paths`), or a plugin fails while it is imported. The overlay load and the rebuild's real load
turn such an exception into a refusal (`of`): `number-failed` for an error of Beancount's decimal arithmetic,
`beancount-failed` for any other, with the file and line of the entry Beancount was working on.

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
from typing import Literal

type Reason = Literal["number-failed", "beancount-failed"]


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


def of(error: BaseException, root: str) -> Failure:
    """The refusal for `error`, raised by one of Beancount's load steps on the ledger rooted at `root`."""
    reason: Reason = "number-failed" if isinstance(error, decimal.DecimalException) else "beancount-failed"
    place = _place(error.__traceback__)
    if place is None:
        return Failure(reason, root, None)
    return Failure(reason, place[0], place[1])
