"""The `copy` job in the engine (`ledger_engine.ledger.copy`; `design/specs/engine-jobs.md` §1 "Commit point", §8.1,
§8.2; ADR-0062 decision 2; S0081), over the in-memory file system, its sources and loads given by the test. Written
from the spec's lines and the owner's decisions of 2026-11-07:

- decision 1: "A stopped copy removes only what this run made, checked by identity … and by content. It keeps and
  names anything it is unsure of … After an engine death it removes nothing and names every entry at its names. The
  source ledger is only ever read, never written.";
- decision 2: "Copy fingerprints each source file before reading it, and again just before its commit point. If
  anything changed, it stops …, cleans up its own files and writes no manifest. It never produces a mixed copy.";
- decision 3: "Copy every file the ledger loads into the new workspace folder. Rewrite only the include lines and the
  documents option that point outside";
- §8.2 step 2: "the copy reads its sources and is refused with `RECOVERY_BLOCKED` if any `.open` journal names one";
  "the legacy-lock try and the Copy re-check of ADR-0062" (held or any error refuses; a free lock is held for the
  reads; a lock file that appears during the copy refuses it);
- the owner's memory-frugal principle (the orchestrator's brief): "copy files in streamed chunks, never whole files
  in memory, and read back by bounded reads or hashes";
- §1: a copy past its commit point is resolved from disk; `NoManifestOverIncompleteFolder` holds at every effect.
"""

from __future__ import annotations

import errno
import hashlib
import posixpath
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import pytest

from ledger_engine.fs.port import AclEntry, LegacyAbsent, LegacyHeld
from ledger_engine.ledger import copy_check, overlay
from ledger_engine.ledger.copy import CopyPlan, CopyPorts, outcome, run
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core.journal import FileEntry, Record, State
from ledger_engine.ledger.core.ledger_set import Absent, Content, FileState, Fingerprint, LedgerSet
from ledger_engine.ledger.create import MANIFEST_DIR, Created, CreateOutcome, NewFile, NotCreated
from ledger_engine.ledger.paths import path_key
from tests.fakes.memfs import Crash, MemFS

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-22"), pytest.mark.inv("INV-32")]

U = "/u"
T = "/u/copy"
ALD = "/app"
B = "/s/books"
ROOT = f"{B}/main.beancount"
PRICES = f"{B}/prices/p.beancount"
RATES = "/s/shared/r.beancount"
DOCS = "/s/docs"
PDF = f"{DOCS}/Assets/Bank/2026-01-07.x.pdf"
MAIN = (
    b'option "title" "Books"\n'
    b'option "documents" "../docs"\n'
    b"2026-01-01 open Assets:Cash EUR\n"
    b'include "prices/p.beancount"\n'
    b'include "../shared/r.beancount"\n'
)
COPIED_MAIN = MAIN.replace(b"../docs", b"documents").replace(b"../shared/r.beancount", b"outside/r.beancount")
SOURCES: dict[str, bytes] = {
    ROOT: MAIN,
    PRICES: b"2026-01-02 price EUR 1.10 USD\n",
    RATES: b"2026-01-03 price EUR 1.11 USD\n",
    PDF: b"%PDF statement " * 20,
}
LEDGER_FILES = (ROOT, PRICES, RATES)
WS_JSON = b'{"format": 1, "min_reader": 1, "lineage_id": "7e1c3b52-6a0e-4f43-9d2c-8f1b2c3d4e5f"}\n'
PLAN = CopyPlan("j8", T, ROOT, (NewFile("workspace.json", WS_JSON),), ALD, ())
EXPECTED: dict[str, bytes] = {
    f"{T}/main.beancount": COPIED_MAIN,
    f"{T}/prices/p.beancount": SOURCES[PRICES],
    f"{T}/outside/r.beancount": SOURCES[RATES],
    f"{T}/documents/Assets/Bank/2026-01-07.x.pdf": SOURCES[PDF],
}
MANIFEST = {f"{T}/{MANIFEST_DIR}/workspace.json": WS_JSON}
CHUNK = 7
_TITLE = re.compile(rb'^option "title" "([^"]*)"', re.MULTILINE)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Job:
    phases: list[str] = field(default_factory=lambda: [])
    gated: int = 0
    at_gate: Callable[[], None] | None = None

    def progress(self, phase: str) -> None:
        self.phases.append(phase)

    def gate(self) -> None:
        self.gated += 1
        if self.at_gate is not None:
            self.at_gate()


@dataclass
class Lock:
    world: World
    path: str

    def release(self) -> None:
        self.world.log.append(f"release {self.path}")
        self.world.held.discard(self.path)


@dataclass
class World:
    """The sources (plain bytes: the source is never on the fake, so nothing of the job can write it), the fake disk
    of the target and the journals, the legacy locks' states, and a log of what the ports were asked."""

    fs: MemFS
    src: dict[str, bytes] = field(default_factory=lambda: dict(SOURCES))
    locks: dict[str, list[str]] = field(default_factory=lambda: {})  # path -> answers in order ("absent", "free"…)
    held: set[str] = field(default_factory=lambda: set())
    log: list[str] = field(default_factory=lambda: [])
    on_source: Callable[[str], None] | None = None
    on_copy_load: Callable[[], None] | None = None
    copy_title: bytes | None = None
    copy_parses: tuple[str, ...] = ()
    copy_documents: tuple[str, ...] = ()  # the files of `document` entries the copy's load checked
    plugins: tuple[tuple[str, None], ...] = ()  # the `plugin` directives the ledger names
    ledger_files: tuple[str, ...] = LEDGER_FILES

    # -- the ports ------------------------------------------------------------------------------------------------
    def try_lock(self, path: str) -> Lock | LegacyAbsent | LegacyHeld:
        answers = self.locks[path]
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        self.log.append(f"try {path} {answer}")
        if answer == "error":
            raise OSError(errno.EIO, "cannot lock")
        if answer == "held":
            return LegacyHeld()
        if answer == "absent":
            return LegacyAbsent()
        self.held.add(path)
        return Lock(self, path)

    def source(self, path: str) -> Iterator[bytes]:
        self.log.append(f"read {path}")
        if self.on_source is not None:
            self.on_source(path)
        if path not in self.src:
            raise FileNotFoundError(errno.ENOENT, "gone")
        data = self.src[path]
        for i in range(0, len(data), CHUNK):
            yield data[i : i + CHUNK]

    def file_state(self, path: str) -> FileState:
        return Content(sha(self.src[path])) if path in self.src else Absent()

    def fingerprint(self) -> Fingerprint:
        listing = {
            DOCS: {p[len(DOCS) + 1 :]: True for p in self.src if p.startswith(DOCS + "/")},
        }
        files = {p: self.file_state(p) for p in self.ledger_files}
        globs = {f"{B}/prices/p.beancount": [PRICES], f"{B}/../shared/r.beancount": [RATES]}
        return Fingerprint.of(ROOT, files, globs, listing, {})

    def observe(self, ledger_set: LedgerSet) -> Fingerprint:
        assert ledger_set == self.fingerprint().ledger_set
        return self.fingerprint()

    def load(self, root: str) -> overlay.LoadResult:
        """Beancount's load as far as the comparison reads it: the options (the title read from the root's bytes, the
        files parsed, the documents root, the plugins named)."""
        self.log.append(f"load {root}")
        return self.load_result(root)

    def check(self, request: copy_check.CheckRequest) -> copy_check.CheckResult:
        """The copy's load in its fresh process, as the check reads it (`copy_check.check_loaded`, the child's own)."""
        self.log.append(f"check {request.root}")
        return copy_check.check_loaded(self.load_result(request.root), request)

    def load_result(self, root: str) -> overlay.LoadResult:
        if root == ROOT:
            title = _TITLE.search(self.src.get(ROOT, b""))
            options = {
                "filename": ROOT,
                "include": sorted(self.ledger_files),
                "documents": ["../docs"],
                "title": title.group(1).decode() if title else "",
                "plugin": list(self.plugins),
            }
            return overlay.Loaded([], [], options, self.fingerprint())
        if self.on_copy_load is not None:
            self.on_copy_load()
        assert root == f"{T}/main.beancount"
        title = _TITLE.search(self.fs.file(root) or b"")
        files = [p for p in EXPECTED if p.endswith(".beancount")] + list(self.copy_parses)
        options = {
            "filename": root,
            "include": sorted(files),
            "documents": ["documents"],
            "title": self.copy_title.decode() if self.copy_title else (title.group(1).decode() if title else ""),
            "plugin": list(self.plugins),
        }
        documents = {p: True for p in self.copy_documents}
        fp = Fingerprint.of(root, {p: Content(sha(self.fs.file(p) or b"")) for p in files}, {}, {}, documents)
        return overlay.Loaded([], [], options, fp)

    def ports(self) -> CopyPorts:
        return CopyPorts(
            self.fs,
            self,  # the legacy locks
            load=self.load,
            source=self.source,
            observe=self.observe,
            file_state=self.file_state,
            regular=lambda path: path in self.src,
            check=self.check,
        )


def new_world(*, target: str = "absent", no_excl: bool = False, case_insensitive: bool = True) -> World:
    fs = MemFS(case_insensitive=case_insensitive)
    fs.outside_mkdirs(U, no_swap=no_excl, no_excl=no_excl)
    fs.outside_mkdirs(f"{ALD}/local")
    if target == "empty":
        fs.outside_mkdirs(T)
    return World(fs)


def go(w: World, job: Job | None = None, plan: CopyPlan = PLAN) -> tuple[CreateOutcome, Job]:
    job = job or Job()
    return run(plan, w.ports(), job), job


def copied_whole(fs: MemFS) -> bool:
    return all(fs.file(p) == b for p, b in {**EXPECTED, **MANIFEST}.items())


def exists(fs: MemFS, path: str) -> bool:
    return any(p == path for p in fs.snapshot())


def not_copied(out: CreateOutcome, reason: str) -> NotCreated:
    assert isinstance(out, NotCreated), out
    assert out.reason == reason, out
    return out


# -- the happy path -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["absent", "empty"])
def test_a_copy_writes_every_file_rewrites_only_the_outside_lines_and_writes_the_manifest_last(target: str) -> None:
    w = new_world(target=target)
    out, job = go(w)
    assert out == Created(T)
    assert copied_whole(w.fs)
    assert job.phases == ["saving", "checking_change"]
    assert job.gated == 1
    assert w.src == SOURCES, "the source is only ever read"


def test_every_file_is_streamed_in_chunks_never_handed_over_whole() -> None:
    w = new_world()
    go(w)
    assert 0 < w.fs.largest_chunk <= CHUNK, "a document reaches the create in chunks"


# -- step 2a: the legacy locks (ADR-0062 decision 2) --------------------------------------------------------------

LOCK = "/legacy/data/.write.lock"


@pytest.mark.parametrize("answer", ["held", "error"])
def test_a_held_or_untried_legacy_lock_refuses_before_any_source_is_read(answer: str) -> None:
    w = new_world()
    w.locks[LOCK] = [answer]
    out, job = go(w, plan=CopyPlan("j8", T, ROOT, PLAN.manifest, ALD, (LOCK,)))
    nc = not_copied(out, "legacy_saving")
    assert nc.path == LOCK
    assert nc.left == ()
    assert not exists(w.fs, T), "the job removes the target folder it made"
    assert not any(e.startswith(("read", "load")) for e in w.log)
    assert job.gated == 0


def test_a_free_legacy_lock_is_held_through_the_reads_and_released_before_the_copys_load() -> None:
    w = new_world()
    w.locks[LOCK] = ["free"]
    out, _ = go(w, plan=CopyPlan("j8", T, ROOT, PLAN.manifest, ALD, (LOCK,)))
    assert out == Created(T)
    release = w.log.index(f"release {LOCK}")
    assert all(i < release for i, e in enumerate(w.log) if e.startswith("read"))
    assert release < w.log.index(f"check {T}/main.beancount")
    assert not w.held


def test_a_lock_file_that_appears_during_the_copy_refuses_it_before_its_manifest() -> None:
    w = new_world()
    w.locks[LOCK] = ["absent", "free"]  # the old app made its lock file (its first save) during the copy
    out, job = go(w, plan=CopyPlan("j8", T, ROOT, PLAN.manifest, ALD, (LOCK,)))
    not_copied(out, "legacy_saving")
    assert job.gated == 0
    assert not exists(w.fs, T)
    assert not w.held, "a lock taken by the re-check is released"


def test_a_lock_named_twice_is_tried_once() -> None:
    """A second `flock` of our own on the same file would see our first one as held and refuse the copy."""
    w = new_world()
    w.locks[LOCK] = ["free"]
    out, _ = go(w, plan=CopyPlan("j8", T, ROOT, PLAN.manifest, ALD, (LOCK, LOCK)))
    assert out == Created(T)
    assert [e for e in w.log if e.startswith("try")] == [f"try {LOCK} free"]


def test_a_new_folder_inside_a_documents_root_is_refused_before_anything_is_copied() -> None:
    """Its own files would change the source's documents listing, so the copy could never be "the same"."""
    w = new_world()
    w.fs.outside_mkdirs(DOCS)
    out, _ = go(w, plan=CopyPlan("j8", f"{DOCS}/copy", ROOT, PLAN.manifest, ALD, ()))
    nc = not_copied(out, "cannot_place")
    assert (nc.path, nc.detail, nc.left) == (DOCS, "inside_documents", ())
    assert w.fs.names_in(DOCS) == []


def test_a_held_lock_is_released_when_the_job_stops() -> None:
    w = new_world()
    w.locks[LOCK] = ["free"]
    w.src.pop(PRICES)  # a source that went away: the job stops after the lock was taken
    go(w, plan=CopyPlan("j8", T, ROOT, PLAN.manifest, ALD, (LOCK,)))
    assert not w.held


# -- step 2b: the source's load -----------------------------------------------------------------------------------


def test_a_missing_source_root_is_refused() -> None:
    w = new_world()
    w.src.pop(ROOT)
    nc = not_copied(go(w)[0], "source_missing")
    assert (nc.path, nc.left) == (ROOT, ())


def test_an_encrypted_source_file_is_refused() -> None:
    w = new_world()
    w.load_result = lambda root: overlay.LoadRefused("encrypted", f"{B}/x.gpg")
    nc = not_copied(go(w)[0], "encrypted")
    assert nc.path == f"{B}/x.gpg"


def test_a_source_load_that_cannot_run_is_refused() -> None:
    w = new_world()

    def boom(root: str) -> overlay.LoadResult:
        raise RuntimeError(root)

    w.load_result = boom
    not_copied(go(w)[0], "source_failed")


# -- r1: a refusal of step 2 makes nothing at all; the ledger's own plugins; a clash -----------------------------


def test_a_refusal_of_step_2_makes_nothing_not_even_the_target_folder() -> None:
    """Step 2 runs before step 1 (S0081 r1): a refused copy leaves no trace in the place the user chose."""
    w = new_world()
    w.locks[LOCK] = ["held"]
    go(w, plan=CopyPlan("j8", T, ROOT, PLAN.manifest, ALD, (LOCK,)))
    assert w.fs.effects == []


def test_a_copy_that_does_not_load_the_same_of_a_ledger_with_its_own_plugin_says_so() -> None:
    """r2 (the owner's "Fresh process"): the copy's load runs in a fresh process, so a plugin of the ledger's own that
    was not copied (BL-0202) makes it differ; the text names the plugin, never "the same"."""
    w = new_world()
    w.plugins = (("my_fx_rates", None), ("beancount.plugins.auto_accounts", None))
    w.copy_title = b"Other"
    out, job = go(w)
    nc = not_copied(out, "plugin_modules")
    assert nc.detail == "my_fx_rates"
    assert job.gated == 0
    assert not exists(w.fs, T)
    assert f"check {T}/main.beancount" in w.log, "the copy's load is the check's (a fresh process), not this one"


def test_a_ledger_with_only_beancounts_own_plugins_that_differs_is_load_differs() -> None:
    w = new_world()
    w.plugins = (("beancount.plugins.auto_accounts", None),)
    w.copy_title = b"Other"
    not_copied(go(w)[0], "load_differs")


def test_a_ledger_with_its_own_plugin_that_loads_the_same_is_copied() -> None:
    """A plugin that is not Beancount's (absent from the engine: an error on both sides, or copied in as a document)
    blocks nothing when the copy loads the same."""
    w = new_world()
    w.plugins = (("my_fx_rates", None),)
    assert go(w)[0] == Created(T)


def test_two_sources_that_would_take_one_name_refuse_and_none_is_dropped() -> None:
    """r1 F2: a path two different sources want is a refusal, naming the second, never a silent drop. (The sources are
    on a case-sensitive volume: two files; the target folds case.)"""
    w = new_world(case_insensitive=False)
    upper = f"{B}/Prices/p.beancount"  # another file on a case-sensitive source, the same name on the target
    w.src[upper] = b"2026-01-02 price EUR 1.20 USD\n"
    w.ledger_files = (*LEDGER_FILES, upper)
    nc = not_copied(go(w)[0], "cannot_place")
    assert nc.detail == "two_sources"
    assert nc.path in {upper, PRICES}, "the file named is one of the two"
    assert not exists(w.fs, T)


def test_a_new_folder_inside_the_legacy_root_is_refused_before_anything_is_made() -> None:
    """INV-26: v0.4 creates nothing under a legacy root (S0081 r1 verifier question)."""
    w = new_world()
    w.fs.outside_mkdirs(f"{U}/legacy/data")
    lock = f"{U}/legacy/data/.write.lock"
    w.locks[lock] = ["absent"]
    nc = not_copied(
        go(w, plan=CopyPlan("j8", f"{U}/legacy/copy", ROOT, PLAN.manifest, ALD, (lock,)))[0], "cannot_place"
    )
    assert (nc.path, nc.detail) == (f"{U}/legacy", "inside_legacy")
    assert w.fs.names_in(f"{U}/legacy") == ["data"]
    assert w.fs.effects == []


def test_a_path_that_cannot_be_resolved_refuses_never_compared_by_its_text() -> None:
    """r2 F10: fail closed. A legacy root whose folder cannot be searched cannot be compared with the target."""
    w = new_world()
    w.fs.outside_mkdirs("/z/legacy/data")
    w.fs.outside_acl("/z", AclEntry(deny=True, perms=frozenset({"search"})))
    lock = "/z/legacy/data/.write.lock"
    w.locks[lock] = ["absent"]
    nc = not_copied(go(w, plan=CopyPlan("j8", T, ROOT, PLAN.manifest, ALD, (lock,)))[0], "source_unreadable")
    assert nc.path == "/z/legacy"
    assert w.fs.effects == []


# -- step 2c: the journals (§8.1, INV-32, `NoCopyOverOpenJournal`) -------------------------------------------------


def put_journal(w: World, file: str, state: State, *, data: bytes | None = None) -> None:
    name = journal.record_name("s1", "j1")
    rec = Record(
        session="s1",
        job="j1",
        workspace_key="k1",
        lineage="0d4c1f1e-0b8a-4c2e-9a39-5b1f0f6f2a10",
        engine_build="dev",
        backup_dir=f"{ALD}/local/k1/backups/{name}",
        files=(
            FileEntry(
                canonical_path=file,
                path_key=path_key(w.fs, file),
                pre="0" * 64,
                post="1" * 64,
                backup=journal.backup_name(0, posixpath.basename(file)),
                temp=journal.temp_name(posixpath.basename(file), name),
                volume="1e0c8a4b-2f6d-4b9e-8c37-5a1d0e9f7b26",
            ),
        ),
        created_dirs=(),
    )
    jd = f"{ALD}/local/k1/journal"
    w.fs.outside_mkdirs(jd)
    w.fs.outside_write(f"{jd}/{journal.file_name(name, state)}", journal.encode(rec) if data is None else data)


@pytest.mark.parametrize("file", [ROOT, RATES])
def test_an_open_journal_naming_a_source_refuses_the_copy_before_anything_is_written(file: str) -> None:
    w = new_world(target="empty")
    put_journal(w, file, State.OPEN)
    nc = not_copied(go(w)[0], "recovery_blocked")
    assert nc.path == file
    assert w.fs.names_in(T) == [], "nothing written"


def test_an_open_journal_that_cannot_be_read_refuses_the_copy() -> None:
    w = new_world()
    put_journal(w, ROOT, State.OPEN, data=b"torn")
    nc = not_copied(go(w)[0], "recovery_blocked")
    assert nc.detail == "unreadable"


@pytest.mark.parametrize("folder", [f"{ALD}/local", f"{ALD}/local/k1/journal"])
def test_a_journal_folder_that_cannot_be_listed_refuses_the_copy(folder: str) -> None:
    """It may hold an `.open` record naming a source (fail closed; §8.1)."""
    w = new_world(target="empty")
    put_journal(w, "/s/other/x.beancount", State.OPEN)
    w.fs.outside_acl(folder, AclEntry(deny=True, perms=frozenset({"list"})))
    nc = not_copied(go(w)[0], "recovery_blocked")
    assert (nc.path, nc.detail) == (folder, "unlisted")
    assert w.fs.names_in(T) == []


@pytest.mark.parametrize("state", [State.CLOSED, State.RESOLVED, State.KEPT, State.PART])
def test_a_journal_that_is_not_open_blocks_nothing(state: State) -> None:
    w = new_world()
    put_journal(w, ROOT, state)
    assert go(w)[0] == Created(T)


def test_an_open_journal_of_another_file_blocks_nothing() -> None:
    w = new_world()
    put_journal(w, "/s/other/x.beancount", State.OPEN)
    assert go(w)[0] == Created(T)


# -- decision 2: a source that changes ----------------------------------------------------------------------------


def test_a_ledger_file_changed_after_the_load_is_never_copied_mixed() -> None:
    w = new_world()

    def edit(path: str) -> None:
        if path == RATES:
            w.src[RATES] = b"2026-01-03 price EUR 9.99 USD\n"

    w.on_source = edit
    out, job = go(w)
    nc = not_copied(out, "source_changed")
    assert nc.path == RATES
    assert job.gated == 0
    assert not exists(w.fs, T), "the job removes what it made"


def test_a_ledger_file_changed_between_its_check_and_its_copy_is_never_copied_mixed() -> None:
    """A ledger file whose lines stay is read once to look for lines to rewrite, then streamed: each read must hold
    the bytes the load parsed (mutant M10)."""
    w = new_world()
    reads: list[str] = []

    def edit_on_second_read(path: str) -> None:
        reads.append(path)
        if path == RATES and reads.count(RATES) == 2:
            w.src[RATES] = b"2026-01-03 price EUR 9.99 USD\n"

    w.on_source = edit_on_second_read
    nc = not_copied(go(w)[0], "source_changed")
    assert nc.path == RATES


def test_a_ledger_file_with_a_rewritten_line_changed_after_the_load_is_never_copied_mixed() -> None:
    """The root's lines are rewritten from bytes read whole, which must be the bytes the load parsed (mutant M11)."""
    w = new_world()

    def edit(path: str) -> None:
        if path == ROOT:
            w.src[ROOT] = MAIN + b"2026-01-09 open Assets:New EUR\n"

    w.on_source = edit
    nc = not_copied(go(w)[0], "source_changed")
    assert nc.path == ROOT


@pytest.mark.parametrize("what", [ROOT, PRICES, PDF, "new document"])
def test_a_source_changed_before_the_commit_point_refuses_and_leaves_no_manifest(what: str) -> None:
    w = new_world()

    def edit() -> None:
        if what == "new document":
            w.src[f"{DOCS}/Assets/Bank/2026-02-01.y.pdf"] = b"new"
        else:
            w.src[what] = w.src[what] + b"; edited\n"

    w.on_copy_load = edit
    out, job = go(w)
    not_copied(out, "source_changed")
    assert job.gated == 0
    assert not exists(w.fs, T)


def test_a_source_that_cannot_be_read_mid_copy_stops_and_its_part_is_removed() -> None:
    w = new_world()
    original = w.source

    def failing(path: str) -> Iterator[bytes]:
        it = original(path)
        if path == PDF:
            yield next(it)
            raise OSError(errno.EIO, "unplugged")
        yield from it

    w.source = failing
    nc = not_copied(go(w)[0], "source_unreadable")
    assert (nc.path, nc.errno, nc.left) == (PDF, errno.EIO, ())
    assert not exists(w.fs, T)


# -- step 5: "loads the same" --------------------------------------------------------------------------------------


def test_a_copy_that_does_not_load_the_same_is_refused() -> None:
    w = new_world()
    w.copy_title = b"Other"
    out, job = go(w)
    nc = not_copied(out, "load_differs")
    assert nc.detail == "options"
    assert job.gated == 0
    assert not exists(w.fs, T)


def test_a_copy_whose_document_entry_names_a_file_outside_its_folder_is_refused() -> None:
    """r1 F3, verifier F2: a `document` entry's file counts as read (a plugin's, which no line names, Limit (plugin
    documents))."""
    w = new_world()
    w.copy_documents = ("/s/elsewhere/x.pdf",)
    nc = not_copied(go(w)[0], "load_outside")
    assert nc.path == "/s/elsewhere/x.pdf"


def test_a_ledger_file_in_a_folder_named_finzytrack_is_refused_naming_it() -> None:
    """r1 verifier F5: every refusal names the file."""
    w = new_world()
    reserved = f"{B}/.finzytrack/x.beancount"
    w.src[reserved] = b"2026-01-02 price EUR 1.20 USD\n"
    w.ledger_files = (*LEDGER_FILES, reserved)
    nc = not_copied(go(w)[0], "cannot_place")
    assert (nc.path, nc.detail) == (reserved, "reserved_name")


def test_a_copy_whose_load_reads_outside_its_folder_is_refused() -> None:
    w = new_world()
    w.copy_parses = ("/s/shared/r.beancount",)
    nc = not_copied(go(w)[0], "load_outside")
    assert nc.path == "/s/shared/r.beancount"


# -- decision 1: the cleanup is the create's ------------------------------------------------------------------------


def test_a_file_another_program_changed_in_the_copy_is_kept_and_named() -> None:
    w = new_world()

    def theirs() -> None:
        w.fs.outside_write(f"{T}/prices/p.beancount", b"their bytes\n")

    w.on_copy_load = theirs
    w.copy_title = b"Other"  # and the job stops
    nc = not_copied(go(w)[0], "load_differs")
    assert nc.left == ("prices/p.beancount",)
    assert w.fs.file(f"{T}/prices/p.beancount") == b"their bytes\n"


# -- the sweeps: a crash, a power loss and an I/O error at every effect --------------------------------------------


def clean_effects(no_excl: bool) -> tuple[str, ...]:
    w = new_world(no_excl=no_excl)
    assert go(w)[0] == Created(T)
    return tuple(w.fs.effects)


def check_disk(fs: MemFS) -> None:
    """`NoManifestOverIncompleteFolder`: a manifest only over a whole copy."""
    if exists(fs, T) and MANIFEST_DIR in fs.names_in(T):
        assert copied_whole(fs), fs.snapshot()


def check_told(fs: MemFS) -> None:
    facts = outcome(fs, PLAN)
    if facts.manifest == "ours":
        assert copied_whole(fs)
        for state in fs.power_loss_states():
            assert copied_whole(state), "told copied, then a power loss took it back"
    else:
        assert facts.manifest == "absent", facts
        here = fs.names_in(T) if exists(fs, T) else []
        assert facts.left is not None
        assert set(here) <= set(facts.left) | {"."}, "after a death every entry in the target is named"


@pytest.mark.parametrize("no_excl", [False, True])
def test_a_crash_at_every_effect_never_leaves_a_manifest_over_an_incomplete_copy(no_excl: bool) -> None:
    for i in range(len(clean_effects(no_excl))):
        w = new_world(no_excl=no_excl)
        w.locks[LOCK] = ["free"]
        w.fs.inject(i)
        with pytest.raises(Crash):
            go(w, plan=CopyPlan("j8", T, ROOT, PLAN.manifest, ALD, (LOCK,)))
        assert not w.held, "a lock is released on every exit"
        check_disk(w.fs)
        for state in w.fs.power_loss_states():
            check_disk(state)
        check_told(w.fs)


@pytest.mark.parametrize("no_excl", [False, True])
@pytest.mark.parametrize("after", [False, True])
def test_an_io_error_at_every_effect_is_told_as_the_disk_holds_it(no_excl: bool, after: bool) -> None:
    for i in range(len(clean_effects(no_excl))):
        w = new_world(no_excl=no_excl)
        w.fs.inject(i, crash=False, err=errno.EIO, after=after)
        try:
            out, _ = go(w)
        except OSError:
            check_told(w.fs)
            continue
        check_disk(w.fs)
        if isinstance(out, Created):
            for state in w.fs.power_loss_states():
                assert copied_whole(state), (i, w.fs.effects[i])
        else:
            assert outcome(w.fs, PLAN).manifest != "ours", (i, w.fs.effects[i], out)
        check_told(w.fs)


def test_the_outcome_of_a_copy_is_ours_only_for_our_manifest() -> None:
    w = new_world()
    go(w)
    assert outcome(w.fs, PLAN).manifest == "ours"
    w.fs.outside_write(f"{T}/{MANIFEST_DIR}/workspace.json", b"{}\n")
    assert outcome(w.fs, PLAN).manifest == "foreign"


def test_the_outcome_names_every_entry_in_the_target_whatever_it_holds() -> None:
    w = new_world(target="empty")
    w.fs.outside_write(f"{T}/a.beancount", b"x")
    w.fs.outside_mkdirs(f"{T}/sub")
    facts = outcome(w.fs, PLAN)
    assert (facts.manifest, facts.left) == ("absent", ("a.beancount", "sub"))


def test_the_outcome_of_a_target_that_does_not_exist_names_nothing() -> None:
    facts = outcome(new_world().fs, PLAN)
    assert (facts.manifest, facts.left) == ("absent", ())


# -- the plan -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "plan",
    [
        CopyPlan("j8", T, "main.beancount", PLAN.manifest, ALD, ()),
        CopyPlan("j8", T, ROOT, PLAN.manifest, "app", ()),
        CopyPlan("j8", T, ROOT, PLAN.manifest, ALD, ("lock",)),
        CopyPlan("J8", T, ROOT, PLAN.manifest, ALD, ()),
        CopyPlan("j8", "u/copy", ROOT, PLAN.manifest, ALD, ()),
        CopyPlan("j8", T, f"{B}/../x.beancount", PLAN.manifest, ALD, ()),
        CopyPlan("j8", T, ROOT, PLAN.manifest, ALD, ("/x.lock",)),  # r2 F9: `<root>/data/.write.lock` only
        CopyPlan("j8", T, ROOT, PLAN.manifest, ALD, ("/legacy/write.lock",)),
    ],
)
def test_a_malformed_plan_is_refused_before_anything_is_read_or_written(plan: CopyPlan) -> None:
    from ledger_engine.ledger.writer import BadPlanError

    w = new_world()
    with pytest.raises(BadPlanError):
        go(w, plan=plan)
    assert w.log == []
    assert w.fs.effects == []
