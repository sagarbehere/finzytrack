"""The `create` job inside the engine (`design/specs/engine-jobs.md` §1 "Commit point", §4, §8.2; ADR-0064 P2,
ADR-0063 decision 1, ADR-0008, ADR-0017, ADR-0067 decision 2; S0080).

`run(plan, ports, job)` writes a new workspace folder: the files the plan names (the starter ledger and anything else
in the folder) and, last, the `.finzytrack/` folder (the manifest and the workspace's tool files). A folder is a
workspace only once it holds `.finzytrack/` (ADR-0017), so a crash, a failure or a Cancel before step 7 leaves a folder
the user can delete, never a half-made workspace. Every byte of a create is Finzytrack's own text. Its steps:

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
4. **Read-back** (`_read_back`): every file equals its bytes (`read_back`, naming the file).
5. **The load check** (`_load_check`): Beancount's own load of the root in the target (`beancount_port.real_load`,
   never decrypting), so relative paths resolve where they will live. It must run (`load_failed`), report no error
   (`load_errors`: all of the text is Finzytrack's, so an error means our text is wrong, ADR-0067 decision 2's
   exception) and parse only files the job wrote (`load_outside`).
6. **The commit point:** `job.gate()` (§4's one table step answers `proceed` or ends the engine).
7. **`.finzytrack/` last** (`_manifest`): written as `.finzytrack.ft-<job>/` (its folders made, its files created and
   synced, each of its folders synced), renamed to `.finzytrack` with an exclusive rename (on a volume without it: a
   check that the name is free, then a plain rename; §6.8's accepted instant), then the target synced: **created**.
   A `.finzytrack` that stands there by then (`manifest_taken`) is left as it is, and the job removes its own temp
   folder. An error the job cannot place once the rename may have happened is raised: the sidecar reads the outcome
   from disk (`outcome`).

Step 2 (reading sources) is copy's (BL-0196). **Every stop before step 7 lists the target once for the delete advice**
(`_advice`): `wrote` (an entry of the job's is there, or the job made the target) and `only_ours` (every entry is a
name the job writes holding its bytes or a prefix of them, the temp folder included; None when the folder cannot be
listed). Nothing is ever removed but the job's own temp folder at `manifest_taken`.

`outcome(fs, plan)` is §1's "resolved from disk" for a create whose engine died: the target `durable_sync`ed first, so
what it reads is what a power loss keeps, then whether `.finzytrack` holds the plan's bytes (`ours`), something else
(`foreign`) or nothing (`absent`), and the delete advice. It writes nothing but the sync.

A malformed plan raises `writer.BadPlanError` before anything is read (a name the job reserves, a path outside the
target, two names one volume could take for one, an encrypted file name: ADR-0059). Python's garbage collector is
paused for the job (ADR-0008 amendment).
"""

from __future__ import annotations

import errno as _errno
import gc
import posixpath
import re
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from ledger_engine.fs.port import DirHandle, FileSystem, Kind, MalformedPathError, check_name
from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import overlay
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
]
type ManifestSeen = Literal["ours", "foreign", "absent", "unknown"]


@dataclass(frozen=True, slots=True)
class NewFile:
    """One file: its path relative to the folder it is written in (POSIX, no `.` or `..`) and its bytes."""

    path: str
    data: bytes


@dataclass(frozen=True, slots=True)
class CreatePlan:
    """A confirmed create: the job's name (`.finzytrack.ft-<job>`), the target folder (absolute), the workspace's
    files relative to it, which of them is the ledger's root, and the files of `.finzytrack/` relative to it."""

    job: str
    target: str
    files: tuple[NewFile, ...]
    root: str
    manifest: tuple[NewFile, ...]


@dataclass(frozen=True, slots=True)
class Created:
    """`.finzytrack/` holds the plan's bytes and is durable, over files that were synced and read back."""

    folder: str


@dataclass(frozen=True, slots=True)
class NotCreated:
    """No `.finzytrack/` of ours stands in the folder. `wrote`: the job made something there; `only_ours`: the folder,
    listed when the job stopped, holds nothing but the job's own bytes (None: it could not be listed)."""

    reason: Reason
    folder: str
    wrote: bool
    only_ours: bool | None
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
    """The create's effects: the file-system port (every write) and Beancount's load (step 5)."""

    fs: FileSystem
    load: Callable[[str], LoadResult] = _real_load


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
        if p.lower().endswith(overlay.ENCRYPTED_SUFFIXES):
            raise BadPlanError("encrypted_file")  # ADR-0059: never written
        key = _folded(p)
        if key in seen:
            raise BadPlanError("two_files_one_name")
        seen[key] = p
        dirs.update(_folded("/".join(parts[:i])) for i in range(1, len(parts)))
    if dirs & set(seen):
        raise BadPlanError("file_and_folder_one_name")


def _check(plan: CreatePlan) -> None:
    """The plan against itself, before anything is read (`BadPlanError`)."""
    if not JOB.fullmatch(plan.job):
        raise BadPlanError("job_name")
    t = plan.target
    if not t.startswith("/") or t == "/" or posixpath.normpath(t) != t or "\0" in t:
        raise BadPlanError("target")
    _check_files(plan.files, top_reserved=True)
    _check_files(plan.manifest, top_reserved=False)
    if plan.root not in {f.path for f in plan.files}:
        raise BadPlanError("root_not_a_file")


def _folders(files: tuple[NewFile, ...]) -> list[str]:
    """Every folder the files need, relative, parents first."""
    out: set[str] = set()
    for f in files:
        parts = f.path.split("/")
        out.update("/".join(parts[:i]) for i in range(1, len(parts)))
    return sorted(out, key=lambda d: (d.count("/"), d))


def _temp(plan: CreatePlan) -> str:
    return f"{MANIFEST_DIR}{TEMP_INFIX}{plan.job}"


def _under(base: str, rel: str) -> str:
    return base if not rel else join(base, rel)


# -- the run ----------------------------------------------------------------------------------------------------------


class _Stop(Exception):
    def __init__(self, reason: Reason, path: str | None = None, detail: str | None = None, err: int | None = None):
        super().__init__(reason)
        self.reason: Reason = reason
        self.path = path
        self.detail = detail
        self.errno = err


def run(plan: CreatePlan, ports: Ports, job: Job) -> CreateOutcome:
    """§8.2 steps 1 and 3-7 in this engine (the module's docstring)."""
    _check(plan)
    collecting = gc.isenabled()
    gc.disable()
    try:
        return _Run(plan, ports, job).run()
    finally:
        if collecting:
            gc.enable()


class _Run:
    def __init__(self, plan: CreatePlan, ports: Ports, job: Job) -> None:
        self.plan = plan
        self.fs = ports.fs
        self.load = ports.load
        self.job = job
        self.handles = Handles(ports.fs)
        self.folder = plan.target  # its canonical path once resolved
        self.made_target = False

    def run(self) -> CreateOutcome:
        try:
            try:
                self._target()
            except _Stop as s:
                return self._stopped(s, listed=self.made_target)
            except PathMovedError as e:
                return self._stopped(_Stop("path_moved", e.canonical), listed=self.made_target)
            except OSError as e:
                return self._stopped(_Stop("io_error", self.folder, err=e.errno), listed=self.made_target)
            try:
                self.job.progress("saving")
                self._write()
                self._sync()
                self._read_back()
                self._load_check()
            except _Stop as s:
                return self._stopped(s)
            except PathMovedError as e:
                return self._stopped(_Stop("path_moved", e.canonical))
            except OSError as e:
                return self._stopped(_Stop("io_error", self.handles.on, err=e.errno))
            self.job.gate()  # step 6: past it, the job finishes or is resolved from disk (§1)
            return self._manifest()
        finally:
            self.handles.close()

    def _stopped(self, s: _Stop, *, listed: bool = True) -> NotCreated:
        """A stop before `.finzytrack` is in place; the target is listed for the delete advice once the job has made
        or written anything (a refusal of step 1 wrote nothing: no advice)."""
        wrote, only_ours = _advice(self.fs, self.plan, self.folder) if listed else (False, False)
        return NotCreated(s.reason, self.folder, wrote or self.made_target, only_ours, s.path, s.detail, s.errno)

    # step 1 ------------------------------------------------------------------------------------------------------
    def _target(self) -> None:
        r = self.fs.resolve(self.plan.target)
        if len(r.rest) > 1:
            raise _Stop("no_parent", core_paths.canonical_path(r))
        self.folder = core_paths.canonical_path(r)
        parent, _, name = self.folder.rpartition("/")
        parent = parent or "/"
        if r.rest:
            with self.fs.open_dir(parent) as h:
                self._folder_rule(h, parent)
                h.mkdir(name)
                self.made_target = True
                h.sync()
            return
        with self.fs.open_dir(parent) as h:
            st = h.stat(name)
        if st is None or st.kind is not Kind.DIR:
            raise _Stop("not_a_folder")
        th = self.handles.at(self.folder)
        if th.names():
            raise _Stop("not_empty")
        self._folder_rule(th, self.folder)

    @staticmethod
    def _folder_rule(h: DirHandle, folder: str) -> None:
        blocked = folder_blocked(h)
        if blocked is not None:
            raise _Stop("permission", folder, blocked.detail)

    # step 3 ------------------------------------------------------------------------------------------------------
    def _write(self) -> None:
        for d in _folders(self.plan.files):
            parent, _, name = d.rpartition("/")
            self._make(self.handles.at(_under(self.folder, parent)), name, join(self.folder, d), mkdir=True)
        for f in self.plan.files:
            parent, _, name = f.path.rpartition("/")
            self._make(self.handles.at(_under(self.folder, parent)), name, join(self.folder, f.path), data=f.data)

    def _make(self, h: DirHandle, name: str, path: str, *, mkdir: bool = False, data: bytes = b"") -> None:
        self.handles.on = path
        try:
            if mkdir:
                h.mkdir(name)
            else:
                h.create(name, data, FILE_MODE)
        except FileExistsError:
            raise _Stop("taken", path) from None

    def _sync(self) -> None:
        for d in reversed(_folders(self.plan.files)):
            self.handles.on = join(self.folder, d)
            self.handles.at(join(self.folder, d)).sync()
        self.handles.on = self.folder
        self.handles.at(self.folder).sync()

    # step 4 ------------------------------------------------------------------------------------------------------
    def _read_back(self) -> None:
        for f in self.plan.files:
            parent, _, name = f.path.rpartition("/")
            path = join(self.folder, f.path)
            self.handles.on = path
            if self.handles.at(_under(self.folder, parent)).read(name) != f.data:
                raise _Stop("read_back", path)

    # step 5 ------------------------------------------------------------------------------------------------------
    def _load_check(self) -> None:
        self.job.progress("checking_change")
        ours = {posixpath.normpath(join(self.folder, f.path)) for f in self.plan.files}
        try:
            result = self.load(join(self.folder, self.plan.root))
        except Exception:  # noqa: BLE001 (the check could not run: an encrypted file met, a plugin, the disk)
            raise _Stop("load_failed") from None
        if result.errors:
            raise _Stop("load_errors", detail=str(result.errors))
        for p in result.parsed:
            if posixpath.normpath(p) not in ours:
                raise _Stop("load_outside", p)

    # step 7 ------------------------------------------------------------------------------------------------------
    def _manifest(self) -> CreateOutcome:
        temp = _temp(self.plan)
        tpath = join(self.folder, temp)
        try:
            th = self.handles.at(self.folder)
            self.handles.on = tpath
            th.mkdir(temp)  # a temp folder that stands there already is not ours: never removed
        except PathMovedError as e:
            return self._stopped(_Stop("path_moved", e.canonical))
        except FileExistsError:
            return self._stopped(_Stop("taken", tpath))
        except OSError as e:
            return self._stopped(_Stop("io_error", tpath, err=e.errno))
        try:
            self._write_temp(tpath)
        except (OSError, PathMovedError) as e:
            _remove_ours(self.fs, th, temp, self.plan.manifest)
            if isinstance(e, PathMovedError):
                return self._stopped(_Stop("path_moved", e.canonical))
            if isinstance(e, FileExistsError):
                return self._stopped(_Stop("taken", self.handles.on))
            return self._stopped(_Stop("io_error", self.handles.on, err=e.errno))
        try:
            if th.caps().excl:
                th.rename_excl(temp, MANIFEST_DIR)
            else:
                if th.stat(MANIFEST_DIR) is not None:
                    raise FileExistsError(_errno.EEXIST, "taken")
                th.rename(temp, MANIFEST_DIR)
        except OSError as e:
            if th.stat(temp) is None:
                raise  # the rename may have happened: the disk tells (`outcome`)
            _remove_ours(self.fs, th, temp, self.plan.manifest)
            if e.errno in TAKEN or th.stat(MANIFEST_DIR) is not None:
                return self._stopped(_Stop("manifest_taken", join(self.folder, MANIFEST_DIR)))
            return self._stopped(_Stop("io_error", join(self.folder, MANIFEST_DIR), err=e.errno))
        th.sync()  # an error here is raised: the sidecar reads the disk
        return Created(self.folder)

    def _write_temp(self, tpath: str) -> None:
        for d in _folders(self.plan.manifest):
            parent, _, name = d.rpartition("/")
            self.handles.on = join(tpath, d)
            self.handles.at(_under(tpath, parent)).mkdir(name)
        for f in self.plan.manifest:
            parent, _, name = f.path.rpartition("/")
            self.handles.on = join(tpath, f.path)
            self.handles.at(_under(tpath, parent)).create(name, f.data, FILE_MODE)
        for d in reversed(_folders(self.plan.manifest)):
            self.handles.at(join(tpath, d)).sync()
        self.handles.at(tpath).sync()


def _remove_ours(fs: FileSystem, parent: DirHandle, name: str, files: tuple[NewFile, ...]) -> None:
    """Remove the job's own temp folder (`name` in `parent`), then sync `parent`; best effort, and only what is the
    job's: a file at one of its names holding its bytes or a prefix of them, and its folders once empty. Anything
    another program put there stays, and so does the folder that holds it."""
    ours = {_nfd(f.path): f.data for f in files}
    base = join(parent.path(), name)
    try:
        for f in files:
            folder, _, leaf = f.path.rpartition("/")
            with fs.open_dir(_under(base, folder)) as h:
                st = h.stat(leaf)
                data = h.read(leaf) if st is not None and st.kind is Kind.FILE else None
                if data is not None and ours[_nfd(f.path)].startswith(data):
                    h.remove(leaf)
                    h.sync()
        for d in reversed(_folders(files)):
            folder, _, leaf = d.rpartition("/")
            with fs.open_dir(_under(base, folder)) as h:
                h.rmdir(leaf)
                h.sync()
        parent.rmdir(name)
        parent.sync()
    except (OSError, PathMovedError):
        pass


# -- the delete advice and the outcome ------------------------------------------------------------------------------


def _nfd(path: str) -> str:
    """A name as every volume we write on compares it at least (§6.3: NFD); case is kept, so on a case-sensitive
    volume another program's `MAIN.beancount` is not ours."""
    return unicodedata.normalize("NFD", path)


def _ours(plan: CreatePlan) -> tuple[dict[str, bytes], set[str]]:
    """Every file of the job by its path relative to the target (the workspace's, the temp folder's and
    `.finzytrack`'s), and every folder."""
    files: dict[str, bytes] = {}
    folders: set[str] = set()
    for base, group in (("", plan.files), (_temp(plan), plan.manifest), (MANIFEST_DIR, plan.manifest)):
        if base:
            folders.add(_nfd(base))
        for f in group:
            files[_nfd(_under(base, f.path) if base else f.path)] = f.data
        folders.update(_nfd(_under(base, d) if base else d) for d in _folders(group))
    return files, folders


def _walk(fs: FileSystem, folder: str, rel: str = "") -> Iterator[tuple[str, Kind, bytes | None]]:
    """Every entry under `folder`, relative, not following links; a folder is walked only when it is one of ours."""
    with fs.open_dir(_under(folder, rel)) as h:
        for name in h.names():
            st = h.stat(name)
            if st is None:
                continue
            path = _under(rel, name) if rel else name
            yield path, st.kind, h.read(name) if st.kind is Kind.FILE else None


def _advice(fs: FileSystem, plan: CreatePlan, folder: str) -> tuple[bool, bool | None]:
    """(`wrote`, `only_ours`) from one listing of the target (the module's docstring); (False, True) when it is not
    there, (False, None) when it cannot be listed."""
    files, folders = _ours(plan)
    wrote, only_ours = False, True
    todo = [""]
    try:
        while todo:
            rel = todo.pop()
            try:
                entries = list(_walk(fs, folder, rel))
            except FileNotFoundError:
                if rel:
                    raise
                return False, True
            for path, kind, data in entries:
                key = _nfd(path)
                if kind is Kind.DIR and key in folders:
                    wrote = True
                    todo.append(path)
                elif kind is Kind.FILE and key in files and data is not None and files[key].startswith(data):
                    wrote = True
                else:
                    only_ours = False
    except (OSError, PathMovedError):
        return wrote, None
    return wrote, only_ours


@dataclass(frozen=True, slots=True)
class Facts:
    """What the disk says about a create (§1): `.finzytrack` after the target's sync (`unknown`: it could not be read;
    `errno`), and the delete advice."""

    manifest: ManifestSeen
    wrote: bool
    only_ours: bool | None
    errno: int | None = None


def _manifest_seen(fs: FileSystem, plan: CreatePlan, folder: str, h: DirHandle) -> ManifestSeen:
    st = h.stat(MANIFEST_DIR)
    if st is None:
        return "absent"
    if st.kind is not Kind.DIR:
        return "foreign"
    base = join(folder, MANIFEST_DIR)
    for f in plan.manifest:
        parent, _, name = f.path.rpartition("/")
        try:
            with fs.open_dir(_under(base, parent)) as d:
                if d.read(name) != f.data:
                    return "foreign"
        except (FileNotFoundError, NotADirectoryError):
            return "foreign"
    return "ours"


def outcome(fs: FileSystem, plan: CreatePlan) -> Facts:
    """§1's "resolved from disk" for a create whose engine died (the module's docstring)."""
    _check(plan)
    try:
        r = fs.resolve(plan.target)
        if r.rest:
            return Facts("absent", wrote=False, only_ours=True)
        folder = core_paths.canonical_path(r)
        with fs.open_dir(folder) as h:
            h.sync()  # what is read below is what a power loss keeps
            seen = _manifest_seen(fs, plan, folder, h)
    except NotADirectoryError:
        return Facts("absent", wrote=False, only_ours=False)
    except OSError as e:
        return Facts("unknown", wrote=False, only_ours=None, errno=e.errno)
    wrote, only_ours = _advice(fs, plan, folder)
    return Facts(seen, wrote, only_ours)
