"""The save job inside the engine (`design/specs/engine-jobs.md` §6.1 steps 1-4, 7, 9-11 around S0060's durable order,
§6.5, §6.6; ADR-0008 step 4, ADR-0013, ADR-0062 decision 5, ADR-0068 D1, D3, D4; S0063, S0070, S0071).

`run(plan, ports, job)`, in one engine. **The save does no Beancount load** (ADR-0068 D1, S0070): it runs none of the
ledger's plugins and trusts the plan, whose fingerprint is what the preview's load read (BL-0106); the sidecar still
retires its engine after it (the safe default). Its steps:
0. **An encrypted file is never written** (ADR-0059): a change whose name, or the canonical name it resolves to (a
   link), ends `.gpg` or `.asc` (any case; the overlay's own candidate rule, `overlay.ENCRYPTED_SUFFIXES`) refuses
   `encrypted_file`, before any ledger byte is written (the refusal re-hashes the targets to say "unchanged"). A link
   swapped in after the gate is met by the writer's own check of the resolved name (`writer.ENCRYPTED_SUFFIXES`).
1. **The records** (§6.1 step 1): an `.open` journal of this workspace, re-read from disk (`records`), refuses
   `unfinished_save`, writing nothing. A `.closed` record gates nothing (ADR-0067); no other record state is read.
2. *(Removed, ADR-0068 D4.)* The save tries no legacy lock: only Copy does (INV-26, BL-0026).
3. *(Removed, ADR-0068 D3.)* No per-file lock: the engine lock is the guard (the `save` op runs only in an engine that
   holds it, `OpSpec.needs_engine_lock`). Every path of the ledger set and every file the save writes is resolved
   first; one that cannot be (a folder that denies looking names up in it, §15) is `NotSaved(io_error)` naming it,
   with the re-hash; nothing was written.
4. **The gate** (ADR-0008 step 4), the last check before the commit point: the disk's fingerprint
   equals the plan's (else `ledger_changed`); the published mirror's equals the plan's (else `mirror_stale`,
   `WORKSPACE_NOT_READY {WARMING}`); the `.open` check again. Steps 5 and 6 (the save's own load and the re-check
   after it) are removed (ADR-0068 D1): an outside save after step 4 is met by the swap's displaced check.
7. **The commit point:** `job.gate()` (§4: the sidecar's one table step answers `proceed` or ends the engine).
8. `writer.durable_commit` (§6.1 step 8), after the workspace's journal and backups folders exist and every folder
   from `app_local_data` down to them has a durable entry (its parent synced, whether it was made now or not). The
   record carries no prediction (journal format 4).
9. `job.committed()` once it `Landed`.
10. The sidecar queues the background rebuild with Beancount's own loader (§6.7, `ledger.rebuild`); `Saved.published`
    stays False.
11. The engine is retired by the sidecar.

**The step rule.** Steps 0-7 write nothing; every refusal
there says `unchanged` only after each file the save would write was hashed again and equals its pre bytes (§11's
rule). Step 8 has its own rule (`writer`). A Cancel ends the engine at the gate, before step 8. Python's garbage
collector is paused for the job (ADR-0008 amendment). A malformed plan raises `writer.BadPlanError` before step 1, and
so does a plan whose name `<session>-<job>` already has a record or a backups folder in this key (§6.5 "One save, one
name": §6.6 tells a dead save's outcome by that name alone; the sidecar makes a new name for every save).

`outcome(...)` is §6.6's reading of the disk after a save's engine died: the job's journal folder `durable_sync`ed,
then its record's state, and with no record (or a `.part`) every target hashed again. The decision is the sidecar's
(`app.platform.engine_jobs.save.save_outcome`).
"""

from __future__ import annotations

import gc
import posixpath
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from ledger_engine.fs.port import FileSystem
from ledger_engine.ledger import fingerprint as _fingerprint
from ledger_engine.ledger import overlay
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core import paths as core_paths
from ledger_engine.ledger.core.journal import Record, State
from ledger_engine.ledger.core.ledger_set import Absent, Content, Fingerprint, digest
from ledger_engine.ledger.handles import join, sha256
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
SESSION: Final = re.compile(r"[a-z0-9_]{1,64}")
"""A session id: lower case only, so two sessions never differ only in case (names derived from `<session>-<job>`
are compared case-folded on a case-insensitive volume; S0061 red team r3 overflow 4)."""

type Phase = Literal["saving"]
type RefusedReason = Literal[
    "unfinished_save",  # an `.open` journal of this workspace (WRITES_PAUSED)
    "ledger_changed",  # the disk moved since the plan (LEDGER_EXTERNALLY_CHANGED)
    "mirror_stale",  # the published mirror is not the plan's (WORKSPACE_NOT_READY {WARMING})
    "encrypted_file",  # a change names an encrypted file (ADR-0059; the preview refuses it first, BL-0106)
]


@dataclass(frozen=True, slots=True)
class SavePlan:
    """A confirmed plan (§2's `save`). `fingerprint`: the plan's, of the ledger set before the save; `mirror`: the
    digest of the published mirror's fingerprint (None: none is published); `changes`: each file as the ledger names
    it (absolute, normalized), its pre SHA-256 (None: the save creates it) and its post bytes; `app_local_data`: the
    app's machine-local folder (journals, backups).
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
    app_local_data: str


class Job(Protocol):
    """What the save needs of its request (`protocol.server.JobContext`)."""

    def progress(self, phase: Phase) -> None: ...

    def gate(self) -> None: ...

    def committed(self) -> None: ...


type Observe = Callable[[Fingerprint], Fingerprint]


def _observe(fp: Fingerprint) -> Fingerprint:
    return _fingerprint.observe(fp.ledger_set)


@dataclass(frozen=True, slots=True)
class Ports:
    """The save's effects: the file-system port (every write) and the observation, which reads the disk with the
    loader's own calls (`fingerprint`)."""

    fs: FileSystem
    observe: Observe = _observe


@dataclass(frozen=True, slots=True)
class Saved:
    """The closed record is durable (§6.1 step 8.5): the save has landed. `published`: the save published the mirror
    itself (never: the rebuild after it does, §6.7)."""

    record: Record
    published: bool = False


@dataclass(frozen=True, slots=True)
class Refused:
    """Refused before the commit point: nothing was written in a ledger folder, the journal or the backups. `unchanged`:
    every file the save would write was hashed again and equals its pre bytes (a created one: absent). `path`: the
    file the refusal is about, when there is one (the `.open` journal, the encrypted file)."""

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


# -- step 0 and the paths ------------------------------------------------------------------------------------------


def _encrypted(fs: FileSystem, path: str) -> bool:
    """Step 0: the change's name, or the canonical name it resolves to (a link to a `.gpg` file, S0070 red team r1 F1),
    ends `.gpg` or `.asc` in any case. A name that cannot be resolved is the next steps' to refuse (an I/O error)."""
    if path.lower().endswith(overlay.ENCRYPTED_SUFFIXES):
        return True
    try:
        resolved = core_paths.canonical_path(fs.resolve(path))
    except OSError:
        return False
    return resolved.lower().endswith(overlay.ENCRYPTED_SUFFIXES)


def _key(fs: FileSystem, path: str) -> str:
    return core_paths.path_key(fs.resolve(path))


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
    """§6.5 "One save, one name": a record of any state named `<session>-<job>` in this key's journal
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


@tla_action("S_Locks", "S_Release")  # S_Locks: no effect since S0071 (ADR-0068 D3); S0072 cuts it (BL-0180)
def _run(plan: SavePlan, ports: Ports, job: Job, pre: Mapping[str, str | None]) -> SaveOutcome:
    def refused(reason: RefusedReason, path: str | None = None) -> Refused:
        return Refused(reason, _unchanged(ports.fs, plan.changes), path)

    for c in plan.changes:  # step 0 (ADR-0059): never an encrypted file, by its name or the name it resolves to
        if _encrypted(ports.fs, c.path):
            return refused("encrypted_file", c.path)
    open_journal = _open_journal(ports.fs, plan)  # step 1
    if open_journal is not None:
        return _paused(open_journal, ports, plan)

    def io_error(path: str | None) -> NotSaved:
        return NotSaved(Step.BACKUP, Reason.IO_ERROR, _unchanged(ports.fs, plan.changes), path=path)

    for p in sorted({*(p for p, _ in plan.fingerprint.files), *pre}):  # step 3: every path resolves
        try:
            _key(ports.fs, p)
        except OSError:  # a folder that denies looking names up in it (§15): not saved, nothing written
            return io_error(p)
    return _gated(plan, ports, job, refused, io_error)


@tla_action("S_Gate", "S_Load", "S_Recheck", "S_Commit", "S_Saved")  # S_Load, S_Recheck: no effect since S0070
def _gated(
    plan: SavePlan,
    ports: Ports,
    job: Job,
    refused: Callable[..., Refused],
    io_error: Callable[[str | None], NotSaved],
) -> SaveOutcome:
    if ports.observe(plan.fingerprint) != plan.fingerprint:  # step 4
        return refused("ledger_changed")
    # TODO(BL-0125): compare with the published generation read at this gate; until a mirror is published
    # (BL-0113) this compares two values of the plan, both sent with it (verifier r1 Q1).
    if plan.mirror != digest(plan.fingerprint):
        return refused("mirror_stale")
    open_journal = _open_journal(ports.fs, plan)
    if open_journal is not None:
        return _paused(open_journal, ports, plan)
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
        changes=plan.changes,
        journal_dir=jd,
        backups_dir=bd,
    )
    out = durable_commit(ports.fs, commit)  # step 8
    if isinstance(out, Landed):
        job.committed()  # step 9
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
