"""Recover-all's work on disk (`ledger_engine.ledger.recover_all`; `design/specs/engine-jobs.md` §8.1 "What" and
"Afterwards", §11's blocked-screen rows; ADR-0063 decision 2; S0062), over the in-memory file system with its
durable layer. Written from the spec's lines:

- "every `local/*/journal/*.open`, found by listing `local/` after a `durable_sync` of each journal directory ...
  whichever workspace it belongs to"; "A `.part` journal is deleted ... never blocks"; "A journal whose file list
  cannot be read blocks every open"; a record already `.resolved` (a try whose last sync failed) is not listed again
  (S0061 red team r1 L7: "Retry should read the disk").
- Keep "first `durable_sync`s every file and folder the journal names, so "now" is what a power loss keeps ... then
  renames the journal `.kept`, syncs its directory".
- Delete the kept copy "removes only a kept copy the journal derives", after the user looked at it; Delete it removes
  only a file at a path the journal records as created (owner, 2026-10-29 and 2026-10-30): never a folder, a link, a
  FIFO, a name of ours, a file at a replaced path, another journal's kept copy, or bytes the user did not see.
"""

from __future__ import annotations

import errno
from dataclasses import replace

import pytest

from ledger_engine.fs.port import Kind
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core.journal import Record, State
from ledger_engine.ledger.core.recovery import Cause
from ledger_engine.ledger.recover_all import (
    ActFailed,
    Done,
    Refused,
    delete_created,
    delete_kept,
    inspect,
    keep,
    list_journals,
)
from ledger_engine.ledger.restore import Failed, Resolved, Stopped, recover_journal
from ledger_engine.ledger.writer import durable_commit, sha256
from tests.fakes.memfs import Crash, MemFS, MemLocks
from tests.ledger.worlds import JD, NAME, World, world

pytestmark = [pytest.mark.inv("INV-32"), pytest.mark.inv("INV-22"), pytest.mark.inv("INV-01")]

ALD = "/w"  # app_local_data in these worlds: `local/` is `/w/local`
KJD = "/w/local/k1/journal"
KBD = "/w/local/k1/backups"
OPEN_SYNC = 15  # the journal folder's sync right after the `.open` rename (the save's effects, `test_restore`)
PART_WRITE = 12  # the `.part` record being written
AFTER_CREATE = 30  # a.bean at post, prices.bean placed (a created path), main.bean at pre


def keyed(*, at: int, err: int | None = None) -> World:
    """The test world with its journal in `local/k1/journal` (§6.5's layout), its save cut at effect `at`."""
    w = world()
    w.fs.outside_mkdirs(KJD)
    w.fs.outside_mkdirs(KBD)
    w = replace(w, commit=replace(w.commit, journal_dir=KJD, backups_dir=KBD), before=w.fs.snapshot())
    w.fs.inject(at, crash=err is None, err=err or errno.EIO)
    with pytest.raises(Crash if err is None else OSError):
        durable_commit(w.fs, w.commit)
    return w


def crashed_at(at: int) -> World:
    """The test world (journal in `/w/local/journal`), its save crashed at effect `at`."""
    w = world()
    w.fs.inject(at, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    return w


def record_of(fs: MemFS, jd: str = JD) -> Record:
    data = fs.file(f"{jd}/{journal.file_name(NAME, State.OPEN)}")
    assert data is not None
    r = journal.decode(data)
    assert isinstance(r, Record)
    return r


# -- the listing --------------------------------------------------------------------------------------------------


def test_the_listing_syncs_each_journal_folder_before_it_reads_it() -> None:
    """§8.1, model finding F1: an `.open` rename the save made but did not sync yet is listed only once it is durable,
    so no power loss after the listing can take back what it listed."""
    w = keyed(at=OPEN_SYNC)
    unsynced = [s for s in w.fs.power_loss_states() if s.file(f"{KJD}/{NAME}.open") is None]
    assert unsynced, "the `.open` rename is not durable before the listing (the precondition)"
    listing = list_journals(w.fs, ALD)
    assert [(j.journal_dir, j.name) for j in listing.journals] == [(KJD, NAME)]
    assert isinstance(listing.journals[0].record, Record)
    for state in w.fs.power_loss_states():
        assert state.file(f"{KJD}/{NAME}.open") is not None, "a power loss took back a listed journal"


def test_a_part_record_is_removed_and_never_listed() -> None:
    w = keyed(at=PART_WRITE)
    w.fs.outside_write(f"{KJD}/s0-j1.resolved", b"{}")
    w.fs.outside_write(f"{KJD}/s0-j2.closed", b"{}")
    w.fs.outside_write(f"{KJD}/notes.txt", b"mine")
    listing = list_journals(w.fs, ALD)
    assert listing.journals == ()
    assert listing.unlisted == ()
    assert listing.parts_removed == 1
    names = w.fs.names_in(KJD)
    assert f"{NAME}.part" not in names
    assert {"s0-j1.resolved", "s0-j2.closed", "notes.txt"} <= set(names), "the listing removed more than `.part`"
    for state in w.fs.power_loss_states():
        assert {"s0-j1.resolved", "s0-j2.closed", "notes.txt"} <= set(state.names_in(KJD))


def test_every_workspaces_journals_are_listed() -> None:
    w = keyed(at=AFTER_CREATE)
    other = "/w/local/k2/journal"
    w.fs.outside_mkdirs(other)
    data = w.fs.file(f"{KJD}/{NAME}.open")
    assert data is not None
    w.fs.outside_write(f"{other}/{NAME}.open", data)
    w.fs.outside_write("/w/local/k3", b"a file, not a workspace folder")
    w.fs.outside_mkdirs("/w/local/k4")  # no journal folder
    listing = list_journals(w.fs, ALD)
    assert [(j.journal_dir, j.name) for j in listing.journals] == [(KJD, NAME), (other, NAME)]
    assert all(isinstance(j.record, Record) for j in listing.journals)


@pytest.mark.parametrize(
    "content",
    [b"", b"torn {", b'{"format":"other"}', b"\xff\xfe"],
)
def test_a_record_that_cannot_be_read_is_listed_unreadable(content: bytes) -> None:
    w = keyed(at=AFTER_CREATE)
    w.fs.outside_write(f"{KJD}/s9-j9.open", content)
    listing = list_journals(w.fs, ALD)
    found = {j.name: j.record for j in listing.journals}
    assert found["s9-j9"] is None
    assert isinstance(found[NAME], Record)


def test_a_record_under_another_name_is_unreadable() -> None:
    w = keyed(at=AFTER_CREATE)
    data = w.fs.file(f"{KJD}/{NAME}.open")
    assert data is not None
    w.fs.outside_write(f"{KJD}/s2-j2.open", data)
    found = {j.name: j.record for j in list_journals(w.fs, ALD).journals}
    assert found["s2-j2"] is None


def test_a_journal_folder_that_cannot_be_synced_is_unlisted_and_nothing_is_removed() -> None:
    w = keyed(at=PART_WRITE)
    n = len(w.fs.effects)
    w.fs.inject(n, crash=False, err=errno.EIO)  # the listing's first effect: the folder's sync
    listing = list_journals(w.fs, ALD)
    assert [(u.folder, u.errno) for u in listing.unlisted] == [(KJD, errno.EIO)]
    assert listing.journals == ()
    assert f"{NAME}.part" in w.fs.names_in(KJD)


def test_no_local_folder_is_an_empty_listing() -> None:
    fs = MemFS()
    fs.outside_mkdirs("/w")
    listing = list_journals(fs, ALD)
    assert listing.journals == ()
    assert listing.unlisted == ()


def test_a_try_whose_last_sync_failed_is_not_listed_again() -> None:
    """S0061 red team r1 L7: the try's last sync failed (the record `.resolved` in memory, every file durably at pre):
    the next listing syncs the folder first and finds no `.open`, so Retry reads the disk instead of reporting
    `failed` again; no power loss after it brings the `.open` back."""
    w = keyed(at=AFTER_CREATE)
    listed = list_journals(w.fs, ALD).journals
    assert [j.name for j in listed] == [NAME]
    w.fs.outside_remove("/w/L/prices.bean")  # the created path empty: the try can resolve
    before = len(w.fs.effects)
    clean = keyed(at=AFTER_CREATE)
    clean.fs.outside_remove("/w/L/prices.bean")
    m = len(clean.fs.effects)
    assert isinstance(recover_journal(clean.fs, MemLocks(), KJD, NAME), Resolved)
    last = len(clean.fs.effects) - m - 1  # the journal folder's sync after the `.resolved` rename
    w.fs.inject(before + last, crash=False, err=errno.EIO)
    out = recover_journal(w.fs, MemLocks(), KJD, NAME)
    assert isinstance(out, Failed), out
    again = list_journals(w.fs, ALD)
    assert again.journals == (), "Retry listed a journal already resolved"
    for state in w.fs.power_loss_states():
        assert state.file(f"{KJD}/{NAME}.open") is None
        assert state.file(f"{KJD}/{NAME}.resolved") is not None


# -- inspect --------------------------------------------------------------------------------------------------------


def test_inspect_shows_each_file_its_kept_copies_and_the_created_path() -> None:
    w = crashed_at(AFTER_CREATE)
    w.fs.outside_write("/w/L/main.bean.kept-s1-j7", b"the editor's version\n")
    w.fs.outside_write("/w/L/main.bean.kept-s1-j7-2", b"another\n")
    w.fs.outside_write("/w/L/main.bean.kept-s5-j5", b"another journal's\n")
    seen = {i.path: i for i in inspect(w.fs, record_of(w.fs))}
    main = seen["/w/L/main.bean"]
    assert main.reachable
    assert not main.created
    assert main.now is not None
    assert main.now.kind is Kind.FILE
    assert main.now.sha256 == sha256(b'include "inc/a.bean"\n')
    assert [(k.name, k.seen.sha256, k.seen.size) for k in main.kept] == [
        ("main.bean.kept-s1-j7", sha256(b"the editor's version\n"), 21),
        ("main.bean.kept-s1-j7-2", sha256(b"another\n"), 8),
    ]
    prices = seen["/w/L/prices.bean"]
    assert prices.created
    assert prices.now is not None
    assert prices.now.sha256 == sha256(b"2026-01-03 price X 1 EUR\n")


def test_inspect_writes_nothing_and_reports_an_unreachable_folder() -> None:
    w = crashed_at(AFTER_CREATE)
    w.fs.outside_move("/w/L/inc", "/w/elsewhere")
    snap = w.fs.snapshot()
    n = len(w.fs.effects)
    seen = {i.path: i for i in inspect(w.fs, record_of(w.fs))}
    assert not seen["/w/L/inc/a.bean"].reachable
    assert w.fs.snapshot() == snap
    assert w.fs.effects[n:] == []


# -- Keep -----------------------------------------------------------------------------------------------------------


def test_keep_syncs_every_file_and_folder_before_its_rename() -> None:
    """§8.1 "Afterwards" (model finding N1): an outside write to a ledger file not yet synced, then Keep: in every
    state a power loss leaves after it, a `.kept` record stands only beside the bytes that were there when the user
    chose Keep."""
    w = crashed_at(AFTER_CREATE)
    w.fs.outside_write("/w/L/main.bean", b"edited, not synced\n")
    w.fs.outside_unsynced("/w/L/main.bean", synced=b'include "inc/a.bean"\n')
    assert any(s.file("/w/L/main.bean") != b"edited, not synced\n" for s in w.fs.power_loss_states())
    assert keep(w.fs, MemLocks(), JD, NAME) == Done()
    assert set(w.fs.names_in(JD)) == {f"{NAME}.kept"}
    for state in w.fs.power_loss_states():
        if state.file(f"{JD}/{NAME}.kept") is not None:
            assert state.file("/w/L/main.bean") == b"edited, not synced\n", "kept over bytes a power loss dropped"
            assert state.file("/w/L/prices.bean") == b"2026-01-03 price X 1 EUR\n"
    assert all(s.file(f"{JD}/{NAME}.kept") is not None for s in w.fs.power_loss_states())


def test_keep_syncs_an_exchange_a_cut_restore_left_unsynced() -> None:
    """§8.1 "Afterwards": "a restore cut by an error may have left an exchange not yet synced"."""
    w = crashed_at(36)  # main.bean swapped to post; its folder's sync not done
    assert any(s.file("/w/L/main.bean") != w.fs.file("/w/L/main.bean") for s in w.fs.power_loss_states())
    now = {p: w.fs.file(p) for p in ("/w/L/main.bean", "/w/L/inc/a.bean", "/w/L/prices.bean")}
    assert keep(w.fs, MemLocks(), JD, NAME) == Done()
    for state in w.fs.power_loss_states():
        assert {p: state.file(p) for p in now} == now


def test_keep_refuses_while_a_files_lock_is_held_and_renames_nothing() -> None:
    w = crashed_at(AFTER_CREATE)
    locks = MemLocks()
    key = next(f.path_key for f in record_of(w.fs).files if f.canonical_path == "/w/L/main.bean")
    locks.outside_hold(key)
    assert keep(w.fs, locks, JD, NAME) == Refused("lock_held", "/w/L/main.bean")
    assert f"{NAME}.open" in w.fs.names_in(JD)


def test_keep_renames_nothing_when_a_sync_fails() -> None:
    w = crashed_at(AFTER_CREATE)
    n = len(w.fs.effects)
    w.fs.inject(n, crash=False, err=errno.EIO)  # the first file's sync
    out = keep(w.fs, MemLocks(), JD, NAME)
    assert isinstance(out, ActFailed), out
    assert out.errno == errno.EIO
    assert f"{NAME}.open" in w.fs.names_in(JD)
    assert f"{NAME}.kept" not in w.fs.names_in(JD)


def test_keep_on_a_read_only_drive_goes_on() -> None:
    """`F_FULLFSYNC` answers `EROFS` on a read-only volume (probed on APFS, S0062): nothing can be pending there, so
    Keep still clears the journal (red team r8 F1: "Keep or a read-write remount clears it")."""
    w = crashed_at(AFTER_CREATE)
    n = len(w.fs.effects)
    for i in range(n, n + 3):
        w.fs.inject(i, crash=False, err=errno.EROFS)
    assert keep(w.fs, MemLocks(), JD, NAME) == Done()
    assert f"{NAME}.kept" in w.fs.names_in(JD)


def test_keep_a_record_that_cannot_be_read() -> None:
    """ADR-0063: Keep resolves "one that cannot be read"."""
    w = crashed_at(AFTER_CREATE)
    w.fs.outside_write(f"{JD}/s9-j9.open", b"torn {")
    assert keep(w.fs, MemLocks(), JD, "s9-j9") == Done()
    assert "s9-j9.kept" in w.fs.names_in(JD)
    assert f"{NAME}.open" in w.fs.names_in(JD), "Keep of one journal touched another"


def test_keep_of_a_journal_that_is_not_open_is_refused() -> None:
    w = crashed_at(AFTER_CREATE)
    assert keep(w.fs, MemLocks(), JD, "s8-j8") == Refused("not_open")


def test_keep_skips_a_folder_that_is_not_there() -> None:
    """ADR-0063: Keep resolves "a journal whose files cannot be reached"."""
    w = crashed_at(AFTER_CREATE)
    w.fs.outside_move("/w/L/inc", "/w/elsewhere")
    assert keep(w.fs, MemLocks(), JD, NAME) == Done()


# -- Delete the kept copy ---------------------------------------------------------------------------------------------

KEPT = "main.bean.kept-s1-j7"
KEPT_BYTES = b"the editor's version\n"


def with_kept() -> World:
    w = crashed_at(AFTER_CREATE)
    w.fs.outside_write(f"/w/L/{KEPT}", KEPT_BYTES)
    return w


def test_delete_the_kept_copy_removes_it_durably_and_then_the_journal_can_resolve() -> None:
    w = with_kept()
    stopped = recover_journal(w.fs, MemLocks(), JD, NAME)
    assert isinstance(stopped, Stopped)
    assert stopped.cause is Cause.KEPT_COPY
    snap = w.fs.snapshot()
    assert delete_kept(w.fs, MemLocks(), JD, NAME, "/w/L/main.bean", KEPT, sha256(KEPT_BYTES)) == Done()
    after = w.fs.snapshot()
    assert set(snap) - set(after) == {f"/w/L/{KEPT}"}, "more than the kept copy went"
    assert {p: e for p, e in after.items()} == {p: e for p, e in snap.items() if p != f"/w/L/{KEPT}"}
    for state in w.fs.power_loss_states():
        assert state.file(f"/w/L/{KEPT}") is None
    w.fs.outside_remove("/w/L/prices.bean")
    assert isinstance(recover_journal(w.fs, MemLocks(), JD, NAME), Resolved)


@pytest.mark.parametrize(
    ("file", "kept", "reason"),
    [
        ("/w/L/main.bean", "main.bean.kept-s5-j5", "not_kept_name"),  # another journal's kept copy
        ("/w/L/main.bean", ".main.bean.ft-s1-j7.tmp", "not_kept_name"),  # a name of ours
        ("/w/L/main.bean", "main.bean", "not_kept_name"),  # the ledger file itself
        ("/w/L/main.bean", "other.bean", "not_kept_name"),
        ("/w/L/main.bean", "MAIN.bean.kept-s1-j7", "not_kept_name"),  # a spelling the volume did not keep as ours
        ("/w/L/main.bean", "prices.bean.kept-s1-j7", "not_kept_name"),  # another file's kept name
        ("/w/L/other.bean", "other.bean.kept-s1-j7", "not_in_record"),  # a file the journal does not name
        ("/w/L/inc/../main.bean", KEPT, "not_in_record"),
    ],
)
def test_delete_the_kept_copy_removes_only_a_name_its_journal_derives(file: str, kept: str, reason: str) -> None:
    w = with_kept()
    for decoy in ("main.bean.kept-s5-j5", "other.bean.kept-s1-j7", "prices.bean.kept-s1-j7"):
        w.fs.outside_write(f"/w/L/{decoy}", KEPT_BYTES)
    snap = w.fs.snapshot()
    assert delete_kept(w.fs, MemLocks(), JD, NAME, file, kept, sha256(KEPT_BYTES)) == Refused(reason)
    assert w.fs.snapshot() == snap


def test_delete_the_kept_copy_refuses_bytes_the_user_did_not_see() -> None:
    w = with_kept()
    out = delete_kept(w.fs, MemLocks(), JD, NAME, "/w/L/main.bean", KEPT, sha256(b"what the screen showed\n"))
    assert out == Refused("changed", f"/w/L/{KEPT}")
    assert w.fs.file(f"/w/L/{KEPT}") == KEPT_BYTES


@pytest.mark.parametrize("kind", ["folder", "link", "fifo"])
def test_delete_the_kept_copy_never_removes_anything_but_a_regular_file(kind: str) -> None:
    w = crashed_at(AFTER_CREATE)
    path = f"/w/L/{KEPT}"
    if kind == "folder":
        w.fs.outside_mkdirs(path)
    elif kind == "link":
        w.fs.outside_symlink(path, "/w/L/main.bean")
    else:
        w.fs.outside_fifo(path)
    snap = w.fs.snapshot()
    assert delete_kept(w.fs, MemLocks(), JD, NAME, "/w/L/main.bean", KEPT, sha256(KEPT_BYTES)) == Refused(
        "not_a_file", path
    )
    assert w.fs.snapshot() == snap


def test_delete_the_kept_copy_refusals_write_nothing() -> None:
    w = with_kept()
    snap = w.fs.snapshot()
    seen = sha256(KEPT_BYTES)
    assert delete_kept(w.fs, MemLocks(), JD, "s8-j8", "/w/L/main.bean", KEPT, seen) == Refused("not_open")
    locks = MemLocks()
    locks.outside_hold(next(f.path_key for f in record_of(w.fs).files if f.canonical_path == "/w/L/main.bean"))
    assert delete_kept(w.fs, locks, JD, NAME, "/w/L/main.bean", KEPT, seen) == Refused("lock_held", "/w/L/main.bean")
    w.fs.outside_write(f"{JD}/s9-j9.open", b"torn {")
    assert delete_kept(w.fs, MemLocks(), JD, "s9-j9", "/w/L/main.bean", KEPT, seen) == Refused("unreadable")
    assert w.fs.file(f"/w/L/{KEPT}") == KEPT_BYTES
    assert {p: e for p, e in w.fs.snapshot().items() if p != f"{JD}/s9-j9.open"} == snap


def test_delete_the_kept_copy_refuses_a_folder_no_longer_at_its_path() -> None:
    """§6.3's path guard: the recorded folder replaced by a link to where it went."""
    w = with_kept()
    w.fs.outside_move("/w/L", "/w/moved")
    w.fs.outside_symlink("/w/L", "/w/moved")
    out = delete_kept(w.fs, MemLocks(), JD, NAME, "/w/L/main.bean", KEPT, sha256(KEPT_BYTES))
    assert out == Refused("path_moved", "/w/L")
    assert w.fs.file(f"/w/moved/{KEPT}") == KEPT_BYTES


def test_delete_the_kept_copy_of_an_absent_one() -> None:
    w = crashed_at(AFTER_CREATE)
    assert delete_kept(w.fs, MemLocks(), JD, NAME, "/w/L/main.bean", KEPT, sha256(KEPT_BYTES)) == Refused(
        "absent", f"/w/L/{KEPT}"
    )


# -- Delete it (a created path) ------------------------------------------------------------------------------------

POST = b"2026-01-03 price X 1 EUR\n"


def test_delete_it_removes_the_file_at_the_created_path_and_retry_resolves() -> None:
    w = crashed_at(AFTER_CREATE)
    stopped = recover_journal(w.fs, MemLocks(), JD, NAME)
    assert isinstance(stopped, Stopped)
    assert stopped.cause is Cause.CREATED_PATH_TAKEN
    snap = w.fs.snapshot()
    assert delete_created(w.fs, MemLocks(), JD, NAME, "/w/L/prices.bean", sha256(POST)) == Done()
    after = w.fs.snapshot()
    assert set(snap) - set(after) == {"/w/L/prices.bean"}
    for state in w.fs.power_loss_states():
        assert state.file("/w/L/prices.bean") is None
    assert isinstance(recover_journal(w.fs, MemLocks(), JD, NAME), Resolved)


@pytest.mark.parametrize(
    ("file", "reason"),
    [
        ("/w/L/main.bean", "not_created"),  # a replaced path
        ("/w/L/inc/a.bean", "not_created"),
        ("/w/L/other.bean", "not_in_record"),
        ("/w/L/.prices.bean.ft-s1-j7.tmp", "not_in_record"),  # a name of ours
        ("/w/L/prices.bean.kept-s1-j7", "not_in_record"),
    ],
)
def test_delete_it_removes_only_a_path_the_journal_records_as_created(file: str, reason: str) -> None:
    w = crashed_at(AFTER_CREATE)
    snap = w.fs.snapshot()
    data = w.fs.file(file) or b""
    assert delete_created(w.fs, MemLocks(), JD, NAME, file, sha256(data)) == Refused(reason)
    assert w.fs.snapshot() == snap


@pytest.mark.parametrize("kind", ["folder", "link", "fifo"])
def test_delete_it_never_removes_anything_but_a_regular_file(kind: str) -> None:
    w = crashed_at(AFTER_CREATE)
    w.fs.outside_remove("/w/L/prices.bean")
    if kind == "folder":
        w.fs.outside_mkdirs("/w/L/prices.bean")
    elif kind == "link":
        w.fs.outside_symlink("/w/L/prices.bean", "/w/L/main.bean")
    else:
        w.fs.outside_fifo("/w/L/prices.bean")
    snap = w.fs.snapshot()
    assert delete_created(w.fs, MemLocks(), JD, NAME, "/w/L/prices.bean", sha256(POST)) == Refused(
        "not_a_file", "/w/L/prices.bean"
    )
    assert w.fs.snapshot() == snap


def test_delete_it_refuses_bytes_the_user_did_not_see() -> None:
    w = crashed_at(AFTER_CREATE)
    w.fs.outside_write("/w/L/prices.bean", b"a sync client's file\n")
    out = delete_created(w.fs, MemLocks(), JD, NAME, "/w/L/prices.bean", sha256(POST))
    assert out == Refused("changed", "/w/L/prices.bean")
    assert w.fs.file("/w/L/prices.bean") == b"a sync client's file\n"


def test_delete_it_of_an_absent_file_and_a_held_lock() -> None:
    w = crashed_at(AFTER_CREATE)
    locks = MemLocks()
    locks.outside_hold(next(f.path_key for f in record_of(w.fs).files if f.pre is None))
    assert delete_created(w.fs, locks, JD, NAME, "/w/L/prices.bean", sha256(POST)) == Refused(
        "lock_held", "/w/L/prices.bean"
    )
    w.fs.outside_remove("/w/L/prices.bean")
    assert delete_created(w.fs, MemLocks(), JD, NAME, "/w/L/prices.bean", sha256(POST)) == Refused(
        "absent", "/w/L/prices.bean"
    )


def test_a_failed_removal_is_reported_with_its_error() -> None:
    w = crashed_at(AFTER_CREATE)
    n = len(w.fs.effects)
    w.fs.inject(n, crash=False, err=errno.EACCES)  # the removal
    out = delete_created(w.fs, MemLocks(), JD, NAME, "/w/L/prices.bean", sha256(POST))
    assert out == ActFailed("/w/L/prices.bean", errno.EACCES)
    assert w.fs.file("/w/L/prices.bean") == POST
