"""The save job inside the engine (`design/specs/engine-jobs.md` §6.1 steps 1-7 and 9-11 around S0060's durable order,
§6.5, §6.6; ADR-0008 steps 4-6, ADR-0012 Q29, ADR-0013, ADR-0062 decisions 2 and 5; S0063).

`run(plan, ports, job)`, in one engine (§5.1: it runs the ledger's plugins, so its engine is retired after it):
1. **The records** (§6.1 step 1): an `.open` journal of this workspace, re-read from disk (`records`), refuses
   `unfinished_save`, writing nothing. A `.closed` record gates nothing (ADR-0067: R4 compares after the save and
   never pauses saving; S0068); no other record state is read.
2. **Legacy-lock detection** (ADR-0062 decision 2; INV-26): every legacy `.write.lock` the plan names, deduplicated by
   `path_key`, tried once each without waiting (`LegacyLocks`); free: held to the end; absent: go on; held, or any
   error: `legacy_app_saving`, before any ledger-set lock.
3. **Ledger-set locks** (ADR-0012 Q29): every file of the plan's ledger set (the absent paths included) and every file
   the save writes, in `path_key` order, each waiting; `waiting_for_files` is shown only when one is not free at once.
4. **The gate, under the locks** (ADR-0008 step 4): the disk's fingerprint equals the plan's (else `ledger_changed`);
   the published mirror's equals the plan's (else `mirror_stale`, `WORKSPACE_NOT_READY {WARMING}`); the `.open` check
   again.
5. **The load** of the candidate (`overlay.load`, the contract's step 3; ADR-0011's one full load). A refused load is
   `load_refused`; judging its errors against the plan's is the contract's (TODO(BL-0106)). Then the prediction R4
   checks (§6.7, S0066): the adopted digest of the load's result (`ledger.digest`), before step 6, so the re-check
   stays right before the commit point (no parse digest since S0067: BL-0173). The load is the save's preview of
   Beancount's result (ADR-0067 decision 2): its errors are advice, never a refusal here.
6. **The re-check, under the locks** (§6.1 step 6; ADR-0013; the owner's TLC run `L_save_deep`): the disk's
   fingerprint equals the plan's **and** the disk equals what the load itself parsed (`ledger_set.as_before` of
   `Loaded.fingerprint`). So an include the load read in an edited form, reverted before this step, refuses; one
   edited and reverted after the load passes (the save builds only on bytes still on disk).
7. **The commit point:** `job.gate()` (§4: the sidecar's one table step answers `proceed` or ends the engine).
8. `writer.durable_commit` (§6.1 step 8), after the workspace's journal and backups folders exist and every folder
   from `app_local_data` down to them has a durable entry (its parent synced, whether it was made now or not). The
   closed record carries the load's post fingerprint as its canonical encoding (R4 observes that ledger set; the
   created files added to the documents listings they fall in, `ledger_set.as_after`) and the adopted digest.
9. `job.committed()` once it `Landed`.
10. The publish (§6.1 step 10) is not built: `Saved.published` is False (TODO(BL-0113)); the rebuild a failed
    publish queues is BL-0152 part 2's. The sidecar queues the record's R4 (S0066).
11. Every lock released on every path out, the legacy locks last; the engine is retired by the sidecar.

**The step rule.** Steps 1-7 write nothing but lock files (`app_local_data/locks/`, never deleted); every refusal
there says `unchanged` only after each file the save would write was hashed again and equals its pre bytes (§11's
rule). Step 8 has its own rule (`writer`). A Cancel ends the engine at the gate, before step 8. Python's garbage
collector is paused for the job (ADR-0008 amendment). A malformed plan raises `writer.BadPlanError` before step 1, and
so does a plan whose name `<session>-<job>` already has a record or a backups folder in this key (§6.5 "One save, one
name": §6.6 tells a dead save's outcome by that name alone; the sidecar makes a new name for every save). An I/O error
taking a ledger-set lock, or one the load meets (an include it cannot read), is `NotSaved(io_error)` naming the file,
with the re-hash; nothing was written.

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
from ledger_engine.ledger import digest as _digest
from ledger_engine.ledger import fingerprint as _fingerprint
from ledger_engine.ledger import overlay
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core import paths as core_paths
from ledger_engine.ledger.core.journal import Record, State
from ledger_engine.ledger.core.ledger_set import Absent, Content, Fingerprint, as_after, as_before, digest, encode
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
from ledger_engine.tla import tla_action

LOCAL: Final = "local"
JOURNAL: Final = "journal"
BACKUPS: Final = "backups"
LOCKS: Final = "locks"
SESSION: Final = re.compile(r"[a-z0-9_]{1,64}")
"""A session id: lower case only, so two sessions never differ only in case (names derived from `<session>-<job>`
are compared case-folded on a case-insensitive volume; S0061 red team r3 overflow 4)."""

type Phase = Literal["waiting_for_files", "checking_change", "saving"]
type RefusedReason = Literal[
    "unfinished_save",  # an `.open` journal of this workspace (WRITES_PAUSED)
    "legacy_app_saving",  # a legacy install's write lock is held, or could not be tried (LEGACY_APP_SAVING)
    "ledger_changed",  # the disk moved since the plan, or during the load (LEDGER_EXTERNALLY_CHANGED)
    "mirror_stale",  # the published mirror is not the plan's (WORKSPACE_NOT_READY {WARMING})
    "load_refused",  # the commit's load refused the candidate (TODO(BL-0106): the contract's refusal)
]


@dataclass(frozen=True, slots=True)
class SavePlan:
    """A confirmed plan (§2's `save`). `fingerprint`: the plan's, of the ledger set before the save; `mirror`: the
    digest of the published mirror's fingerprint (None: none is published); `changes`: each file as the ledger names
    it (absolute, normalized), its pre SHA-256 (None: the save creates it) and its post bytes; `legacy_locks`: the
    `data/.write.lock` of every legacy install whose config names a file of this ledger set (ADR-0062; the sidecar
    knows them from M3, BL-0026); `app_local_data`: the app's machine-local folder (journals, backups, locks).
    `session`-`job` names this save alone (§6.5 "One save, one name"; the sidecar makes a new job part for every save):
    a name that already has a record or a backups folder in this key raises `BadPlanError("name_taken")`."""

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
    """The commit's load: the overlay under `policy`."""

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
    file the refusal is about, when there is one (the legacy lock, the `.open` journal, the refused load's subject)."""

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


def records(fs: FileSystem, app_local_data: str, workspace_key: str, lineage: str) -> str | None:
    """This workspace's `.open` journal, re-read from disk (§6.5): one in any `local/*/journal/` whose record names
    this registry key or lineage id (one in this key's own folder is this workspace's); its path, or None. Fail closed:
    an `.open` journal that cannot be read counts as this workspace's; a journal folder that cannot be listed (other
    than absent) raises `OSError`, which the caller refuses as `unfinished_save` (it may hold an `.open` record). No
    other state gates a save (ADR-0067; S0068)."""
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
                    if parsed is None or parsed[1] is not State.OPEN:
                        continue
                    if key != workspace_key:
                        data = jd.read(name)
                        rec = journal.Unreadable("gone") if data is None else journal.decode(data)
                        if isinstance(rec, Record) and workspace_key != rec.workspace_key and lineage != rec.lineage:
                            continue  # another workspace's journal
                    return join(folder, name)
    return None


def _open_journal(fs: FileSystem, plan: SavePlan) -> str | None:
    """§6.1 steps 1 and 4: the path of this workspace's `.open` journal (the journal folder itself when it cannot be
    listed: fail closed), or None."""
    try:
        return records(fs, plan.app_local_data, plan.workspace_key, plan.lineage)
    except OSError:
        return journal_dir(plan)


# -- steps 2 and 3: the locks -------------------------------------------------------------------------------------


class _LegacyRefused(Exception):
    def __init__(self, path: str) -> None:
        self.path = path


class _LockFailed(Exception):
    """An I/O error taking the ledger-set lock of `path` (red team r1 L2)."""

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


@tla_action("S_Locks")
@contextmanager
def _ledger_locks(ports: Ports, keys: Mapping[str, str], job: Job) -> Generator[None]:
    """ADR-0012 Q29: each key's lock, in `path_key` order, waiting (§6.1 step 3). `keys`: each key's file (named if
    its lock cannot be taken: `_LockFailed`)."""
    with ExitStack() as held:
        shown = False
        for key in sorted(keys):
            try:
                lock: Lock | None = ports.locks.try_lock(key)
                if lock is None:
                    if not shown:
                        job.progress("waiting_for_files")
                        shown = True
                    lock = ports.locks.lock(key)
            except OSError:
                raise _LockFailed(keys[key]) from None
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
    """The folder `name` in `parent`, its entry durable: `parent` is synced whether or not the folder was made now. A
    folder that was there already may be the mkdir of an earlier engine that died before its sync; a power loss could
    then drop it, with the `.open` journal or the backups in it, while the ledger files stay swapped (round 1, verifier
    F1). A sync with nothing to flush costs nothing (§6.2)."""
    path = join(parent, name)
    with fs.open_dir(parent) as h:
        if h.stat(name) is None:
            h.mkdir(name)
        h.sync()
    return path


def _name_taken(fs: FileSystem, plan: SavePlan) -> bool:
    """§6.5 "One save, one name": a record of any state (or an R4 count) named `<session>-<job>` in this key's journal
    folder, or a backups folder of that name. Names are compared case-folded (a case-insensitive volume). A folder that
    is absent holds nothing; one that cannot be read is step 1's to refuse (`unfinished_save`)."""
    name = journal.record_name(plan.session, plan.job).casefold()
    key_dir = join(join(plan.app_local_data, LOCAL), plan.workspace_key)
    try:
        with fs.open_dir(join(key_dir, JOURNAL)) as jd:
            if any(entry.casefold().startswith(f"{name}.") for entry in jd.names()):
                return True
    except OSError:
        pass
    try:
        with fs.open_dir(join(key_dir, BACKUPS)) as bd:
            return bd.stat(name) is not None
    except OSError:
        return False


def _paused(path: str, ports: Ports, plan: SavePlan) -> Refused:
    return Refused("unfinished_save", _unchanged(ports.fs, plan.changes), path)


def run(plan: SavePlan, ports: Ports, job: Job) -> SaveOutcome:
    """§6.1 steps 1-11 in this engine (the module's docstring)."""
    pre = _check(plan)
    if _name_taken(ports.fs, plan):
        raise BadPlanError("name_taken")
    collecting = gc.isenabled()
    gc.disable()
    try:
        return _run(plan, ports, job, pre)
    finally:
        if collecting:
            gc.enable()


@tla_action("S_Release")
def _run(plan: SavePlan, ports: Ports, job: Job, pre: Mapping[str, str | None]) -> SaveOutcome:
    def refused(reason: RefusedReason, path: str | None = None) -> Refused:
        return Refused(reason, _unchanged(ports.fs, plan.changes), path)

    open_journal = _open_journal(ports.fs, plan)  # step 1
    if open_journal is not None:
        return _paused(open_journal, ports, plan)

    def io_error(path: str | None) -> NotSaved:
        return NotSaved(Step.BACKUP, Reason.IO_ERROR, _unchanged(ports.fs, plan.changes), path=path)

    keys: dict[str, str] = {}
    for p in sorted({*(p for p, _ in plan.fingerprint.files), *pre}):
        try:
            keys.setdefault(_key(ports.fs, p), p)
        except OSError:  # a folder that denies looking names up in it (§15): not saved, nothing written
            return io_error(p)
    try:
        with _legacy_locks(ports, plan.legacy_locks), _ledger_locks(ports, keys, job):  # steps 2, 3 (11: released)
            return _under_locks(plan, ports, job, pre, refused, io_error)
    except _LegacyRefused as e:
        return refused("legacy_app_saving", e.path)
    except _LockFailed as e:
        return io_error(e.path)


@tla_action("S_Gate", "S_Load", "S_Recheck", "S_Commit", "S_Saved")
def _under_locks(
    plan: SavePlan,
    ports: Ports,
    job: Job,
    pre: Mapping[str, str | None],
    refused: Callable[..., Refused],
    io_error: Callable[[str | None], NotSaved],
) -> SaveOutcome:
    if ports.observe(plan.fingerprint) != plan.fingerprint:  # step 4
        return refused("ledger_changed")
    # TODO(BL-0125): compare with the published generation read under the locks; until a mirror is published
    # (BL-0113) this compares two values of the plan, both sent before the locks were taken (verifier r1 Q1).
    if plan.mirror != digest(plan.fingerprint):
        return refused("mirror_stale")
    open_journal = _open_journal(ports.fs, plan)
    if open_journal is not None:
        return _paused(open_journal, ports, plan)
    job.progress("checking_change")
    try:
        loaded = ports.load(plan.root, {c.path: c.post for c in plan.changes})  # step 5
    except OSError as e:  # an include it cannot read (red team r1 L2): before the gate, nothing written
        return io_error(e.filename if isinstance(e.filename, str) else None)
    if isinstance(loaded, overlay.LoadRefused):
        return refused("load_refused", loaded.subject)
    # R4's prediction (§6.7), before the re-check
    adopted = _digest.of_result(loaded.entries, loaded.errors, loaded.options, _digest.path_keys(ports.fs))
    if ports.observe(plan.fingerprint) != plan.fingerprint:  # step 6
        return refused("ledger_changed")
    if ports.observe(loaded.fingerprint) != as_before(loaded.fingerprint, pre):
        return refused("ledger_changed")
    job.gate()  # step 7: past it, the save finishes (§4)
    job.progress("saving")
    jd = journal_dir(plan)
    try:
        # every folder from the app's own down, its entry durable before the first write (round 1, verifier F1)
        app_parent, _, app_name = plan.app_local_data.rstrip("/").rpartition("/")
        ald = _ensure_dir(ports.fs, app_parent or "/", app_name)
        key_dir = _ensure_dir(ports.fs, _ensure_dir(ports.fs, ald, LOCAL), plan.workspace_key)
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
        post_fingerprint=encode(as_after(loaded.fingerprint, pre)).decode("ascii"),
        adopted_digest=adopted,
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

type RecordSeen = Literal["none", "part", "open", "closed", "resolved", "kept", "unknown"]


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
