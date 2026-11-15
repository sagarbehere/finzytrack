"""The engine jobs' load, read and observation ports over the in-memory file system (`tests/fakes/memfs.py`), for the
write core's sequence model (`test_write_sequence_model.py`; ARCHITECTURE.md §8.2; S0121).

**The load is the product's own overlay load** (`ledger_engine.ledger.overlay.load`), with its two names that touch
the disk, `overlay.path` (`os.path`) and `overlay.fp` (`ledger.fingerprint`), pointed at the fake for the call only
(`unittest.mock.patch.object`, undone when the load returns or raises). Beancount itself is not patched: it parses
the bytes the overlay hands it (`parse_string`), then sorts, books, runs its plugins and validates as in every load.
So the entries the model checks are Beancount's, the candidate overlay, the `not-reached` refusal and the load's own
fingerprint (`Loaded.fingerprint`, from which the plan's fingerprint is made) are the product's. The rebuild's real
load (`beancount_port.real_load`, Beancount's `loader._load` reading the real disk) is replaced by the same overlay
load with nothing in its overlay, whose equality with Beancount's loader is pinned on the real disk by
`test_overlay.py` (guardrails 1 and 2) and `test_parity_corpus.py`.

**The observation** (`observe`) asks the fake what `fingerprint.observe` asks the disk, for the ledger sets the model
makes: each file's bytes (or absent) and each `include` as a plain path (matched when the file exists).

**Fidelity limit** (what these ports never exercise; each is covered on the real disk by the tests named):
- glob `include`s (only plain paths: `ValueError` on a pattern): `test_overlay.py`, `test_fingerprint.py`,
  `test_rebuild.py`;
- plugins other than Beancount's own, and the module lookups of their imports: `test_plugin_fingerprint.py`,
  `test_rebuild.py`, `test_open.py`;
- documents roots and `document` files (`ValueError` when a set names one): `test_fingerprint.py`, `test_overlay.py`;
- encrypted files and the load policy's refusals: `test_overlay.py`, `test_open.py`;
- links, special files (a FIFO) and unreadable files in the ledger: `test_fingerprint.py`, `test_rebuild.py`,
  `test_save.py`;
- Beancount's own loader as the rebuild runs it (`real_load`): the overlay load stands in for it (above);
- the module lookups of Beancount's own plugins: they are imported before the model's first load (`_warm`), so no
  load here records one, where the engine's (one load per process) does: `test_plugin_fingerprint.py`.
"""

from __future__ import annotations

import glob
import hashlib
import posixpath
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from functools import cache
from types import SimpleNamespace
from unittest import mock

from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import fingerprint as real_fp
from ledger_engine.ledger import overlay, precision
from ledger_engine.ledger import preview as pv
from ledger_engine.ledger.beancount_port import Directive, OptionsMap, ParseResult
from ledger_engine.ledger.core.ledger_set import Absent, Content, FileState, Fingerprint, LedgerSet
from ledger_engine.ledger.policy import DESKTOP, LoadPolicy
from tests.fakes.memfs import MemFS

type Hook = Callable[[str], None]
"""Called before each port call with its name (`load`, `observe`, `read`): the model's crash point counts them."""


def _state(fs: MemFS, p: str) -> FileState:
    data = fs.file(p)
    return Absent() if data is None else Content(hashlib.sha256(data).hexdigest())


def _plain(pattern: str) -> str:
    if glob.has_magic(pattern):
        msg = f"the fake load follows plain includes only: {pattern}"
        raise ValueError(msg)
    return pattern


def observe(fs: MemFS, ledger_set: LedgerSet) -> Fingerprint:
    """`fingerprint.observe` over the fake, for files and plain-path includes (the module's fidelity limit)."""
    if ledger_set.documents or ledger_set.document_files or ledger_set.modules:
        msg = f"the fake observation reads no documents and no modules: {ledger_set}"
        raise ValueError(msg)
    return Fingerprint.of(
        ledger_set.root,
        {p: _state(fs, p) for p in ledger_set.files},
        {g: [g] if fs.file(_plain(g)) is not None else [] for g in ledger_set.globs},
        {},
        {},
    )


def _read(fs: MemFS, p: str) -> bytes:
    data = fs.file(p)
    if data is None:
        raise FileNotFoundError(2, "no such file", p)
    return data


@contextmanager
def _on(fs: MemFS) -> Generator[None]:
    """The overlay's disk names pointed at the fake while the load runs (the module's docstring)."""

    def exists(p: str) -> bool:
        return fs.file(p) is not None

    def matches(pattern: str) -> list[str]:
        return [pattern] if fs.file(_plain(pattern)) is not None else []

    def read_regular(p: str) -> bytes:
        return _read(fs, p)

    def no_documents(_: str) -> object:
        msg = "the fake load lists no documents root"
        raise ValueError(msg)

    path = SimpleNamespace(
        isabs=posixpath.isabs,
        normpath=posixpath.normpath,
        join=posixpath.join,
        dirname=posixpath.dirname,
        splitext=posixpath.splitext,
        abspath=posixpath.abspath,
        exists=exists,
    )
    fp = SimpleNamespace(
        read_regular=read_regular,
        content=real_fp.content,
        matches=matches,
        listing=no_documents,
        document_file=no_documents,
        regular=exists,
    )
    with mock.patch.object(overlay, "path", path), mock.patch.object(overlay, "fp", fp):
        yield


@cache
def _warm() -> None:
    """One load before the model's first, so Beancount's own plugin modules are imported (the module cache answers
    later loads, which then record no module lookup: `ledger.imports`). In the engine each job loads in a fresh
    process, so every load records them; here no load does, and the observation refuses a set that names one."""
    fs = MemFS()
    fs.outside_mkdirs("/warm")
    fs.outside_write("/warm/main.bean", b"2026-01-01 open Assets:A\n")
    with _on(fs):
        overlay.load("/warm/main.bean", {}, DESKTOP)


def load(fs: MemFS, root: str, candidates: Mapping[str, bytes], policy: LoadPolicy) -> overlay.LoadResult:
    """The overlay load over the fake (the module's docstring)."""
    _warm()
    with _on(fs):
        return overlay.load(root, candidates, policy)


def real_load(fs: MemFS, root: str, parsed: list[str]) -> ParseResult:
    """The rebuild's load over the fake: the overlay load with nothing in its overlay (the module's docstring);
    `parsed` receives the files it parsed. A refusal cannot happen on the model's ledgers and fails the test."""
    _warm()
    # The rebuild records its load's imports itself (`imports.recording` around `ports.load`), and a recording
    # cannot nest: the overlay's load inside its own decimal context, without the overlay's recording.
    with _on(fs), port.beancount_context():
        out = overlay._load(root, {}, DESKTOP, overlay._Seen())  # pyright: ignore[reportPrivateUsage]
    assert isinstance(out, overlay.Loaded), out
    parsed.extend(p for p, s in out.fingerprint.files if isinstance(s, Content))
    return out.entries, out.errors, out.options


def warnings(
    fs: MemFS, entries: list[Directive], options: OptionsMap, parsed: Sequence[str]
) -> list[precision.RoundingWarning]:
    """`precision.of_disk` over the fake: the files parsed, read again."""
    files = {p: data for p in parsed if (data := fs.file(p)) is not None}
    return precision.warnings(files, entries, options)


def preview_ports(fs: MemFS, store: object, hook: Hook) -> pv.Ports:
    def load_(root: str, candidates: Mapping[str, bytes], policy: LoadPolicy) -> overlay.LoadResult:
        hook("load")
        return load(fs, root, candidates, policy)

    def observe_(ledger_set: LedgerSet) -> Fingerprint:
        hook("observe")
        return observe(fs, ledger_set)

    def read_(p: str) -> bytes:
        hook("read")
        return _read(fs, p)

    return pv.Ports(load=load_, observe=observe_, read=read_, fs=fs, store=store)  # pyright: ignore[reportArgumentType]
