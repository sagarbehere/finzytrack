"""Recovery writes only on the drive the journal names (`design/specs/engine-jobs.md` §6.3 "Drive identity", §6.4,
§8.1 step 2, §11; ADR-0012 amendment of 2026-11-08; S0089, BL-0188), over the in-memory file system.

Owner, 2026-11-08, "Volume ID in journal (Recommended)": "Each save journal records the drive's volume UUID. Before
recovery writes, it checks the drive at that path has the same UUID; if not, it writes nothing, keeps the journal, and
blocks that workspace with a message naming the drive ('plug in the drive that held your ledger, then Retry'). v0.4 is
unreleased, so every journal will carry the UUID; one without it is refused the same way."

The bytes it protects: the files on another drive mounted at the path a journal names (never written, renamed or
removed by recovery), and the ledger on the right drive (recovered only there). Each must-not-pass case below checks
that the try made no port effect at all and that every entry, its bytes and its modification time are as before; the
matching case recovers as before (`check_recovered`).
"""

from __future__ import annotations

import errno
from dataclasses import replace

import pytest

from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core.journal import Record, State
from ledger_engine.ledger.core.recovery import Cause
from ledger_engine.ledger.recover_all import Done, Refused, delete_created, delete_kept, inspect, keep
from ledger_engine.ledger.restore import Failed, Resolved, Stopped, recover_journal
from ledger_engine.ledger.writer import Landed, NotSaved, Reason, Unfinished, durable_commit, sha256
from tests.fakes.memfs import Crash, MemFS
from tests.ledger.worlds import JD, NAME, World, check_recovered, journals, world

pytestmark = [pytest.mark.inv("INV-32"), pytest.mark.inv("INV-22"), pytest.mark.inv("INV-01")]

USB = 2  # the drive the ledger folder `/w/L` is on; the journal stays on the Mac's own disk (dev 1)
CLONE = "3f2a9c1e-7b45-4d08-a6e3-91c0d5b7e2f4"  # another drive's UUID (a cloned stick with the same files)
AFTER_CREATE = 30  # a.bean at post, prices.bean placed (a created path), main.bean at pre (`test_recover_all`)


def on_drive(*, new_dir: bool = False, inc_drive: int | None = None) -> World:
    """The test world with `/w/L` on the drive `USB` (and, with `inc_drive`, `/w/L/inc` on a drive of its own)."""
    w = world(new_dir=new_dir)
    w.fs.outside_mount("/w/L", USB)
    if inc_drive is not None:
        w.fs.outside_mount("/w/L/inc", inc_drive)
    return replace(w, before=w.fs.snapshot())


def effects_of_the_save(*, new_dir: bool = False) -> list[str]:
    w = on_drive(new_dir=new_dir)
    durable_commit(w.fs, w.commit)
    return w.fs.effects


EFFECTS = effects_of_the_save()
CLOSE = EFFECTS.index(f"rename {JD}:{NAME}.open,{NAME}.closed")  # every file at post, temps removed
FIRST_SWAP = next(i for i, e in enumerate(EFFECTS) if e.startswith("swap "))
NEW_DIR_CLOSE = effects_of_the_save(new_dir=True).index(f"rename {JD}:{NAME}.open,{NAME}.closed")
CUTS = [(i, False) for i in (FIRST_SWAP + 1, AFTER_CREATE, CLOSE)] + [(NEW_DIR_CLOSE, True)]


def crashed(at: int, *, new_dir: bool = False, inc_drive: int | None = None) -> World:
    w = on_drive(new_dir=new_dir, inc_drive=inc_drive)
    w.fs.inject(at, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    assert State.OPEN in journals(w.fs)
    return w


def state_of(fs: MemFS) -> tuple[object, ...]:
    """Everything a write could change: every entry with its bytes, the modification times, the effects made."""
    return (fs.snapshot(), dict(fs.mtimes), len(fs.effects), [s.snapshot() for s in fs.power_loss_states()])


def record_of(fs: MemFS) -> Record:
    data = fs.file(f"{JD}/{journal.file_name(NAME, State.OPEN)}")
    assert data is not None
    r = journal.decode(data)
    assert isinstance(r, Record)
    return r


# -- the save records the drive -----------------------------------------------------------------------------------


def test_the_journal_names_the_drive_of_every_folder_the_save_writes_in() -> None:
    w = on_drive(new_dir=True, inc_drive=3)
    assert isinstance(durable_commit(w.fs, w.commit), Landed)
    data = w.fs.file(f"{JD}/{journal.file_name(NAME, State.CLOSED)}")
    assert data is not None
    r = journal.decode(data)
    assert isinstance(r, Record)
    usb, inc = w.fs.volume_of(USB), w.fs.volume_of(3)
    assert {f.canonical_path: f.volume for f in r.files} == {
        "/w/L/inc/a.bean": inc,
        "/w/L/y2026/prices.bean": usb,
        "/w/L/main.bean": usb,
    }
    assert [(d.canonical_path, d.volume) for d in r.created_dirs] == [("/w/L/y2026", usb)]


def test_a_save_on_a_drive_with_no_uuid_is_refused_before_anything_is_written() -> None:
    """Owner: "every journal will carry the UUID; one without it is refused the same way": a journal that could
    never be undone is never written, so the save refuses before its first effect (`no_drive_id`)."""
    w = on_drive()
    w.fs.outside_volume_id(USB, None)
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved)
    assert (out.reason, out.unchanged, out.open_journal) == (Reason.NO_DRIVE_ID, True, False)
    assert out.path is not None
    assert out.path.startswith("/w/L/")
    assert w.fs.effects == []
    assert w.fs.snapshot() == w.before


def test_a_save_whose_drive_uuid_cannot_be_read_is_not_saved_and_writes_nothing() -> None:
    w = on_drive()
    w.fs.outside_volume_error(USB)
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved)
    assert (out.reason, out.open_journal) == (Reason.IO_ERROR, False)
    assert w.fs.effects == []
    assert w.fs.snapshot() == w.before


# -- recover-all's try ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("at", "new_dir"), CUTS)
@pytest.mark.parametrize(
    ("drive", "cause"), [(CLONE, Cause.WRONG_DRIVE), (None, Cause.UNKNOWN_DRIVE)], ids=["other", "none"]
)
def test_another_drive_at_the_path_is_never_written_and_the_journal_stays(
    at: int, new_dir: bool, drive: str | None, cause: Cause
) -> None:
    """Must not pass: the drive at `/w/L` now reports another UUID (a cloned stick with the same files) or none:
    the try stops with nothing written anywhere, the journal `.open`."""
    w = crashed(at, new_dir=new_dir)
    w.fs.outside_volume_id(USB, drive)
    before = state_of(w.fs)
    out = recover_journal(w.fs, JD, NAME)
    assert isinstance(out, Stopped), out
    assert out.cause is cause
    assert out.entry is not None
    assert out.entry.startswith("/w/L")
    assert state_of(w.fs) == before
    assert State.OPEN in journals(w.fs)


@pytest.mark.parametrize(("at", "new_dir"), CUTS)
def test_a_drive_uuid_that_cannot_be_read_fails_the_try_with_nothing_written(at: int, new_dir: bool) -> None:
    w = crashed(at, new_dir=new_dir)
    w.fs.outside_volume_error(USB)
    before = state_of(w.fs)
    out = recover_journal(w.fs, JD, NAME)
    assert isinstance(out, Failed), out
    assert (out.cause, out.errno) == (Cause.IO_ERROR, errno.EIO)
    assert out.entry is not None
    assert out.entry.startswith("/w/L")
    assert state_of(w.fs) == before


@pytest.mark.parametrize(("at", "new_dir"), CUTS)
def test_the_right_drive_plugged_in_again_is_recovered_as_before(at: int, new_dir: bool) -> None:
    """The matching case: the drive that held the ledger back at its path; Retry undoes the save."""
    w = crashed(at, new_dir=new_dir)
    right = w.fs.volume_of(USB)
    w.fs.outside_volume_id(USB, CLONE)
    assert isinstance(recover_journal(w.fs, JD, NAME), Stopped)
    w.fs.outside_volume_id(USB, right)
    out = recover_journal(w.fs, JD, NAME)
    if isinstance(out, Stopped) and out.cause is Cause.CREATED_PATH_TAKEN:
        assert out.path is not None
        w.fs.outside_remove(out.path)
        out = recover_journal(w.fs, JD, NAME)
    assert isinstance(out, Resolved), out
    check_recovered(w.fs, w, out)


def test_one_wrong_drive_stops_the_try_before_any_write_on_the_right_one() -> None:
    """`inc/` on a drive of its own, `/w/L` on another: only `inc/`'s drive changed. The try writes nothing at all,
    not on `/w/L` either, whose drive is right (owner: on any mismatch "it writes nothing")."""
    w = crashed(CLOSE, inc_drive=3)
    w.fs.outside_volume_id(3, CLONE)
    before = state_of(w.fs)
    out = recover_journal(w.fs, JD, NAME)
    assert isinstance(out, Stopped)
    assert (out.cause, out.entry) == (Cause.WRONG_DRIVE, "/w/L/inc")
    assert state_of(w.fs) == before


def test_a_different_drive_with_other_files_at_the_same_path_is_never_touched() -> None:
    """The stick that held the ledger is gone; another stick with its own `L` folder is mounted at the same path.
    Recovery writes nothing there; when the right stick is back, Retry recovers it."""
    w = crashed(AFTER_CREATE)
    w.fs.outside_mkdirs("/away")
    w.fs.outside_move("/w/L", "/away/L")  # the ledger's stick unplugged (kept aside in the fake)
    w.fs.outside_mkdirs("/w/L/inc")
    for path, data in (("/w/L/main.bean", b"theirs\n"), ("/w/L/inc/a.bean", b"theirs too\n")):
        w.fs.outside_write(path, data)
    w.fs.outside_write(f"/w/L/.main.bean.ft-{NAME}.tmp", b"a file of theirs at one of our names\n")
    w.fs.outside_mount("/w/L", 4)
    before = state_of(w.fs)
    out = recover_journal(w.fs, JD, NAME)
    assert isinstance(out, Stopped)
    assert out.cause is Cause.WRONG_DRIVE
    assert state_of(w.fs) == before
    w.fs.outside_move("/w/L", "/away/theirs")
    w.fs.outside_move("/away/L", "/w/L")
    w.fs.outside_remove("/w/L/prices.bean")  # the created path, as the user would after `created_path_taken`
    assert isinstance(recover_journal(w.fs, JD, NAME), Resolved)
    assert w.fs.file("/away/theirs/main.bean") == b"theirs\n"


# -- the save's own undo (§6.4) -----------------------------------------------------------------------------------


def test_the_saves_rollback_writes_nothing_once_its_drive_is_another() -> None:
    """The drive changes under a running save (it was unplugged and another one mounted there) and the swap fails:
    the rollback writes nothing on the drive now at the path; the journal stays `.open` for recover-all."""
    w = on_drive()

    def swap_drive(i: int, label: str) -> None:
        if i == FIRST_SWAP:
            w.fs.outside_volume_id(USB, CLONE)

    w.fs.on_effect = swap_drive
    w.fs.inject(FIRST_SWAP, crash=False, err=errno.EIO)
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Unfinished | NotSaved), out
    if isinstance(out, Unfinished):
        assert out.cause == Cause.WRONG_DRIVE.value
    else:
        assert out.open_journal
    assert len(w.fs.effects) == FIRST_SWAP + 1, w.fs.effects[FIRST_SWAP:]  # the failed swap was the last effect
    assert State.OPEN in journals(w.fs)


# -- the blocked screen's actions -----------------------------------------------------------------------------------


KEPT = "main.bean.kept-s1-j7"
KEPT_BYTES = b"the editor's version\n"
POST = b"2026-01-03 price X 1 EUR\n"


@pytest.mark.parametrize(("drive", "reason"), [(CLONE, "wrong_drive"), (None, "unknown_drive")])
def test_delete_actions_refuse_a_file_on_another_drive_and_remove_nothing(drive: str | None, reason: str) -> None:
    w = crashed(AFTER_CREATE)
    w.fs.outside_write(f"/w/L/{KEPT}", KEPT_BYTES)
    w.fs.outside_volume_id(USB, drive)
    before = state_of(w.fs)
    for out in (
        delete_kept(w.fs, JD, NAME, "/w/L/main.bean", KEPT, sha256(KEPT_BYTES)),
        delete_created(w.fs, JD, NAME, "/w/L/prices.bean", sha256(POST)),
    ):
        assert out == Refused(reason, "/w/L"), out
    assert state_of(w.fs) == before


def test_keep_on_another_drive_syncs_nothing_there_and_keeps_the_journal_as_asked() -> None:
    """Keep writes no ledger byte (§8.1); on another drive it syncs nothing there, as for a folder that is not
    there, and renames the journal `.kept` as the user chose. The confirmation says the save will not be undone."""
    w = crashed(AFTER_CREATE)
    w.fs.outside_volume_id(USB, CLONE)
    n = len(w.fs.effects)
    assert keep(w.fs, JD, NAME) == Done()
    assert all(not e.split(" ", 1)[1].startswith("/w/L") for e in w.fs.effects[n:]), w.fs.effects[n:]
    assert set(w.fs.names_in(JD)) == {f"{NAME}.kept"}


def test_inspect_shows_a_file_on_another_drive_as_not_reachable() -> None:
    w = crashed(AFTER_CREATE)
    w.fs.outside_volume_id(USB, CLONE)
    seen = inspect(w.fs, record_of(w.fs))
    assert [(i.path, i.reachable, i.now, i.kept) for i in seen] == [
        ("/w/L/inc/a.bean", False, None, ()),
        ("/w/L/prices.bean", False, None, ()),
        ("/w/L/main.bean", False, None, ()),
    ]  # the screen never shows the other drive's files as this save's


def test_the_drives_are_checked_before_step_2_moves_an_outside_save_to_its_kept_name() -> None:
    """Step 2 may write once before step 3: an outside save found at one of our names is moved to its kept name. With
    `inc/` on a drive that is right and `/w/L` on one that is not, that move must not happen either: every drive is
    checked first (owner: on any mismatch "it writes nothing")."""
    w = crashed(CLOSE, inc_drive=3)
    w.fs.outside_write(f"/w/L/inc/.a.bean.ft-{NAME}.tmp", b"an editor's save into our temp\n")
    w.fs.outside_volume_id(USB, CLONE)
    before = state_of(w.fs)
    out = recover_journal(w.fs, JD, NAME)
    assert isinstance(out, Stopped)
    assert (out.cause, out.entry) == (Cause.WRONG_DRIVE, "/w/L")
    assert state_of(w.fs) == before
