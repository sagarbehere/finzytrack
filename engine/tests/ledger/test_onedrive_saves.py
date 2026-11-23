"""Saves in a OneDrive folder (S0151, BL-0283; owner, 2026-11-23: "1"; ADR-0013's amendment of 2026-11-23), over the
in-memory file system in Windows mode with OneDrive's ACL as probed on the owner's PC, 2026-11-23 (`worlds.in_onedrive`:
`(D;CI;DT;;;WD)` on the OneDrive root, `(D;CIID;DT;;;WD)` on every folder in it, files with no deny).

Windows grants removing or renaming away an entry when its folder grants `delete_child` **or** the entry itself
grants `DELETE`. So on Windows the folder rule (`handles.folder_blocked`) asks whether the entries the save makes may be
removed (`Right.REMOVE_ENTRIES`: the folder's `delete_child`, or the `DELETE` a new entry here would get), and a deny
entry of `delete_child` alone blocks nothing (`handles.blocking_entry(..., by_entry=True)`). Every other part of the
rule holds: a deny that new files inherit, a direct deny of adding entries, a file that grants no `DELETE` in a folder
that grants no `delete_child`, a folder whose new entries would get no `DELETE`. macOS and Linux keep the owner's rule
of 2026-10-30.

The best-effort sweeps over this world without a folder the save makes are `test_writer_best_effort.py`'s
(`BEST_EFFORT`); the ones here add the folder the save makes (it inherits OneDrive's deny), the rollback, recovery,
Keep, the history trim and the create there. The runner's real folder with OneDrive's SDDL is in
`tests/fs/test_windows.py`.
"""

from __future__ import annotations

import dataclasses
import errno
from functools import cache

import pytest

from ledger_engine.fs.port import AclEntry, Right
from ledger_engine.ledger import history
from ledger_engine.ledger.core.journal import State
from ledger_engine.ledger.core.recovery import Cause
from ledger_engine.ledger.create import Created, NotCreated
from ledger_engine.ledger.handles import Blocked, folder_blocked
from ledger_engine.ledger.recover_all import ActFailed, Done, delete_kept, keep
from ledger_engine.ledger.restore import Failed, Resolved, Stopped
from ledger_engine.ledger.writer import Landed, NotSaved, Reason, Unfinished, durable_commit, sha256
from tests.fakes.memfs import Crash, MemFS
from tests.ledger import test_create as tc
from tests.ledger.test_writer import check_told
from tests.ledger.worlds import (
    JD,
    NAME,
    ONEDRIVE_DENY,
    ONEDRIVE_ROOT_DENY,
    World,
    check_disk,
    check_every_power_loss,
    check_recovered,
    in_onedrive,
    journals,
    recover,
    world,
)

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-22")]

MAIN = "/w/L/main.bean"
FOREIGN = b"; saved by another program\n"


def onedrive(*, new_dir: bool = False) -> World:
    return world(volume="onedrive", new_dir=new_dir)


@cache
def clean_effects(new_dir: bool = False) -> tuple[str, ...]:
    w = onedrive(new_dir=new_dir)
    assert isinstance(durable_commit(w.fs, w.commit), Landed)
    return tuple(w.fs.effects)


def deny(*perms: str, flags: tuple[str, ...] = ()) -> AclEntry:
    return AclEntry(deny=True, perms=frozenset(perms), flags=frozenset(flags))


# -- the folder rule on Windows ----------------------------------------------------------------------------------------


def test_the_folder_rule_lets_a_onedrive_folder_through_on_windows() -> None:
    """The owner's hand check, 2026-11-23: every OneDrive folder carries a deny of `delete_child`, so the folder's own
    answer to removals is no; the entries the save makes would still grant `DELETE`, and the entry denies nothing
    else."""
    w = onedrive()
    for folder, entry in (("/w/L", ONEDRIVE_ROOT_DENY), ("/w/L/inc", ONEDRIVE_DENY)):
        with w.fs.open_dir(folder) as d:
            assert d.acl() == (entry,)
            assert d.caps().delete_by_entry
            assert d.allowed(".", Right.REMOVE_ENTRIES)
            assert d.allowed("main.bean" if folder == "/w/L" else "a.bean", Right.DELETE)
            assert folder_blocked(d) is None, folder


def test_on_macos_and_linux_the_same_folder_is_refused_as_before() -> None:
    """@regression-guard (it holds on the base): the POSIX rule is unchanged (owner, 2026-10-30): a deny of
    `delete_child` refuses the folder there, naming the entry."""
    w = onedrive()
    w.fs.windows = False
    with w.fs.open_dir("/w/L") as d:
        assert not d.caps().delete_by_entry
        assert folder_blocked(d) == Blocked(None)  # `_RMFILE_OK` says no first
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved), out
    assert (out.reason, out.unchanged, out.entry) == (Reason.PERMISSION, True, "/w/L/inc"), out  # the first target
    assert w.fs.snapshot() == w.before


def _off_local_ntfs(where: str) -> World:
    """A folder with OneDrive's ACL on a network share (an NTFS server's, `remote`) or on a local drive of another
    type: removals are not judged per entry there (S0151 round 1, red team Q1: a share's server judges us by its own
    view of our logon, which this process's token does not show; ReFS, FAT32 and exFAT never probed)."""
    if where == "share":
        w = world(volume="share")
        w.fs.outside_acl("/w/L", ONEDRIVE_ROOT_DENY)
        w.fs.outside_acl("/w/L/inc", ONEDRIVE_DENY)
    else:
        w = onedrive()
        w.fs.outside_fs_type(1, where)
    return dataclasses.replace(w, before=w.fs.snapshot())


@pytest.mark.parametrize("where", ["share", "refs", "exfat", "fat32"])
def test_off_local_ntfs_the_folder_rule_asks_the_folders_own_delete_child_as_before_s0151(where: str) -> None:
    """Must-not-pass (fails closed): `Caps.delete_by_entry` False, `REMOVE_ENTRIES` the folder's own `delete_child`,
    so OneDrive's deny refuses the save `permission`, nothing written, as at e8c4f19d (on aa51aca8 the share and
    every Windows drive judged removals per entry, and the save landed)."""
    w = _off_local_ntfs(where)
    with w.fs.open_dir("/w/L") as d:
        assert not d.caps().delete_by_entry
        assert not d.allowed(".", Right.REMOVE_ENTRIES)
        assert folder_blocked(d) == Blocked(None)
    out = _refused(w)
    assert (out.entry, out.detail) == ("/w/L/inc", None), out


def test_on_a_share_a_folder_that_grants_delete_child_still_saves() -> None:
    """@regression-guard (it holds on the base): the share's folders with no deny keep saving."""
    w = world(volume="share")
    with w.fs.open_dir("/w/L") as d:
        assert d.allowed(".", Right.REMOVE_ENTRIES)
    assert isinstance(durable_commit(w.fs, w.commit), Landed)


def test_a_save_in_a_onedrive_folder_lands_with_nothing_of_ours_left() -> None:
    w = onedrive()
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Landed), out
    for t in w.targets:
        assert w.fs.file(t.path) == t.post
        for name in t.scratch:
            assert w.fs.file(name) is None, name
    assert set(journals(w.fs)) == {State.CLOSED}
    check_every_power_loss(w.fs, w)


def test_a_save_that_makes_a_folder_there_lands_and_the_folder_inherits_onedrives_deny() -> None:
    """The folder the save makes inherits `(D;CIID;DT;;;WD)`: what the save puts in it is removed by its own
    `DELETE`, and the undo removes the folder by its own `DELETE` (below)."""
    w = onedrive(new_dir=True)
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Landed), out
    with w.fs.open_dir("/w/L/y2026") as d:
        assert d.acl() == (ONEDRIVE_DENY,)
        assert folder_blocked(d) is None
    assert w.fs.file("/w/L/y2026/prices.bean") == w.targets[1].post
    assert [n for n in w.fs.names_in("/w/L/y2026") if ".ft-" in n] == []


# -- must-not-pass: what still refuses on Windows ----------------------------------------------------------------------


def _refused(w: World) -> NotSaved:
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved), out
    assert (out.reason, out.unchanged) == (Reason.PERMISSION, True), out
    assert w.fs.snapshot() == w.before, "nothing written"
    return out


@pytest.mark.parametrize(
    ("entry", "detail"),
    [
        # what new files inherit: Windows' own answer for the entries the save makes refuses first (`REMOVE_ENTRIES`)
        (deny("delete", flags=("file_inherit",)), None),
        (deny("delete", flags=("file_inherit", "only_inherit")), None),
        (deny("add_file"), None),  # adding entries, on the folder itself: `CHANGE_ENTRIES`
        (deny("delete", flags=("directory_inherit",)), None),  # the folder the save makes could not be removed
        (deny("delete_child", "add_file", flags=("directory_inherit",)), None),
        # what the access checks let through, the ACL rule refuses
        (deny("delete_child", flags=("unknown",)), "deny delete_child,unknown"),  # a flag no adapter names
        (deny("writeattr", flags=("directory_inherit",)), "deny writeattr,directory_inherit"),
        (deny("readsecurity", flags=("file_inherit", "only_inherit")), "deny readsecurity,file_inherit,only_inherit"),
        (deny("delete_child", "write", flags=("file_inherit",)), "deny delete_child,write,file_inherit"),
    ],
)
def test_a_deny_that_reaches_what_the_save_writes_or_removes_still_refuses(entry: AclEntry, detail: str | None) -> None:
    """Must-not-pass (S0151): the save still refuses `permission`, nothing written, naming the OneDrive folder that
    holds the folder it would make (the base refused at the first target's folder, `/w/L/inc`, for OneDrive's own
    entry)."""
    w = onedrive(new_dir=True)
    w.fs.outside_acl("/w/L", entry)
    out = _refused(w)
    assert (out.entry, out.detail) == ("/w/L", detail), out


def test_a_file_without_delete_in_a_folder_without_delete_child_still_refuses_naming_the_file() -> None:
    w = onedrive()
    w.fs.outside_deny(MAIN, Right.DELETE)
    out = _refused(w)
    assert (out.path, out.entry, out.detail) == (MAIN, None, None), out


def test_a_file_without_delete_is_saved_where_its_folder_grants_delete_child() -> None:
    """Windows' other half: the folder's `delete_child` grants it (the fake's Windows mode; the adapter asks Windows,
    which decides it so)."""
    w = onedrive()
    w.fs.outside_acl_clear("/w/L")
    w.fs.outside_deny(MAIN, Right.DELETE)
    assert isinstance(durable_commit(w.fs, w.commit), Landed)


@pytest.mark.parametrize("folder", ["/w/L", "/w/L/inc"])
def test_a_folder_whose_new_entries_would_get_no_delete_refuses(folder: str) -> None:
    """No deny entry shows it: the folder's allow entries grant what is made there no `DELETE` (`REMOVE_ENTRIES`
    asks Windows what a new entry would get); with the folder's own `delete_child` denied, nothing of ours could be
    removed."""
    w = onedrive()
    w.fs.outside_grant_no_delete(folder)
    out = _refused(w)
    assert (out.entry, out.detail) == (folder, None), out


def test_new_entries_without_delete_are_fine_where_the_folder_grants_delete_child() -> None:
    w = onedrive()
    w.fs.outside_acl_clear("/w/L")
    w.fs.outside_grant_no_delete("/w/L")
    assert isinstance(durable_commit(w.fs, w.commit), Landed)


def test_a_folder_made_in_a_folder_whose_new_folders_would_get_no_delete_refuses_at_its_holder() -> None:
    """The folder the save makes, and what is made in it, must be removable too: a `delete` its new folders inherit
    refuses the holder (the undo removes that folder by its own `DELETE`)."""
    w = onedrive(new_dir=True)
    w.fs.outside_acl("/w/L", deny("delete", flags=("directory_inherit", "only_inherit")))
    out = _refused(w)
    assert out.entry == "/w/L", out


def test_a_folder_whose_made_folders_deny_delete_child_refuses_where_their_entries_get_no_delete() -> None:
    """Must-not-pass (S0151 round 1, red team RT-1): the folder grants itself `delete_child`, its new folders inherit a
    deny of it (`(D;CIIO;DT;;;WD)`), and what is made here gets no `DELETE`. The folder's own `delete_child` covers
    only its direct entries: what the save puts in the folder it makes could never be removed. Refused `permission`,
    nothing written (on aa51aca8 the folder's own `delete_child` answered yes for every level, and the save that made
    a folder ended `Unfinished`, its recovery never resolving)."""
    w = onedrive(new_dir=True)
    w.fs.outside_acl_clear("/w/L")
    w.fs.outside_acl_clear("/w/L/inc")
    w.fs.outside_acl("/w/L", deny("delete_child", flags=("directory_inherit", "only_inherit")))
    w.fs.outside_grant_no_delete("/w/L")
    w = dataclasses.replace(w, before=w.fs.snapshot())
    with w.fs.open_dir("/w/L") as d:
        assert not d.allowed(".", Right.REMOVE_ENTRIES)
    out = _refused(w)
    assert (out.entry, out.detail) == ("/w/L", None), out


def test_a_folder_whose_made_folders_deny_delete_child_is_fine_while_their_entries_grant_delete() -> None:
    """@regression-guard (it holds on the base). Its twin: the same deny, with what is made here granting `DELETE` (the
    entry's own side): the save lands."""
    w = onedrive(new_dir=True)
    w.fs.outside_acl_clear("/w/L")
    w.fs.outside_acl_clear("/w/L/inc")
    w.fs.outside_acl("/w/L", deny("delete_child", flags=("directory_inherit", "only_inherit")))
    assert isinstance(durable_commit(w.fs, w.commit), Landed)


def test_a_journal_or_backups_folder_with_onedrives_acl_is_judged_by_the_same_rule() -> None:
    w = onedrive()
    in_onedrive(w.fs, "/w/local")
    assert isinstance(durable_commit(w.fs, w.commit), Landed)
    w = onedrive()
    in_onedrive(w.fs, "/w/local")
    w.fs.outside_grant_no_delete(JD)
    out = _refused(w)
    assert (out.path, out.entry) == (None, JD), out


# -- the rollback, recovery, Keep, the trim and the create there -------------------------------------------------------


def test_the_rollback_in_a_onedrive_folder_puts_the_old_bytes_back_and_removes_the_folder_it_made() -> None:
    e = clean_effects(True)
    at = e.index("move /w/L:.main.bean.ft-s1-j7.new,main.bean")  # every other file placed, the root not yet
    w = onedrive(new_dir=True)
    w.fs.inject(at, crash=False, err=errno.EIO)
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Unfinished | NotSaved), out
    check_told(w.fs, w, out)
    check_disk(w.fs, w)
    if isinstance(out, Unfinished):
        check_recovered(w.fs, w, recover(w.fs))
    for t in w.targets:
        if t.pre is not None:
            assert w.fs.file(t.path) == t.pre


def test_recovery_in_a_onedrive_folder_resolves_after_a_crash_and_removes_the_folder_it_made() -> None:
    w = onedrive(new_dir=True)
    w.fs.inject(clean_effects(True).index(f"rename {JD}:{NAME}.open,{NAME}.closed"), crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    out = recover(w.fs)
    if isinstance(out, Stopped) and out.cause is Cause.CREATED_PATH_TAKEN:
        w.fs.outside_remove(out.path or "")
        out = recover(w.fs)
    assert out == Resolved(NAME), out
    assert "/w/L/y2026" not in w.fs.snapshot()
    check_recovered(w.fs, w, out)


def _crashed(new_dir: bool = False) -> World:
    w = onedrive(new_dir=new_dir)
    w.fs.inject(clean_effects(new_dir).index("move /w/L:.main.bean.ft-s1-j7.new,main.bean") + 1, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    return w


def test_recovery_stops_on_an_entry_of_ours_that_lost_its_delete_and_writes_nothing() -> None:
    """Between the crash and recovery the file the save placed loses its `DELETE` (another program's ACL change): its
    folder grants no `delete_child`, so the put-back could not replace it: `permission`, nothing written."""
    w = _crashed()
    w.fs.outside_grant_no_delete(MAIN, new=False, own=True)
    before = w.fs.snapshot()
    out = recover(w.fs)
    assert isinstance(out, Stopped), out
    assert out.cause is Cause.PERMISSION, out
    assert w.fs.snapshot() == before
    assert set(journals(w.fs)) == {State.OPEN}


def test_an_outside_file_at_our_name_that_grants_no_delete_is_never_lost_and_never_told_resolved() -> None:
    """@regression-guard (it holds on the base; S0151 round 1, RT-4: the outcome pinned, no `OSError` let through).
    Keep's move of an outside save at one of our names needs its `DELETE`: refused, the try ends `Failed` (`io_error`,
    `EACCES`), never "undone", the outside bytes where they were, the journal still `.open`."""
    w = _crashed()
    temp = "/w/L/.main.bean.ft-s1-j7.tmp"
    w.fs.outside_write(temp, FOREIGN)
    w.fs.outside_grant_no_delete(temp, new=False, own=True)
    out = recover(w.fs)
    assert out == Failed(Cause.IO_ERROR, MAIN, errno.EACCES), out  # RT-4: the outcome pinned, never an exception
    assert FOREIGN in {w.fs.file(p) for p in w.fs.snapshot() if p.startswith("/w/L/")}
    assert set(journals(w.fs)) == {State.OPEN}


def test_keep_and_a_kept_copys_delete_work_in_a_onedrive_folder() -> None:
    w = _crashed()
    w.fs.outside_write("/w/L/.main.bean.ft-s1-j7.tmp", FOREIGN)
    assert isinstance(recover(w.fs), Stopped)
    kept = next(n for n in w.fs.names_in("/w/L") if ".kept-" in n)
    assert delete_kept(w.fs, JD, NAME, MAIN, kept, sha256(FOREIGN)) == Done()
    assert kept not in w.fs.names_in("/w/L")
    assert keep(w.fs, JD, NAME) == Done()
    assert set(journals(w.fs)) == {State.KEPT}


def test_a_kept_copy_that_grants_no_delete_is_not_deleted_and_says_so() -> None:
    w = _crashed()
    w.fs.outside_write("/w/L/.main.bean.ft-s1-j7.tmp", FOREIGN)
    assert isinstance(recover(w.fs), Stopped)
    kept = next(n for n in w.fs.names_in("/w/L") if ".kept-" in n)
    w.fs.outside_grant_no_delete(f"/w/L/{kept}", new=False, own=True)
    out = delete_kept(w.fs, JD, NAME, MAIN, kept, sha256(FOREIGN))
    assert isinstance(out, ActFailed), out
    assert w.fs.file(f"/w/L/{kept}") == FOREIGN


def test_the_history_trim_in_a_onedrive_app_folder_keeps_sixteen() -> None:
    """The trim removes our own records and backups by their own `DELETE` there (and never fails the save: an entry
    it cannot remove is left)."""
    fs = MemFS(windows=True)
    jd, bd = "/app/local/k1/journal", "/app/local/k1/backups"
    fs.outside_mkdirs(jd)
    fs.outside_mkdirs(bd)
    in_onedrive(fs, "/app")
    for i in range(20):
        fs.outside_write(f"{jd}/s0-j{i:03d}.closed", b"{}")
        fs.outside_mkdirs(f"{bd}/s0-j{i:03d}")
        fs.outside_write(f"{bd}/s0-j{i:03d}/0-a.bean", b"old")
    fs.outside_write(f"{jd}/s9-jcur.closed", b"{}")
    fs.outside_grant_no_delete(f"{jd}/s0-j000.closed", new=False, own=True)
    out = history.trim(fs, "/app", "k1", "s9-jcur")
    assert out.removed == 4, out
    assert out.left >= 1, out
    assert fs.file(f"{jd}/s0-j000.closed") == b"{}"
    assert fs.file(f"{jd}/s0-j001.closed") is None


def _create_fs() -> MemFS:
    fs = MemFS(windows=True)
    fs.outside_mkdirs(tc.U)
    in_onedrive(fs, tc.U)
    return fs


def test_a_create_in_a_onedrive_folder_is_created() -> None:
    fs = _create_fs()
    out, _ = tc.go(fs)
    assert out == Created(tc.T)
    assert tc.created_whole(fs)


def test_a_create_in_a_onedrive_folder_whose_new_entries_get_no_delete_is_not_created_and_makes_nothing() -> None:
    fs = _create_fs()
    fs.outside_grant_no_delete(tc.U)
    before = fs.snapshot()
    out, _ = tc.go(fs)
    assert isinstance(out, NotCreated), out
    assert fs.snapshot() == before


@pytest.mark.parametrize("after", [False, True])
def test_an_io_error_at_every_effect_of_a_create_in_a_onedrive_folder_is_told_as_the_disk_holds_it(
    after: bool,
) -> None:
    from ledger_engine.ledger.create import outcome

    fs0 = _create_fs()
    assert tc.go(fs0)[0] == Created(tc.T)
    for i in range(len(fs0.effects)):
        fs = _create_fs()
        fs.inject(i, crash=False, err=errno.EIO, after=after)
        try:
            out, _ = tc.go(fs)
        except OSError:
            assert outcome(fs, tc.PLAN).manifest in ("ours", "absent"), (i, fs0.effects[i])
            continue
        if isinstance(out, Created):
            assert tc.created_whole(fs), (i, fs0.effects[i])
        else:
            assert isinstance(out, NotCreated), out
            assert outcome(fs, tc.PLAN).manifest != "ours", (i, fs0.effects[i])


# -- sweeps with the folder the save makes (the best-effort file sweeps the rest) --------------------------------------


def _cuts(label: str) -> list[int | None]:
    return [None, 0, 7] if label.startswith("write ") else [None]


CRASH_POINTS = [(i, cut) for i, x in enumerate(clean_effects(True)) for cut in _cuts(x)]


@pytest.mark.parametrize(("at", "cut"), CRASH_POINTS)
def test_a_crash_at_every_effect_then_every_power_loss_leaves_a_disk_recovery_resolves(
    at: int, cut: int | None
) -> None:
    w = onedrive(new_dir=True)
    w.fs.inject(at, crash=True, cut=cut)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    for state in (w.fs, *w.fs.power_loss_states()):
        check_disk(state, w)
        check_recovered(state, w, recover(state))


@pytest.mark.parametrize("at", range(len(clean_effects(True))))
def test_an_io_error_at_every_effect_is_rolled_back_and_told_truthfully(at: int) -> None:
    w = onedrive(new_dir=True)
    w.fs.inject(at, crash=False, err=errno.ENOSPC)
    out = durable_commit(w.fs, w.commit)
    check_disk(w.fs, w)
    check_every_power_loss(w.fs, w)
    check_told(w.fs, w, out)
    assert not isinstance(out, Landed), (at, w.fs.effects[at])
    if isinstance(out, Unfinished):
        check_recovered(w.fs, w, recover(w.fs))
    else:
        assert isinstance(out, NotSaved), out
        assert State.OPEN not in journals(w.fs)


@pytest.mark.parametrize("at", range(len(clean_effects(True))))
def test_an_effect_that_happens_but_answers_an_error_is_never_told_saved_and_recovery_resolves(at: int) -> None:
    w = onedrive(new_dir=True)
    w.fs.inject(at, crash=False, err=errno.EIO, after=True)
    out = durable_commit(w.fs, w.commit)
    check_disk(w.fs, w)
    check_told(w.fs, w, out)
    assert not isinstance(out, Landed)
    check_recovered(w.fs, w, recover(w.fs))


def _rollback_points() -> list[tuple[int, int]]:
    e = clean_effects(True)
    start = e.index(f"rename {JD}:{NAME}.part,{NAME}.open")
    out: list[tuple[int, int]] = []
    for at in range(start, len(e)):
        w = onedrive(new_dir=True)
        w.fs.inject(at, crash=False)
        durable_commit(w.fs, w.commit)
        out += [(at, j) for j in range(at + 1, len(w.fs.effects))]
    return out


ROLLBACK_POINTS = _rollback_points()


@pytest.mark.parametrize(("at", "j"), ROLLBACK_POINTS)
def test_an_io_error_or_a_crash_in_the_rollback_is_told_truthfully_and_recovery_then_resolves(at: int, j: int) -> None:
    w = onedrive(new_dir=True)
    w.fs.inject(at, crash=False)
    w.fs.inject(j, crash=False, err=errno.EIO)
    out = durable_commit(w.fs, w.commit)
    assert not isinstance(out, Landed)
    check_told(w.fs, w, out)
    for state in (w.fs, *w.fs.power_loss_states()):
        check_disk(state, w)
        check_recovered(state, w, recover(state))
    w = onedrive(new_dir=True)
    w.fs.inject(at, crash=False)
    w.fs.inject(j, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    for state in (w.fs, *w.fs.power_loss_states()):
        check_disk(state, w)
        check_recovered(state, w, recover(state))
