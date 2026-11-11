"""The `copy` job inside the engine (`design/specs/engine-jobs.md` §1 "Commit point", §4, §8.1, §8.2; ADR-0064 P2,
ADR-0063 decision 1, ADR-0062 decision 2, ADR-0059, ADR-0017, ADR-0067; S0081, the owner's decisions of 2026-11-07).

`run(plan, ports, job)` copies a ledger into a new workspace folder: a test copy, Copy adoption of a legacy ledger,
"New workspace from settings of…". It is the create's writer (`create.run_steps`: the target, the exclusive writes,
the read-back, the commit point, `.finzytrack/` last and the cleanup of a stop, one code for both: owner, "Reuse
create's code; do not write a second cleanup") with the copy's own steps 2 and 5 (`_CopySteps`):

2. **The sources**, before step 1 makes anything (a refusal here leaves nothing, not even the target folder):
   a. a target under the legacy root of a lock the request names refuses (`cannot_place`, `inside_legacy`; INV-26);
      each lock tried once, without waiting (`LegacyLocks.try_lock`, ADR-0062): held, or any error, refuses
      (`legacy_saving`; fail closed); a free one is held through the copy's file reads; an absent one is checked
      again just before the commit point;
   b. the source loaded once with the overlay load (Beancount's full load, plugins included, never decrypting:
      `encrypted`), its result reduced to `loads_same.Summary` and dropped. Its `fingerprint` hashes exactly the bytes
      it parsed: the "fingerprint before reading" of the owner's decision 2. The plugins it names that are not
      Beancount's own are remembered, for the text of a copy that then does not load the same (BL-0202);
   c. no `.open` journal may name a source ledger file (a save writes only ledger files): one that does, one that
      cannot be read and a journal folder that cannot be listed refuse (`recovery_blocked`; spec §8.1, INV-32, model
      `NoCopyOverOpenJournal`);
   d. a target inside a documents root, or one holding a path the ledger names, refuses (`cannot_place`, compared by
      `path_key`); the layout and the rewrites (`core.copy_layout`): every ledger file the load parsed (each streamed
      from its source and checked against the fingerprint, or, when a line must be rewritten, read whole, checked,
      rewritten), every regular file under each documents root (no `.finzytrack` folder), every documents root folder,
      and every `document` entry's file (streamed, their hashes recorded as copied).
5. **"Loads the same"**: the held locks released, the copy loaded **in a fresh process** (`fresh_load.check`,
   `copy_check`: nothing of the source's load, which ran its plugins in this engine, can reach it; the owner's choice
   of 2026-11-07, S0081 r2); it must run (`load_failed`), read only inside the target (`load_outside`, a `document`
   entry's file included), and give the source's summary (`load_differs`, or `plugin_modules` when the source names a
   plugin of its own). Then the re-check, just before the commit point: the source's ledger set observed again equals
   the fingerprint, every copied document hashes as it was copied, every absent lock is still absent
   (`source_changed`, `legacy_saving`).

`outcome(fs, plan)` is the create's outcome read; it names every entry in the target, since the file list was known
only to the engine that died (`create.outcome_of`, `every`). The source is only ever read: the job writes, locks or
syncs nothing there (the lock file is opened read-only, INV-26). A malformed plan raises `writer.BadPlanError` before
anything is read.
"""

from __future__ import annotations

import gc
import posixpath
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field

from ledger_engine.fs.port import FileSystem, LegacyAbsent, LegacyHeld, LegacyLocks, Lock
from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import copy_check, create, fingerprint, fresh_load, loads_same, overlay
from ledger_engine.ledger.core import copy_layout as cl
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core.journal import Record, State
from ledger_engine.ledger.core.ledger_set import Absent, Content, FileState, Fingerprint, LedgerSet
from ledger_engine.ledger.create import CreateOutcome, CreatePlan, Facts, NewFile, Run, Stop, Streamed
from ledger_engine.ledger.handles import join
from ledger_engine.ledger.paths import path_key
from ledger_engine.ledger.policy import DESKTOP
from ledger_engine.ledger.writer import BadPlanError
from ledger_engine.tla import tla_action

LEGACY_LOCK_TAIL = "/data/.write.lock"
BEANCOUNT_PACKAGES = frozenset({"beancount", "beanquery"})
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
    """Beancount's full load of the source (the overlay load, nothing in its overlay). Its plugins run in this engine,
    which the sidecar retires after the job (§5.1); the copy's own load runs in a fresh process (`fresh_load`)."""
    return overlay.load(root, {}, DESKTOP)


def parsed_from(data: bytes, filename: str) -> cl.Parsed:
    """What Beancount parses from one file: its `include` strings, its `option "documents"` strings, and each
    `document` directive's line and resolved file."""
    entries, _, options = port.parse_string(data, filename)
    docs = [
        (int(e.meta["lineno"]), str(e.filename))
        for e in entries
        if isinstance(e, port.Document) and e.meta.get("filename") == filename
    ]
    return cl.Parsed([str(i) for i in options["include"]], [str(d) for d in options["documents"]], docs)


@dataclass(frozen=True, slots=True)
class CopyPorts:
    """The copy's effects: the file-system port (every write in the target, the journals), the legacy locks, the
    source's load, the copy's load in a fresh process (`fresh_load.check`), the reads of a source (`chunks`), the
    observation of a ledger set and the state of one file, what the parser reads from one file, and whether a path is
    a regular file."""

    fs: FileSystem
    legacy: LegacyLocks
    load: Callable[[str], overlay.LoadResult] = _overlay_load
    check: Callable[[copy_check.CheckRequest], copy_check.CheckResult] = fresh_load.check
    source: Callable[[str], Iterator[bytes]] = fingerprint.chunks
    observe: Callable[[LedgerSet], Fingerprint] = fingerprint.observe
    file_state: Callable[[str], FileState] = fingerprint.file_state
    parse: Callable[[bytes, str], cl.Parsed] = parsed_from
    regular: Callable[[str], bool] = fingerprint.regular


def _absolute(p: str, what: str) -> None:
    if not p.startswith("/") or p == "/" or posixpath.normpath(p) != p or "\0" in p:
        raise BadPlanError(what)


def _check(plan: CopyPlan) -> CreatePlan:
    """The plan against itself, before anything is read (`BadPlanError`); the create's plan it starts from."""
    _absolute(plan.source, "source")
    _absolute(plan.app_local_data, "app_local_data")
    for lock in plan.legacy_locks:
        _absolute(lock, "legacy_lock")
        if not lock.endswith(LEGACY_LOCK_TAIL):  # `<legacy root>/data/.write.lock` only (ADR-0062; r2 F9)
            raise BadPlanError("legacy_lock")
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
    quoted: dict[str, set[str]] = field(default_factory=lambda: {})  # a rewritten include's text -> its old texts
    own_plugins: tuple[str, ...] = ()  # plugins the source names that are not Beancount's own

    def release(self) -> None:
        while self.held:
            self.held.pop().release()

    @tla_action("CP_Load")
    def prepare(self, run: Run) -> None:
        """Step 2, before step 1 makes anything (`create.Steps`)."""
        run.say_saving()
        target = self._key(self.plan.target)
        for lock in self.plan.legacy_locks:  # INV-26: never a new folder under a legacy root (S0081 r1 question)
            legacy_root = lock[: -len(LEGACY_LOCK_TAIL)]
            if _in(self._key(legacy_root), target):
                raise Stop("cannot_place", legacy_root, "inside_legacy")
        self._locks()
        root = posixpath.normpath(self.plan.source)
        loaded = self._load_source(root)
        fp = loaded.fingerprint
        self.own_plugins = tuple(
            str(name) for name, _ in loaded.options["plugin"] if str(name).split(".")[0] not in BEANCOUNT_PACKAGES
        )
        try:
            self.summary = loads_same.summary(loaded, _same)
        except loads_same.UncomparableError as e:
            raise Stop("load_failed", detail=e.kind) from None
        del loaded  # the source's entries are not kept while the copy is written and loaded
        gc.collect()  # the job pauses the collector (ADR-0008 amendment): free the load's cycles before the next
        files = dict(fp.files)
        self._journals([p for p, s in files.items() if isinstance(s, Content | Absent)])
        for root_folder, _ in fp.documents:  # compared as the volume names them, links resolved (r1 F6)
            if _in(self._key(root_folder), target):  # its own files would change the source's listing
                raise Stop("cannot_place", root_folder, "inside_documents")
        named = (
            *files,
            *(cl.literal_folder(posixpath.normpath(g)) for g, _ in fp.globs),
            *(p for p, _ in fp.document_files),
        )
        for p in named:  # a path the ledger names inside the new folder would read the copy (r2 F5)
            if _in(target, self._key(cl.norm_path(p))):
                raise Stop("cannot_place", p, "inside_target")
        src = cl.SourceSet(
            root=root,
            files=tuple(p for p, s in files.items() if isinstance(s, Content)),
            missing=tuple(p for p, s in files.items() if isinstance(s, Absent)),
            globs=tuple(g for g, _ in fp.globs),
            documents=tuple(d for d, _ in fp.documents),
            document_files=tuple(cl.norm_path(p) for p, _ in fp.document_files),
            listed=tuple(join(d, n) for d, listing in fp.documents for n, _ in listing or ()),
        )
        lay = cl.layout(src)
        new_files = self._files(lay, src, files, fp)
        # every documents root that exists is made, even with nothing in it: Beancount reports a missing one (r1)
        roots = tuple(
            dict.fromkeys(d for r, listing in fp.documents if listing is not None and (d := _placed_or(lay, r)))
        )
        plan = CreatePlan(self.plan.job, self.plan.target, new_files, _placed(lay, root), self.plan.manifest, roots)
        try:
            create.check_plan(plan)
        except BadPlanError as e:  # `_files` names what it refuses; this is the writer's own check, kept
            raise Stop("cannot_place", detail=str(e)) from None
        self.fingerprint = fp
        self.layout = lay
        run.plan = plan

    def _key(self, p: str) -> str:
        """`p`'s `path_key` (links resolved, compared as its volume compares names). One that cannot be resolved
        refuses the copy (fail closed, r2 F10): it is never compared by its text."""
        try:
            return path_key(self.ports.fs, p)
        except OSError as e:
            raise Stop("source_unreadable", p, err=e.errno) from None
        except ValueError:
            raise Stop("source_unreadable", p) from None

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
            if loaded.read is not None and self.ports.observe(loaded.read.ledger_set) != loaded.read:
                raise Stop("source_changed")  # changed since Beancount failed on it (S0098 round 2, verifier F2)
            line = None if loaded.line is None else str(loaded.line)
            match loaded.reason:
                case "encrypted":
                    raise Stop("encrypted", loaded.subject)
                case "number-failed":  # Beancount's own load raised on the ledger (`ledger.load_failure`)
                    raise Stop("number_failed", loaded.subject, line)
                case "beancount-failed":
                    raise Stop("beancount_failed", loaded.subject, line)
                case _:
                    raise Stop("source_failed", loaded.subject)
        if not isinstance(dict(loaded.fingerprint.files).get(root), Content):
            raise Stop("source_missing", root)
        return loaded

    @tla_action("CP_Check")
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
        """Step 2d: every file the copy writes, ledger files first. A path two different sources would take (compared
        by `path_key`: one file named in NFC and NFD is one source, r2 F2) refuses the copy, naming one (r1 F2: nothing
        is ever dropped). Under a documents root, every `.finzytrack` folder (a workspace's own, at any depth: r2 F1)
        and every entry that is not a regular file (r1 F5) are left out; any other path through such a name refuses."""
        out: dict[str, tuple[str, NewFile]] = {}

        def add(path: str, data: bytes | Streamed) -> bool:
            dst = _placed(lay, path)
            if _reserved(dst):
                raise Stop("cannot_place", path, "reserved_name")
            key = _fold(dst)
            source = self._key(path)
            if key in out:
                if out[key][0] != source:
                    raise Stop("cannot_place", path, "two_sources")
                return False
            out[key] = (source, NewFile(dst, data))
            return True

        for p in src.files:
            state = files[p]
            assert isinstance(state, Content)  # noqa: S101 (`src.files` holds only read files)
            add(p, self._ledger_file(lay, src, p, state.sha256))
        for root, listing in fp.documents:
            for name, there in listing or ():
                path = join(root, name)
                if not there or _reserved(name) or not self.ports.regular(path):
                    continue
                if add(path, Streamed(path, None)):
                    self.documents.append((path, _placed(lay, path)))
        for raw, there in fp.document_files:  # a `document` entry's file, wherever it is (r1 F3; decision 3)
            path = cl.norm_path(raw)  # never opened unless a regular file: `chunks` refuses any other (r2 (h))
            if there and add(path, Streamed(path, None)):
                self.documents.append((path, _placed(lay, path)))
        return tuple(f for _, f in out.values())

    def _ledger_file(self, lay: cl.Layout, src: cl.SourceSet, p: str, sha: str) -> bytes | Streamed:
        """One ledger file: streamed when no line changes, else its rewritten bytes (read whole, as the load read
        it, and checked against the fingerprint first). What Beancount parses from it decides (`copy_layout`)."""
        try:
            data = b"".join(self.ports.source(p))
        except OSError as e:
            raise Stop("source_unreadable", p, err=e.errno) from None
        if fingerprint.content(data).sha256 != sha:
            raise Stop("source_changed", p)
        parsed = self.ports.parse(data, p)
        if p != src.root:
            parsed = cl.Parsed(parsed.includes, (), parsed.document_lines)  # Beancount reads that option from the root
        new = cl.rewrite(lay, p, data, parsed)
        if isinstance(new, cl.Refusal):
            raise Stop("cannot_rewrite", p, detail=str(new.line) if new.line else None)
        if new is None:
            return Streamed(p, sha)
        for n, o in new.quoted:
            self.quoted.setdefault(n, set()).add(o)
        return new.data

    # step 5 ---------------------------------------------------------------------------------------------------------
    @tla_action("CP_Load")
    def check(self, run: Run) -> None:
        self.release()  # held only for the copy's file reads (ADR-0062 Consequences)
        self.job.progress("checking_change")
        assert self.summary is not None  # noqa: S101 (set by `prepare`)
        assert self.fingerprint is not None  # noqa: S101
        assert self.layout is not None  # noqa: S101
        folder = run.folder
        quoted = tuple((n, next(iter(o))) for n, o in self.quoted.items() if len(o) == 1)  # ambiguous: left (r1 F4)
        request = copy_check.CheckRequest(join(folder, run.plan.root), folder, self.layout, quoted)
        result = self.ports.check(request)  # a fresh process: nothing of the source's load reaches it (r2 V1)
        if result.outcome != "checked" or result.summary is None:
            raise Stop("load_failed", detail=result.subject)
        for p in result.read:
            if not _in(folder, p):  # a `document` entry's file too (r1 F3, verifier F2)
                raise Stop("load_outside", p)
        copied = result.summary
        diff = loads_same.first_difference(self.summary, copied)
        if diff is not None:
            where = diff.path if diff.path is not None and diff.path.startswith("/") else None  # not `<plugin>`
            if self.own_plugins:  # their code is not copied (BL-0202): the likely cause, said
                raise Stop("plugin_modules", where, self.own_plugins[0])
            raise Stop("load_differs", where, diff.what if diff.line is None or where is None else str(diff.line))
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


def _fold(name: str) -> str:
    """A path as a case-insensitive volume compares it."""
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", name).casefold())


def _reserved(path: str) -> bool:
    """Whether a component of `path` is Finzytrack's own (`.finzytrack`, `.finzytrack.ft-*`)."""
    return any(
        (c := _fold(part)) == create.MANIFEST_DIR or c.startswith(create.MANIFEST_DIR + create.TEMP_INFIX)
        for part in path.split("/")
    )


def _placed_or(lay: cl.Layout, p: str) -> str:
    """Where `p` goes in the copy ("" for the copy's folder itself)."""
    return cl.place(lay, p) or ""


def _placed(lay: cl.Layout, p: str) -> str:
    dst = cl.place(lay, p)
    if not dst:
        raise Stop("cannot_place", p)
    return dst
