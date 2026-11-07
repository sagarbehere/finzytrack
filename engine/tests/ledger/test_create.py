"""The `create` job in the engine (`ledger_engine.ledger.create`; `design/specs/engine-jobs.md` §1 "Commit point", §4,
§8.2, §10, §13's create properties; ADR-0064 P2; S0080), over the in-memory file system. Written from the spec's lines:

- §8.2 "A folder is a workspace only once it holds the `.finzytrack/` manifest, so the job writes the manifest
  **last**; a crash, a failure or a Cancel before that leaves a folder the user can delete, never a half-made
  workspace" (`NoManifestOverIncompleteFolder`);
- step 1 "The target must not exist, or must be an empty folder; a non-empty target is refused before anything is
  written" (`NonEmptyTargetUntouched`);
- step 3 "each file created exclusively … each directory made with a `mkdir` that fails if it exists … Only a
  collision with a name the job writes stops it, before its commit point, leaving the other file as it is"
  (`DroppedFileNeverReplaced`);
- step 4 "Read back every file" (`ForeignOverwriteCaughtByReadBack`);
- step 5 the load check on the files in the target; step 6 the commit point; step 7 the manifest written under a temp
  name, synced, renamed exclusively; a `.finzytrack` that appeared meanwhile is left as it is and the job's own temp
  removed;
- the delete advice: "You can delete it." only when the folder, listed when the job stops, holds nothing but the
  job's own files;
- §1 "After the commit point the job finishes or is resolved from disk": `outcome` reads the target after a
  `durable_sync` of it, so what it tells is what a power loss keeps.
"""

from __future__ import annotations

import errno
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import cache

import pytest

from ledger_engine.fs.port import AclEntry, Right
from ledger_engine.ledger.create import (
    MANIFEST_DIR,
    Created,
    CreatePlan,
    LoadResult,
    NewFile,
    NotCreated,
    Ports,
    outcome,
    run,
)
from ledger_engine.ledger.handles import PathMovedError
from ledger_engine.ledger.writer import BadPlanError
from tests.fakes.memfs import Crash, MemDir, MemFS

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-22")]

U = "/u"
T = "/u/new"
MAIN = b'option "title" "Books"\n2026-01-01 open Assets:Cash EUR\ninclude "prices/p.beancount"\n'
PRICES = b"2026-01-02 price EUR 1.10 USD\n"
WS_JSON = b'{"format": 1, "min_reader": 1, "lineage_id": "0d4c1f1e-0b8a-4c2e-9a39-5b1f0f6f2a10"}\n'
SETTINGS = b"theme: plain\n"
PLAN = CreatePlan(
    job="j7",
    target=T,
    files=(NewFile("main.beancount", MAIN), NewFile("prices/p.beancount", PRICES)),
    root="main.beancount",
    manifest=(NewFile("workspace.json", WS_JSON), NewFile("rules/r.yaml", SETTINGS)),
)
TEMP = f"{T}/{MANIFEST_DIR}.ft-j7"
OURS: dict[str, bytes] = {
    f"{T}/main.beancount": MAIN,
    f"{T}/prices/p.beancount": PRICES,
}
MANIFEST: dict[str, bytes] = {
    f"{T}/{MANIFEST_DIR}/workspace.json": WS_JSON,
    f"{T}/{MANIFEST_DIR}/rules/r.yaml": SETTINGS,
}
FOREIGN = b"saved by another program\n"


@dataclass
class Job:
    """The request's job context: `gate` records the commit point; `at_gate` lets an outside program act there."""

    phases: list[str] = field(default_factory=lambda: [])
    gated: int = 0
    effects_at_gate: int | None = None
    fs: MemFS | None = None
    at_gate: Callable[[], None] | None = None

    def progress(self, phase: str) -> None:
        self.phases.append(phase)

    def gate(self) -> None:
        self.gated += 1
        if self.fs is not None:
            self.effects_at_gate = len(self.fs.effects)
        if self.at_gate is not None:
            self.at_gate()


def good_load(fs: MemFS) -> Callable[[str], LoadResult]:
    """Beancount's load, as far as the check reads it: the files it parsed, which must be the job's, and its errors."""

    def load(root: str) -> LoadResult:
        assert root == f"{T}/main.beancount"
        assert fs.file(root) == MAIN, "the check loads the files in the target, as written"
        return LoadResult(errors=0, parsed=(root, f"{T}/prices/p.beancount"))

    return load


def new_fs(*, target: str = "absent", no_excl: bool = False) -> MemFS:
    fs = MemFS()
    fs.outside_mkdirs(U, no_swap=no_excl, no_excl=no_excl)
    if target == "empty":
        fs.outside_mkdirs(T)
    return fs


def go(fs: MemFS, job: Job | None = None, *, load: Callable[[str], LoadResult] | None = None, plan: CreatePlan = PLAN):
    job = job or Job()
    job.fs = fs
    return run(plan, Ports(fs, load or good_load(fs)), job), job


def created_whole(fs: MemFS) -> bool:
    return all(fs.file(p) == b for p, b in {**OURS, **MANIFEST}.items())


# -- the happy paths ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["absent", "empty"])
def test_a_create_writes_the_files_then_the_manifest_last_and_is_created(target: str) -> None:
    fs = new_fs(target=target)
    out, job = go(fs)
    assert out == Created(T)
    assert created_whole(fs)
    assert fs.names_in(T) == sorted([MANIFEST_DIR, "main.beancount", "prices"])
    assert job.gated == 1
    assert job.phases == ["saving", "checking_change"]
    gate = job.effects_at_gate
    assert gate is not None
    assert not any(MANIFEST_DIR in e for e in fs.effects[:gate]), "nothing of the manifest before the commit point"
    assert all(MANIFEST_DIR in e or e.startswith("sync") for e in fs.effects[gate:]), fs.effects[gate:]
    for state in fs.power_loss_states():
        assert all(state.file(p) == b for p, b in {**OURS, **MANIFEST}.items()), "created is durable when told"


def test_a_create_on_a_volume_without_the_exclusive_rename_is_created() -> None:
    fs = new_fs(no_excl=True)
    out, _ = go(fs)
    assert out == Created(T)
    assert created_whole(fs)


def test_the_files_are_written_with_a_plain_files_mode() -> None:
    fs = new_fs()
    go(fs)
    snap = fs.snapshot()
    assert snap[f"{T}/main.beancount"] == ("file", MAIN, 0o644)
    assert snap[f"{T}/{MANIFEST_DIR}/workspace.json"] == ("file", WS_JSON, 0o644)


# -- step 1: the target ------------------------------------------------------------------------------------------


def test_a_non_empty_target_is_refused_before_anything_is_written() -> None:
    fs = new_fs(target="empty")
    fs.outside_write(f"{T}/notes.txt", FOREIGN)
    before = fs.snapshot()
    out, job = go(fs)
    assert out == NotCreated("not_empty", T, "none")
    assert fs.effects == []
    assert fs.snapshot() == before
    assert job.gated == 0


def test_a_folder_holding_only_a_finder_file_is_not_empty() -> None:
    """Limit (empty folder): an entry of any name makes the folder non-empty."""
    fs = new_fs(target="empty")
    fs.outside_write(f"{T}/.DS_Store", b"\0\0\0\1Bud1")
    out, _ = go(fs)
    assert isinstance(out, NotCreated)
    assert out.reason == "not_empty"
    assert fs.effects == []


def test_a_file_at_the_target_is_not_a_folder() -> None:
    fs = new_fs()
    fs.outside_write(T, FOREIGN)
    out, _ = go(fs)
    assert out == NotCreated("not_a_folder", T, "none")
    assert fs.effects == []
    assert fs.file(T) == FOREIGN


def test_a_target_whose_folder_does_not_exist_is_refused() -> None:
    fs = MemFS()
    out, _ = go(fs)
    assert isinstance(out, NotCreated)
    assert out.reason == "no_parent"
    assert out.advice == "none"
    assert fs.effects == []


@pytest.mark.parametrize("target", ["absent", "empty"])
def test_a_folder_the_system_will_not_let_us_write_in_is_refused_before_any_write(target: str) -> None:
    fs = new_fs(target=target)
    fs.outside_deny(T if target == "empty" else U, Right.CHANGE_ENTRIES)
    out, _ = go(fs)
    assert isinstance(out, NotCreated)
    assert out.reason == "permission"
    assert out.path == (T if target == "empty" else U)
    assert fs.effects == []


# -- step 3: exclusive writes --------------------------------------------------------------------------------------


def drop_at(fs: MemFS, label: str, path: str, *, folder: bool = False) -> None:
    """Another program puts an entry at `path` just before the effect `label` (once)."""

    def hook(_i: int, lab: str) -> None:
        if lab == label:
            fs.on_effect = None
            if folder:
                fs.outside_mkdirs(path)
            else:
                fs.outside_write(path, FOREIGN)

    fs.on_effect = hook


def test_a_file_dropped_under_a_name_the_job_writes_is_never_replaced() -> None:
    fs = new_fs()
    drop_at(fs, f"create {T}:main.beancount", f"{T}/main.beancount")
    out, job = go(fs)
    assert isinstance(out, NotCreated)
    assert (out.reason, out.path, out.advice) == ("taken", f"{T}/main.beancount", "others")
    assert fs.file(f"{T}/main.beancount") == FOREIGN
    assert MANIFEST_DIR not in fs.names_in(T)
    assert job.gated == 0


def test_a_folder_dropped_under_a_folder_name_the_job_makes_stops_it() -> None:
    fs = new_fs()
    drop_at(fs, f"mkdir {T}:prices", f"{T}/prices", folder=True)
    out, _ = go(fs)
    assert isinstance(out, NotCreated)
    assert (out.reason, out.path) == ("taken", f"{T}/prices")
    assert fs.names_in(f"{T}/prices") == []
    assert MANIFEST_DIR not in fs.names_in(T)


def test_a_file_under_another_name_is_left_alone_and_only_spoils_the_delete_advice() -> None:
    fs = new_fs()
    drop_at(fs, f"create {T}:main.beancount", f"{T}/.DS_Store")
    out, _ = go(fs)
    assert out == Created(T)
    assert fs.file(f"{T}/.DS_Store") == FOREIGN


# -- step 4: read-back ---------------------------------------------------------------------------------------------


def test_a_foreign_overwrite_before_the_read_back_stops_the_job_before_its_manifest() -> None:
    fs = new_fs()

    def overwrite(_i: int, lab: str) -> None:
        if lab.startswith(f"sync {T}:"):
            fs.on_effect = None
            fs.outside_write(f"{T}/main.beancount", FOREIGN)

    fs.on_effect = overwrite
    out, job = go(fs)
    assert isinstance(out, NotCreated)
    assert (out.reason, out.path, out.advice) == ("read_back", f"{T}/main.beancount", "others")
    assert fs.file(f"{T}/main.beancount") == FOREIGN
    assert MANIFEST_DIR not in fs.names_in(T)
    assert job.gated == 0


# -- step 5: the load check ----------------------------------------------------------------------------------------


def test_a_ledger_that_loads_with_errors_is_not_created() -> None:
    fs = new_fs()
    out, job = go(fs, load=lambda _root: LoadResult(errors=2, parsed=(f"{T}/main.beancount",)))
    assert isinstance(out, NotCreated)
    assert (out.reason, out.detail, out.advice) == ("load_errors", "2", "delete")
    assert MANIFEST_DIR not in fs.names_in(T)
    assert job.gated == 0


def test_a_load_that_parses_a_file_the_job_did_not_write_is_not_created() -> None:
    fs = new_fs()
    out, _ = go(fs, load=lambda _root: LoadResult(errors=0, parsed=(f"{T}/main.beancount", "/u/other.beancount")))
    assert isinstance(out, NotCreated)
    assert (out.reason, out.path) == ("load_outside", "/u/other.beancount")
    assert MANIFEST_DIR not in fs.names_in(T)


@pytest.mark.parametrize("exc", [RuntimeError("a plugin raised"), OSError(errno.EIO, "io")])
def test_a_load_that_cannot_run_is_not_created(exc: Exception) -> None:
    fs = new_fs()

    def load(_root: str) -> LoadResult:
        raise exc

    out, _ = go(fs, load=load)
    assert isinstance(out, NotCreated)
    assert out.reason == "load_failed"
    assert MANIFEST_DIR not in fs.names_in(T)


# -- step 7: the manifest last ----------------------------------------------------------------------------------


@pytest.mark.parametrize("no_excl", [False, True])
def test_a_finzytrack_folder_that_appears_at_the_commit_point_is_left_and_ours_removed(no_excl: bool) -> None:
    fs = new_fs(no_excl=no_excl)
    theirs = f"{T}/{MANIFEST_DIR}/workspace.json"

    def put_theirs() -> None:
        fs.outside_mkdirs(f"{T}/{MANIFEST_DIR}")
        fs.outside_write(theirs, FOREIGN)

    job = Job(at_gate=put_theirs)
    out, _ = go(fs, job)
    assert isinstance(out, NotCreated)
    assert out.reason == "manifest_taken"
    assert fs.file(theirs) == FOREIGN
    assert fs.names_in(f"{T}/{MANIFEST_DIR}") == ["workspace.json"]
    assert f"{MANIFEST_DIR}.ft-j7" not in fs.names_in(T), "the job removes its own temp folder"
    assert all(fs.file(p) == b for p, b in OURS.items()), "the job's ledger files stay"
    assert outcome(fs, PLAN).manifest == "foreign", "a folder holding other bytes is not ours"


def test_a_foreign_temp_folder_of_the_jobs_name_is_never_removed() -> None:
    """A `.finzytrack.ft-<job>` the job did not make is not the job's: it stops (`taken`) and leaves it whole."""
    fs = new_fs()
    theirs = f"{TEMP}/workspace.json"

    def put_theirs() -> None:
        fs.outside_mkdirs(TEMP)
        fs.outside_write(theirs, FOREIGN)

    out, _ = go(fs, Job(at_gate=put_theirs))
    assert isinstance(out, NotCreated)
    assert (out.reason, out.path, out.advice) == ("taken", TEMP, "others")
    assert fs.file(theirs) == FOREIGN
    assert MANIFEST_DIR not in fs.names_in(T)


@pytest.mark.parametrize("inside", [[], ["rules"]])
def test_an_empty_foreign_temp_folder_of_the_jobs_name_is_never_removed(inside: list[str]) -> None:
    fs = new_fs()

    def put_theirs() -> None:
        fs.outside_mkdirs(TEMP)
        for name in inside:
            fs.outside_mkdirs(f"{TEMP}/{name}")

    out, _ = go(fs, Job(at_gate=put_theirs))
    assert isinstance(out, NotCreated)
    assert out.reason == "taken"
    assert fs.names_in(TEMP) == inside, "the folders another program made stay"


def test_a_foreign_file_put_into_the_temp_folder_stops_the_job_and_is_never_removed() -> None:
    """Another program's file at a name the job writes inside its own temp folder: the job stops, removes only its
    own names (none here), and the foreign file and the folder that holds it stay."""
    fs = new_fs()
    theirs = f"{TEMP}/workspace.json"
    drop_at(fs, f"create {TEMP}:workspace.json", theirs)
    out, _ = go(fs)
    assert isinstance(out, NotCreated)
    assert (out.reason, out.path, out.advice) == ("taken", theirs, "others")
    assert fs.file(theirs) == FOREIGN
    assert MANIFEST_DIR not in fs.names_in(T)


def test_a_foreign_file_beside_ours_in_the_temp_folder_stays_when_ours_are_removed() -> None:
    fs = new_fs()
    theirs = f"{TEMP}/notes.txt"
    drop_at(fs, f"create {TEMP}:workspace.json", theirs)
    out, _ = go(fs, Job(at_gate=lambda: fs.outside_write(f"{T}/{MANIFEST_DIR}", FOREIGN)))
    assert isinstance(out, NotCreated)
    assert out.reason == "manifest_taken"
    assert fs.file(theirs) == FOREIGN, "never removed"
    assert fs.file(f"{TEMP}/workspace.json") is None, "the job's own names are removed"


@pytest.mark.parametrize("no_excl", [False, True])
def test_a_finzytrack_file_that_appears_at_the_commit_point_is_left_as_it_is(no_excl: bool) -> None:
    """On a volume without the exclusive rename too: a plain rename would replace the file (S0080 mutant E12)."""
    fs = new_fs(no_excl=no_excl)
    job = Job(at_gate=lambda: fs.outside_write(f"{T}/{MANIFEST_DIR}", FOREIGN))
    out, _ = go(fs, job)
    assert isinstance(out, NotCreated)
    assert out.reason == "manifest_taken"
    assert fs.file(f"{T}/{MANIFEST_DIR}") == FOREIGN


# -- the delete advice -------------------------------------------------------------------------------------------


def test_the_delete_advice_says_only_ours_when_the_folder_holds_only_the_jobs_files() -> None:
    fs = new_fs(target="empty")
    out, _ = go(fs, load=lambda _root: LoadResult(errors=1, parsed=()))
    assert isinstance(out, NotCreated)
    assert out.advice == "delete"


def test_a_file_another_program_added_spoils_the_delete_advice() -> None:
    fs = new_fs()

    def load(_root: str) -> LoadResult:
        fs.outside_write(f"{T}/prices/notes.txt", FOREIGN)
        return LoadResult(errors=1, parsed=())

    out, _ = go(fs, load=load)
    assert isinstance(out, NotCreated)
    assert out.advice == "others"


def test_a_file_of_ours_changed_by_another_program_spoils_the_delete_advice() -> None:
    fs = new_fs()

    def load(_root: str) -> LoadResult:
        fs.outside_write(f"{T}/prices/p.beancount", PRICES + b"; my own line\n")
        return LoadResult(errors=1, parsed=())

    out, _ = go(fs, load=load)
    assert isinstance(out, NotCreated)
    assert out.advice == "others"


# -- the plan ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "plan",
    [
        replace(PLAN, job="J7"),
        replace(PLAN, target="u/new"),
        replace(PLAN, target="/u/../new"),
        replace(PLAN, target="/"),
        replace(PLAN, files=()),
        replace(PLAN, manifest=()),
        replace(PLAN, root="other.beancount"),
        replace(PLAN, files=(*PLAN.files, NewFile("../escape.beancount", b""))),
        replace(PLAN, files=(*PLAN.files, NewFile("/abs.beancount", b""))),
        replace(PLAN, files=(*PLAN.files, NewFile("a//b.beancount", b""))),
        replace(PLAN, files=(*PLAN.files, NewFile("MAIN.beancount", b""))),
        replace(PLAN, files=(*PLAN.files, NewFile("prices", b""))),
        replace(PLAN, files=(*PLAN.files, NewFile(".finzytrack/workspace.json", b""))),
        replace(PLAN, files=(*PLAN.files, NewFile(".FinzyTrack", b""))),
        replace(PLAN, files=(*PLAN.files, NewFile(".finzytrack.ft-j7", b""))),
        replace(PLAN, files=(*PLAN.files, NewFile("secret.beancount.gpg", b""))),
        replace(PLAN, root="main.beancount.asc", files=(NewFile("main.beancount.asc", b""),)),
        replace(PLAN, manifest=(*PLAN.manifest, NewFile("../x", b""))),
        replace(PLAN, manifest=(*PLAN.manifest, NewFile("WORKSPACE.json", b""))),
    ],
)
def test_a_malformed_plan_is_refused_before_anything_is_read_or_written(plan: CreatePlan) -> None:
    fs = new_fs()
    with pytest.raises(BadPlanError):
        go(fs, plan=plan)
    assert fs.effects == []


# -- crashes, power loss and I/O errors at every effect -----------------------------------------------------------


@cache
def clean_effects(no_excl: bool) -> tuple[str, ...]:
    fs = new_fs(no_excl=no_excl)
    assert go(fs)[0] == Created(T)
    return tuple(fs.effects)


def check_disk(fs: MemFS) -> None:
    """Every state, crash or not, before or after a power loss: the manifest never over files that are not whole,
    and the manifest itself whole whenever it stands (`NoManifestOverIncompleteFolder`)."""
    if _exists(fs, T) and MANIFEST_DIR in fs.names_in(T):
        assert created_whole(fs), fs.snapshot()


def _exists(fs: MemFS, path: str) -> bool:
    return any(p == path for p in fs.snapshot())


def check_told(fs: MemFS) -> None:
    """`outcome` (§1: resolved from disk) answers what the disk holds, and what it answers is what every power loss
    after it keeps: "ours" only when the manifest and every file are whole and durable."""
    facts = outcome(fs, PLAN)
    if facts.manifest == "ours":
        assert created_whole(fs)
        for state in fs.power_loss_states():
            assert created_whole(state), "told created, then a power loss took it back"
    else:
        assert facts.manifest == "absent", facts
        for state in fs.power_loss_states():
            assert MANIFEST_DIR not in (state.names_in(T) if _exists(state, T) else []), "told not created"


@pytest.mark.parametrize("no_excl", [False, True])
def test_a_crash_at_every_effect_never_leaves_a_manifest_over_an_incomplete_folder(no_excl: bool) -> None:
    for i in range(len(clean_effects(no_excl))):
        fs = new_fs(no_excl=no_excl)
        fs.inject(i)
        with pytest.raises(Crash):
            go(fs)
        check_disk(fs)
        for state in fs.power_loss_states():
            check_disk(state)
        check_told(fs)
        if _exists(fs, T) and MANIFEST_DIR not in fs.names_in(T):
            assert outcome(fs, PLAN).advice in ("delete", "check", "none"), (i, fs.effects[i], fs.snapshot())


@pytest.mark.parametrize("no_excl", [False, True])
@pytest.mark.parametrize("after", [False, True])
def test_an_io_error_at_every_effect_is_told_as_the_disk_holds_it(no_excl: bool, after: bool) -> None:
    """An error answered before or after the effect took place: "created" only with the manifest whole and durable;
    "not created" only with no manifest of ours; an error the job cannot place is raised (the sidecar then reads the
    disk, `outcome`)."""
    for i in range(len(clean_effects(no_excl))):
        fs = new_fs(no_excl=no_excl)
        fs.inject(i, crash=False, err=errno.EIO, after=after)
        try:
            out, _ = go(fs)
        except OSError:
            check_told(fs)
            continue
        check_disk(fs)
        if isinstance(out, Created):
            for state in fs.power_loss_states():
                assert created_whole(state), (i, fs.effects[i])
        else:
            assert isinstance(out, NotCreated), out
            assert outcome(fs, PLAN).manifest != "ours", (i, fs.effects[i], out)
        check_told(fs)


def test_a_power_loss_after_a_create_keeps_it_or_leaves_no_manifest() -> None:
    fs = new_fs()
    go(fs)
    for state in fs.power_loss_states():
        check_disk(state)


# -- the outcome read ---------------------------------------------------------------------------------------------


def test_the_outcome_of_a_target_that_does_not_exist_is_no_manifest() -> None:
    facts = outcome(new_fs(), PLAN)
    assert (facts.manifest, facts.advice) == ("absent", "none")


def test_the_outcome_of_a_created_workspace_is_ours() -> None:
    fs = new_fs()
    go(fs)
    assert outcome(fs, PLAN).manifest == "ours"


def test_the_outcome_of_a_foreign_finzytrack_folder_is_foreign() -> None:
    fs = new_fs()
    go(fs, Job(at_gate=lambda: fs.outside_write(f"{T}/{MANIFEST_DIR}", FOREIGN)))
    facts = outcome(fs, PLAN)
    assert facts.manifest == "foreign"
    assert facts.advice == "others"


def test_the_outcome_of_a_finzytrack_folder_with_our_names_but_other_bytes_is_foreign() -> None:
    fs = new_fs()

    def put_theirs() -> None:
        for path in MANIFEST:
            fs.outside_mkdirs(path.rpartition("/")[0])
            fs.outside_write(path, FOREIGN)

    go(fs, Job(at_gate=put_theirs))
    assert outcome(fs, PLAN).manifest == "foreign"
    for path, data in MANIFEST.items():
        fs.outside_write(path, data)
    assert outcome(fs, PLAN).manifest == "ours"


def test_a_file_cut_short_after_a_death_asks_to_check_the_folder() -> None:
    fs = new_fs()
    i = clean_effects(False).index(f"write {T}:main.beancount")
    fs.inject(i, cut=5)
    with pytest.raises(Crash):
        go(fs)
    assert fs.file(f"{T}/main.beancount") == MAIN[:5]
    facts = outcome(fs, PLAN)
    assert (facts.manifest, facts.advice) == ("absent", "check"), "who wrote a cut file is not known after a death"


def test_the_outcome_syncs_the_target_before_it_reads() -> None:
    fs = new_fs()
    rename = next(k for k, e in enumerate(clean_effects(False)) if e.startswith(f"rename {T}:"))
    fs.inject(rename + 1)  # the death right after the rename, before the target's sync
    with pytest.raises(Crash):
        go(fs)
    before = len(fs.effects)
    assert outcome(fs, PLAN).manifest == "ours"
    assert fs.effects[before:] == [f"sync {T}:"]


def test_the_temp_folder_of_a_job_cut_in_its_manifest_write_asks_to_check_the_folder() -> None:
    fs = new_fs()
    k = next(k for k, e in enumerate(clean_effects(False)) if e.startswith(f"create {TEMP}:workspace.json"))
    fs.inject(k + 1)
    with pytest.raises(Crash):
        go(fs)
    assert f"{MANIFEST_DIR}.ft-j7" in fs.names_in(T)
    facts = outcome(fs, PLAN)
    assert (facts.manifest, facts.advice) == ("absent", "check")


def test_the_target_moved_away_during_the_writes_stops_the_job() -> None:
    fs = new_fs()

    def move(_i: int, lab: str) -> None:
        if lab == f"mkdir {T}:prices":
            fs.on_effect = None
            fs.outside_move(T, "/u/elsewhere")

    fs.on_effect = move
    out, _ = go(fs)
    assert isinstance(out, NotCreated)
    assert out.reason == "path_moved"
    assert MANIFEST_DIR not in fs.names_in("/u/elsewhere")


def test_the_target_moved_away_at_the_commit_point_stops_the_job_before_its_manifest() -> None:
    fs = new_fs()
    out, _ = go(fs, Job(at_gate=lambda: fs.outside_move(T, "/u/elsewhere")))
    assert isinstance(out, NotCreated)
    assert out.reason == "path_moved"
    assert MANIFEST_DIR not in fs.names_in("/u/elsewhere")
    assert not any(n.startswith(MANIFEST_DIR) for n in fs.names_in("/u/elsewhere"))


# -- S0080 round 1 (red team RT1, RT2, RT4, RT7; verifier V1, V3, V4) ----------------------------------------------


def put(fs: MemFS, what: str, path: str) -> None:
    """Another program's entry at `path`: an empty file, a prefix of our main file's bytes, all of them, or a folder."""
    match what:
        case "empty":
            fs.outside_write(path, b"")
        case "prefix":
            fs.outside_write(path, MAIN[:6])
        case "all":
            fs.outside_write(path, MAIN)
        case _:
            fs.outside_mkdirs(path)


@pytest.mark.parametrize("what", ["empty", "prefix", "all", "folder"])
def test_a_name_another_program_took_first_is_never_called_ours_whatever_it_holds(what: str) -> None:
    """Red team r1 RT1: the live job judges from the names it made, never from bytes, so another program's entry at
    one of its names, empty, a prefix of the job's bytes or even all of them, keeps "You can delete it." away."""
    fs = new_fs()

    def hook(_i: int, lab: str) -> None:
        if lab == f"create {T}:main.beancount":
            fs.on_effect = None
            put(fs, what, f"{T}/main.beancount")

    fs.on_effect = hook
    out, _ = go(fs)
    assert isinstance(out, NotCreated)
    assert out.reason == "taken", what
    assert out.advice == "others", what


@pytest.mark.parametrize(
    ("what", "path"),
    [
        ("empty", f"{T}/main.beancount"),
        ("prefix", f"{T}/main.beancount"),
        ("folder", f"{T}/prices"),
        ("folder", f"{T}/{MANIFEST_DIR}"),
    ],
)
def test_the_outcome_read_never_calls_an_entry_it_cannot_tell_ours(what: str, path: str) -> None:
    """Verifier r1 V4, red team r1 RT7: after a death, who made a name is not known; an entry that holds less than
    all of the job's bytes may be the user's, so the advice is to check, never to delete."""
    fs = new_fs(target="empty")
    put(fs, what, path)
    facts = outcome(fs, PLAN)
    assert facts.advice == "check", (what, path)


def test_a_manifest_changed_in_its_temp_folder_before_the_rename_is_never_created() -> None:
    """Red team r1 RT2: `.finzytrack/`'s files are read back before the rename (ADR-0008 step 6)."""
    fs = new_fs()

    def hook(_i: int, lab: str) -> None:
        if lab.startswith(f"rename {T}:"):
            fs.on_effect = None
            fs.outside_write(f"{TEMP}/workspace.json", FOREIGN)

    fs.on_effect = hook
    out, _ = go(fs)
    facts = outcome(fs, PLAN)
    assert not isinstance(out, Created) or facts.manifest == "ours", (out, facts)


@pytest.mark.parametrize("when", ["sync", "rename"])
def test_a_manifest_changed_around_the_rename_is_told_as_the_disk_holds_it(when: str) -> None:
    """Red team r1 RT2: before the temp's read-back (its folder's sync) the change stops the job before the rename;
    after the rename the job reads `.finzytrack` back and never answers "created" over other bytes."""
    fs = new_fs()
    label = f"sync {TEMP}:" if when == "sync" else f"sync {T}:"
    path = f"{TEMP}/workspace.json" if when == "sync" else f"{T}/{MANIFEST_DIR}/workspace.json"
    seen = {"n": 0}

    def hook(_i: int, lab: str) -> None:
        if lab == label:
            seen["n"] += 1
            if when == "sync" or seen["n"] == 2:  # the target's second sync is the one after the rename
                fs.on_effect = None
                fs.outside_write(path, FOREIGN)

    fs.on_effect = hook
    out, _ = go(fs)
    assert isinstance(out, NotCreated), out
    assert out.reason == "read_back"
    assert outcome(fs, PLAN).manifest != "ours"
    assert fs.file(path) == FOREIGN, "never removed"


def test_the_target_moved_just_before_the_rename_is_never_told_created_at_its_old_path() -> None:
    """Verifier r1 V1: the rename goes through the target's descriptor; `.finzytrack` is read back at the target's
    path, so a folder moved in that instant raises and the disk at the path tells (`outcome`)."""
    fs = new_fs()
    rename = next(e for e in clean_effects(False) if e.startswith(f"rename {T}:"))

    def move(_i: int, lab: str) -> None:
        if lab == rename:
            fs.on_effect = None
            fs.outside_move(T, "/u/elsewhere")

    fs.on_effect = move
    try:
        out, _ = go(fs)
    except PathMovedError:
        out = None
    assert out != Created(T)
    assert outcome(fs, PLAN).manifest == "absent"


def test_the_listing_never_reads_a_file_longer_than_the_jobs_own() -> None:
    """Red team r1 RT4: a big foreign file in the folder is never read into the engine (memory-frugal hosting)."""
    fs = new_fs()
    big = b"x" * 5_000_000
    read: list[int] = []
    original = MemDir.read

    def counting(self: MemDir, name: str) -> bytes | None:
        out = original(self, name)
        read.append(len(out or b""))
        return out

    def hook(_i: int, lab: str) -> None:
        if lab == f"create {T}:main.beancount":
            fs.on_effect = None
            fs.outside_write(f"{T}/movie.mov", big)
            fs.outside_write(f"{T}/main.beancount", big)

    fs.on_effect = hook
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(MemDir, "read", counting)
        out, _ = go(fs)
        facts = outcome(fs, PLAN)
    assert isinstance(out, NotCreated)
    assert (out.advice, facts.advice) == ("others", "others")
    assert max(read, default=0) < len(big)


def test_a_folder_the_job_made_and_left_empty_is_its_own_to_delete() -> None:
    fs = new_fs()
    fs.inject(clean_effects(False).index(f"mkdir {T}:prices"), crash=False, err=errno.EIO)
    out, _ = go(fs)
    assert isinstance(out, NotCreated)
    assert (out.reason, out.advice) == ("io_error", "delete")


def test_a_target_that_cannot_be_listed_after_a_stop_asks_to_check_it() -> None:
    """Verifier r1 V2 (engine side): a listing that fails is `check`, never "nothing written"."""
    fs = new_fs()

    def load(_root: str) -> LoadResult:
        fs.outside_deny(T, Right.CHANGE_ENTRIES)
        fs.outside_acl(T, AclEntry(deny=True, perms=frozenset({"list"})))
        return LoadResult(errors=1, parsed=())

    out, _ = go(fs, load=load)
    assert isinstance(out, NotCreated)
    assert out.advice == "check"


def test_every_answer_names_the_target_as_requested() -> None:
    """Red team r1 RT6: `Created.folder` is the request's target, live or read from disk alike."""
    fs = MemFS()
    fs.outside_mkdirs("/real/u")
    fs.outside_symlink("/u", "/real/u")
    out, _ = go(fs, load=lambda root: LoadResult(0, (root, root.replace("main.beancount", "prices/p.beancount"))))
    assert out == Created(T), "the target as requested, not its canonical path /real/u/new"
    assert fs.file("/real/u/new/main.beancount") == MAIN


def test_a_target_moved_after_the_temp_folder_is_written_gets_no_manifest_anywhere() -> None:
    """Verifier r1 V1: the temp folder's read-back, the job's last step before the rename, goes through the path
    guard, so a folder moved after the temp folder was written never becomes a workspace where the user did not ask
    for one."""
    fs = new_fs()

    def move(_i: int, lab: str) -> None:
        if lab == f"sync {TEMP}:":
            fs.on_effect = None
            fs.outside_move(T, "/u/elsewhere")

    fs.on_effect = move
    out, _ = go(fs)
    assert isinstance(out, NotCreated)
    assert out.reason == "path_moved"
    assert MANIFEST_DIR not in fs.names_in("/u/elsewhere")
