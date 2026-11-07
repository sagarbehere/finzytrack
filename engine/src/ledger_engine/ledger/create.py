"""The `create` job inside the engine (`design/specs/engine-jobs.md` §1 "Commit point", §4, §8.2; ADR-0064 P2,
ADR-0063 decision 1, ADR-0008, ADR-0017, ADR-0067 decision 2; S0080).

`run(plan, ports, job)` writes a new workspace folder: the files the plan names (the starter ledger and anything else
in the folder) and, last, the `.finzytrack/` folder (the manifest and the workspace's tool files). A folder is a
workspace only once it holds `.finzytrack/` (ADR-0017), so a crash, a failure or a Cancel before step 7 never leaves a
half-made workspace. Every byte of a create is Finzytrack's own text. Its steps:

1. **The target** (`_target`): absent, then made with an exclusive `mkdir` and its parent synced; or an existing
   empty folder (no entry at all). A non-empty folder (`not_empty`), something else at the path (`not_a_folder`) or a
   missing parent folder (`no_parent`) is refused before anything is written, and so is a folder the system would
   not let us write in (`permission`: the save's folder rule, `handles.folder_blocked`, on the folder the job writes
   in).
3. **Exclusive writes** (`_write`, `_sync`): every folder made with an exclusive `mkdir`, every file with
   `DirHandle.create` (exclusive; its data synced), then every folder synced, deepest first, the target last. A name
   that is taken stops the job (`taken`) and the entry there is left as it is; other names that appear are left
   alone. Every folder is reached through `handles.Handles` (§6.3's path guard: one descriptor, its path checked at
   every use; a folder that moved is `path_moved`).
4. **Read-back** (`_read_back`): every file holds what the job wrote, by its SHA-256 and length, read at most that
   length plus one byte (`read_back`, naming the file).
5. **The load check** (`_load_check`): Beancount's own load of the root in the target (`beancount_port.real_load`,
   never decrypting), so relative paths resolve where they will live. It must run (`load_failed`), report no error
   (`load_errors`: all of the text is Finzytrack's, so an error means our text is wrong, ADR-0067 decision 2's
   exception) and parse only files the job wrote (`load_outside`).
6. **The commit point:** `job.gate()` (§4's one table step answers `proceed` or ends the engine).
7. **`.finzytrack/` last** (`_manifest`): written as `.finzytrack.ft-<job>/` (its folders made, its files created and
   synced, each of its folders synced, each of its files read back through the path guard), renamed to `.finzytrack`
   with an exclusive rename (on a volume without it: a check that the name is free, then a plain rename; §6.8's
   accepted instant), the target synced, and `.finzytrack` read back at the target's path: **created** only for the
   plan's bytes (else `read_back`). A `.finzytrack` that stands there at the rename (`manifest_taken`) is left as it
   is. An error the job cannot place once the rename may have happened is raised, and so is a folder moved since the
   rename: the sidecar reads the outcome from disk (`outcome`).

Step 2 (reading sources) and a copy's step 5 are `ledger_engine.ledger.copy`'s (S0081), through `Steps`. **Every stop
removes what this run made, and only that** (`_cleanup`; owner, 2026-11-07, the middle path): a name whose exclusive
create or `mkdir` succeeded in this run, still the same entry (a file's identity read from the create's own descriptor,
a folder's by a `stat` right after its `mkdir`) and, for a file, still holding exactly the job's bytes (its SHA-256 and
length, a bounded read); a folder only by `rmdir`; the target folder last, when the job made it. Whatever fails a check,
a name whose identity could not be read, a name whose create failed otherwise than `EEXIST` but that stands there now,
and the target folder the job made when it cannot be removed (`.`) are kept and named (`NotCreated.left`); another
program's entry at a name the job never made is neither removed nor named. None when the cleanup could not finish.

`outcome(fs, plan)` is §1's "resolved from disk" for a create whose engine died: the target `durable_sync`ed first, so
what it reads is what a power loss keeps, then whether `.finzytrack` holds the plan's bytes (`ours`), something else
(`foreign`) or nothing (`absent`), and every entry at the job's names, whatever it holds (`_at_our_names`). It
removes nothing and writes nothing but the sync. Every read that decides whether a file is the job's reads at most
the job's bytes for it plus one (`DirHandle.read`'s `limit`).

A malformed plan raises `writer.BadPlanError` before anything is read (a name the job reserves, a path outside the
target, two names one volume could take for one, an encrypted file name: ADR-0059). Python's garbage collector is
paused for the job (ADR-0008 amendment).
"""

from __future__ import annotations

import errno as _errno
import gc
import hashlib
import posixpath
import re
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from ledger_engine.fs.port import DirHandle, FileSystem, Kind, MalformedPathError, check_name
from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import fingerprint, overlay
from ledger_engine.ledger.core import paths as core_paths
from ledger_engine.ledger.handles import Handles, PathMovedError, folder_blocked, join
from ledger_engine.ledger.writer import BadPlanError

MANIFEST_DIR: Final = ".finzytrack"
TEMP_INFIX: Final = ".ft-"
FILE_MODE: Final = 0o644
JOB: Final = re.compile(r"[a-z0-9_]{1,64}")
TAKEN: Final = frozenset({_errno.EEXIST, _errno.ENOTEMPTY, _errno.EISDIR, _errno.ENOTDIR})
"""What an exclusive create, `mkdir` or rename answers when a name it writes is taken."""

type Phase = Literal["saving", "checking_change"]
type Reason = Literal[
    "not_empty",  # the target holds an entry (step 1)
    "not_a_folder",  # something other than a folder stands at the target
    "no_parent",  # the folder the target goes in does not exist
    "permission",  # the system would not let us write in the folder (`path`; `detail`: the ACL entry)
    "taken",  # a name the job writes was taken meanwhile (`path`)
    "read_back",  # a file is not what the job wrote (`path`)
    "load_failed",  # the load check could not run
    "load_errors",  # Beancount reported errors (`detail`: how many)
    "load_outside",  # the load parsed a file the job did not write (`path`)
    "manifest_taken",  # a `.finzytrack` stood there at step 7
    "path_moved",  # a folder of the job's moved (§6.3)
    "io_error",  # the system answered an error (`errno`, `path`)
    # a copy's own (`ledger_engine.ledger.copy`, S0081):
    "legacy_saving",  # an old Finzytrack app's write lock is held, or could not be tried (`path`: the lock file)
    "recovery_blocked",  # an `.open` journal names a source (`path`), or a journal could not be read (`detail`)
    "source_missing",  # the source's root does not exist (`path`)
    "source_unreadable",  # a source file could not be read (`path`, `errno`)
    "source_failed",  # the source's load could not run
    "encrypted",  # the source holds an encrypted ledger file (`path`; ADR-0059)
    "source_changed",  # a source changed while it was copied (`path`)
    "cannot_rewrite",  # a line naming another file could not be pointed at the copy (`path`, `detail`: the line)
    "cannot_place",  # a source's name cannot be written in the copy's folder (`path`, `detail`: why)
    "load_differs",  # the copy does not load the same as the source (`path`, `detail`: the line, or what differs)
    "plugin_modules",  # the source's load imported a module of its own (`path`: where from; BL-0202)
]
type ManifestSeen = Literal["ours", "foreign", "absent", "unknown"]


@dataclass(frozen=True, slots=True)
class Streamed:
    """A file copied from `source` (an absolute path, read as the loader reads it), streamed in chunks (S0081: a copy
    never holds a document whole): `sha256`, the bytes it must hold (a ledger file: what the source's load parsed), or
    None (a document: whatever its source holds, recorded as it is copied)."""

    source: str
    sha256: str | None


@dataclass(frozen=True, slots=True)
class NewFile:
    """One file: its path relative to the folder it is written in (POSIX, no `.` or `..`) and its bytes, or the source
    it is streamed from (a copy's)."""

    path: str
    data: bytes | Streamed


@dataclass(frozen=True, slots=True)
class CreatePlan:
    """A confirmed create: the job's name (`.finzytrack.ft-<job>`), the target folder (absolute), the workspace's
    files relative to it, which of them is the ledger's root, the files of `.finzytrack/` relative to it, and the
    folders to make even when no file goes in them (a copy's documents root: Beancount reports a missing one, S0081
    r1)."""

    job: str
    target: str
    files: tuple[NewFile, ...]
    root: str
    manifest: tuple[NewFile, ...]
    folders: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Created:
    """`.finzytrack/` holds the plan's bytes and is durable, over files that were synced and read back. `folder` is
    the plan's target as requested, in every answer and after a death alike (S0080 r1 RT6)."""

    folder: str


@dataclass(frozen=True, slots=True)
class NotCreated:
    """No `.finzytrack/` of ours stands in the folder (the plan's target as requested). `left`: what the job left in
    it, by path relative to it, because it could not be sure each is only its own (`Run._cleanup`; owner,
    2026-11-07, the middle path); empty when it left nothing of its own (a refusal of step 1 wrote nothing); None when
    the folder could not be cleaned up or listed (the user is asked to check it)."""

    reason: Reason
    folder: str
    left: tuple[str, ...] | None
    path: str | None = None
    detail: str | None = None
    errno: int | None = None


type CreateOutcome = Created | NotCreated


@dataclass(frozen=True, slots=True)
class LoadResult:
    """What the load check reads of Beancount's load: how many errors, and every file it parsed (absolute)."""

    errors: int
    parsed: tuple[str, ...]


def _real_load(root: str) -> LoadResult:
    """Beancount's own load of `root` (user code: the sidecar retires the engine after the job)."""
    entries, errors, options = port.real_load(root)
    del entries
    return LoadResult(len(errors), (str(options["filename"]), *(str(f) for f in options["include"])))


@dataclass(frozen=True, slots=True)
class Ports:
    """The create's effects: the file-system port (every write), Beancount's load (step 5) and, for a copy, the reader
    of a source file (`fingerprint.chunks`)."""

    fs: FileSystem
    load: Callable[[str], LoadResult] = _real_load
    source: Callable[[str], Iterator[bytes]] = fingerprint.chunks


class Job(Protocol):
    """What the create needs of its request (`protocol.server.JobContext`)."""

    def progress(self, phase: Phase) -> None: ...

    def gate(self) -> None: ...


# -- the plan ---------------------------------------------------------------------------------------------------------


def _folded(path: str) -> str:
    """A path as a case-insensitive volume compares it (§6.3's caseless matching)."""
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", path).casefold())


def _check_files(files: tuple[NewFile, ...], *, top_reserved: bool) -> None:
    if not files:
        raise BadPlanError("no_files")
    seen: dict[str, str] = {}
    dirs: set[str] = set()
    for f in files:
        p = f.path
        if not p or p.startswith("/") or posixpath.normpath(p) != p:
            raise BadPlanError("file_path")
        try:
            parts = [check_name(c) for c in p.split("/")]
        except MalformedPathError:
            raise BadPlanError("file_path") from None
        top = _folded(parts[0])
        if top_reserved and (top == MANIFEST_DIR or top.startswith(MANIFEST_DIR + TEMP_INFIX)):
            raise BadPlanError("reserved_name")
        if isinstance(f.data, bytes) and p.lower().endswith(overlay.ENCRYPTED_SUFFIXES):
            raise BadPlanError("encrypted_file")  # ADR-0059: never written (a copied document is copied byte for byte)
        key = _folded(p)
        if key in seen:
            raise BadPlanError("two_files_one_name")
        seen[key] = p
        dirs.update(_folded("/".join(parts[:i])) for i in range(1, len(parts)))
    if dirs & set(seen):
        raise BadPlanError("file_and_folder_one_name")


def check_base(plan: CreatePlan) -> None:
    """The job's name, the target and `.finzytrack/`'s files (`BadPlanError`): what a copy's request names before its
    files are known (S0081)."""
    if not JOB.fullmatch(plan.job):
        raise BadPlanError("job_name")
    t = plan.target
    if not t.startswith("/") or t == "/" or posixpath.normpath(t) != t or "\0" in t:
        raise BadPlanError("target")
    _check_files(plan.manifest, top_reserved=False)
    if any(not isinstance(f.data, bytes) for f in plan.manifest):
        raise BadPlanError("manifest_file")  # `.finzytrack/`'s files are always Finzytrack's own text


def check_plan(plan: CreatePlan) -> None:
    """The plan against itself, before anything is read or written (`BadPlanError`)."""
    check_base(plan)
    _check_files(plan.files, top_reserved=True)
    if plan.folders:  # each a path a file could have, and not a file's
        _check_files(tuple(NewFile(f, b"") for f in plan.folders), top_reserved=True)
        if {_folded(f) for f in plan.folders} & {_folded(f.path) for f in plan.files}:
            raise BadPlanError("file_and_folder_one_name")
    if plan.root not in {f.path for f in plan.files}:
        raise BadPlanError("root_not_a_file")


_check = check_plan


def _folders(files: tuple[NewFile, ...], extra: tuple[str, ...] = ()) -> list[str]:
    """Every folder the files need, and the `extra` folders and theirs, relative, parents first."""
    out: set[str] = set()
    for p in (*(f.path for f in files), *(e + "/." for e in extra)):
        parts = p.split("/")
        out.update("/".join(parts[:i]) for i in range(1, len(parts)))
    return sorted(out, key=lambda d: (d.count("/"), d))


def _temp(plan: CreatePlan) -> str:
    return f"{MANIFEST_DIR}{TEMP_INFIX}{plan.job}"


def _under(base: str, rel: str) -> str:
    return base if not rel else join(base, rel)


# -- the run ----------------------------------------------------------------------------------------------------------


class Stop(Exception):
    """A step stops the job, before its commit point, with this reason (the job then cleans up, `Run._cleanup`)."""

    def __init__(self, reason: Reason, path: str | None = None, detail: str | None = None, err: int | None = None):
        super().__init__(reason)
        self.reason: Reason = reason
        self.path = path
        self.detail = detail
        self.errno = err


class Steps(Protocol):
    """What a job adds to the create's writer (`Run`): step 2 (`prepare`: it may read sources and replace `run.plan`;
    it runs before step 1, so a refusal there leaves nothing made, not even the target folder: S0081 r1) and step 5
    (`check`, on the files in the target, just before the commit point). Each raises `Stop` to stop the job. A copy is
    the other user (`ledger_engine.ledger.copy`, S0081)."""

    def prepare(self, run: Run) -> None: ...

    def check(self, run: Run) -> None: ...


class CreateSteps:
    """The create's: nothing to read in step 2; step 5 is the load check (`_load_check`)."""

    def prepare(self, run: Run) -> None:
        del run

    def check(self, run: Run) -> None:
        _load_check(run)


def run(plan: CreatePlan, ports: Ports, job: Job) -> CreateOutcome:
    """§8.2 steps 1 and 3-7 in this engine (the module's docstring)."""
    _check(plan)
    return run_steps(plan, ports, job, CreateSteps())


def run_steps(plan: CreatePlan, ports: Ports, job: Job, steps: Steps) -> CreateOutcome:
    """The writer with a job's own steps 2 and 5 (`Steps`), the garbage collector paused (ADR-0008 amendment)."""
    collecting = gc.isenabled()
    gc.disable()
    try:
        return Run(plan, ports, job, steps).run()
    finally:
        if collecting:
            gc.enable()


class Run:
    """One run of the writer: §8.2 steps 1, 3, 4, 6 and 7, with the job's steps 2 and 5, and the cleanup of a stop."""

    def __init__(self, plan: CreatePlan, ports: Ports, job: Job, steps: Steps) -> None:
        self.plan = plan
        self.fs = ports.fs
        self.load = ports.load
        self.source = ports.source
        self.steps = steps
        self.job = job
        self.handles = Handles(ports.fs)
        self._said_saving = False
        self.folder = plan.target  # its canonical path once resolved; answers name `plan.target` (r1 RT6)
        self.made_target = False
        self.target_identity: tuple[int, int] | None = None
        # every path, relative to the target, whose exclusive create or mkdir succeeded in this run, with the identity
        # it had then and its bytes (None: a folder), in the order made (r2: the owner's middle path)
        self.made: dict[str, _Made] = {}
        self.unsure: list[str] = []  # names whose create failed otherwise than `EEXIST` but that stand there now

    def run(self) -> CreateOutcome:
        try:
            try:
                self.steps.prepare(self)  # step 2 before step 1: a copy reads its sources before it makes anything
                self._target()
            except Stop as s:
                return self._stopped(s)
            except PathMovedError as e:
                return self._stopped(Stop("path_moved", e.canonical))
            except OSError as e:
                return self._stopped(Stop("io_error", self.folder, err=e.errno))
            try:
                self.say_saving()
                self._write()
                self._sync()
                self._read_back()
                self.steps.check(self)  # step 5
            except Stop as s:
                return self._stopped(s)
            except PathMovedError as e:
                return self._stopped(Stop("path_moved", e.canonical))
            except OSError as e:
                return self._stopped(Stop("io_error", self.handles.on, err=e.errno))
            self.job.gate()  # step 6: past it, the job finishes or is resolved from disk (§1)
            return self._manifest()
        finally:
            self.handles.close()

    def say_saving(self) -> None:
        """The `saving` phase, once (a copy says it when it starts reading its sources)."""
        if not self._said_saving:
            self._said_saving = True
            self.job.progress("saving")

    def _stopped(self, s: Stop) -> NotCreated:
        """A stop with no `.finzytrack` of ours in place: the job removes what it made (`_cleanup`) and names what it
        left."""
        return NotCreated(s.reason, self.plan.target, self._cleanup(), s.path, s.detail, s.errno)

    def _cleanup(self) -> tuple[str, ...] | None:
        """Remove what this run made, newest first, and only that (owner, 2026-11-07, the middle path; r2 F1, F2):
        a name whose exclusive create or mkdir succeeded in this run, never judged by its bytes alone. Just before
        each removal the entry must still be the one the job made (its identity, `(st_dev, st_ino)`, as recorded at
        its create) and, for a file, still hold exactly the job's bytes (a bounded read); a folder goes only by
        `rmdir`, which fails on a non-empty one; the target folder last, when the job made it (named `.` when it
        stays, r3 V1). Anything that fails a check is kept and named, with every name in `unsure` (a create that
        failed otherwise than `EEXIST` but whose name stands there now, a made name whose identity could not be read;
        it may be ours or another program's). Every folder is reached through the path guard. Returns the names
        kept (relative to the target, a folder only when nothing kept is inside it), or None when the cleanup could
        not finish (a folder moved, an error): the user is asked to check the folder."""
        kept = list(self.unsure)
        try:
            for rel, m in reversed(list(self.made.items())):
                parent, _, leaf = rel.rpartition("/")
                h = self.handles.at(_under(self.folder, parent))
                st = h.stat(leaf)
                if st is None:
                    continue
                if st.identity != m.identity:
                    kept.append(rel)
                    continue
                if m.data is None:
                    try:
                        h.rmdir(leaf)
                    except OSError:
                        kept.append(rel)
                        continue
                elif st.kind is Kind.FILE and h.sha256(leaf, m.data[1] + 1) == m.data:
                    h.remove(leaf)
                else:
                    kept.append(rel)
                    continue
                h.sync()
            if self.made_target:  # its rmdir fails while anything is in it
                parent, _, name = self.folder.rpartition("/")
                ph = self.handles.at(parent or "/")
                st = ph.stat(name)
                if st is not None and self.target_identity is None:
                    kept.append(TARGET)  # not known as the job's: named, never removed
                elif st is not None and st.identity == self.target_identity:
                    try:
                        self.handles.release(self.folder)
                        ph.rmdir(name)
                        ph.sync()
                    except OSError:
                        kept.append(TARGET)  # the folder the job made stays (another program's entry in it): named
        except (OSError, PathMovedError):
            return None
        return _named(kept)

    # step 1 ------------------------------------------------------------------------------------------------------
    def _target(self) -> None:
        r = self.fs.resolve(self.plan.target)
        if len(r.rest) > 1:
            raise Stop("no_parent", core_paths.canonical_path(r))
        self.folder = core_paths.canonical_path(r)
        parent, _, name = self.folder.rpartition("/")
        parent = parent or "/"
        if r.rest:
            with self.fs.open_dir(parent) as h:
                self._folder_rule(h, parent)
                h.mkdir(name)
                self.made_target = True
                try:
                    st = h.stat(name)
                except OSError:
                    st = None  # its identity unknown: the cleanup names it, never removes it (r3 F3, V2)
                self.target_identity = None if st is None else st.identity
                h.sync()
            return
        with self.fs.open_dir(parent) as h:
            st = h.stat(name)
        if st is None or st.kind is not Kind.DIR:
            raise Stop("not_a_folder")
        th = self.handles.at(self.folder)
        if th.names():
            raise Stop("not_empty")
        self._folder_rule(th, self.folder)

    @staticmethod
    def _folder_rule(h: DirHandle, folder: str) -> None:
        blocked = folder_blocked(h)
        if blocked is not None:
            raise Stop("permission", folder, blocked.detail)

    # step 3 ------------------------------------------------------------------------------------------------------
    def _write(self) -> None:
        for d in _folders(self.plan.files, self.plan.folders):
            parent, _, name = d.rpartition("/")
            self._make(self.handles.at(_under(self.folder, parent)), name, join(self.folder, d), mkdir=True)
        for f in self.plan.files:
            parent, _, name = f.path.rpartition("/")
            self._make(self.handles.at(_under(self.folder, parent)), name, join(self.folder, f.path), data=f.data)

    def _make(self, h: DirHandle, name: str, path: str, *, mkdir: bool = False, data: bytes | Streamed = b"") -> None:
        self.handles.on = path
        rel = path[len(self.folder) + 1 :]
        feed: _Feed | None = None
        try:
            if mkdir:
                h.mkdir(name)
                identity = None
            elif isinstance(data, bytes):
                identity = h.create(name, data, FILE_MODE)  # read from the create's own descriptor (r3 F4)
            else:
                feed = _Feed(self.source(data.source))
                identity = h.create(name, feed, FILE_MODE)
        except FileExistsError:
            raise Stop("taken", path) from None  # another program's entry: never touched, never named
        except OSError:
            self._unsure_if_there(h, name, rel)  # ours cut short, or another program's: kept and named (r2 F2)
            raise
        if identity is None:
            # a folder (no descriptor: the instant between the mkdir and this stat is the stated Limit (cleanup race))
            try:
                st = h.stat(name)
            except OSError:
                st = None
            if st is None:
                self.unsure.append(rel)  # made, but not known as ours: kept and named, never dropped (r3 F3, V2)
                return
            identity = st.identity
        if mkdir:
            written = None
        elif isinstance(data, bytes):
            written = _digest(data)
        else:
            written = None if feed is None else feed.digest()
        self.made[rel] = _Made(identity, written)
        if feed is not None and isinstance(data, Streamed):
            if feed.error is not None:
                raise Stop("source_unreadable", data.source, err=feed.error.errno)
            if data.sha256 is not None and written is not None and written[0] != data.sha256:
                raise Stop("source_changed", data.source)

    def _unsure_if_there(self, h: DirHandle, name: str, rel: str) -> None:
        """A name whose create or mkdir failed otherwise than `EEXIST`: named when it stands there now, or when that
        cannot be told."""
        try:
            there = h.stat(name) is not None
        except OSError:
            there = True
        if there:
            self.unsure.append(rel)

    def _sync(self) -> None:
        for d in reversed(_folders(self.plan.files, self.plan.folders)):
            self.handles.on = join(self.folder, d)
            self.handles.at(join(self.folder, d)).sync()
        self.handles.on = self.folder
        self.handles.at(self.folder).sync()

    # step 4 ------------------------------------------------------------------------------------------------------
    def _read_back(self) -> None:
        """Every file holds what the job wrote (its SHA-256 and length, read at most that length plus one byte)."""
        for f in self.plan.files:
            parent, _, name = f.path.rpartition("/")
            path = join(self.folder, f.path)
            self.handles.on = path
            wrote = self.made[f.path].data
            if wrote is None or self.handles.at(_under(self.folder, parent)).sha256(name, wrote[1] + 1) != wrote:
                raise Stop("read_back", path)

    # step 7 ------------------------------------------------------------------------------------------------------
    def _manifest(self) -> CreateOutcome:
        temp = _temp(self.plan)
        tpath = join(self.folder, temp)
        try:
            th = self.handles.at(self.folder)
            self._make(th, temp, tpath, mkdir=True)  # a temp folder that stands there already is not ours
            self._write_temp(tpath)  # its read-back, last, checks the folder's path through the guard (r1 V1)
        except (OSError, PathMovedError, Stop) as e:
            if isinstance(e, Stop):
                return self._stopped(e)
            if isinstance(e, PathMovedError):
                return self._stopped(Stop("path_moved", e.canonical))
            if isinstance(e, FileExistsError):
                return self._stopped(Stop("taken", self.handles.on))
            return self._stopped(Stop("io_error", self.handles.on, err=e.errno))
        try:
            if th.caps().excl:
                th.rename_excl(temp, MANIFEST_DIR)
            else:
                if th.stat(MANIFEST_DIR) is not None:
                    raise FileExistsError(_errno.EEXIST, "taken")
                th.rename(temp, MANIFEST_DIR)  # §6.8: an entry put there in this instant can be replaced (r1 RT8)
        except OSError as e:
            if th.stat(temp) is None:
                raise  # the rename may have happened: the disk tells (`outcome`)
            if e.errno in TAKEN or th.stat(MANIFEST_DIR) is not None:
                return self._stopped(Stop("manifest_taken", join(self.folder, MANIFEST_DIR)))
            return self._stopped(Stop("io_error", join(self.folder, MANIFEST_DIR), err=e.errno))
        th.sync()  # an error here is raised: the sidecar reads the disk
        self.made = {
            (MANIFEST_DIR + rel[len(temp) :] if rel.split("/")[0] == temp else rel): m for rel, m in self.made.items()
        }
        # `.finzytrack` read back through the path guard (ADR-0008 step 6; r1 RT2, V1): "created" only for our bytes
        # at the target's path. A folder moved since raises (`PathMovedError`): the sidecar reads the disk at the path.
        if _manifest_seen(self.handles, self.plan, self.folder) != "ours":
            return self._stopped(Stop("read_back", join(self.folder, MANIFEST_DIR)))
        return Created(self.plan.target)

    def _write_temp(self, tpath: str) -> None:
        for d in _folders(self.plan.manifest):
            parent, _, name = d.rpartition("/")
            self._make(self.handles.at(_under(tpath, parent)), name, join(tpath, d), mkdir=True)
        for f in self.plan.manifest:
            parent, _, name = f.path.rpartition("/")
            self._make(self.handles.at(_under(tpath, parent)), name, join(tpath, f.path), data=f.data)
        for d in reversed(_folders(self.plan.manifest)):
            self.handles.at(join(tpath, d)).sync()
        self.handles.at(tpath).sync()
        for f in self.plan.manifest:  # read back before the rename (ADR-0008 step 6; r1 RT2)
            parent, _, name = f.path.rpartition("/")
            if self.handles.at(_under(tpath, parent)).read(name, limit=len(_text(f)) + 1) != f.data:
                raise Stop("read_back", join(tpath, f.path))


# step 5 --------------------------------------------------------------------------------------------------------------
def _load_check(r: Run) -> None:
    """The create's load check (the module's docstring, step 5)."""
    r.job.progress("checking_change")
    ours = {posixpath.normpath(join(r.folder, f.path)) for f in r.plan.files}
    try:
        result = r.load(join(r.folder, r.plan.root))
    except Exception:  # noqa: BLE001 (the check could not run: an encrypted file met, a plugin, the disk)
        raise Stop("load_failed") from None
    if result.errors:
        raise Stop("load_errors", detail=str(result.errors))
    for p in result.parsed:
        if posixpath.normpath(p) not in ours:
            raise Stop("load_outside", p)


@dataclass(frozen=True, slots=True)
class _Made:
    """An entry this run made: its identity then, and the SHA-256 and length of the bytes it wrote (None: a
    folder)."""

    identity: tuple[int, int]
    data: tuple[str, int] | None


def _text(f: NewFile) -> bytes:
    """A `.finzytrack/` file's bytes (`_check` refuses any other kind there)."""
    return f.data if isinstance(f.data, bytes) else b""


def _digest(data: bytes) -> tuple[str, int]:
    return hashlib.sha256(data).hexdigest(), len(data)


class _Feed:
    """A source's chunks as a create writes them, hashed and counted on the way (S0081). A read error ends the feed
    early instead of raising inside the create, so the file the job made is known as its own (its identity, and what
    it holds) and the cleanup removes it; the job then stops (`source_unreadable`)."""

    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks
        self._sha = hashlib.sha256()
        self._n = 0
        self.error: OSError | None = None

    def __iter__(self) -> Iterator[bytes]:
        try:
            for chunk in self._chunks:
                self._sha.update(chunk)
                self._n += len(chunk)
                yield chunk
        except OSError as e:
            self.error = e

    def digest(self) -> tuple[str, int]:
        return self._sha.hexdigest(), self._n


def written(r: Run, rel: str) -> tuple[str, int] | None:
    """What the job wrote at `rel` (relative to the target): its SHA-256 and length, or None."""
    m = r.made.get(rel)
    return None if m is None else m.data


# -- the outcome after a death ---------------------------------------------------------------------------------------


TARGET: Final = "."
"""In `NotCreated.left`, the target folder itself (the job made it and could not remove it; r3 V1)."""


def _named(entries: list[str]) -> tuple[str, ...]:
    """The names to show: each once, a folder (the target included) only when nothing named is inside it."""
    names = set(entries)
    inner = {e for e in names if e != TARGET}
    return tuple(
        sorted(e for e in names if not (e == TARGET and inner) and not any(o.startswith(e + "/") for o in inner))
    )


def _nfd(path: str) -> str:
    """A name as every volume we write on compares it at least (§6.3: NFD); case is kept, so on a case-sensitive
    volume another program's `MAIN.beancount` is not at one of our names."""
    return unicodedata.normalize("NFD", path)


def _ours(plan: CreatePlan) -> tuple[set[str], set[str]]:
    """Every file of the job by its path relative to the target (the workspace's, the temp folder's and
    `.finzytrack`'s), and every folder."""
    files: set[str] = set()
    folders: set[str] = set()
    for base, group in (("", plan.files), (_temp(plan), plan.manifest), (MANIFEST_DIR, plan.manifest)):
        if base:
            folders.add(_nfd(base))
        for f in group:
            files.add(_nfd(_under(base, f.path) if base else f.path))
        folders.update(_nfd(_under(base, d) if base else d) for d in _folders(group, () if base else plan.folders))
    return files, folders


def _at_our_names(handles: Handles, plan: CreatePlan, folder: str, *, every: bool = False) -> tuple[str, ...] | None:
    """After a death, who made a name is not known, so nothing is removed (owner, 2026-11-07) and every entry at one
    of the job's names is named for the user to check, whatever it holds (r3: no guessing by content; a torn file of
    ours holds anything): the plan's files and folders, the temp folder and `.finzytrack`'s names, and the target
    itself when it holds nothing else of them. A folder of the job's names is walked (never through a link: the path
    guard) and named only when nothing named is inside it. None when the folder cannot be listed; empty when there is
    no folder. With `every` (a copy, whose file list only the engine that died knew; the target was empty when the job
    began): every entry in the target, by its own name, whatever it holds."""
    files, folders = _ours(plan)
    found: list[str] = [TARGET]
    if every:
        try:
            try:
                h = handles.at(folder)
            except FileNotFoundError:
                return ()
            found.extend(h.names())
        except (OSError, PathMovedError):
            return None
        return _named(found)
    todo = [""]
    try:
        while todo:
            rel = todo.pop()
            try:
                h = handles.at(_under(folder, rel))
            except FileNotFoundError:
                if rel:
                    raise
                return ()
            for name in h.names():
                st = h.stat(name)
                path = _under(rel, name) if rel else name
                key = _nfd(path)
                if st is None or (key not in files and key not in folders):
                    continue
                found.append(path)
                if st.kind is Kind.DIR and key in folders:
                    todo.append(path)
    except (OSError, PathMovedError):
        return None
    return _named(found)


@dataclass(frozen=True, slots=True)
class Facts:
    """What the disk says about a create (§1): `.finzytrack` after the target's sync (`unknown`: it could not be read;
    `errno`), and every entry at the job's names (`_at_our_names`; None: not listed)."""

    manifest: ManifestSeen
    left: tuple[str, ...] | None
    errno: int | None = None


def _manifest_seen(handles: Handles, plan: CreatePlan, folder: str) -> ManifestSeen:
    """What stands at `.finzytrack` in `folder`: the plan's bytes in every one of its files (`ours`), something else
    (`foreign`), or nothing (`absent`). Read through the path guard, each file at most its bytes plus one."""
    st = handles.at(folder).stat(MANIFEST_DIR)
    if st is None:
        return "absent"
    if st.kind is not Kind.DIR:
        return "foreign"
    base = join(folder, MANIFEST_DIR)
    for f in plan.manifest:
        parent, _, name = f.path.rpartition("/")
        try:
            h = handles.at(_under(base, parent))
            fst = h.stat(name)
            if fst is None or fst.kind is not Kind.FILE or h.read(name, limit=len(_text(f)) + 1) != f.data:
                return "foreign"
        except (FileNotFoundError, NotADirectoryError, PathMovedError):
            return "foreign"
    return "ours"


def outcome(fs: FileSystem, plan: CreatePlan) -> Facts:
    """§1's "resolved from disk" for a create whose engine died (the module's docstring). It removes nothing."""
    _check(plan)
    return outcome_of(fs, plan)


def outcome_of(fs: FileSystem, plan: CreatePlan, *, every: bool = False) -> Facts:
    """`outcome` for a checked plan; with `every`, a copy's (`_at_our_names`)."""
    handles = Handles(fs)
    try:
        r = fs.resolve(plan.target)
        if r.rest:
            return Facts("absent", ())
        folder = core_paths.canonical_path(r)
        handles.at(folder).sync()  # what is read below is what a power loss keeps
        seen = _manifest_seen(handles, plan, folder)
        return Facts(seen, () if seen == "ours" else _at_our_names(handles, plan, folder, every=every))
    except NotADirectoryError:
        return Facts("absent", ())  # a file stands at the target: no workspace was begun there (r2 verifier F4)
    except (OSError, PathMovedError) as e:
        return Facts("unknown", None, errno=e.errno if isinstance(e, OSError) else None)
    finally:
        handles.close()
