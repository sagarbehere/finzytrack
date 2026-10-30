"""S0061 verifier round 8 (adopted): an ACL entry added by another program during the save, at every effect, in every
folder the save touches, of several shapes (spec §15). High bar: what the save told matches the disk (INV-22) in every
power-loss state; no ledger byte lost; once every ACL is cleared, recovery finishes."""

from __future__ import annotations

import pytest

from ledger_engine.fs.port import AclEntry
from ledger_engine.ledger.writer import Landed, durable_commit
from tests.fakes.memfs import Crash
from tests.ledger.test_writer import check_told
from tests.ledger.worlds import BD, JD, check_disk, check_recovered, undo, world


def _deny(perms: str, flags: str = "") -> AclEntry:
    return AclEntry(True, frozenset(perms.split(",")), frozenset(f for f in flags.split(",") if f))


ENTRIES = {
    "read_fi": _deny("read", "file_inherit"),
    "delete_fi": _deny("delete", "file_inherit"),
    "write_fi": _deny("write,append", "file_inherit"),
    "readattr_fi": _deny("readattr,readextattr", "file_inherit"),
    "delchild_direct": _deny("delete_child"),
    "addfile_direct": _deny("add_file"),
    "search_direct": _deny("search"),
    "delete_child_di": _deny("delete_child,add_file,search", "directory_inherit,file_inherit"),
}
FOLDERS = ["/w/L", "/w/L/inc", JD, BD]


def _effects(new_dir: bool) -> int:
    w = world(new_dir=new_dir)
    durable_commit(w.fs, w.commit)
    return len(w.fs.effects)


N = {nd: _effects(nd) for nd in (False, True)}
CASES = [(nd, f, e, j) for nd in (False, True) for f in FOLDERS for e in ENTRIES for j in range(N[nd])]


@pytest.mark.parametrize(("new_dir", "folder", "entry", "j"), CASES)
def test_entry_during_save(new_dir: bool, folder: str, entry: str, j: int) -> None:
    w = world(new_dir=new_dir)
    fs = w.fs

    def hook(i: int, label: str) -> None:
        if i == j:
            fs.on_effect = None
            target = folder
            if folder == BD:
                pass
            fs.outside_acl(target, ENTRIES[entry])

    fs.on_effect = hook
    out = durable_commit(fs, w.commit)
    fs.on_effect = None
    check_told(fs, w, out)
    for node in fs.nodes.values():
        acl = getattr(node, "acl", None)
        if acl is not None:
            acl.clear()
    if isinstance(out, Landed):
        return
    got = undo(fs, w)
    check_recovered(fs, w, got)


CRASH_CASES = [
    (f, e, j, k)
    for f in FOLDERS
    for e in ("read_fi", "delete_fi", "delete_child_di")
    for j in range(0, N[False], 2)
    for k in range(j, N[False], 3)
]


@pytest.mark.parametrize(("folder", "entry", "j", "k"), CRASH_CASES)
def test_entry_then_crash(folder: str, entry: str, j: int, k: int) -> None:
    w = world()
    fs = w.fs

    def hook(i: int, label: str) -> None:
        if i == j:
            fs.on_effect = None
            fs.outside_acl(folder, ENTRIES[entry])

    fs.on_effect = hook
    fs.inject(k, crash=True)
    try:
        out = durable_commit(fs, w.commit)
    except Crash:
        out = None
    fs.on_effect = None
    fs._faults.clear()  # pyright: ignore[reportPrivateUsage]  # a crash point past the save's last effect never fires
    if out is not None:
        check_told(fs, w, out)
    for state in (fs, *fs.power_loss_states()):
        check_disk(state, w)
        for node in state.nodes.values():
            acl = getattr(node, "acl", None)
            if acl is not None:
                acl.clear()
        if out is not None and isinstance(out, Landed):
            continue
        got = undo(state, w)
        check_recovered(state, w, got)
