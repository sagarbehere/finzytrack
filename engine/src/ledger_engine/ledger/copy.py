"""The `copy` job inside the engine (`design/specs/engine-jobs.md` §1 "Commit point", §4, §8.1, §8.2; ADR-0064 P2,
ADR-0063 decision 1, ADR-0062 decision 2, ADR-0059, ADR-0017, ADR-0067; S0081, the owner's decisions of 2026-11-07).

`run(plan, ports, job)` copies a ledger into a new workspace folder: a test copy, Copy adoption of a legacy ledger,
"New workspace from settings of…". It is the create's writer (`create.run_steps`: the target, the exclusive writes,
the read-back, the commit point, `.finzytrack/` last and the cleanup of a stop, one code for both: owner, "Reuse
create's code; do not write a second cleanup") with the copy's own steps 2 and 5 (`_CopySteps`):

2. **The sources**, before anything is written into the target:
   a. each legacy write lock the request names tried once, without waiting (`LegacyLocks.try_lock`, ADR-0062): held,
      or any error, refuses (`legacy_saving`; fail closed); a free one is held through the copy's file reads; an
      absent one is checked again just before the commit point;
   b. the source loaded once with the overlay load (Beancount's full load, plugins included, never decrypting:
      `encrypted`), the modules its plugins imported then forgotten (`beancount_port.user_modules_forgotten`), its
      result reduced to `loads_same.Summary` and dropped. Its `fingerprint` hashes exactly the bytes it parsed: the
      "fingerprint before reading" of the owner's decision 2;
   c. no `.open` journal may name a source ledger file (a save writes only ledger files): one that does, one that
      cannot be read and a journal folder that cannot be listed refuse (`recovery_blocked`; spec §8.1, INV-32, model
      `NoCopyOverOpenJournal`);
   d. the layout and the rewrites (`core.copy_layout`): every ledger file the load parsed (each streamed from its
      source and checked against the fingerprint, or, when a line must be rewritten, read whole, checked, rewritten),
      every file under each documents root, and each `document` entry's file under the source's folder (streamed,
      their hashes recorded as copied).
5. **"Loads the same"** (`loads_same`): the held locks released, the copy loaded with the overlay load in the
   target; it must run (`load_failed`), read only inside the target (`load_outside`; a `document` entry's absolute
   path may name the original file: spec Limit), and give the source's summary (`load_differs`). Then the re-check,
   just before the commit point: the source's ledger set observed again equals the fingerprint, every copied
   document hashes as it was copied, every absent lock is still absent (`source_changed`, `legacy_saving`).

`outcome(fs, plan)` is the create's outcome read; it names every entry in the target, since the file list was known
only to the engine that died (`create.outcome_of`, `every`). The source is only ever read: the job writes, locks or
syncs nothing there (the lock file is opened read-only, INV-26). A malformed plan raises `writer.BadPlanError` before
anything is read.
"""

from __future__ import annotations

import gc
import posixpath
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field

from ledger_engine.fs.port import FileSystem, LegacyAbsent, LegacyHeld, LegacyLocks, Lock
from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import create, fingerprint, loads_same, overlay
from ledger_engine.ledger.core import copy_layout as cl
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core.journal import Record, State
from ledger_engine.ledger.core.ledger_set import Absent, Content, FileState, Fingerprint, LedgerSet
from ledger_engine.ledger.create import CreateOutcome, CreatePlan, Facts, NewFile, Run, Stop, Streamed
from ledger_engine.ledger.handles import join
from ledger_engine.ledger.paths import path_key
from ledger_engine.ledger.policy import DESKTOP
from ledger_engine.ledger.writer import BadPlanError

LOCAL = "local"
JOURNAL = "journal"


@dataclass(frozen=True, slots=True)
class CopyPlan:
    """A confirmed copy: the job's name, the target folder (absolute), the source ledger's root (absolute, as the
    user named it), the files of `.finzytrack/` (the manifest with the copy's new lineage id), the app's local data
    folder (where the journals are) and the legacy write-lock files to try."""

    job: str
    target: str
    source: str
    manifest: tuple[NewFile, ...]
    app_local_data: str
    legacy_locks: tuple[str, ...]


def _overlay_load(root: str) -> overlay.LoadResult:
    """Beancount's full load (the overlay load, nothing in its overlay), the modules its plugins imported forgotten
    after it, so the next load imports its own."""
    with port.user_modules_forgotten():
        return overlay.load(root, {}, DESKTOP)


def _parse_names(data: bytes, filename: str) -> tuple[list[str], list[str]]:
    """The `include` strings and the `option "documents"` strings Beancount parses from one file."""
    _, _, options = port.parse_string(data, filename)
    return [str(i) for i in options["include"]], [str(d) for d in options["documents"]]


@dataclass(frozen=True, slots=True)
class CopyPorts:
    """The copy's effects: the file-system port (every write in the target, the journals), the legacy locks, the
    load, the reads of a source (`chunks`), the observation of a ledger set and the state of one file, and the
    parser's include and documents strings of a file."""

    fs: FileSystem
    legacy: LegacyLocks
    load: Callable[[str], overlay.LoadResult] = _overlay_load
    source: Callable[[str], Iterator[bytes]] = fingerprint.chunks
    observe: Callable[[LedgerSet], Fingerprint] = fingerprint.observe
    file_state: Callable[[str], FileState] = fingerprint.file_state
    parse: Callable[[bytes, str], tuple[list[str], list[str]]] = _parse_names


def _absolute(p: str, what: str) -> None:
    if not p.startswith("/") or p == "/" or posixpath.normpath(p) != p or "\0" in p:
        raise BadPlanError(what)


def _check(plan: CopyPlan) -> CreatePlan:
    """The plan against itself, before anything is read (`BadPlanError`); the create's plan it starts from."""
    _absolute(plan.source, "source")
    _absolute(plan.app_local_data, "app_local_data")
    for lock in plan.legacy_locks:
        _absolute(lock, "legacy_lock")
    base = CreatePlan(plan.job, plan.target, (), "", plan.manifest)
    create.check_base(base)
    return base


def run(plan: CopyPlan, ports: CopyPorts, job: create.Job) -> CreateOutcome:
    """The module's docstring."""
    base = _check(plan)
    steps = _CopySteps(plan, ports, job)
    try:
        return create.run_steps(base, create.Ports(ports.fs, source=ports.source), job, steps)
    finally:
        steps.release()


def outcome(fs: FileSystem, plan: CopyPlan) -> Facts:
    """§1's "resolved from disk" for a copy whose engine died: the create's, naming every entry in the target."""
    return create.outcome_of(fs, _check(plan), every=True)


# -- step 2 ------------------------------------------------------------------------------------------------------------


@dataclass
class _CopySteps:
    plan: CopyPlan
    ports: CopyPorts
    job: create.Job
    held: list[Lock] = field(default_factory=lambda: [])
    absent: list[str] = field(default_factory=lambda: [])
    summary: loads_same.Summary | None = None
    fingerprint: Fingerprint | None = None
    layout: cl.Layout | None = None
    documents: list[tuple[str, str]] = field(default_factory=lambda: [])  # (source, path in the copy)

    def release(self) -> None:
        while self.held:
            self.held.pop().release()

    def prepare(self, run: Run) -> None:
        self._locks()
        root = posixpath.normpath(self.plan.source)
        loaded = self._load_source(root)
        fp = loaded.fingerprint
        try:
            self.summary = loads_same.summary(loaded, _same)
        except loads_same.UncomparableError as e:
            raise Stop("load_failed", detail=e.kind) from None
        del loaded  # the source's entries are not kept while the copy is written and loaded
        gc.collect()  # the job pauses the collector (ADR-0008 amendment): free the load's cycles before the next
        files = dict(fp.files)
        self._journals([p for p, s in files.items() if isinstance(s, Content | Absent)])
        src = cl.SourceSet(
            root=root,
            files=tuple(p for p, s in files.items() if isinstance(s, Content)),
            missing=tuple(p for p, s in files.items() if isinstance(s, Absent)),
            globs=tuple(g for g, _ in fp.globs),
            documents=tuple(d for d, _ in fp.documents),
            document_files=tuple(p for p, there in fp.document_files if there),
        )
        for root_folder in src.documents:
            if _in(root_folder, run.folder):  # its own files would change the source's listing: never "the same"
                raise Stop("cannot_place", root_folder, "inside_documents")
        lay = cl.layout(src)
        new_files = self._files(lay, src, files, fp)
        plan = CreatePlan(self.plan.job, self.plan.target, new_files, _placed(lay, root), self.plan.manifest)
        try:
            create.check_plan(plan)
        except BadPlanError as e:
            raise Stop("cannot_place", detail=str(e)) from None
        self.fingerprint = fp
        self.layout = lay
        run.plan = plan

    def _locks(self) -> None:
        """Step 2a (ADR-0062 decision 2): each lock tried once, never waited for; only free and absent go on. A lock
        named twice is tried once (a second `flock` of our own would see the first as held)."""
        for lock in dict.fromkeys(self.plan.legacy_locks):
            try:
                got = self.ports.legacy.try_lock(lock)
            except OSError as e:
                raise Stop("legacy_saving", lock, err=e.errno) from None
            match got:
                case LegacyHeld():
                    raise Stop("legacy_saving", lock)
                case LegacyAbsent():
                    self.absent.append(lock)
                case _:
                    self.held.append(got)

    def _load_source(self, root: str) -> overlay.Loaded:
        try:
            loaded = self.ports.load(root)
        except OSError as e:
            raise Stop("source_unreadable", e.filename if isinstance(e.filename, str) else root, err=e.errno) from None
        except Exception:  # noqa: BLE001 (the load could not run: a plugin, Beancount)
            raise Stop("source_failed") from None
        if isinstance(loaded, overlay.LoadRefused):
            raise Stop("encrypted" if loaded.reason == "encrypted" else "source_failed", loaded.subject)
        if not isinstance(dict(loaded.fingerprint.files).get(root), Content):
            raise Stop("source_missing", root)
        return loaded

    def _journals(self, sources: Sequence[str]) -> None:
        """Step 2c: no `.open` journal of any workspace may name a source ledger file (by `path_key`); an `.open`
        record that cannot be read, and a journal folder that cannot be listed, refuse too (fail closed)."""
        fs = self.ports.fs
        try:
            keys = {path_key(fs, p): p for p in sources}
        except OSError as e:
            raise Stop("source_unreadable", None, err=e.errno) from None
        local = join(self.plan.app_local_data, LOCAL)
        for key in self._listed(local, missing=(FileNotFoundError,)):
            folder = join(join(local, key), JOURNAL)
            self._journal_folder(folder, self._listed(folder, missing=(FileNotFoundError, NotADirectoryError)), keys)

    def _listed(self, folder: str, *, missing: tuple[type[OSError], ...]) -> list[str]:
        """The names in `folder` (none when it is `missing`); a folder that cannot be opened or listed refuses the
        copy: it may hold an `.open` record (fail closed)."""
        try:
            with self.ports.fs.open_dir(folder) as h:
                return h.names()
        except missing:
            return []
        except OSError:
            raise Stop("recovery_blocked", folder, "unlisted") from None

    def _journal_folder(self, folder: str, names: list[str], keys: dict[str, str]) -> None:
        if not any((p := journal.parse_file_name(n)) is not None and p[1] is State.OPEN for n in names):
            return
        try:
            jd = self.ports.fs.open_dir(folder)
        except OSError:
            raise Stop("recovery_blocked", folder, "unlisted") from None
        with jd:
            for name in names:
                parsed = journal.parse_file_name(name)
                if parsed is None or parsed[1] is not State.OPEN:
                    continue
                try:
                    data = jd.read(name)
                except OSError:
                    data = None
                rec = journal.Unreadable("gone") if data is None else journal.decode(data)
                if not isinstance(rec, Record):
                    raise Stop("recovery_blocked", join(folder, name), "unreadable")
                for f in rec.files:
                    if f.path_key in keys:
                        raise Stop("recovery_blocked", keys[f.path_key])

    def _files(
        self, lay: cl.Layout, src: cl.SourceSet, files: dict[str, FileState], fp: Fingerprint
    ) -> tuple[NewFile, ...]:
        """Step 2d: every file the copy writes, ledger files first; a path two sources would take goes to the first."""
        out: dict[str, NewFile] = {}
        for p in src.files:
            state = files[p]
            assert isinstance(state, Content)  # noqa: S101 (`src.files` holds only read files)
            data = self._ledger_file(lay, src, p, state.sha256)
            out.setdefault(_placed(lay, p), NewFile(_placed(lay, p), data))
        for root, listing in fp.documents:
            for name, there in listing or ():
                if there:
                    path = join(root, name)
                    dst = _placed(lay, path)
                    if dst not in out:
                        out[dst] = NewFile(dst, Streamed(path, None))
                        self.documents.append((path, dst))
        for path in src.document_files:
            dst = cl.place(lay, path)
            if dst and cl.inside(lay, path) and dst not in out:
                out[dst] = NewFile(dst, Streamed(path, None))
                self.documents.append((path, dst))
        return tuple(out.values())

    def _ledger_file(self, lay: cl.Layout, src: cl.SourceSet, p: str, sha: str) -> bytes | Streamed:
        """One ledger file: streamed when no line changes, else its rewritten bytes (read whole, as the load read
        it, and checked against the fingerprint first)."""
        try:
            data = b"".join(self.ports.source(p))
        except OSError as e:
            raise Stop("source_unreadable", p, err=e.errno) from None
        if fingerprint.content(data).sha256 != sha:
            raise Stop("source_changed", p)
        new = cl.rewrite(lay, p, data, is_root=p == src.root, parse=lambda: self.ports.parse(data, p))
        if isinstance(new, cl.Refusal):
            raise Stop("cannot_rewrite", p, detail=str(new.line) if new.line else None)
        return Streamed(p, sha) if new is None else new

    # step 5 ---------------------------------------------------------------------------------------------------------
    def check(self, run: Run) -> None:
        self.release()  # held only for the copy's file reads (ADR-0062 Consequences)
        self.job.progress("checking_change")
        assert self.summary is not None  # noqa: S101 (set by `prepare`)
        assert self.fingerprint is not None  # noqa: S101
        assert self.layout is not None  # noqa: S101
        folder = run.folder
        try:
            loaded = self.ports.load(join(folder, run.plan.root))
        except Exception:  # noqa: BLE001 (the check could not run)
            raise Stop("load_failed") from None
        if isinstance(loaded, overlay.LoadRefused):
            raise Stop("load_failed", loaded.subject)
        for p in (*(f for f, _ in loaded.fingerprint.files), *(d for d, _ in loaded.fingerprint.documents)):
            if not _in(folder, p):
                raise Stop("load_outside", p)
        pattern, table = cl.back(self.layout, folder)

        def back(s: str) -> str:
            return pattern.sub(lambda m: table[m.group(0)], s)

        try:
            copied = loads_same.summary(loaded, back)
        except loads_same.UncomparableError as e:
            raise Stop("load_failed", detail=e.kind) from None
        del loaded
        diff = loads_same.first_difference(self.summary, copied)
        if diff is not None:
            raise Stop("load_differs", diff.path, diff.what if diff.line is None else str(diff.line))
        self._recheck(run)

    def _recheck(self, run: Run) -> None:
        """Just before the commit point (owner's decision 2; ADR-0062's re-check): the source as it was read."""
        assert self.fingerprint is not None  # noqa: S101
        if self.ports.observe(self.fingerprint.ledger_set) != self.fingerprint:
            raise Stop("source_changed")
        for path, dst in self.documents:
            state = self.ports.file_state(path)
            wrote = create.written(run, dst)
            if not isinstance(state, Content) or wrote is None or state.sha256 != wrote[0]:
                raise Stop("source_changed", path)
        for lock in self.absent:
            try:
                got = self.ports.legacy.try_lock(lock)
            except OSError as e:
                raise Stop("legacy_saving", lock, err=e.errno) from None
            if not isinstance(got, LegacyAbsent):
                if not isinstance(got, LegacyHeld):
                    got.release()
                raise Stop("legacy_saving", lock)


def _same(s: str) -> str:
    return s


def _in(folder: str, p: str) -> bool:
    return p == folder or p.startswith(folder.rstrip("/") + "/")


def _placed(lay: cl.Layout, p: str) -> str:
    dst = cl.place(lay, p)
    if not dst:
        raise Stop("cannot_place", p)
    return dst
