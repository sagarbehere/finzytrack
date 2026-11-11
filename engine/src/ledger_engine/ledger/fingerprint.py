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

The modules a load's imports looked up (`ledger.imports`, S0099, BL-0124) are asked again of the import system's path
search with the same folders (`beancount_port.find_modules`, fresh folder listings, nothing imported or run), the
file found is hashed and a package's folders listed by importable names (`module`): the plugin code the next load would
run.

A copy streams its sources with `chunks` and checks each with `file_state` (S0081). The save's gate calls it inside
the save job (`ledger.save`), the rebuild after a save before and after its load (`ledger.rebuild`, S0070); the open's
freshness check will inside its job (TODO(BL-0152)).
"""

from __future__ import annotations

import errno
import glob
import hashlib
import importlib.machinery
import os
import stat
from collections.abc import Iterator
from os import path
from typing import Final

from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger.core.ledger_set import (
    Absent,
    Content,
    FileState,
    Fingerprint,
    LedgerSet,
    Module,
    PackageListing,
    Unreadable,
)


def content(data: bytes) -> Content:
    return Content(hashlib.sha256(data).hexdigest())


def matches(pattern: str) -> list[str]:
    """A glob include's matches, in the order the loader parses them."""
    return glob.glob(pattern, recursive=True)


def file_state(p: str) -> FileState:
    """What the next load would find at `p`: no file (nothing, or a link to nothing), these bytes, or a read that
    fails. Read with `chunks`, so a FIFO or another special file swapped in never blocks the observation (the save's
    gate before its commit point, S0102, BL-0245): it is `Unreadable("EINVAL")`, as for the copy since S0081."""
    if not path.exists(p):
        return Absent()
    h = hashlib.sha256()
    try:
        for chunk in chunks(p):
            h.update(chunk)
    except OSError as e:
        return Unreadable(errno.errorcode.get(e.errno, type(e).__name__) if e.errno else type(e).__name__)
    return Content(h.hexdigest())


def read_regular(p: str) -> bytes:
    """The whole bytes of the regular file at `p` (links followed), never blocking on a FIFO or another special file
    swapped in: anything but a regular file raises `OSError` (`EINVAL`), as a failed read does (S0102, BL-0245). The
    overlay load and the rebuild's warnings read ledger files with it."""
    return b"".join(chunks(p))


def chunks(p: str, size: int = 1 << 20) -> Iterator[bytes]:
    """The bytes of the regular file at `p`, as the loader opens it (links followed), `size` at a time: a copy streams
    its sources with it (S0081), never holding a document whole. Opened without blocking, so a FIFO swapped in never
    hangs the job (S0081 r1 F5): anything but a regular file raises `OSError` (`EINVAL`), as `open` and `read` do."""
    fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "not a regular file", p)
        os.set_blocking(fd, True)
        while chunk := f.read(size):
            yield chunk


def regular(p: str) -> bool:
    """Whether `p` is a regular file (links followed): a copy copies only those from a documents folder (S0081 r1 F5;
    a FIFO, a socket or a device there is left out)."""
    try:
        return stat.S_ISREG(os.stat(p).st_mode)
    except OSError:
        return False


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


_IMPORTABLE: Final = tuple(importlib.machinery.all_suffixes())  # `.py`, `.pyc` and the extension module suffixes


def _module_name(name: str) -> str | None:
    """The module a file name would import as (`inspect.getmodulename`: a source, bytecode or extension module suffix
    removed), when it is a Python identifier; else None."""
    for suffix in sorted(_IMPORTABLE, key=len, reverse=True):
        if name.endswith(suffix):
            stem = name[: -len(suffix)]
            return stem if stem.isidentifier() else None
    return None


def _is_package(folder: str) -> bool:
    """Whether a subfolder is a package `pkgutil.iter_modules` yields: it holds an `__init__` module."""
    try:
        with os.scandir(folder) as entries:
            return any(_module_name(e.name) == "__init__" for e in entries)
    except OSError:
        return False


def package_listing(folder: str) -> PackageListing:
    """A package folder's importable names, sorted: each module file whose name is a Python identifier, and each
    subfolder `name/` whose name is one and that is a package (`__init__` inside), as `pkgutil.iter_modules(__path__)`
    yields them (S0099 r1, red team F1; r2, verifier F3: no `.#x.py`, `._x.py`, `.git/`, `__pycache__/` or data folder);
    None when it cannot be listed. Other files, and the folders above the package (a `pythonpath` folder), are not
    listed."""
    try:
        with os.scandir(folder) as entries:
            names: list[str] = []
            for e in entries:
                if e.is_dir():
                    if e.name.isidentifier() and _is_package(e.path):
                        names.append(e.name + "/")
                elif _module_name(e.name) is not None:
                    names.append(e.name)
    except OSError:
        return None
    return tuple(sorted(names))


def module(lookup: port.ModuleLookup) -> Module | None:
    """The path search's answer as the fingerprint holds it: None when not found, else where, with the state of the
    file the loader reads (`file_state`: its bytes' SHA-256, or why there are none) and, for a package, the listing of
    each of its folders."""
    if not lookup.found:
        return None
    state = None if lookup.file is None else file_state(lookup.file)
    listings = None if lookup.locations is None else tuple(package_listing(d) for d in lookup.locations)
    return Module(lookup.origin, lookup.locations, state, listings)


def observe(ledger_set: LedgerSet) -> Fingerprint:
    """The ledger set as the disk has it now."""
    return Fingerprint.of(
        ledger_set.root,
        {p: file_state(p) for p in ledger_set.files},
        {g: matches(g) for g in ledger_set.globs},
        {d: listing(d) for d in ledger_set.documents},
        {p: document_file(p) for p in ledger_set.document_files},
        {(m.name, m.path): module(m) for m in port.find_modules(ledger_set.modules)},
    )
