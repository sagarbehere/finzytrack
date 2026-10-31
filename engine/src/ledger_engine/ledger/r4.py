"""R4, the re-check after a save, inside the engine (`design/specs/engine-jobs.md` §6.5, §6.7; ADR-0008's amendments,
ADR-0010 guardrail 3 as amended by ADR-0064; BL-0118; S0066).

A landed save leaves a `.closed` record that says "saved" and that its R4 is owed; it pauses the next save of that
workspace (`ledger.save`'s step 1). R4 checks the save's prediction (the record's adopted digest, from the overlay
load) against Beancount's own loader on the disk. Each function below acts on **one** record, named by its key
folder and its name (`<app_local_data>/local/<key>/journal/<name>.<state>`), and only while that record is in the
state it expects, by an exclusive rename followed by a sync of the journal folder before it answers (§6.5: "every
rename … is followed by a sync of the journal directory before anything acts on the new state or shows it"):

- `check` (steps 1-3; runs the ledger's plugins, so its engine is retired after it): the record `.closed` and
  readable; no `.open` journal of this workspace (§6.5's match by key or lineage) and the disk's fingerprint of the
  record's ledger set equal to its post fingerprint, else `superseded`; then Beancount's real load
  (`beancount_port.reconcile_load`: no pickle cache, never the replica) and its digest (`ledger.digest`); then the
  fingerprint and the `.open` check again, else `superseded`. An engine build other than the record's, or a record
  without digests (`not_computed`), cannot be compared: `superseded_build` (the publish of that load is
  TODO(BL-0113)). Digest equal: `pass`. Different: `differs`, the record left `.closed` for `confirm`.
- `confirm` (step 4; parse-only, in a fresh engine that ran no user code): Beancount's own parse of the disk
  (`reconcile_parse`) and the replica's (`overlay.parse`, which also says which bytes it parsed), then the fingerprint
  once more (model observation O1: moved since step 3 → `superseded`, never a false mismatch from an edit made during
  the confirm). Both parse digests equal each other and the record's: `nondeterministic` (the plugins vary run to run:
  owner, 2026-10-26, "non-deterministic plugins do not block saving"). Anything else: `mismatch`, the record renamed
  `.mismatch`, saving paused.
- `attempt` (parse-only): one failure to run this record's R4 counted on disk (`journal.attempt_name`, created
  exclusively, the folder synced); the third renames the record `.mismatch` ("could not check"; ADR-0008's amendment of
  2026-10-27: a count, not a time).
- `resume` (parse-only): **Resume saving**, the one way out of `.mismatch`: renamed `.done`.

`pass`, `superseded`, `superseded_build` and `nondeterministic` rename the record `.done`. A record that is not
`.closed` when read (another job ended it, the user resumed) is `gone`: nothing is done. A record that cannot be read
is `unreadable`: the sidecar counts it as a failure to run, so a broken record ends "could not check" after three
tries, never a pass. Every port operation may raise `OSError` (a failure to run, counted by the sidecar).

**The step rule** (S0066's journal): `pass` rests on the record `.closed`, readable and of this build, no `.open`
journal, the fingerprint equal to the record's before and after the real load, and the load's digest equal to the
record's. `superseded` rests only on a moved fingerprint or an `.open` journal (or a build the prediction cannot be
compared with), never on digests. `mismatch` rests on a digest that differed with the fingerprint unchanged through
the confirm, and parse digests that do not all agree. Nothing here renames a `.mismatch` record but `resume`.
"""

from __future__ import annotations

import contextlib
import gc
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from ledger_engine.fs.port import DirHandle, FileSystem
from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import digest as _digest
from ledger_engine.ledger import fingerprint as _fingerprint
from ledger_engine.ledger import overlay
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core.journal import Record, State
from ledger_engine.ledger.core.ledger_set import Fingerprint, UndecodableError, decode
from ledger_engine.ledger.handles import join
from ledger_engine.ledger.policy import LoadPolicy
from ledger_engine.ledger.save import JOURNAL, LOCAL, NOT_COMPUTED, records
from ledger_engine.tla import tla_action

type CheckOutcome = Literal["gone", "unreadable", "superseded", "superseded_build", "pass", "differs"]
type ConfirmOutcome = Literal["gone", "unreadable", "superseded", "superseded_build", "nondeterministic", "mismatch"]
type AttemptOutcome = Literal["gone", "counted", "could_not_check"]
type ResumeOutcome = Literal["done", "gone"]

type Phase = Literal["checking_last_change"]
PHASE: Final[Phase] = "checking_last_change"


class R4Error(Exception):
    """R4 could not run to an outcome (the replica refused the ledger it accepted at the save): a failure to run,
    counted by the sidecar like an engine that stopped."""


class Job(Protocol):
    def progress(self, phase: Phase) -> None: ...


def _observe(fp: Fingerprint) -> Fingerprint:
    return _fingerprint.observe(fp.ledger_set)


@dataclass(frozen=True, slots=True)
class Ports:
    """R4's effects: the file-system port (the records), the running engine's build, Beancount's real load and parse
    of the disk (each a digest), the replica's parse, and the observation of a ledger set."""

    fs: FileSystem
    build: str
    load: Callable[[str], str]
    parse_beancount: Callable[[str], str]
    parse_replica: Callable[[str], overlay.Parsed | overlay.LoadRefused]
    observe: Callable[[Fingerprint], Fingerprint] = _observe


def desktop(fs: FileSystem, build: str, policy: LoadPolicy) -> Ports:
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

    def parse_beancount(root: str) -> str:
        entries, errors, options = port.reconcile_parse(root)
        return _digest.of_result(entries, errors, options, key_of)

    def parse_replica(root: str) -> overlay.Parsed | overlay.LoadRefused:
        return overlay.parse(root, policy, key_of)

    return Ports(fs, build, load, parse_beancount, parse_replica)


@dataclass(frozen=True, slots=True)
class Answer:
    """`outcome`; `loaded`: the ledger's plugins ran in this engine (retire it); `record`: the record's path after
    the op (None when gone); `backup`: the save's backup folder (None when the record cannot be read)."""

    outcome: str
    loaded: bool = False
    record: str | None = None
    backup: str | None = None


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


def _open_journal(fs: FileSystem, app_local_data: str, rec: Record) -> bool:
    """An `.open` journal of the record's workspace (§6.5's match by key or lineage). `OSError` when a journal folder
    cannot be listed: R4 does not run then (the save refuses `unfinished_save` on its own)."""
    out = records(fs, app_local_data, rec.workspace_key, rec.lineage)
    return out is not None and out[0] == "unfinished_save"


def _moved(ports: Ports, app_local_data: str, closed: _Closed) -> bool:
    return ports.observe(closed.fingerprint) != closed.fingerprint or _open_journal(
        ports.fs, app_local_data, closed.record
    )


def _cannot_compare(ports: Ports, rec: Record) -> bool:
    return rec.engine_build != ports.build or NOT_COMPUTED in (rec.adopted_digest, rec.parse_digest)


@tla_action("R4_Sync")
def _rename(jd: DirHandle, name: str, src: State, dst: State) -> bool:
    """`src` → `dst` exclusively, then the folder synced before anything acts on it (§6.5). False: no `src` (gone)."""
    try:
        jd.rename_excl(journal.file_name(name, src), journal.file_name(name, dst))
    except FileNotFoundError:
        return False
    jd.sync()
    return True


def _end(jd: DirHandle, closed: _Closed, outcome: str, dst: State, *, loaded: bool) -> Answer:
    name = closed.record.name
    if not _rename(jd, name, State.CLOSED, dst):
        return Answer("gone", loaded)
    return Answer(outcome, loaded, join(jd.path(), journal.file_name(name, dst)), closed.record.backup_dir)


@tla_action("R4_Start", "R4_Load", "R4_Check")
def check(ports: Ports, app_local_data: str, key: str, name: str, job: Job) -> Answer:
    """Steps 1-3 (the module's docstring)."""
    with ports.fs.open_dir(journal_dir(app_local_data, key)) as jd:
        closed = _read_closed(jd, name)
        if not isinstance(closed, _Closed):
            return Answer(closed)
        if _moved(ports, app_local_data, closed):  # step 1
            return _end(jd, closed, "superseded", State.DONE, loaded=False)
        job.progress(PHASE)
        got = ports.load(closed.fingerprint.root)  # step 2: Beancount's real loader (user code)
        if _moved(ports, app_local_data, closed):  # step 3
            return _end(jd, closed, "superseded", State.DONE, loaded=True)
        if _cannot_compare(ports, closed.record):
            return _end(jd, closed, "superseded_build", State.DONE, loaded=True)  # TODO(BL-0113): publish the load
        if got == closed.record.adopted_digest:
            return _end(jd, closed, "pass", State.DONE, loaded=True)
        return Answer("differs", True, join(jd.path(), journal.file_name(name, State.CLOSED)), closed.record.backup_dir)


@tla_action("R4_Confirm")
def confirm(ports: Ports, app_local_data: str, key: str, name: str, job: Job) -> Answer:
    """Step 4 (the module's docstring): parse-only."""
    with ports.fs.open_dir(journal_dir(app_local_data, key)) as jd:
        closed = _read_closed(jd, name)
        if not isinstance(closed, _Closed):
            return Answer(closed)
        rec = closed.record
        if _moved(ports, app_local_data, closed):
            return _end(jd, closed, "superseded", State.DONE, loaded=False)
        if _cannot_compare(ports, rec):
            return _end(jd, closed, "superseded_build", State.DONE, loaded=False)
        job.progress(PHASE)
        beancount = ports.parse_beancount(closed.fingerprint.root)
        replica = ports.parse_replica(closed.fingerprint.root)
        if isinstance(replica, overlay.LoadRefused):
            raise R4Error(replica.reason)
        states = dict(closed.fingerprint.files)
        read_other = any(states.get(p) != s for p, s in replica.files.items())
        if read_other or _moved(ports, app_local_data, closed):  # O1: an edit during the confirm
            return _end(jd, closed, "superseded", State.DONE, loaded=False)
        if beancount == replica.digest == rec.parse_digest:
            return _end(jd, closed, "nondeterministic", State.DONE, loaded=False)
        return _end(jd, closed, "mismatch", State.MISMATCH, loaded=False)


def _attempts(jd: DirHandle, name: str) -> int:
    return sum(1 for n in range(1, journal.ATTEMPTS + 1) if jd.stat(journal.attempt_name(name, n)) is not None)


def attempt(fs: FileSystem, app_local_data: str, key: str, name: str) -> Answer:
    """One failure to run this record's R4 counted (§6.7); the third renames it `.mismatch` ("could not check")."""
    with fs.open_dir(journal_dir(app_local_data, key)) as jd:
        if jd.stat(journal.file_name(name, State.CLOSED)) is None:
            return Answer("gone")
        n = _attempts(jd, name)
        if n < journal.ATTEMPTS:
            with contextlib.suppress(FileExistsError):  # an earlier count whose folder sync was cut: it counts once
                jd.create(journal.attempt_name(name, n + 1), b"", 0o600)
            jd.sync()
            n += 1
        backup = _backup(jd, name)
        if n < journal.ATTEMPTS:
            return Answer("counted", record=join(jd.path(), journal.file_name(name, State.CLOSED)), backup=backup)
        if not _rename(jd, name, State.CLOSED, State.MISMATCH):
            return Answer("gone")
        return Answer("could_not_check", record=join(jd.path(), journal.file_name(name, State.MISMATCH)), backup=backup)


def _backup(jd: DirHandle, name: str) -> str | None:
    data = jd.read(journal.file_name(name, State.CLOSED))
    rec = None if data is None else journal.decode(data)
    return rec.backup_dir if isinstance(rec, Record) else None


def resume(fs: FileSystem, app_local_data: str, key: str, name: str) -> Answer:
    """Resume saving (§6.5, §6.7): this `.mismatch` record renamed `.done`, synced; nothing else is ever lifted."""
    with fs.open_dir(journal_dir(app_local_data, key)) as jd:
        if not _rename(jd, name, State.MISMATCH, State.DONE):
            return Answer("gone")
        return Answer("done", record=join(jd.path(), journal.file_name(name, State.DONE)))
