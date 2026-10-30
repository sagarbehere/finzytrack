"""§6.6's reading of the disk after a save's engine died in the session (`ledger_engine.ledger.save.outcome`; S0063),
over the in-memory file system with its durable layer. Written from the spec's lines: "the sidecar first
`durable_sync`s the job's journal directory, then decides the outcome from disk ... so it never tells an outcome from
a record a power loss could still take back (a `.closed` rename, or an `.open` rename, made just before the engine
died; model finding F1)"; "no record → not saved; the targets are hashed again, and "Your ledger is unchanged" is said
only if they equal the pre hashes (ADR-0012 F-2)"."""

from __future__ import annotations

import errno

import pytest

from ledger_engine.ledger import save as sv
from ledger_engine.ledger.writer import Change, sha256
from tests.fakes.memfs import MemFS

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


@pytest.mark.parametrize("state", ["open", "closed", "done", "mismatch", "kept"])  # nothing at the created path
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
