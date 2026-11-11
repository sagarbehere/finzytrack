"""§6.6's reading of the disk after a save's engine died in the session (`ledger_engine.ledger.save.outcome`; S0063),
over the in-memory file system with its durable layer. Written from the spec's lines: "the sidecar first
`durable_sync`s the job's journal directory, then decides the outcome from disk ... so it never tells an outcome from
a record a power loss could still take back (a `.closed` rename, or an `.open` rename, made just before the engine
died; model finding F1)"; "no record → not saved; the targets are hashed again, and "Your ledger is unchanged" is said
only if they equal the pre hashes (ADR-0012 F-2)". And the save's own folders over the same durable layer (round 1:
the journal and backups folders, and their parents, durable before the commit point)."""

from __future__ import annotations

import errno
from collections.abc import Callable
from dataclasses import replace

import pytest

from ledger_engine.fs.port import Resolved
from ledger_engine.ledger import save as sv
from ledger_engine.ledger.core.ledger_set import Absent, Content, Fingerprint, Unreadable
from ledger_engine.ledger.writer import Change, sha256
from tests.fakes.memfs import Crash, MemFS
from tests.mirror.fake_store import FakeStore, outside_publish

pytestmark = [pytest.mark.inv("INV-22"), pytest.mark.inv("INV-01")]

ALD = "/app"
JD = "/app/local/k1/journal"
PRE = b"2026-01-01 open Assets:Cash\n"
CHANGES = (Change("/L/main.bean", sha256(PRE), b""), Change("/L/new.bean", None, b""))


def world() -> MemFS:
    fs = MemFS()
    fs.outside_mkdirs(JD)
    fs.outside_mkdirs("/L")
    fs.outside_write("/L/main.bean", PRE)
    return fs


def facts(fs: MemFS) -> sv.OutcomeFacts:
    return sv.outcome(fs, ALD, "k1", "s1", "j7", CHANGES)


@pytest.mark.parametrize("state", ["open", "closed", "kept"])  # nothing at the created path (S0068: no `.done`)
def test_a_record_found_is_told_by_its_state(state: str) -> None:
    fs = world()
    fs.outside_write(f"{JD}/s1-j7.{state}", b"{}")
    assert facts(fs) == sv.OutcomeFacts(state)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("state", ["part", "resolved", None])
def test_with_no_record_a_part_or_resolved_the_targets_are_hashed_again(state: str | None) -> None:
    fs = world()
    if state is not None:
        fs.outside_write(f"{JD}/s1-j7.{state}", b"{}")
    seen = "none" if state is None else state
    assert facts(fs) == sv.OutcomeFacts(seen, unchanged=True)  # pyright: ignore[reportArgumentType]
    fs.outside_write("/L/main.bean", PRE + b"; an editor\n")
    assert facts(fs) == sv.OutcomeFacts(seen, unchanged=False, path="/L/main.bean")  # pyright: ignore[reportArgumentType]


def test_a_file_the_save_was_creating_that_now_exists_is_not_unchanged() -> None:
    fs = world()
    fs.outside_write("/L/new.bean", b"x")
    assert facts(fs) == sv.OutcomeFacts("none", unchanged=False, path="/L/new.bean")


def test_another_saves_records_are_not_this_ones() -> None:
    fs = world()
    fs.outside_write(f"{JD}/s1-j8.closed", b"{}")
    fs.outside_write(f"{JD}/s0-j7.open", b"{}")
    assert facts(fs) == sv.OutcomeFacts("none", unchanged=True)


def test_no_journal_folder_means_no_record() -> None:
    fs = MemFS()
    fs.outside_mkdirs("/L")
    fs.outside_write("/L/main.bean", PRE)
    assert facts(fs) == sv.OutcomeFacts("none", unchanged=True)


def test_a_record_in_two_states_is_unknown() -> None:
    fs = world()
    fs.outside_write(f"{JD}/s1-j7.open", b"{}")
    fs.outside_write(f"{JD}/s1-j7.closed", b"{}")
    assert facts(fs).record == "unknown"


@pytest.mark.parametrize(("src", "dst"), [("open", "closed"), ("part", "open")])
def test_a_rename_made_just_before_the_death_is_synced_before_it_is_told(src: str, dst: str) -> None:
    """Model finding F1: told from the folder only after its sync, so every power-loss state keeps what was told."""
    fs = world()
    fs.outside_write(f"{JD}/s1-j7.{src}", b"{}")
    with fs.open_dir(JD) as h:
        h.rename_excl(f"s1-j7.{src}", f"s1-j7.{dst}")  # the dead engine's last effect, not synced
    told = facts(fs)
    assert told.record == dst
    for state in fs.power_loss_states():
        assert state.names_in(JD) == [f"s1-j7.{dst}"]


def test_a_folder_whose_sync_fails_is_unknown() -> None:
    fs = world()
    fs.outside_write(f"{JD}/s1-j7.closed", b"{}")
    fs.inject(len(fs.effects), crash=False, err=errno.EIO)
    assert facts(fs) == sv.OutcomeFacts("unknown", errno=errno.EIO)


def test_an_open_record_names_a_file_the_save_was_creating_that_stands_there() -> None:
    """The undo never removes it (owner, 2026-10-30: "Never remove it (Recommended)"), so Reopen's text must not
    promise to undo the change: the outcome names the file."""
    fs = world()
    fs.outside_write(f"{JD}/s1-j7.open", b"{}")
    assert facts(fs) == sv.OutcomeFacts("open")
    fs.outside_write("/L/new.bean", b"x")
    assert facts(fs) == sv.OutcomeFacts("open", path="/L/new.bean")


# -- round 1: the save's own folders are durable before its commit point (verifier r1 F1) --------------------------
#
# Spec §6.1 step 8.2 "an `.open` journal is never torn" and §6.2 "A directory sync with nothing to flush costs
# nothing": each folder of `<app>/local/<key>/{journal,backups}`, the app's folder included, must be durable before
# the first swap, also when it was already there but its entry was never synced (an earlier engine died between its
# `mkdir` and that sync). The app's folder itself is the sidecar's since S0100 (below). Otherwise
# a power loss can drop the folder, with the `.open` journal and every backup, while the swapped ledger files stay.

A, B = b"a pre\n", b"b pre\n"
FP = Fingerprint("/L/a.bean", (("/L/a.bean", Content(sha256(A))), ("/L/b.bean", Content(sha256(B)))), (), (), ())
CHAIN = (f"{ALD}/local", f"{ALD}/local/k1", JD, f"{ALD}/local/k1/backups")
"""The save's own folders. The app's folder itself is not among them since S0100 (BL-0163 (a); owner, 2026-11-10:
"Sidecar makes it once (Recommended)"): the sidecar makes it and syncs its parent at start (`app.main`)."""


class Job:
    def progress(self, phase: str) -> None: ...
    def gate(self) -> None: ...
    def committed(self) -> None: ...


def save_world(not_durable: str) -> MemFS:
    """The ledger `a.bean`, `b.bean`, and the app folder whose `not_durable` folder exists but was never synced."""
    fs = MemFS()
    fs.outside_mkdirs("/L")
    fs.outside_write("/L/a.bean", A)
    fs.outside_write("/L/b.bean", B)
    outside_publish(fs, FP, "k1")  # the gate's fresh mirror (S0087)
    parent, _, name = not_durable.rpartition("/")
    fs.outside_mkdirs(parent or "/")
    with fs.open_dir(parent or "/") as h:
        h.mkdir(name)  # the earlier engine's mkdir; it died before the folder's sync
    return fs


def app_id(fs: MemFS) -> tuple[int, int]:
    """The app folder's identity in the fake, as the sidecar's start records it (S0102); (0, 0) when it is missing."""
    r = fs.resolve(ALD)
    return r.identity if not r.rest and r.identity is not None else (0, 0)


def two_file_plan(fs: MemFS, **kw: object) -> sv.SavePlan:
    plan = sv.SavePlan(
        session="s1",
        job="j7",
        workspace_key="k1",
        lineage="lin1",
        engine_build="b",
        root="/L/a.bean",
        changes=(Change("/L/a.bean", sha256(A), b"a post\n"), Change("/L/b.bean", sha256(B), b"b post\n")),
        fingerprint=FP,
        app_cache="/cache",
        app_local_data=ALD,
        app_folder=app_id(fs),
    )
    return replace(plan, **kw)


def save_ports(fs: MemFS) -> sv.Ports:
    return sv.Ports(fs, observe=lambda fp: fp, store=FakeStore())


@pytest.mark.parametrize("not_durable", CHAIN)
def test_a_landed_save_keeps_its_closed_record_and_backups_through_a_power_loss(not_durable: str) -> None:
    fs = save_world(not_durable)
    out = sv.run(two_file_plan(fs), save_ports(fs), Job())
    assert isinstance(out, sv.Saved), out
    for state in fs.power_loss_states():
        if state.file("/L/a.bean") == b"a post\n" or state.file("/L/b.bean") == b"b post\n":
            assert state.file(f"{JD}/s1-j7.closed") is not None, "its files swapped, its record gone"
            assert state.file(f"{ALD}/local/k1/backups/s1-j7/0-a.bean") == A
            assert state.file(f"{ALD}/local/k1/backups/s1-j7/1-b.bean") == B


@pytest.mark.parametrize("not_durable", CHAIN)
def test_a_save_cut_after_its_first_swap_never_leaves_a_half_applied_ledger_without_its_journal(
    not_durable: str,
) -> None:
    labels: list[str] = []
    fs = save_world(not_durable)
    first = len(fs.effects)
    fs.on_effect = lambda i, label: labels.append(label)
    sv.run(two_file_plan(fs), save_ports(fs), Job())
    swaps = [i for i, label in enumerate(labels) if "swap" in label]
    assert len(swaps) == 2, labels
    fs = save_world(not_durable)
    fs.inject(first + swaps[1], crash=True)  # the engine dies at the second swap: a.bean is swapped, b.bean is not
    with pytest.raises(Crash):
        sv.run(two_file_plan(fs), save_ports(fs), Job())
    torn = 0
    for state in fs.power_loss_states():
        if state.file("/L/a.bean") == b"a post\n" and state.file("/L/b.bean") == B:
            torn += 1
            assert state.file(f"{JD}/s1-j7.open") is not None, "half applied, and no journal for recover-all"
            assert state.file(f"{ALD}/local/k1/backups/s1-j7/0-a.bean") == A
    assert torn, "the cut must leave a half-applied state to check"


# -- S0100: the app folder is the sidecar's (BL-0163 (a)); the gate's unreadable file (BL-0163 (d)) ------------------
#
# Owner, 2026-11-10: "Sidecar makes it once (Recommended)": "The sidecar creates the app folder at start and syncs its
# parent once; if that fails, the app says so at start. The save and recovery then write and sync only inside the app
# folder, never in or above its parent."


def test_S0100_the_save_never_opens_or_syncs_the_app_folders_parent() -> None:
    fs = save_world(f"{ALD}/local")
    fs.outside_mkdirs(ALD)
    labels: list[str] = []
    fs.on_effect = lambda _, label: labels.append(label)
    out = sv.run(two_file_plan(fs), save_ports(fs), Job())
    assert isinstance(out, sv.Saved), out
    assert "sync /:" not in labels, labels
    assert [x for x in labels if x.startswith("mkdir /:")] == []
    assert f"sync {ALD}:" in labels, "the app folder itself is synced: `local/` is made in it"


def test_S0100_a_missing_app_folder_is_not_saved_with_its_own_reason_and_nothing_made_above_it() -> None:
    fs = MemFS()
    fs.outside_mkdirs("/L")
    fs.outside_write("/L/a.bean", A)
    fs.outside_write("/L/b.bean", B)
    outside_publish(fs, FP, "k1")
    before = fs.snapshot()
    out = sv.run(two_file_plan(fs), save_ports(fs), Job())
    assert isinstance(out, sv.NotSaved), out
    assert (out.reason.value, out.unchanged, out.entry) == ("app_folder_missing", True, ALD)
    assert fs.snapshot() == before, "nothing made in or above the app folder's parent"


def test_S0100_BL0163d_a_file_made_unreadable_after_the_preview_is_an_io_error_naming_it() -> None:
    """BL-0163 (d): the gate's observation finds a file it cannot read (a `chmod 000` after the preview): not saved,
    an I/O error naming that file, never "your ledger changed"."""
    fs = save_world(f"{ALD}/local")
    fs.outside_mkdirs(ALD)

    def observe(fp: Fingerprint) -> Fingerprint:
        files = tuple((p, Unreadable("EACCES") if p == "/L/b.bean" else s) for p, s in fp.files)
        return replace(fp, files=files)

    out = sv.run(two_file_plan(fs), sv.Ports(fs, observe=observe, store=FakeStore()), Job())
    assert isinstance(out, sv.NotSaved), out
    assert (out.reason.value, out.path, out.unchanged) == ("io_error", "/L/b.bean", True)


def test_S0100_BL0163d_a_file_whose_bytes_changed_is_still_ledger_changed() -> None:
    """@regression-guard (holds on 437d6298). Must pass: a readable change of bytes stays `ledger_changed`."""
    fs = save_world(f"{ALD}/local")
    fs.outside_mkdirs(ALD)

    def observe(fp: Fingerprint) -> Fingerprint:
        files = tuple((p, Content("0" * 64) if p == "/L/b.bean" else s) for p, s in fp.files)
        return replace(fp, files=files)

    out = sv.run(two_file_plan(fs), sv.Ports(fs, observe=observe, store=FakeStore()), Job())
    assert out == sv.Refused("ledger_changed", True)


def test_S0100_BL0163b_a_step_3_io_error_names_the_changed_file_apart(monkeypatch: pytest.MonkeyPatch) -> None:
    """BL-0163 (b) at the save's own steps: a path that cannot be resolved is `io_error` naming it; the re-hash's
    changed file is `changed`."""
    fs = save_world(f"{ALD}/local")
    fs.outside_mkdirs(ALD)
    fs.outside_write("/L/a.bean", b"an editor\n")
    real = fs.resolve

    def resolve(path: str) -> Resolved:
        if path == "/L/b.bean":
            raise PermissionError(errno.EACCES, "search denied")
        return real(path)

    monkeypatch.setattr(fs, "resolve", resolve)
    out = sv.run(two_file_plan(fs), save_ports(fs), Job())
    assert isinstance(out, sv.NotSaved), out
    assert (out.reason.value, out.path, out.unchanged, out.changed) == ("io_error", "/L/b.bean", False, "/L/a.bean")


# -- S0100, BL-0182: §6.6's "changed by another program" after a `.resolved` record -----------------------------------

PLANNED = {"/L/a.bean": (A, b"a post\n"), "/L/b.bean": (B, b"b post\n")}
TWO = (Change("/L/a.bean", sha256(A), b""), Change("/L/b.bean", sha256(B), b""))


def _effects_of_a_clean_save() -> int:
    fs = save_world(f"{ALD}/local")
    first = len(fs.effects)
    assert isinstance(sv.run(two_file_plan(fs), save_ports(fs), Job()), sv.Saved)
    return len(fs.effects) - first


def _resolved_after(fail: int, die: int, edit: int | None) -> tuple[MemFS, sv.OutcomeFacts] | None:
    """A save that fails at its effect `fail` (an I/O error, so it rolls back) and whose engine dies at its effect
    `die`, with an editor writing `/L/b.bean` just before its effect `edit`; the disk's outcome, when its record is
    `.resolved`."""
    fs = save_world(f"{ALD}/local")
    first = len(fs.effects)
    fs.inject(first + fail, crash=False, err=errno.EIO)
    fs.inject(first + die, crash=True)
    if edit is not None:

        def editor(i: int, _: str) -> None:
            if i == first + edit:
                fs.outside_write("/L/b.bean", b"an editor's version\n")

        fs.on_effect = editor
    try:
        sv.run(two_file_plan(fs), save_ports(fs), Job())
    except Crash:
        pass
    else:
        return None  # the engine did not die there
    fs.on_effect = None
    facts = sv.outcome(fs, ALD, "k1", "s1", "j7", TWO)
    return (fs, facts) if facts.record == "resolved" else None


def test_BL0182_without_another_program_a_resolved_record_always_reads_unchanged() -> None:
    """@regression-guard (BL-0182: it holds on 437d6298; it pins §6.6's text). Spec §6.6: after an engine death a
    `.resolved` record with a file not at its pre bytes says "changed by another
    program". Without any other program, every failure point of the save whose rollback resolved, cut by an engine
    death at every later effect, must read unchanged: our own rollback never leaves a file of ours not at pre."""
    n = _effects_of_a_clean_save()
    seen = 0
    for fail in range(n):
        for die in range(fail + 1, fail + 40):
            got = _resolved_after(fail, die, None)
            if got is not None:
                seen += 1
                assert got[1].unchanged is True, (fail, die, got[1])
    assert seen, "the sweep must reach a resolved record"


def test_BL0182_a_resolved_record_with_a_changed_file_holds_another_programs_bytes_never_ours() -> None:
    """@regression-guard (BL-0182, as above). With an editor writing `/L/b.bean` at any effect: a `.resolved` record
    whose file is not at its pre bytes holds the editor's bytes, never the save's post bytes (so "changed by another
    program" is true)."""
    n = _effects_of_a_clean_save()
    changed = 0
    for fail in range(n):
        for die in range(fail + 1, fail + 40, 3):
            for edit in range(0, die, 4):
                got = _resolved_after(fail, die, edit)
                if got is None or got[1].unchanged:
                    continue
                changed += 1
                fs, facts = got
                assert facts.path is not None
                assert fs.file(facts.path) != PLANNED[facts.path][1], (fail, die, edit, facts)
    assert changed, "the sweep must reach a resolved record with a changed file"


# -- S0100 round 1 (red team F1 High, verifier F1): the gate's unreadable file never says "unchanged" falsely --------


def _gate_with(observe_files: Callable[[Fingerprint], Fingerprint], **kw: object) -> sv.SaveOutcome:
    fs = save_world(f"{ALD}/local")
    fs.outside_mkdirs(ALD)
    return sv.run(two_file_plan(fs, **kw), sv.Ports(fs, observe=observe_files, store=FakeStore()), Job())


ONLY_B = (Change("/L/b.bean", sha256(B), b"b post\n"),)  # a.bean is in the set; this save does not write it


def test_S0100_r1_an_unreadable_include_the_plan_never_named_after_an_edit_is_ledger_changed() -> None:
    """Verifier r1 F1: another program edits the root to include `new.bean`, which cannot be read. The re-hash checks
    only `b.bean`, so "Your ledger is unchanged" would be false: `ledger_changed`."""

    def observe(fp: Fingerprint) -> Fingerprint:
        files = tuple((p, Content("0" * 64) if p == "/L/a.bean" else s) for p, s in fp.files)
        return replace(fp, files=(*files, ("/L/new.bean", Unreadable("EACCES"))))

    out = _gate_with(observe, changes=ONLY_B)
    assert out == sv.Refused("ledger_changed", True)


def test_S0100_r1_an_include_this_save_does_not_write_made_unreadable_is_ledger_changed() -> None:
    """Red team r1 F1: a file of the set the save does not write, edited then made unreadable: its bytes are not
    checked by the re-hash, so nothing may say the ledger is unchanged: `ledger_changed`."""

    def observe(fp: Fingerprint) -> Fingerprint:
        return replace(fp, files=tuple((p, Unreadable("EACCES") if p == "/L/a.bean" else s) for p, s in fp.files))

    out = _gate_with(observe, changes=ONLY_B)
    assert out == sv.Refused("ledger_changed", True)


def test_S0100_r1_an_unreadable_target_with_another_file_changed_is_ledger_changed() -> None:
    """Red team r1 F1: a target made unreadable while another file of the set changed readably: `ledger_changed`."""

    def observe(fp: Fingerprint) -> Fingerprint:
        files = tuple((p, Unreadable("EACCES") if p == "/L/b.bean" else s) for p, s in fp.files)
        return replace(fp, files=files, globs=(("/L/*.bean", ("/L/a.bean", "/L/b.bean", "/L/c.bean")),))

    out = _gate_with(observe, changes=ONLY_B)
    assert out == sv.Refused("ledger_changed", True)


def test_S0100_r1_a_file_standing_unreadable_where_the_save_creates_one_is_ledger_changed() -> None:
    """Round 1: only a file the plan read (at its pre bytes) and now cannot be read is an I/O error; a file that
    appeared where the save creates one, unreadable, is a change of the ledger (the plan saw nothing there)."""
    created = Change("/L/c.bean", None, b"c\n")
    fp = replace(FP, files=(*FP.files, ("/L/c.bean", Absent())))
    changes = (Change("/L/a.bean", sha256(A), b"a post\n"), Change("/L/b.bean", sha256(B), b"b post\n"), created)

    def observe(seen: Fingerprint) -> Fingerprint:
        return replace(seen, files=tuple((p, Unreadable("EACCES") if p == "/L/c.bean" else s) for p, s in seen.files))

    out = _gate_with(observe, changes=changes, fingerprint=fp)
    assert isinstance(out, sv.Refused), out
    assert out.reason == "ledger_changed", out
