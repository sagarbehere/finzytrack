"""R4, the re-check after a save, inside the engine (`design/specs/engine-jobs.md` §6.7; ADR-0067 decision 3,
ADR-0010 guardrail 3 as first written; BL-0118; S0066, S0068).

A landed save leaves a `.closed` record: "saved", with the save's prediction (the adopted digest of the overlay load,
the post fingerprint). R4 compares that prediction with Beancount's own loader on the disk, and **gates nothing**: on
a difference the sidecar logs it and shows a notice; saving goes on (owner, 2026-10-31: "Yes, draft that ADR. I'm
approving it already, so go ahead and implement it."). It reads one record, named by its key folder and its name
(`<app_local_data>/local/<key>/journal/<name>.closed`), and **writes nothing**: the record stays `.closed`.

`check` (runs the ledger's plugins, so its engine is retired after it, `Answer.loaded`):
1. the record `.closed` and readable, else `gone` / `unreadable`; what Beancount reads of the record's ledger set
   (`beancount_reads`: the documents listings' undated names left out) equal to its post fingerprint, else
   `superseded` (the disk is no longer the saved state; an outside edit, not the prediction, would make a difference);
2. Beancount's real load (`beancount_port.reconcile_load`: no pickle cache, never the replica, no encrypted file
   decrypted) and its digest (`ledger.digest`);
3. the fingerprint again, else `superseded`; an engine build other than the record's cannot be compared, `superseded`
   (publishing that load is the mirror publish's, TODO(BL-0113)); digest equal: `pass`; else `differs`.

**The step rule** (S0068): `pass` and `differs` rest on the record `.closed`, readable and of this build, and the
disk's fingerprint equal to the record's post fingerprint before and after the real load; then on the digests' being
equal or not. `superseded` rests only on a moved fingerprint or another build: never on the digests. An encrypted file
met by the load (never decrypted) raises `R4Error`, and any port operation may raise `OSError`: R4 did not run, which
the sidecar logs; nothing is shown and nothing is gated.
"""

from __future__ import annotations

import gc
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from ledger_engine.fs.port import DirHandle, FileSystem
from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import digest as _digest
from ledger_engine.ledger import fingerprint as _fingerprint
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core.journal import Record, State
from ledger_engine.ledger.core.ledger_set import Fingerprint, Listing, UndecodableError, decode
from ledger_engine.ledger.handles import join
from ledger_engine.ledger.save import JOURNAL, LOCAL
from ledger_engine.tla import tla_action

type CheckOutcome = Literal["gone", "unreadable", "superseded", "pass", "differs"]

type Phase = Literal["checking_last_change"]
PHASE: Final[Phase] = "checking_last_change"


class R4Error(Exception):
    """R4 could not run to an outcome (its load met an encrypted file): logged by the sidecar, nothing shown."""


ENCRYPTED: Final = "an encrypted file met on the saved disk"


class Job(Protocol):
    def progress(self, phase: Phase) -> None: ...


def _observe(fp: Fingerprint) -> Fingerprint:
    return _fingerprint.observe(fp.ledger_set)


@dataclass(frozen=True, slots=True)
class Ports:
    """R4's effects: the file-system port (the record), the running engine's build, Beancount's real load of the
    disk (its digest), and the observation of a ledger set."""

    fs: FileSystem
    build: str
    load: Callable[[str], str]
    observe: Callable[[Fingerprint], Fingerprint] = _observe


def desktop(fs: FileSystem, build: str) -> Ports:
    """The real ports: Beancount's loader with no pickle cache (BL-0118), the garbage collector paused while it
    loads (ADR-0008 amendment), file names keyed on `fs`."""
    key_of = _digest.path_keys(fs)

    def load(root: str) -> str:
        collecting = gc.isenabled()
        gc.disable()
        try:
            entries, errors, options = port.reconcile_load(root)
            return _digest.of_result(entries, errors, options, key_of)
        finally:
            if collecting:
                gc.enable()

    return Ports(fs, build, load)


@dataclass(frozen=True, slots=True)
class Answer:
    """`outcome`; `loaded`: the ledger's plugins ran in this engine (retire it)."""

    outcome: CheckOutcome
    loaded: bool = False


def journal_dir(app_local_data: str, key: str) -> str:
    return join(join(join(app_local_data, LOCAL), key), JOURNAL)


@dataclass(frozen=True, slots=True)
class _Closed:
    record: Record
    fingerprint: Fingerprint


def _read_closed(jd: DirHandle, name: str) -> _Closed | Literal["gone", "unreadable"]:
    data = jd.read(journal.file_name(name, State.CLOSED))
    if data is None:
        return "gone"
    rec = journal.decode(data)
    if not isinstance(rec, Record) or rec.name != name:
        return "unreadable"
    try:
        fp = decode(rec.post_fingerprint.encode("ascii"))
    except (UnicodeEncodeError, UndecodableError):
        return "unreadable"
    return _Closed(rec, fp)


_DATED: Final = re.compile(r"(\d\d\d\d)-(\d\d)-(\d\d).(.*)")
"""`ops.documents.find_documents`' test of a file name (Beancount 3.2.0, ADR-0004): only a name that matches can
become a `document` entry."""


def _dated(listing: Listing) -> Listing:
    return None if listing is None else tuple((n, e) for n, e in listing if _DATED.match(n.rpartition("/")[2]))


def beancount_reads(fp: Fingerprint) -> Fingerprint:
    """`fp` without the documents-listing names Beancount never turns into an entry (`find_documents` skips a file
    whose name does not match `_DATED`; nothing else reads them). The listing keeps every name for the gate and the
    mirror (a superset, `fingerprint.py`); R4 compares only this part, so a `.DS_Store`, an editor's swap file or a
    sync tool's file in a documents folder does not leave the save unchecked (S0066 red team r1 F2)."""
    documents = tuple((d, _dated(listing)) for d, listing in fp.documents)
    return Fingerprint(fp.root, fp.files, fp.globs, documents, fp.document_files)


def _moved(ports: Ports, closed: _Closed) -> bool:
    """The disk is no longer the saved state: what Beancount reads of the record's ledger set differs from its post
    fingerprint."""
    return beancount_reads(ports.observe(closed.fingerprint)) != beancount_reads(closed.fingerprint)


@tla_action("R4_Load", "R4_Check")
def check(ports: Ports, app_local_data: str, key: str, name: str, job: Job) -> Answer:
    """The module's docstring: reads one record, writes nothing."""
    with ports.fs.open_dir(journal_dir(app_local_data, key)) as jd:
        closed = _read_closed(jd, name)
    if not isinstance(closed, _Closed):
        return Answer(closed)
    if _moved(ports, closed):  # step 1
        return Answer("superseded")
    job.progress(PHASE)
    try:
        got = ports.load(closed.fingerprint.root)  # step 2: Beancount's real loader (user code)
    except port.EncryptedFileMet:
        got = None
    if _moved(ports, closed):  # step 3
        return Answer("superseded", loaded=True)
    if got is None:
        raise R4Error(ENCRYPTED)
    if closed.record.engine_build != ports.build:
        return Answer("superseded", loaded=True)  # TODO(BL-0113): publish the real load
    return Answer("pass" if got == closed.record.adopted_digest else "differs", loaded=True)
