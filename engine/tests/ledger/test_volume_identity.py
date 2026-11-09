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
from ledger_engine.ledger.core.journal import FolderEntry, Record, State
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


def on_drive(*, new_dir: bool = False, inc_drive: int | None = None, volume: str = "swap") -> World:
    """The test world with `/w/L` on the drive `USB` (and, with `inc_drive`, `/w/L/inc` on a drive of its own);
    `volume`: the world's write mode (`plain`: a drive without the swap or the exclusive rename, as FAT)."""
    w = world(new_dir=new_dir, volume=volume)
    w.fs.outside_mount("/w/L", USB)
    if inc_drive is not None:
        w.fs.outside_mount("/w/L/inc", inc_drive)
    return replace(w, before=w.fs.snapshot())


def effects_of_the_save(*, new_dir: bool = False, volume: str = "swap") -> list[str]:
    w = on_drive(new_dir=new_dir, volume=volume)
    durable_commit(w.fs, w.commit)
    return w.fs.effects


EFFECTS = effects_of_the_save()
CLOSE = EFFECTS.index(f"rename {JD}:{NAME}.open,{NAME}.closed")  # every file at post, temps removed
FIRST_SWAP = next(i for i, e in enumerate(EFFECTS) if e.startswith("swap "))
NEW_DIR_CLOSE = effects_of_the_save(new_dir=True).index(f"rename {JD}:{NAME}.open,{NAME}.closed")
PLAIN_EFFECTS = effects_of_the_save(volume="plain")
AFTER_CREATE_PLAIN = PLAIN_EFFECTS.index(f"rename {JD}:{NAME}.open,{NAME}.closed")  # every file at post
NEW_DIR_MKDIR = effects_of_the_save(new_dir=True).index("mkdir /w/L:y2026")  # a.bean at post, y2026 not made yet
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


def test_the_journal_names_every_folder_with_its_drive_file_system_and_birth() -> None:
    """Format 6 (owner, 2026-11-08: "Drive ID + folder birth (Recommended)"): each folder the undo may write in, with
    the drive's UUID, the file system and the folder's creation time; a folder the save makes has none (it is made
    after the record) and is on the drive of the folder it is made in."""
    w = on_drive(new_dir=True, inc_drive=3)
    w.fs.outside_fs_type(3, "msdos")
    born = {p: w.fs.open_dir(p).mark().born for p in ("/w/L", "/w/L/inc")}
    assert isinstance(durable_commit(w.fs, w.commit), Landed)
    data = w.fs.file(f"{JD}/{journal.file_name(NAME, State.CLOSED)}")
    assert data is not None
    r = journal.decode(data)
    assert isinstance(r, Record)
    usb, inc = w.fs.volume_of(USB), w.fs.volume_of(3)
    assert usb is not None
    assert inc is not None
    assert r.folders == (
        FolderEntry("/w/L", usb, "apfs", born["/w/L"]),
        FolderEntry("/w/L/inc", inc, "msdos", born["/w/L/inc"]),
        FolderEntry("/w/L/y2026", usb, "apfs", None),
    )


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


# -- S0089 round 1: the folder's birth and file system (red team H1, M1, L1; verifier F3) ---------------------------


def _twin_stick(w: World, *, born: int | None, fs_type: str, main: bytes | None) -> None:
    """The ledger's stick unplugged (kept aside at `/away/L`) and another one mounted at `/w/L` that reports the same
    drive UUID (FAT derives it from a 32-bit format serial, red team H1: two sticks formatted with one serial share it)
    and the same file system, with its own `L` folder made at `born` (None: the same moment, a block-for-block clone).
    `main`: its `main.bean`, or none at all (the red team's case: recovery put the pre bytes there)."""
    usb = w.fs.volume_of(USB)
    l_born = w.fs.open_dir("/w/L").mark().born
    inc_born = w.fs.open_dir("/w/L/inc").mark().born
    w.fs.outside_mkdirs("/away")
    w.fs.outside_move("/w/L", "/away/L")
    w.fs.outside_mkdirs("/w/L/inc")
    w.fs.outside_mount("/w/L", 9)
    w.fs.outside_volume_id(9, usb)
    w.fs.outside_fs_type(9, fs_type)
    w.fs.outside_born("/w/L", l_born if born is None else born)
    w.fs.outside_born("/w/L/inc", inc_born if born is None else born + 1)
    if main is not None:
        w.fs.outside_write("/w/L/main.bean", main)


@pytest.mark.parametrize("main", [None, b'include "inc/a.bean"\n'], ids=["missing", "at_pre"])
def test_another_stick_with_the_same_drive_id_but_its_own_folder_is_never_written(main: bytes | None) -> None:
    """Red team H1 on the fake (FAT, best effort): the other stick reports the same drive UUID and file system; its
    ledger folder was made at another moment. Must not pass: recovery writes nothing there (before, it put the pre
    bytes on its missing `main.bean`, resolved the journal, and the right stick stayed at post with no journal)."""
    w = on_drive(volume="plain")
    w.fs.outside_fs_type(USB, "msdos")
    w.fs.inject(AFTER_CREATE_PLAIN, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    _twin_stick(w, born=1_794_157_294 * 10**9, fs_type="msdos", main=main)
    before = state_of(w.fs)
    out = recover_journal(w.fs, JD, NAME)
    assert isinstance(out, Stopped), out
    assert (out.cause, out.entry) == (Cause.WRONG_DRIVE, "/w/L/inc")
    assert state_of(w.fs) == before
    assert State.OPEN in journals(w.fs)


def test_a_block_for_block_clone_is_taken_for_the_same_stick() -> None:
    """The stated limit (§6.3 "Limit (drive identity)"): a clone keeps the drive UUID, the file system and every
    folder's creation time, so recovery undoes the save on it as on the original."""
    w = on_drive(volume="plain")
    w.fs.outside_fs_type(USB, "msdos")
    w.fs.inject(AFTER_CREATE_PLAIN, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    _twin_stick(w, born=None, fs_type="msdos", main=b'include "inc/a.bean"\n')
    w.fs.outside_write("/w/L/inc/a.bean", b"2026-01-01 open Assets:A\n")
    assert isinstance(recover_journal(w.fs, JD, NAME), Resolved)  # its files at pre: nothing to put back


def test_the_same_drive_id_on_another_file_system_is_another_drive() -> None:
    w = crashed(CLOSE)
    _twin_stick(w, born=None, fs_type="exfat", main=None)
    before = state_of(w.fs)
    out = recover_journal(w.fs, JD, NAME)
    assert isinstance(out, Stopped)
    assert out.cause is Cause.WRONG_DRIVE
    assert state_of(w.fs) == before


def test_a_folder_made_again_at_the_same_path_on_the_same_drive_is_another_folder() -> None:
    """The ledger folder deleted and made again (another program's, a restore from a copy) after the save was cut:
    another creation time, so the undo writes nothing there (fails closed; Keep stays the way out)."""
    w = crashed(CLOSE)
    w.fs.outside_born("/w/L/inc", w.fs.open_dir("/w/L/inc").mark().born + 1)
    before = state_of(w.fs)
    out = recover_journal(w.fs, JD, NAME)
    assert isinstance(out, Stopped)
    assert (out.cause, out.entry) == (Cause.WRONG_DRIVE, "/w/L/inc")
    assert state_of(w.fs) == before


@pytest.mark.parametrize(
    ("fs_type", "shift", "same"),
    [
        ("msdos", 3600 * 10**9, True),  # the Mac's time zone moved one hour: FAT keeps local time
        ("msdos", -(5 * 3600 + 45 * 60) * 10**9, True),  # Nepal
        ("msdos", 14 * 3600 * 10**9, True),
        ("msdos", (14 * 3600 + 15 * 60) * 10**9, False),  # beyond any zone
        ("msdos", (3600 + 1) * 10**9, False),  # not a whole zone
        ("msdos", 1, False),
        ("exfat", 3600 * 10**9, False),  # exFAT keeps the zone: an hour is another folder
        ("apfs", 3600 * 10**9, False),
    ],
)
def test_a_fat_folders_birth_read_in_another_time_zone_is_the_same_folder(fs_type: str, shift: int, same: bool) -> None:
    w = on_drive(volume="plain")
    w.fs.outside_fs_type(USB, fs_type)
    w.fs.inject(AFTER_CREATE_PLAIN, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    for p in ("/w/L", "/w/L/inc"):
        w.fs.outside_born(p, w.fs.open_dir(p).mark().born + shift)
    before = state_of(w.fs)
    out = recover_journal(w.fs, JD, NAME)
    if same:
        if isinstance(out, Stopped) and out.cause is Cause.CREATED_PATH_TAKEN:
            assert out.path is not None
            w.fs.outside_remove(out.path)
            out = recover_journal(w.fs, JD, NAME)
        assert isinstance(out, Resolved), out
    else:
        assert isinstance(out, Stopped)
        assert out.cause is Cause.WRONG_DRIVE
        assert state_of(w.fs) == before


@pytest.mark.parametrize("fs_type", ["msdos", "exfat"])
def test_a_version_4_uuid_on_fat_or_exfat_names_no_drive(fs_type: str) -> None:
    """Red team M1: macOS gives a FAT or exFAT drive whose serial clashes with a mounted one a random version 4 UUID,
    new at every mount. A save there is refused before anything is written (`no_drive_id`); recovery meeting one is
    `unknown_drive` (the right stick, mounted beside its twin, cannot be told by its UUID)."""
    w = on_drive(volume="plain")
    w.fs.outside_fs_type(USB, fs_type)
    w.fs.outside_volume_id(USB, "5b0f3c2e-8d1a-4e6b-9c7f-2a4d6e8b0c1d")  # version 4
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved)
    assert (out.reason, out.unchanged) == (Reason.NO_DRIVE_ID, True)
    assert w.fs.effects == []
    w2 = on_drive(volume="plain")
    w2.fs.outside_fs_type(USB, fs_type)
    w2.fs.outside_volume_id(USB, "4e386abb-6e49-38eb-8ef8-a36ba10a34aa")  # version 3: from the serial
    w2.fs.inject(AFTER_CREATE_PLAIN, crash=True)
    with pytest.raises(Crash):
        durable_commit(w2.fs, w2.commit)
    w2.fs.outside_volume_id(USB, "5b0f3c2e-8d1a-4e6b-9c7f-2a4d6e8b0c1d")
    before = state_of(w2.fs)
    got = recover_journal(w2.fs, JD, NAME)
    assert isinstance(got, Stopped)
    assert got.cause is Cause.UNKNOWN_DRIVE
    assert state_of(w2.fs) == before


def test_a_version_4_uuid_on_apfs_is_a_drive_id() -> None:
    """APFS's own volume UUIDs are random (version 4) and stable: they name the drive."""
    w = on_drive()
    w.fs.outside_volume_id(USB, "5b0f3c2e-8d1a-4e6b-9c7f-2a4d6e8b0c1d")
    assert isinstance(durable_commit(w.fs, w.commit), Landed)


def test_a_link_to_another_drive_in_place_of_a_ledger_folder_is_path_moved_and_keep_refuses() -> None:
    """Red team L1 (S0071 R1-3): the path guard runs before the drive check, so a link in place of a ledger folder is
    `path_moved` (as before S0089), and Keep refuses it rather than skipping it as another drive."""
    w = crashed(AFTER_CREATE)
    w.fs.outside_mkdirs("/other/L")
    w.fs.outside_mount("/other", 4)
    w.fs.outside_mkdirs("/away")
    w.fs.outside_move("/w/L/inc", "/away/inc")
    w.fs.outside_symlink("/w/L/inc", "/other/L")
    before = state_of(w.fs)
    out = recover_journal(w.fs, JD, NAME)
    assert isinstance(out, Stopped)
    assert out.cause is Cause.PATH_MOVED
    assert state_of(w.fs) == before
    assert keep(w.fs, JD, NAME) == Refused("path_moved", "/w/L/inc")
    assert State.OPEN in journals(w.fs)


def test_a_folder_the_save_makes_appearing_on_another_drive_during_step_3_stops_the_try() -> None:
    """Verifier F3: a folder the save makes, not there at step 2, made during step 3 with another drive mounted on
    it: it is first opened in step 3 and refused there (`wrong_drive`); nothing is written in it."""
    w = crashed(NEW_DIR_MKDIR, new_dir=True)
    n = len(w.fs.effects)

    def mount_there(i: int, label: str) -> None:
        if i == n:
            w.fs.outside_mkdirs("/w/L/y2026")
            w.fs.outside_mount("/w/L/y2026", 6)

    w.fs.on_effect = mount_there
    out = recover_journal(w.fs, JD, NAME)
    assert isinstance(out, Stopped), out
    assert (out.cause, out.entry) == (Cause.WRONG_DRIVE, "/w/L/y2026")
    assert w.fs.names_in("/w/L/y2026") == []
    assert all("/w/L/y2026" not in e for e in w.fs.effects[n:])


@pytest.mark.parametrize("fs_type", ["msdos", "exfat"])
def test_a_ledger_at_the_top_level_of_a_fat_or_exfat_drive_is_not_saved(fs_type: str) -> None:
    """S0089 round 1 (red team H1's residue): FAT and exFAT keep no creation time for a drive's top level (0, probed),
    so a ledger there could not be told from one on a twin stick; the save refuses before writing (`no_folder_id`).
    The same folder on APFS (a real creation time) saves."""
    w = on_drive(volume="plain")
    w.fs.outside_fs_type(USB, fs_type)
    w.fs.outside_born("/w/L", 0)
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved)
    assert (out.reason, out.unchanged, out.path) == (Reason.NO_FOLDER_ID, True, "/w/L/prices.bean")
    assert w.fs.effects == []
    w2 = on_drive()
    w2.fs.outside_born("/w/L", 0)
    assert isinstance(durable_commit(w2.fs, w2.commit), Landed)
