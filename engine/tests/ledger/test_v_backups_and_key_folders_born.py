"""BL-0277 (1), S0125 round 2's V2-F1: the birth-time identity (B2; S0125 round 1, owner: "B2") of the save's other
folders. `test_v_journal_folder_born.py` pins it for `journal/`; these pin it for `backups/` (the writer's anchor,
`writer.anchored`, checked before each write up to the first swap) and for `<key>/` (the identity the journal and
backups folders are made through, `save._ensure_dir`'s `parent_is`). On Linux a folder removed and made again at once
may take the freed inode number (ext4 reuses it, BL-0262), so only its creation time tells it from the one the save
found. Each test is a `@regression-guard` (the base held it) and kills a mutant that keeps the two-part identity there
(S0156's Hand-off): `Anchor(..., (bd, bd_id[:2]))`, and `parent_is=key_id[:2]` on the journal's and on the backups'
line of `save._gated`."""

from __future__ import annotations

import pytest

from ledger_engine.fs.port import DirHandle, FolderId
from ledger_engine.ledger import save as sv
from ledger_engine.ledger.writer import NotSaved, Reason
from tests.ledger.test_save_outcome import ALD, CHAIN, A, B, Job, app_id, born_of, save_ports, save_world, two_file_plan

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-22")]

KEY = f"{ALD}/local/k1"
BD = f"{KEY}/backups"


def _not_saved_unchanged(out: sv.SaveOutcome) -> None:
    assert isinstance(out, NotSaved), out
    assert out.reason is Reason.APP_FOLDER_MISSING
    assert out.unchanged


def test_a_backups_folder_with_the_same_inode_but_another_creation_time_stops_the_save() -> None:
    """@regression-guard (held on the base; BL-0277).
    Must not pass `(bd, bd_id[:2])` in the writer's anchor: the backups folder made again with the freed inode
    number after the backups were written, at the record's `.part`, would keep the save going, and the ledger files
    would be swapped while the backups the record names are gone."""
    fs = save_world(CHAIN[0])
    start = (*app_id(fs), born_of(fs, ALD))
    flipped: list[str] = []

    def flip(_: int, label: str) -> None:
        if not flipped and label.startswith(f"create {KEY}/journal:") and label.endswith(".part"):
            flipped.append(label)
            fs.outside_born(BD, born_of(fs, BD) + 1)

    fs.on_effect = flip
    out = sv.run(two_file_plan(fs, app_folder=start), save_ports(fs), Job())
    assert flipped, "the save wrote no record"
    _not_saved_unchanged(out)
    assert (fs.file("/L/a.bean"), fs.file("/L/b.bean")) == (A, B)
    assert not any(e.startswith(("swap /L:", "rename /L:", "create /L:")) for e in fs.effects), "a ledger write"


def test_a_key_folder_with_the_same_inode_but_another_creation_time_makes_no_backups_folder_in_it() -> None:
    """@regression-guard (held on the base; BL-0277).
    Must not pass `parent_is=key_id[:2]` on the backups' line: `<key>/` made again with the freed inode number after
    the journal folder was made in it is not the folder the save found, so no backups folder is made there and the
    save stops before any write."""
    fs = save_world(CHAIN[0])
    start = (*app_id(fs), born_of(fs, ALD))
    flipped: list[str] = []

    def flip(_: int, label: str) -> None:
        if not flipped and label == f"mkdir {KEY}:journal":
            flipped.append(label)
            fs.outside_born(KEY, born_of(fs, KEY) + 1)

    fs.on_effect = flip
    out = sv.run(two_file_plan(fs, app_folder=start), save_ports(fs), Job())
    assert flipped, "the save made no journal folder"
    _not_saved_unchanged(out)
    assert f"mkdir {KEY}:backups" not in fs.effects, "a backups folder made in another <key>/ folder"
    assert (fs.file("/L/a.bean"), fs.file("/L/b.bean")) == (A, B)


def test_a_key_folder_made_again_right_after_its_identity_was_read_gets_no_journal_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """@regression-guard (held on the base; BL-0277).
    Must not pass `parent_is=key_id[:2]` on the journal's line: `<key>/` made again with the freed inode number
    between the read of its identity and the journal folder's `mkdir` (no effect of the fake falls there, so the
    read itself is the hook) gets no journal folder made in it."""
    fs = save_world(CHAIN[0])
    start = (*app_id(fs), born_of(fs, ALD))
    real = sv.folder_id
    flipped: list[FolderId] = []

    def folder_id(h: DirHandle) -> FolderId:
        found = real(h)
        if not flipped and found == real_key_id():
            flipped.append(found)
            fs.outside_born(KEY, born_of(fs, KEY) + 1)
        return found

    def real_key_id() -> FolderId | None:
        try:
            with fs.open_dir(KEY) as k:
                return real(k)
        except FileNotFoundError:
            return None

    monkeypatch.setattr(sv, "folder_id", folder_id)
    out = sv.run(two_file_plan(fs, app_folder=start), save_ports(fs), Job())
    assert flipped, "the save never read <key>/'s identity"
    _not_saved_unchanged(out)
    assert f"mkdir {KEY}:journal" not in fs.effects, "a journal folder made in another <key>/ folder"
    assert (fs.file("/L/a.bean"), fs.file("/L/b.bean")) == (A, B)


def test_the_same_worlds_with_no_folder_made_again_land() -> None:
    """@regression-guard: the companion, so the world above is one the save lands in."""
    fs = save_world(CHAIN[0])
    out = sv.run(two_file_plan(fs, app_folder=(*app_id(fs), born_of(fs, ALD))), save_ports(fs), Job())
    assert isinstance(out, sv.Saved), out
