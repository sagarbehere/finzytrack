"""The history trim (`design/specs/engine-jobs.md` §6.5 "The last 16 history records of each kind are kept per
workspace; older ones are deleted", INV-10; BL-0169; S0074), over the in-memory file system with crash, I/O-error and
power-loss injection at every effect of the trim.

Written from the spec and the slice's brief (`design/journal/S0074-history-trim.md`): the trim runs in the save's own
engine after the save landed; it reads and deletes only in `local/<key>/journal/` and `local/<key>/backups/`; a
save's history is its journal entries `<tag>.<suffix>` and its backups folder `backups/<tag>`; kinds `closed`,
`resolved`, `kept`, `leftover` (development-build files) and `orphan` (a backups folder with no journal entry) keep
their newest 16 each; it never touches an `.open` or `.part` journal or a backups folder one names (in any key), the
current save, anything it cannot classify, or anything outside its two folders; it fails closed and never fails the
save.
"""

from __future__ import annotations

import errno
from dataclasses import dataclass, replace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ledger_engine.fs.port import AclEntry, Kind
from ledger_engine.ledger import history
from ledger_engine.ledger import save as sv
from ledger_engine.ledger.core import history as core
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core.journal import FileEntry, Record
from ledger_engine.ledger.core.ledger_set import Content, Fingerprint, digest
from ledger_engine.ledger.writer import Change, sha256
from tests.fakes.memfs import Crash, MemFS

pytestmark = [pytest.mark.inv("INV-10"), pytest.mark.inv("INV-32"), pytest.mark.inv("INV-27")]

ALD = "/app"
LOCAL = f"{ALD}/local"
KD = f"{LOCAL}/k1"
JD = f"{KD}/journal"
BD = f"{KD}/backups"
OTHER_JD = f"{LOCAL}/k2/journal"
CURRENT = "s9-jcur"
KEEP = 16


def tag(i: int) -> str:
    return f"s0-j{i:03d}"


def world() -> MemFS:
    fs = MemFS()
    fs.outside_mkdirs(JD)
    fs.outside_mkdirs(BD)
    fs.outside_mkdirs("/L")
    fs.outside_write("/L/a.bean", b"ledger\n")
    return fs


def seed(fs: MemFS, t: str, *suffixes: str, backups: bool = True) -> None:
    """A save's history: its journal entries (oldest first, in the order given) and its backups folder."""
    for s in suffixes:
        fs.outside_write(f"{JD}/{t}.{s}", b"{}")
    if backups:
        fs.outside_mkdirs(f"{BD}/{t}")
        fs.outside_write(f"{BD}/{t}/0-a.bean", b"old " + t.encode())


def history_of(fs: MemFS, t: str) -> list[str]:
    """Every path of the save `t`'s history that exists now."""
    snap = fs.snapshot()
    return sorted(p for p in snap if p.startswith((f"{JD}/{t}.", f"{BD}/{t}")))


def trim(fs: MemFS, current: str = CURRENT) -> history.Trimmed:
    return history.trim(fs, ALD, "k1", current)


def present(fs: MemFS, t: str) -> bool:
    return bool(history_of(fs, t))


# -- the rule: 16 per kind, the newest ------------------------------------------------------------------------------


def test_twenty_closed_saves_leave_the_newest_sixteen_with_their_backups() -> None:
    fs = world()
    for i in range(20):
        seed(fs, tag(i), "closed")
    seed(fs, CURRENT, "closed")
    out = trim(fs)
    assert out == history.Trimmed(removed=5, left=0, skipped=False)  # 21 closed: the current one and 15 others stay
    assert [present(fs, tag(i)) for i in range(20)] == [False] * 5 + [True] * 15
    assert present(fs, CURRENT)
    assert len(fs.names_in(BD)) == KEEP
    assert len([n for n in fs.names_in(JD) if n.endswith(".closed")]) == KEEP


def test_each_kind_is_counted_on_its_own() -> None:
    fs = world()
    for i in range(17):
        seed(fs, tag(i), "closed")
    for i in range(100, 103):
        seed(fs, tag(i), "kept")
    for i in range(200, 202):
        seed(fs, tag(i), "resolved")
    out = trim(fs, current="s9-none")
    assert out.removed == 1
    assert not present(fs, tag(0))
    assert all(present(fs, tag(i)) for i in [*range(1, 17), 100, 101, 102, 200, 201])


@pytest.mark.parametrize("state", ["kept", "resolved"])
def test_kept_and_resolved_records_keep_sixteen_too(state: str) -> None:
    fs = world()
    for i in range(18):
        seed(fs, tag(i), state)
    trim(fs, current="s9-none")
    assert [present(fs, tag(i)) for i in range(18)] == [False] * 2 + [True] * 16


def test_development_build_files_are_their_own_kind_and_go_with_their_side_files() -> None:
    """ADR-0067: `.done`, `.mismatch` and `.r4-attempt-<n>` files are not records; the trim may delete them."""
    fs = world()
    for i in range(18):
        seed(fs, tag(i), "done", "r4-attempt-1", "r4-attempt-2")
    for i in range(100, 104):
        seed(fs, tag(i), "mismatch")
    trim(fs, current="s9-none")
    assert [present(fs, tag(i)) for i in range(18)] == [False] * 6 + [True] * 12  # 22 leftovers: 16 stay
    assert all(present(fs, tag(i)) for i in range(100, 104))


def test_backups_folders_without_a_record_keep_sixteen() -> None:
    """A save that stopped before its journal (or whose `.part` recover-all removed) leaves only its backups."""
    fs = world()
    for i in range(19):
        seed(fs, tag(i), backups=True)
    trim(fs, current="s9-none")
    assert [present(fs, tag(i)) for i in range(19)] == [False] * 3 + [True] * 16


def test_a_record_without_its_backups_folder_is_trimmed_by_its_record() -> None:
    fs = world()
    for i in range(17):
        seed(fs, tag(i), "closed", backups=i % 2 == 0)
    trim(fs, current="s9-none")
    assert not present(fs, tag(0))
    assert all(present(fs, tag(i)) for i in range(1, 17))


def test_the_current_save_stays_even_when_the_clock_says_it_is_the_oldest() -> None:
    """A clock set back: the save just landed must keep its record (spec §6.6 reads it after an engine death)."""
    fs = world()
    for i in range(20):
        seed(fs, tag(i), "closed")
    seed(fs, CURRENT, "closed")
    fs.outside_mtime(f"{JD}/{CURRENT}.closed", 0)
    fs.outside_mtime(f"{BD}/{CURRENT}", 0)
    trim(fs)
    assert present(fs, CURRENT)
    assert sorted(fs.names_in(BD)) == sorted([CURRENT, *(tag(i) for i in range(5, 20))])


def test_sixteen_or_fewer_deletes_nothing() -> None:
    fs = world()
    for i in range(16):
        seed(fs, tag(i), "closed")
    before = fs.snapshot()
    assert trim(fs, current="s9-none") == history.Trimmed(0, 0, False)
    assert fs.snapshot() == before


# -- what it never touches (must-not-pass) ---------------------------------------------------------------------------


def many_closed(fs: MemFS, n: int = 20, start: int = 10) -> None:
    for i in range(start, start + n):
        seed(fs, tag(i), "closed")


@pytest.mark.parametrize("state", ["open", "part"])
def test_an_open_or_part_journal_and_its_backups_are_never_touched(state: str) -> None:
    fs = world()
    seed(fs, tag(0), state)  # the oldest of all
    many_closed(fs)
    trim(fs, current="s9-none")
    assert history_of(fs, tag(0)) == [f"{BD}/{tag(0)}", f"{BD}/{tag(0)}/0-a.bean", f"{JD}/{tag(0)}.{state}"]
    assert fs.file(f"{BD}/{tag(0)}/0-a.bean") == b"old " + tag(0).encode()


def test_an_open_journal_beside_older_history_of_its_own_name_keeps_all_of_it() -> None:
    """A save named like an `.open` one (a dev `.done` file of that name): the whole name is protected."""
    fs = world()
    seed(fs, tag(0), "done", "open")
    many_closed(fs)
    trim(fs, current="s9-none")
    assert f"{JD}/{tag(0)}.done" in history_of(fs, tag(0))


def _record(t: str, backup_dir: str) -> bytes:
    session, _, job = t.partition("-")
    rec = Record(
        session=session,
        job=job,
        workspace_key="k2",
        lineage="lin2",
        engine_build="b",
        backup_dir=backup_dir,
        files=(
            FileEntry(
                canonical_path="/L/a.bean",
                path_key="/L/a.bean",
                pre=sha256(b"x"),
                post=sha256(b"y"),
                backup=journal.backup_name(0, "a.bean"),
                temp=journal.temp_name("a.bean", t),
            ),
        ),
        created_dirs=(),
    )
    return journal.encode(rec)


@pytest.mark.parametrize("state", ["open", "part"])
def test_an_open_or_part_journal_in_another_keys_folder_protects_a_backups_folder_of_its_name_here(state: str) -> None:
    fs = world()
    seed(fs, tag(0), backups=True)  # an orphan here, the oldest
    fs.outside_mkdirs(OTHER_JD)
    fs.outside_write(f"{OTHER_JD}/{tag(0)}.{state}", b"{}")  # unreadable: its name alone protects
    for i in range(10, 30):
        seed(fs, tag(i), backups=True)
    trim(fs, current="s9-none")
    assert present(fs, tag(0))


def test_a_readable_open_record_in_another_key_that_names_a_backups_folder_here_protects_it() -> None:
    """A record names a backups folder of its own name only (`journal._check`), wherever it is: its name protects."""
    fs = world()
    seed(fs, "s5-jother", backups=True)  # an orphan here, the oldest
    fs.outside_mkdirs(OTHER_JD)
    fs.outside_write(f"{OTHER_JD}/s5-jother.open", _record("s5-jother", f"{BD}/s5-jother"))
    for i in range(10, 30):
        seed(fs, tag(i), backups=True)
    trim(fs, current="s9-none")
    assert present(fs, "s5-jother")
    assert not present(fs, tag(10))  # the trim did run


def test_a_record_can_only_name_a_backups_folder_of_its_own_name() -> None:
    """Why the name alone protects: the record format refuses any other backups folder."""
    with pytest.raises(ValueError, match="backup_dir"):
        _record("s5-jother", f"{BD}/{tag(1)}")


def test_another_keys_journal_folder_that_cannot_be_listed_stops_the_whole_trim() -> None:
    fs = world()
    many_closed(fs)
    fs.outside_mkdirs(OTHER_JD)
    fs.outside_acl(OTHER_JD, AclEntry(deny=True, perms=frozenset({"list"})))
    before = fs.snapshot()
    assert trim(fs, current="s9-none") == history.Trimmed(0, 0, True)
    assert fs.snapshot() == before


def test_another_key_folder_without_a_journal_folder_does_not_stop_the_trim() -> None:
    fs = world()
    fs.outside_mkdirs(f"{LOCAL}/k3/backups")
    fs.outside_write(f"{LOCAL}/k4", b"a file in local/, not a key folder")
    many_closed(fs)
    assert trim(fs, current="s9-none") == history.Trimmed(removed=4, left=0, skipped=False)


@pytest.mark.parametrize("folder", ["journal", "backups"])
def test_a_link_in_place_of_the_journal_or_backups_folder_stops_the_whole_trim(folder: str) -> None:
    """Never delete through a link: it could lead into a ledger folder (INV-27, INV-30)."""
    fs = MemFS()
    fs.outside_mkdirs(KD)
    fs.outside_mkdirs("/L/journal")
    fs.outside_mkdirs("/L/backups")
    other = "backups" if folder == "journal" else "journal"
    fs.outside_mkdirs(f"{KD}/{other}")
    fs.outside_symlink(f"{KD}/{folder}", f"/L/{folder}")
    for i in range(20):
        fs.outside_write(f"{KD}/journal/{tag(i)}.closed", b"{}")
        fs.outside_mkdirs(f"{KD}/backups/{tag(i)}")
        fs.outside_write(f"{KD}/backups/{tag(i)}/0-a.bean", b"user's file")
    before = fs.snapshot()
    assert trim(fs, current="s9-none") == history.Trimmed(0, 0, True)
    assert fs.snapshot() == before


def test_a_link_in_place_of_the_key_folder_stops_the_whole_trim() -> None:
    fs = MemFS()
    fs.outside_mkdirs(LOCAL)
    fs.outside_mkdirs("/L/journal")
    fs.outside_mkdirs("/L/backups")
    fs.outside_symlink(KD, "/L")
    for i in range(20):
        fs.outside_write(f"/L/journal/{tag(i)}.closed", b"{}")
    before = fs.snapshot()
    assert trim(fs, current="s9-none").skipped
    assert fs.snapshot() == before


def test_a_link_in_place_of_the_local_folder_stops_the_whole_trim() -> None:
    fs = MemFS()
    fs.outside_mkdirs(ALD)
    fs.outside_mkdirs("/L/k1/journal")
    fs.outside_mkdirs("/L/k1/backups")
    fs.outside_symlink(LOCAL, "/L")
    for i in range(20):
        fs.outside_write(f"/L/k1/journal/{tag(i)}.closed", b"{}")
    before = fs.snapshot()
    assert trim(fs, current="s9-none").skipped
    assert fs.snapshot() == before


def test_a_backups_folder_holding_a_link_or_a_folder_is_left_with_its_record() -> None:
    fs = world()
    seed(fs, tag(0), "closed")
    seed(fs, tag(1), "closed")
    fs.outside_symlink(f"{BD}/{tag(0)}/1-b.bean", "/L/a.bean")
    fs.outside_mkdirs(f"{BD}/{tag(1)}/sub")
    many_closed(fs)
    out = trim(fs, current="s9-none")
    assert present(fs, tag(0))
    assert fs.file(f"{BD}/{tag(0)}/0-a.bean") is not None
    assert fs.file(f"{JD}/{tag(0)}.closed") is not None
    assert f"{BD}/{tag(1)}/sub" in history_of(fs, tag(1))
    assert fs.file(f"{JD}/{tag(1)}.closed") is not None
    assert fs.file("/L/a.bean") == b"ledger\n"
    assert out.left == 2


def test_a_backups_entry_that_is_a_link_or_a_file_is_left() -> None:
    fs = world()
    fs.outside_write(f"{JD}/{tag(0)}.closed", b"{}")
    fs.outside_symlink(f"{BD}/{tag(0)}", "/L")
    fs.outside_write(f"{BD}/{tag(1)}", b"a file, not ours")
    many_closed(fs)
    trim(fs, current="s9-none")
    assert fs.file(f"{JD}/{tag(0)}.closed") is not None
    assert fs.snapshot()[f"{BD}/{tag(0)}"] == ("link", "/L")
    assert fs.file(f"{BD}/{tag(1)}") == b"a file, not ours"
    assert fs.file("/L/a.bean") == b"ledger\n"


@pytest.mark.parametrize(
    "suffixes",
    [
        ("verifying",),  # a state a newer app may write
        ("closed", "verifying"),
        ("closed", "resolved"),  # two record states
        ("closed.bak",),
    ],
)
def test_a_save_whose_history_cannot_be_classified_is_left(suffixes: tuple[str, ...]) -> None:
    fs = world()
    seed(fs, tag(0), *suffixes)
    many_closed(fs)
    trim(fs, current="s9-none")
    assert len(history_of(fs, tag(0))) == len(suffixes) + 2


def test_a_journal_entry_that_is_not_a_regular_file_protects_its_save() -> None:
    fs = world()
    fs.outside_mkdirs(f"{JD}/{tag(0)}.closed")
    seed(fs, tag(0))
    fs.outside_write(f"{JD}/{tag(1)}.closed", b"{}")
    fs.outside_symlink(f"{JD}/{tag(1)}.resolved", "/L/a.bean")
    many_closed(fs)
    trim(fs, current="s9-none")
    assert present(fs, tag(0))
    assert f"{BD}/{tag(0)}/0-a.bean" in history_of(fs, tag(0))
    assert fs.file(f"{JD}/{tag(1)}.closed") is not None
    assert fs.file("/L/a.bean") == b"ledger\n"


def test_an_entry_whose_stat_fails_protects_its_save() -> None:
    fs = world()
    seed(fs, tag(0), "closed")
    fs.outside_acl(f"{JD}/{tag(0)}.closed", AclEntry(deny=True, perms=frozenset({"readattr"})))
    seed(fs, tag(1), "closed")
    fs.outside_acl(f"{BD}/{tag(1)}", AclEntry(deny=True, perms=frozenset({"readattr"})))
    many_closed(fs)
    trim(fs, current="s9-none")
    assert present(fs, tag(0))
    assert len(history_of(fs, tag(1))) == 3


def test_names_that_are_not_ours_are_never_touched() -> None:
    fs = world()
    for n in ("notes.txt", ".DS_Store", "s0-j000", "x.closed", "a-b-c.closed"):
        fs.outside_write(f"{JD}/{n}", b"not ours")
    for n in ("README", "x.y", "a-b-c", *(f"x{i}" for i in range(20))):  # more than 16: no kind counts them
        fs.outside_mkdirs(f"{BD}/{n}")
        fs.outside_write(f"{BD}/{n}/f", b"not ours")
    many_closed(fs)
    trim(fs, current="s9-none")
    for i in range(20):
        assert fs.file(f"{BD}/x{i}/f") == b"not ours"
    for n in ("notes.txt", ".DS_Store", "s0-j000", "x.closed", "a-b-c.closed"):
        assert fs.file(f"{JD}/{n}") == b"not ours"
    for n in ("README", "x.y", "a-b-c"):
        assert fs.file(f"{BD}/{n}/f") == b"not ours"


def test_nothing_outside_the_keys_journal_and_backups_folders_changes() -> None:
    """Kept copies are the user's files beside the ledger (INV-27, INV-30, INV-32); another key's history is its own."""
    fs = world()
    fs.outside_write(f"/L/a.bean.kept-{tag(10)}", b"the user's kept copy")
    fs.outside_mkdirs(f"{LOCAL}/k2/backups")
    fs.outside_mkdirs(OTHER_JD)
    for i in range(40, 60):
        fs.outside_write(f"{OTHER_JD}/{tag(i)}.closed", b"{}")
        fs.outside_mkdirs(f"{LOCAL}/k2/backups/{tag(i)}")
    fs.outside_write(f"{KD}/other.txt", b"x")
    fs.outside_mkdirs(f"{ALD}/locks")
    many_closed(fs)
    before = fs.snapshot()
    trim(fs, current="s9-none")
    after = fs.snapshot()
    outside = {p: v for p, v in before.items() if not p.startswith((f"{JD}/", f"{BD}/"))}
    assert {p: after.get(p) for p in outside} == outside


def test_a_file_that_cannot_be_removed_leaves_that_save_and_the_trim_goes_on() -> None:
    fs = world()
    seed(fs, tag(0), "closed")
    fs.outside_lock(f"{BD}/{tag(0)}/0-a.bean")
    many_closed(fs, n=18)
    out = trim(fs, current="s9-none")
    assert out == history.Trimmed(removed=2, left=1, skipped=False)
    assert fs.file(f"{JD}/{tag(0)}.closed") is not None  # its record stays, so the next trim tries again
    assert not present(fs, tag(10))
    assert not present(fs, tag(11))


def test_an_entry_that_appears_after_the_choice_leaves_that_save() -> None:
    """Belt and braces under the engine lock: each save's journal entries are listed again before any delete."""
    fs = world()
    many_closed(fs, n=18)  # 18: tag(10) and tag(11) are chosen

    def outside(i: int, label: str) -> None:
        if label.startswith("remove") and fs.file(f"{JD}/{tag(11)}.open") is None:
            fs.outside_write(f"{JD}/{tag(11)}.open", b"{}")

    fs.on_effect = outside
    out = trim(fs, current="s9-none")
    assert out == history.Trimmed(removed=1, left=1, skipped=False)
    assert not present(fs, tag(10))
    assert f"{BD}/{tag(11)}/0-a.bean" in history_of(fs, tag(11))
    assert fs.file(f"{JD}/{tag(11)}.closed") is not None


def test_a_failed_sync_of_the_backups_folder_leaves_every_record() -> None:
    """No record goes before its backups folder's removal is durable, so a power loss never leaves an orphan."""
    fs = world()
    many_closed(fs, n=18)
    labels: list[str] = []
    probe = world()
    many_closed(probe, n=18)
    probe.on_effect = lambda i, label: labels.append(label)
    trim(probe, current="s9-none")
    at = labels.index(f"sync {BD}:")
    fs.inject(len(fs.effects) + at, crash=False, err=errno.EIO)
    out = trim(fs, current="s9-none")
    assert out == history.Trimmed(removed=0, left=2, skipped=False)
    assert fs.file(f"{JD}/{tag(10)}.closed") is not None
    assert fs.file(f"{JD}/{tag(11)}.closed") is not None
    assert trim(fs, current="s9-none") == history.Trimmed(removed=2, left=0, skipped=False)


def test_no_local_folder_means_nothing_to_trim() -> None:
    fs = MemFS()
    assert trim(fs).skipped


# -- crashes, I/O errors and power losses at every effect ------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    fs: MemFS
    before: dict[str, tuple[object, ...]]  # every path before the trim
    protected: dict[str, tuple[object, ...]]  # path -> what must stand there whatever happens
    victims: tuple[str, ...]


def case() -> Case:
    fs = world()
    seed(fs, tag(0), "open")
    seed(fs, tag(1), "part")
    seed(fs, tag(2), "done", "r4-attempt-1")
    fs.outside_write(f"/L/a.bean.kept-{tag(3)}", b"kept")
    fs.outside_write(f"{JD}/notes.txt", b"n")
    for i in range(10, 28):
        seed(fs, tag(i), "closed")
    seed(fs, CURRENT, "closed")
    snap = fs.snapshot()
    keep_prefixes = (f"{JD}/{tag(0)}", f"{BD}/{tag(0)}", f"{JD}/{tag(1)}", f"{BD}/{tag(1)}", f"{JD}/{CURRENT}")
    protected = {
        p: v
        for p, v in snap.items()
        if p.startswith((*keep_prefixes, f"{BD}/{CURRENT}", "/L/")) or p == f"{JD}/notes.txt"
    }
    return Case(fs, snap, protected, (tag(10), tag(11), tag(12)))


def check(fs: MemFS, c: Case) -> None:
    snap = fs.snapshot()
    for p, v in c.protected.items():
        assert snap.get(p) == v, f"{p} touched"
    allowed = tuple(f"{JD}/{t}." for t in c.victims) + tuple(f"{BD}/{t}" for t in c.victims)
    for p, v in c.before.items():
        if p not in snap:
            assert p.startswith(allowed), f"{p} deleted"
        else:
            assert snap[p] == v, f"{p} changed"
    assert set(snap) <= set(c.before), "the trim made something"


def converged(fs: MemFS) -> None:
    trim(fs)
    closed = [n for n in fs.names_in(JD) if n.endswith(".closed")]
    assert len(closed) == KEEP
    assert f"{CURRENT}.closed" in closed
    assert all(not present(fs, t) for t in (tag(10), tag(11), tag(12)))


def trim_effects() -> int:
    c = case()
    first = len(c.fs.effects)
    trim(c.fs)
    n = len(c.fs.effects) - first
    assert n > 0
    return n


N_EFFECTS = trim_effects()


def test_the_case_deletes_exactly_the_three_oldest_closed_saves() -> None:
    c = case()
    before = c.fs.snapshot()
    assert trim(c.fs) == history.Trimmed(removed=3, left=0, skipped=False)
    gone = sorted(set(before) - set(c.fs.snapshot()))
    assert gone == sorted(p for t in c.victims for p in before if p.startswith((f"{JD}/{t}.", f"{BD}/{t}")))
    check(c.fs, c)


@pytest.mark.parametrize("at", range(N_EFFECTS))
def test_an_io_error_at_any_effect_leaves_only_history_gone_and_the_next_trim_finishes(at: int) -> None:
    c = case()
    c.fs.inject(len(c.fs.effects) + at, crash=False, err=errno.EIO)
    out = trim(c.fs)
    assert out.left >= 1
    assert not out.skipped
    check(c.fs, c)
    converged(c.fs)
    check(c.fs, c)


@pytest.mark.parametrize("at", range(N_EFFECTS))
def test_a_crash_or_power_loss_at_any_effect_leaves_only_history_gone(at: int) -> None:
    c = case()
    c.fs.inject(len(c.fs.effects) + at, crash=True)
    with pytest.raises(Crash):
        trim(c.fs)
    check(c.fs, c)
    states = list(c.fs.power_loss_states())
    assert states
    for state in states:
        check(state, c)
        converged(state)
        check(state, c)


def test_a_power_loss_after_a_whole_trim_leaves_only_history_gone() -> None:
    c = case()
    trim(c.fs)
    for state in c.fs.power_loss_states():
        check(state, c)
        converged(state)


# -- inside the save ---------------------------------------------------------------------------------------------------


A0 = b"2026-01-01 open Assets:Cash\n"


class Job:
    def progress(self, phase: str) -> None: ...
    def gate(self) -> None: ...
    def committed(self) -> None: ...


def plan_for(data: bytes, job: str) -> sv.SavePlan:
    fp = Fingerprint("/L/a.bean", (("/L/a.bean", Content(sha256(data))),), (), (), ())
    return sv.SavePlan(
        session="s1",
        job=job,
        workspace_key="k1",
        lineage="lin1",
        engine_build="b",
        root="/L/a.bean",
        changes=(Change("/L/a.bean", sha256(data), data + b"; x\n"),),
        fingerprint=fp,
        mirror=digest(fp),
        app_local_data=ALD,
    )


def ports(fs: MemFS) -> sv.Ports:
    return sv.Ports(fs, observe=lambda fp: fp)


def save_world() -> MemFS:
    fs = MemFS()
    fs.outside_mkdirs(JD)
    fs.outside_mkdirs(BD)
    fs.outside_mkdirs("/L")
    fs.outside_write("/L/a.bean", A0)
    return fs


def test_twenty_landed_saves_leave_sixteen_records_and_sixteen_backups_folders() -> None:
    fs = save_world()
    jobs = [f"j{i:02d}" for i in range(20)]
    for job in jobs:
        data = fs.file("/L/a.bean")
        assert data is not None
        out = sv.run(plan_for(data, job), ports(fs), Job())
        assert isinstance(out, sv.Saved), out
    assert fs.names_in(JD) == [f"s1-{j}.closed" for j in jobs[4:]]
    assert fs.names_in(BD) == [f"s1-{j}" for j in jobs[4:]]


def test_the_saved_outcome_carries_what_the_trim_did() -> None:
    fs = save_world()
    for i in range(16):
        seed(fs, tag(i), "closed")
    out = sv.run(plan_for(A0, "j01"), ports(fs), Job())
    assert isinstance(out, sv.Saved)
    assert out.trimmed == history.Trimmed(removed=1, left=0, skipped=False)
    assert not present(fs, tag(0))


def test_the_save_keeps_its_own_record_when_the_clock_was_set_back() -> None:
    """The save names itself to the trim: history written under a clock far ahead stays newer than it."""
    fs = save_world()
    for i in range(20):
        seed(fs, tag(i), "closed")
        fs.outside_mtime(f"{JD}/{tag(i)}.closed", 10**18 + i)
    out = sv.run(plan_for(A0, "j01"), ports(fs), Job())
    assert isinstance(out, sv.Saved)
    assert fs.file(f"{JD}/s1-j01.closed") is not None
    assert fs.file(f"{BD}/s1-j01/0-a.bean") == A0
    assert [present(fs, tag(i)) for i in range(20)] == [False] * 5 + [True] * 15


def test_a_save_that_does_not_land_trims_nothing() -> None:
    """A regression guard (it passes on the base too): only a landed save runs the trim (brief, rule 1)."""
    fs = save_world()
    for i in range(20):
        seed(fs, tag(i), "closed")
    before = fs.snapshot()
    moved = Fingerprint("/L/a.bean", (("/L/a.bean", Content(sha256(b"moved"))),), (), (), ())
    out = sv.run(plan_for(A0, "j01"), sv.Ports(fs, observe=lambda fp: moved), Job())  # the disk moved: refused
    assert not isinstance(out, sv.Saved)
    assert {p: v for p, v in fs.snapshot().items() if p.startswith(KD)} == {
        p: v for p, v in before.items() if p.startswith(KD)
    }


def _save_effects(fs: MemFS) -> int:
    """How many effects the save itself makes before its trim: the same save in a world with nothing to trim."""
    first = len(fs.effects)
    out = sv.run(plan_for(A0, "j01"), ports(fs), Job())
    assert isinstance(out, sv.Saved)
    return len(fs.effects) - first


def saved_case() -> MemFS:
    fs = save_world()
    for i in range(18):
        seed(fs, tag(i), "closed")
    return fs


SAVE_EFFECTS = _save_effects(save_world())
SAVE_TRIM_EFFECTS = _save_effects(saved_case()) - SAVE_EFFECTS


def test_the_trim_runs_after_the_closed_record_is_durable() -> None:
    labels: list[str] = []
    fs = saved_case()
    fs.on_effect = lambda i, label: labels.append(label)
    sv.run(plan_for(A0, "j01"), ports(fs), Job())
    close = max(i for i, label in enumerate(labels) if label.startswith("rename") and label.endswith(".closed"))
    assert labels[close + 1].startswith("sync")
    assert SAVE_TRIM_EFFECTS > 0
    trim_labels = labels[SAVE_EFFECTS:]
    assert all(label.startswith(("remove", "rmdir")) for label in trim_labels if label != f"sync {BD}:")
    # the backups folder's removal is durable before the first record goes (a power loss makes no orphan)
    first_record = min(i for i, label in enumerate(trim_labels) if label.startswith(f"remove {JD}:"))
    assert trim_labels[first_record - 1] == f"sync {BD}:"
    assert len(labels) == SAVE_EFFECTS + SAVE_TRIM_EFFECTS


@pytest.mark.parametrize("at", range(SAVE_TRIM_EFFECTS))
def test_an_io_error_in_the_trim_never_changes_the_saves_outcome(at: int) -> None:
    fs = saved_case()
    fs.inject(len(fs.effects) + SAVE_EFFECTS + at, crash=False, err=errno.EIO)
    out = sv.run(plan_for(A0, "j01"), ports(fs), Job())
    assert isinstance(out, sv.Saved), out
    assert out.trimmed is not None
    assert out.trimmed.left >= 1


@pytest.mark.parametrize("at", range(SAVE_TRIM_EFFECTS))
def test_an_engine_death_in_the_trim_is_read_from_disk_as_saved(at: int) -> None:
    fs = saved_case()
    fs.inject(len(fs.effects) + SAVE_EFFECTS + at, crash=True)
    with pytest.raises(Crash):
        sv.run(plan_for(A0, "j01"), ports(fs), Job())
    change = plan_for(A0, "j01").changes
    assert sv.outcome(fs, ALD, "k1", "s1", "j01", change) == sv.OutcomeFacts("closed")
    for state in fs.power_loss_states():
        assert sv.outcome(state, ALD, "k1", "s1", "j01", change) == sv.OutcomeFacts("closed")
        assert state.file(f"{BD}/s1-j01/0-a.bean") == A0


# -- the pure choice ---------------------------------------------------------------------------------------------------

_TAGS = st.integers(0, 30).map(tag)
_SUFFIX = st.sampled_from(["closed", "resolved", "kept", "open", "part", "done", "mismatch", "r4-attempt-2", "zzz"])
_KIND = st.sampled_from([Kind.FILE, Kind.FILE, Kind.FILE, Kind.DIR, Kind.LINK, None])


@st.composite
def listings(draw: st.DrawFn) -> tuple[list[core.Entry], list[core.Entry], frozenset[str], str]:
    jn = draw(st.lists(st.tuples(_TAGS, _SUFFIX, _KIND, st.integers(0, 50)), max_size=60))
    bn = draw(st.lists(st.tuples(_TAGS, st.sampled_from([Kind.DIR, Kind.DIR, Kind.FILE, None]), st.integers(0, 50))))
    j = {f"{t}.{s}": core.Entry(f"{t}.{s}", k, m) for t, s, k, m in jn}
    b = {t: core.Entry(t, k, m) for t, k, m in bn}
    protected = frozenset(draw(st.lists(_TAGS, max_size=3)))
    current = draw(_TAGS)
    return list(j.values()), list(b.values()), protected, current


def _group(entries: list[core.Entry], backups: list[core.Entry], t: str) -> tuple[list[core.Entry], list[core.Entry]]:
    return [e for e in entries if e.name.partition(".")[0] == t], [e for e in backups if e.name == t]


@settings(max_examples=400, deadline=None)
@given(listings())
def test_the_choice_never_picks_a_protected_save_and_keeps_the_newest_sixteen_per_kind(
    args: tuple[list[core.Entry], list[core.Entry], frozenset[str], str],
) -> None:
    jn, bn, protected, current = args
    victims = core.select(jn, bn, protected, current, keep=3)
    picked = {v.tag for v in victims}
    assert current not in picked
    assert not (picked & protected)
    for v in victims:
        js, bs = _group(jn, bn, v.tag)
        assert all(e.kind is Kind.FILE for e in js)
        assert all(e.kind is Kind.DIR for e in bs)
        assert len(bs) <= 1
        suffixes = {e.name.partition(".")[2] for e in js}
        assert not suffixes & {"open", "part", "zzz"}
        assert len(suffixes & {"closed", "resolved", "kept"}) <= 1
        assert set(v.journal) == {e.name for e in js}
        assert v.backups == (bs[0].name if bs else None)
    # per kind: the victims are the oldest, and exactly max(0, n - 3) of each kind go
    every = core.classify(jn, bn, protected, current)
    for kind in core.HistoryKind:
        of_kind = [g for g in every if g.kind is kind]
        gone = [g for g in of_kind if g.tag in picked]
        assert len(gone) == max(0, len(of_kind) - 3)
        stay = [g for g in of_kind if g.tag not in picked]
        for g in gone:
            assert all(g.order < s.order for s in stay)


def test_the_choice_never_picks_the_current_save_even_when_nothing_is_kept() -> None:
    entries = [core.Entry(f"{t}.closed", Kind.FILE, i) for i, t in enumerate([CURRENT, tag(1), tag(2)])]
    chosen = core.select(entries, [], frozenset(), CURRENT, keep=0)
    assert [g.tag for g in chosen] == [tag(1), tag(2)]


def test_the_choice_keeps_sixteen_by_default() -> None:
    assert core.KEEP == KEEP


def test_a_trimmed_value_is_plain_data() -> None:
    t = history.Trimmed(removed=1, left=0, skipped=False)
    assert replace(t, left=2).left == 2
