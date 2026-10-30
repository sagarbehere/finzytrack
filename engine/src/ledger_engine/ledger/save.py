"""The save job inside the engine (`design/specs/engine-jobs.md` §6.1 steps 1-7 and 9-11 around S0060's durable order,
§6.5, §6.6; ADR-0008 steps 4-6, ADR-0012 Q29, ADR-0013, ADR-0062 decisions 2 and 5; S0063).

`run(plan, ports, job)`, in one engine (§5.1: it runs the ledger's plugins, so its engine is retired after it):
1. **R4 first, if owed** (§6.1 step 1): this workspace's records re-read from disk (`records`): a `.closed` record
   means an R4 is owed. Until R4 exists (TODO(BL-0152)) it refuses `check_pending` (S0060's plan for this slice),
   writing nothing; a `.mismatch` refuses `check_failed`; an `.open` journal `unfinished_save`.
2. **Legacy-lock detection** (ADR-0062 decision 2; INV-26): every legacy `.write.lock` the plan names, deduplicated by
   `path_key`, tried once each without waiting (`LegacyLocks`); free: held to the end; absent: go on; held, or any
   error: `legacy_app_saving`, before any ledger-set lock.
3. **Ledger-set locks** (ADR-0012 Q29): every file of the plan's ledger set (the absent paths included) and every file
   the save writes, in `path_key` order, each waiting; `waiting_for_files` is shown only when one is not free at once.
4. **The gate, under the locks** (ADR-0008 step 4): the disk's fingerprint equals the plan's (else `ledger_changed`);
   the published mirror's equals the plan's (else `mirror_stale`, `WORKSPACE_NOT_READY {WARMING}`); the records again.
5. **The load** of the candidate (`overlay.load`, the contract's step 3; ADR-0011's one full load). A refused load is
   `load_refused`; judging its errors against the plan's is the contract's (TODO(BL-0106)).
6. **The re-check, under the locks** (§6.1 step 6; ADR-0013; the owner's TLC run `L_save_deep`): the disk's
   fingerprint equals the plan's **and** the disk equals what the load itself parsed (`ledger_set.as_before` of
   `Loaded.fingerprint`). So an include the load read in an edited form, reverted before this step, refuses; one
   edited and reverted after the load passes (the save builds only on bytes still on disk).
7. **The commit point:** `job.gate()` (§4: the sidecar's one table step answers `proceed` or ends the engine).
8. `writer.durable_commit` (§6.1 step 8), after the workspace's journal and backups folders exist.
9. `job.committed()` once it `Landed`.
10. The publish (§6.1 step 10) is not built: `Saved.published` is False (TODO(BL-0113)); the sidecar queues nothing
    yet (the rebuild is BL-0152's).
11. Every lock released on every path out, the legacy locks last; the engine is retired by the sidecar.

**The step rule.** Steps 1-7 write nothing but lock files (`app_local_data/locks/`, never deleted); every refusal
there says `unchanged` only after each file the save would write was hashed again and equals its pre bytes (§11's
rule). Step 8 has its own rule (`writer`). A Cancel ends the engine at the gate, before step 8. Python's garbage
collector is paused for the job (ADR-0008 amendment). A malformed plan raises `writer.BadPlanError` before step 1.

`outcome(...)` is §6.6's reading of the disk after a save's engine died: the job's journal folder `durable_sync`ed,
then its record's state, and with no record (or a `.part`) every target hashed again. The decision is the sidecar's
(`app.platform.engine_jobs.save.save_outcome`).
"""

from __future__ import annotations

import gc
import posixpath
import re
from collections.abc import Callable, Generator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from ledger_engine.fs.port import FileSystem, LegacyAbsent, LegacyHeld, LegacyLocks, Lock, Locks
from ledger_engine.ledger import fingerprint as _fingerprint
from ledger_engine.ledger import overlay
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core import paths as core_paths
from ledger_engine.ledger.core.journal import Record, State
from ledger_engine.ledger.core.ledger_set import Absent, Content, Fingerprint, as_before, digest
from ledger_engine.ledger.handles import join, sha256
from ledger_engine.ledger.policy import LoadPolicy
from ledger_engine.ledger.writer import (
    BadPlanError,
    Change,
    Commit,
    Landed,
    NotSaved,
    Reason,
    Step,
    Unfinished,
    durable_commit,
)

LOCAL: Final = "local"
JOURNAL: Final = "journal"
BACKUPS: Final = "backups"
LOCKS: Final = "locks"
NOT_COMPUTED: Final = "not_computed"
"""The closed record's adopted and parse digests until R4 defines them (TODO(BL-0152)): R4 cannot compare a
prediction it does not have, so it treats such a record as it treats an engine-build change (§6.7 step 1)."""
SESSION: Final = re.compile(r"[a-z0-9_]{1,64}")
"""A session id: lower case only, so two sessions never differ only in case (names derived from `<session>-<job>`
are compared case-folded on a case-insensitive volume; S0061 red team r3 overflow 4)."""

type Phase = Literal["waiting_for_files", "checking_change", "saving"]
type Paused = Literal["unfinished_save", "check_pending", "check_failed"]
type RefusedReason = Literal[
    "unfinished_save",  # an `.open` journal of this workspace (WRITES_PAUSED)
    "check_pending",  # an R4 is owed and could not run first (WRITES_PAUSED; until BL-0152, always)
    "check_failed",  # a `.mismatch` record (WRITES_PAUSED)
    "legacy_app_saving",  # a legacy install's write lock is held, or could not be tried (LEGACY_APP_SAVING)
    "ledger_changed",  # the disk moved since the plan, or during the load (LEDGER_EXTERNALLY_CHANGED)
    "mirror_stale",  # the published mirror is not the plan's (WORKSPACE_NOT_READY {WARMING})
    "load_refused",  # the commit's load refused the candidate (TODO(BL-0106): the contract's refusal)
]
GATING: Final = frozenset({State.OPEN, State.CLOSED, State.MISMATCH})


@dataclass(frozen=True, slots=True)
class SavePlan:
    """A confirmed plan (§2's `save`). `fingerprint`: the plan's, of the ledger set before the save; `mirror`: the
    digest of the published mirror's fingerprint (None: none is published); `changes`: each file as the ledger names
    it (absolute, normalized), its pre SHA-256 (None: the save creates it) and its post bytes; `legacy_locks`: the
    `data/.write.lock` of every legacy install whose config names a file of this ledger set (ADR-0062; the sidecar
    knows them from M3, BL-0026); `app_local_data`: the app's machine-local folder (journals, backups, locks)."""

    session: str
    job: str
    workspace_key: str
    lineage: str
    engine_build: str
    root: str
    changes: tuple[Change, ...]
    fingerprint: Fingerprint
    mirror: str | None
    legacy_locks: tuple[str, ...]
    app_local_data: str


class Job(Protocol):
    """What the save needs of its request (`protocol.server.JobContext`)."""

    def progress(self, phase: Phase) -> None: ...

    def gate(self) -> None: ...

    def committed(self) -> None: ...


type Load = Callable[[str, Mapping[str, bytes]], overlay.LoadResult]
type Observe = Callable[[Fingerprint], Fingerprint]


def _observe(fp: Fingerprint) -> Fingerprint:
    return _fingerprint.observe(fp.ledger_set)


@dataclass(frozen=True, slots=True)
class Ports:
    """The save's effects: the file-system port (every write), the ledger-set and legacy locks, and the load and the
    observation, which read the disk with the loader's own calls (`overlay`, `fingerprint`)."""

    fs: FileSystem
    locks: Locks
    legacy: LegacyLocks
    load: Load
    observe: Observe = _observe


def desktop_load(policy: LoadPolicy) -> Load:
    def load(root: str, candidates: Mapping[str, bytes]) -> overlay.LoadResult:
        return overlay.load(root, candidates, policy)

    return load


@dataclass(frozen=True, slots=True)
class Saved:
    """The closed record is durable (§6.1 step 8.5): the save has landed. `published`: the mirror was published from
    the load (TODO(BL-0113): never yet)."""

    record: Record
    published: bool = False


@dataclass(frozen=True, slots=True)
class Refused:
    """Refused before the commit point: nothing was written in a ledger folder, the journal or the backups. `unchanged`:
    every file the save would write was hashed again and equals its pre bytes (a created one: absent). `path`: the
    file the refusal is about, when there is one (the legacy lock, the record, the refused load's subject)."""

    reason: RefusedReason
    unchanged: bool
    path: str | None = None


type SaveOutcome = Saved | Refused | NotSaved | Unfinished


# -- the plan ---------------------------------------------------------------------------------------------------------


def _check(plan: SavePlan) -> dict[str, str | None]:
    """The plan against itself, before anything is read (`BadPlanError`); the pre hash of each change by its path."""
    if not SESSION.fullmatch(plan.session):
        raise BadPlanError("session_not_lower_case")
    if posixpath.normpath(plan.root) != plan.root or plan.root != plan.fingerprint.root:
        raise BadPlanError("root_not_the_fingerprints")
    states = dict(plan.fingerprint.files)
    pre: dict[str, str | None] = {}
    for c in plan.changes:
        if not c.path.startswith("/") or posixpath.normpath(c.path) != c.path or c.path in pre:
            raise BadPlanError("change_path")
        state = states.get(c.path)
        if c.pre is None and state not in (None, Absent()):
            raise BadPlanError("created_file_in_the_plan")  # the plan saw a file there
        if c.pre is not None and state != Content(c.pre):
            raise BadPlanError("pre_not_the_plans")  # a replaced file is in the plan's ledger set, at these bytes
        pre[c.path] = c.pre
    if not plan.changes:
        raise BadPlanError("no_changes")
    return pre


def journal_dir(plan: SavePlan) -> str:
    return join(join(join(plan.app_local_data, LOCAL), plan.workspace_key), JOURNAL)


# -- step 1 and the gate's records (§6.5) ------------------------------------------------------------------------------


def records(fs: FileSystem, app_local_data: str, workspace_key: str, lineage: str) -> tuple[Paused, str | None] | None:
    """This workspace's gating records, re-read from disk (§6.5): `.open`, `.closed` and `.mismatch` in any
    `local/*/journal/` whose record names this registry key or lineage id (a record in this key's own folder is this
    workspace's). The worst of them (`.open`, then `.mismatch`, then `.closed`) with its path, or None. Fail closed: a
    gating record that cannot be read counts as this workspace's; a journal folder that cannot be listed (other than
    absent) raises `OSError`, which the caller refuses as `unfinished_save` (it may hold an `.open` record)."""
    found: dict[State, str] = {}
    local = join(app_local_data, LOCAL)
    try:
        keys = fs.open_dir(local)
    except FileNotFoundError:
        return None
    with keys:
        for key in keys.names():
            folder = join(join(local, key), JOURNAL)
            try:
                jd = fs.open_dir(folder)
            except (FileNotFoundError, NotADirectoryError):
                continue
            with jd:
                for name in jd.names():
                    parsed = journal.parse_file_name(name)
                    if parsed is None or parsed[1] not in GATING:
                        continue
                    if key != workspace_key:
                        data = jd.read(name)
                        rec = journal.Unreadable("gone") if data is None else journal.decode(data)
                        if isinstance(rec, Record) and workspace_key != rec.workspace_key and lineage != rec.lineage:
                            continue  # another workspace's record
                    found.setdefault(parsed[1], join(folder, name))
    order: tuple[tuple[State, Paused], ...] = (
        (State.OPEN, "unfinished_save"),
        (State.MISMATCH, "check_failed"),
        (State.CLOSED, "check_pending"),
    )
    for state, paused in order:
        if state in found:
            return paused, found[state]
    return None


def _records_refusal(fs: FileSystem, plan: SavePlan) -> tuple[RefusedReason, str | None] | None:
    try:
        out = records(fs, plan.app_local_data, plan.workspace_key, plan.lineage)
    except OSError:
        return "unfinished_save", journal_dir(plan)
    if out is None:
        return None
    # TODO(BL-0152): a `.closed` record runs its R4 here first (§6.1 step 1); until then it refuses `check_pending`.
    return out


# -- steps 2 and 3: the locks -------------------------------------------------------------------------------------


class _LegacyRefused(Exception):
    def __init__(self, path: str) -> None:
        self.path = path


def _key(fs: FileSystem, path: str) -> str:
    return core_paths.path_key(fs.resolve(path))


@contextmanager
def _legacy_locks(ports: Ports, paths: tuple[str, ...]) -> Generator[None]:
    """ADR-0062 decision 2: each legacy lock once, without waiting, deduplicated by `path_key`, in key order."""
    by_key: dict[str, str] = {}
    for p in paths:
        try:
            by_key.setdefault(_key(ports.fs, p), p)
        except (OSError, ValueError):
            raise _LegacyRefused(p) from None
    with ExitStack() as held:
        for key in sorted(by_key):
            path = by_key[key]
            try:
                out = ports.legacy.try_lock(path)
            except OSError:
                raise _LegacyRefused(path) from None
            if isinstance(out, LegacyHeld):
                raise _LegacyRefused(path)
            if not isinstance(out, LegacyAbsent):
                held.callback(out.release)
        yield


@contextmanager
def _ledger_locks(ports: Ports, keys: list[str], job: Job) -> Generator[None]:
    """ADR-0012 Q29: each key's lock, in `path_key` order, waiting (§6.1 step 3)."""
    with ExitStack() as held:
        shown = False
        for key in sorted(set(keys)):
            lock: Lock | None = ports.locks.try_lock(key)
            if lock is None:
                if not shown:
                    job.progress("waiting_for_files")
                    shown = True
                lock = ports.locks.lock(key)
            held.callback(lock.release)
        yield


# -- the run -----------------------------------------------------------------------------------------------------------


def _unchanged(fs: FileSystem, changes: tuple[Change, ...]) -> bool:
    """Every file the save would write, hashed again, equals its pre bytes (a created one: absent); any failure says
    no (§11's rule)."""
    try:
        for c in changes:
            r = fs.resolve(c.path)
            if r.rest:
                now = None
            else:
                canonical = core_paths.canonical_path(r)
                folder, _, base = canonical.rpartition("/")
                with fs.open_dir(folder or "/") as h:
                    data = h.read(base)
                now = None if data is None else sha256(data)
            if now != c.pre:
                return False
    except (OSError, ValueError):
        return False
    return True


def _ensure_dir(fs: FileSystem, parent: str, name: str) -> str:
    path = join(parent, name)
    with fs.open_dir(parent) as h:
        if h.stat(name) is None:
            h.mkdir(name)
            h.sync()
    return path


def run(plan: SavePlan, ports: Ports, job: Job) -> SaveOutcome:
    """§6.1 steps 1-11 in this engine (the module's docstring)."""
    pre = _check(plan)
    collecting = gc.isenabled()
    gc.disable()
    try:
        return _run(plan, ports, job, pre)
    finally:
        if collecting:
            gc.enable()


def _run(plan: SavePlan, ports: Ports, job: Job, pre: Mapping[str, str | None]) -> SaveOutcome:
    def refused(reason: RefusedReason, path: str | None = None) -> Refused:
        return Refused(reason, _unchanged(ports.fs, plan.changes), path)

    owed = _records_refusal(ports.fs, plan)  # step 1
    if owed is not None:
        return refused(*owed)
    keys: list[str] = []
    for p in sorted({*(p for p, _ in plan.fingerprint.files), *pre}):
        try:
            keys.append(_key(ports.fs, p))
        except OSError:  # a folder that denies looking names up in it (§15): not saved, nothing written
            return NotSaved(Step.BACKUP, Reason.IO_ERROR, _unchanged(ports.fs, plan.changes), path=p)
    try:
        with _legacy_locks(ports, plan.legacy_locks), _ledger_locks(ports, keys, job):  # steps 2, 3 (11: released)
            return _under_locks(plan, ports, job, pre, refused)
    except _LegacyRefused as e:
        return refused("legacy_app_saving", e.path)


def _under_locks(
    plan: SavePlan,
    ports: Ports,
    job: Job,
    pre: Mapping[str, str | None],
    refused: Callable[..., Refused],
) -> SaveOutcome:
    if ports.observe(plan.fingerprint) != plan.fingerprint:  # step 4
        return refused("ledger_changed")
    if plan.mirror != digest(plan.fingerprint):
        return refused("mirror_stale")
    owed = _records_refusal(ports.fs, plan)
    if owed is not None:
        return refused(*owed)
    job.progress("checking_change")
    loaded = ports.load(plan.root, {c.path: c.post for c in plan.changes})  # step 5
    if isinstance(loaded, overlay.LoadRefused):
        return refused("load_refused", loaded.subject)
    if ports.observe(plan.fingerprint) != plan.fingerprint:  # step 6
        return refused("ledger_changed")
    if ports.observe(loaded.fingerprint) != as_before(loaded.fingerprint, pre):
        return refused("ledger_changed")
    job.gate()  # step 7: past it, the save finishes (§4)
    job.progress("saving")
    jd = journal_dir(plan)
    try:
        key_dir = _ensure_dir(ports.fs, _ensure_dir(ports.fs, plan.app_local_data, LOCAL), plan.workspace_key)
        _ensure_dir(ports.fs, key_dir, JOURNAL)
        bd = _ensure_dir(ports.fs, key_dir, BACKUPS)
    except OSError:
        return NotSaved(Step.BACKUP, Reason.IO_ERROR, _unchanged(ports.fs, plan.changes))
    commit = Commit(
        session=plan.session,
        job=plan.job,
        workspace_key=plan.workspace_key,
        lineage=plan.lineage,
        engine_build=plan.engine_build,
        post_fingerprint=digest(loaded.fingerprint),
        adopted_digest=NOT_COMPUTED,
        parse_digest=NOT_COMPUTED,
        changes=plan.changes,
        journal_dir=jd,
        backups_dir=bd,
    )
    out = durable_commit(ports.fs, commit)  # step 8
    if isinstance(out, Landed):
        job.committed()  # step 9
        # TODO(BL-0113): step 10, the mirror built from `loaded` and published under the locks.
        return Saved(out.record)
    return out


# -- §6.6: the outcome after an engine death ---------------------------------------------------------------------------

type RecordSeen = Literal["none", "part", "open", "closed", "done", "mismatch", "resolved", "kept", "unknown"]


@dataclass(frozen=True, slots=True)
class OutcomeFacts:
    """What the disk says about one save (§6.6), after its journal folder's sync: its record's state (`unknown`: the
    folder could not be synced or listed, or the record stands in two states); with no record or a `.part` (and after
    `.resolved`), `unchanged`: every target hashed again equals its pre bytes, and `path` the first that does not; with
    an `.open` record, `path`: a file the save was creating that stands there now (the undo will ask about it)."""

    record: RecordSeen
    unchanged: bool | None = None
    path: str | None = None
    errno: int | None = None


def _first_changed(fs: FileSystem, changes: tuple[Change, ...]) -> str | None:
    for c in changes:
        if not _unchanged(fs, (c,)):
            return c.path
    return None


def outcome(
    fs: FileSystem, app_local_data: str, workspace_key: str, session: str, job: str, changes: tuple[Change, ...]
) -> OutcomeFacts:
    """§6.6 (the module's docstring): the record's state only after a `durable_sync` of its folder (model finding F1:
    a rename made just before the death may not be durable yet)."""
    name = journal.record_name(session, job)
    folder = join(join(join(app_local_data, LOCAL), workspace_key), JOURNAL)
    states: list[State] = []
    try:
        with fs.open_dir(folder) as jd:
            jd.sync()
            for entry in jd.names():
                parsed = journal.parse_file_name(entry)
                if parsed is not None and parsed[0] == name:
                    states.append(parsed[1])
    except FileNotFoundError:
        pass  # no journal folder: the save never reached its journal
    except OSError as e:
        return OutcomeFacts("unknown", errno=e.errno)
    if len(states) > 1:
        return OutcomeFacts("unknown")
    seen: RecordSeen = "none" if not states else states[0].value
    if seen in ("none", "part", "resolved"):
        changed = _first_changed(fs, changes)
        return OutcomeFacts(seen, changed is None, changed)
    if seen in ("open", "kept"):
        # a file the save was creating that now stands there: the undo will ask about it (§6.4; spec §11's Reopen)
        return OutcomeFacts(seen, path=_first_changed(fs, tuple(c for c in changes if c.pre is None)))
    return OutcomeFacts(seen)
