"""§6.6's reading of the disk after a save's engine died in the session (`ledger_engine.ledger.save.outcome`; S0063),
over the in-memory file system with its durable layer. Written from the spec's lines: "the sidecar first
`durable_sync`s the job's journal directory, then decides the outcome from disk ... so it never tells an outcome from
a record a power loss could still take back (a `.closed` rename, or an `.open` rename, made just before the engine
died; model finding F1)"; "no record → not saved; the targets are hashed again, and "Your ledger is unchanged" is said
only if they equal the pre hashes (ADR-0012 F-2)". And the save's own folders over the same durable layer (round 1:
the journal and backups folders, and their parents, durable before the commit point)."""

from __future__ import annotations

import errno
from collections.abc import Mapping
from dataclasses import replace

import pytest

from ledger_engine.fs.port import LegacyAbsent
from ledger_engine.ledger import overlay
from ledger_engine.ledger import save as sv
from ledger_engine.ledger.core.ledger_set import Content, Fingerprint, digest
from ledger_engine.ledger.writer import Change, sha256
from tests.fakes.memfs import Crash, MemFS, MemLocks

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
# `mkdir` and that sync; the app's folder made by a start just before). Otherwise
# a power loss can drop the folder, with the `.open` journal and every backup, while the swapped ledger files stay.

A, B = b"a pre\n", b"b pre\n"
FP = Fingerprint("/L/a.bean", (("/L/a.bean", Content(sha256(A))), ("/L/b.bean", Content(sha256(B)))), (), (), ())
CHAIN = (ALD, f"{ALD}/local", f"{ALD}/local/k1", JD, f"{ALD}/local/k1/backups")


class NoLegacy:
    def try_lock(self, path: str) -> LegacyAbsent:
        return LegacyAbsent()


class Job:
    def progress(self, phase: str) -> None: ...
    def gate(self) -> None: ...
    def committed(self) -> None: ...


def _loaded(root: str, candidates: Mapping[str, bytes]) -> overlay.LoadResult:
    return overlay.Loaded([], [], {}, FP)


def save_world(not_durable: str) -> MemFS:
    """The ledger `a.bean`, `b.bean`, and the app folder whose `not_durable` folder exists but was never synced."""
    fs = MemFS()
    fs.outside_mkdirs("/L")
    fs.outside_write("/L/a.bean", A)
    fs.outside_write("/L/b.bean", B)
    parent, _, name = not_durable.rpartition("/")
    fs.outside_mkdirs(parent or "/")
    with fs.open_dir(parent or "/") as h:
        h.mkdir(name)  # the earlier engine's mkdir; it died before the folder's sync
    return fs


def two_file_plan(**kw: object) -> sv.SavePlan:
    plan = sv.SavePlan(
        session="s1",
        job="j7",
        workspace_key="k1",
        lineage="lin1",
        engine_build="b",
        root="/L/a.bean",
        changes=(Change("/L/a.bean", sha256(A), b"a post\n"), Change("/L/b.bean", sha256(B), b"b post\n")),
        fingerprint=FP,
        mirror=digest(FP),
        legacy_locks=(),
        app_local_data=ALD,
    )
    return replace(plan, **kw)


def save_ports(fs: MemFS) -> sv.Ports:
    return sv.Ports(fs, MemLocks(), NoLegacy(), _loaded, observe=lambda fp: fp)


@pytest.mark.parametrize("not_durable", CHAIN)
def test_a_landed_save_keeps_its_closed_record_and_backups_through_a_power_loss(not_durable: str) -> None:
    fs = save_world(not_durable)
    out = sv.run(two_file_plan(), save_ports(fs), Job())
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
    sv.run(two_file_plan(), save_ports(fs), Job())
    swaps = [i for i, label in enumerate(labels) if "swap" in label]
    assert len(swaps) == 2, labels
    fs = save_world(not_durable)
    fs.inject(first + swaps[1], crash=True)  # the engine dies at the second swap: a.bean is swapped, b.bean is not
    with pytest.raises(Crash):
        sv.run(two_file_plan(), save_ports(fs), Job())
    torn = 0
    for state in fs.power_loss_states():
        if state.file("/L/a.bean") == b"a post\n" and state.file("/L/b.bean") == B:
            torn += 1
            assert state.file(f"{JD}/s1-j7.open") is not None, "half applied, and no journal for recover-all"
            assert state.file(f"{ALD}/local/k1/backups/s1-j7/0-a.bean") == A
    assert torn, "the cut must leave a half-applied state to check"
