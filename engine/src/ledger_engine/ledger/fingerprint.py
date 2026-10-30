"""Observing a ledger set on disk (S0051; the value and its digest: `core.ledger_set`).

The observation asks the disk the loader's own questions, with the loader's own calls, so equal fingerprints mean
the next load reads the same inputs: `os.path.exists` then the bytes for a file (`loader._parse_recursive`);
`glob.glob(search, recursive=True)` for each `include`, plain paths too, under the string the loader globs (same: for
a plain path it asks `lexists` of the path as written, before the loader normalizes it); `os.path.exists` then
`os.walk(followlinks=True)` for a documents root and `os.path.exists` for each name found (`ops.documents.find_
documents` through `account.walk`, then `verify_document_files_exist`); `os.path.exists` for the file of each
`document` entry Beancount checks (`verify_document_files_exist`). The overlay load uses the same functions while it
loads (`overlay.load`), so the fingerprint it records and a later observation cannot drift apart. The inventory of every
read a Beancount 3.2.0 load makes, and why each is here or irrelevant, is in the S0051 journal's Hand-off.

A documents root's listing holds every file name under it, not only the dated names in account folders that
Beancount turns into entries: a superset cannot miss what Beancount reads, and an extra file costs at most one
rebuild or one re-planned save.

Not observed yet: the source files of plugins from outside the engine's packages (TODO(BL-0124)).

The save's gate and re-check call it inside the save job (`ledger.save`, S0063); the open's freshness check and R4
will inside theirs (TODO(BL-0152)).
"""

from __future__ import annotations

import errno
import glob
import hashlib
import os
from os import path

from ledger_engine.ledger.core.ledger_set import (
    Absent,
    Content,
    FileState,
    Fingerprint,
    LedgerSet,
    Unreadable,
)


def content(data: bytes) -> Content:
    return Content(hashlib.sha256(data).hexdigest())


def matches(pattern: str) -> list[str]:
    """A glob include's matches, in the order the loader parses them."""
    return glob.glob(pattern, recursive=True)


def file_state(p: str) -> FileState:
    """What the next load would find at `p`: no file (nothing, or a link to nothing), these bytes, or a read that
    fails."""
    if not path.exists(p):
        return Absent()
    try:
        with open(p, "rb") as f:
            return Content(hashlib.file_digest(f, "sha256").hexdigest())
    except OSError as e:
        return Unreadable(errno.errorcode.get(e.errno, type(e).__name__) if e.errno else type(e).__name__)


def listing(root: str) -> dict[str, bool] | None:
    """A documents root's file names, relative to it, each with whether it leads to a file; None when the root does
    not exist (Beancount's error case)."""
    if not path.exists(root):
        return None
    names: dict[str, bool] = {}
    for folder, _dirs, files in os.walk(root, followlinks=True):
        rel = folder[len(root) + 1 :]
        for name in files:
            names[path.join(rel, name) if rel else name] = path.exists(path.join(folder, name))
    return names


def document_file(p: str) -> bool:
    """Whether a `document` entry's file exists, as `ops.documents.verify_document_files_exist` asks."""
    return path.exists(p)


def observe(ledger_set: LedgerSet) -> Fingerprint:
    """The ledger set as the disk has it now."""
    return Fingerprint.of(
        ledger_set.root,
        {p: file_state(p) for p in ledger_set.files},
        {g: matches(g) for g in ledger_set.globs},
        {d: listing(d) for d in ledger_set.documents},
        {p: document_file(p) for p in ledger_set.document_files},
    )
