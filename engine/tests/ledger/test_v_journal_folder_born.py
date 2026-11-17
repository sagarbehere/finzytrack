"""Verifier S0125 r1 F-journal-id: the brief says the journal folder's identity on Linux carries its creation time
(BL-0262). The writer's anchor holds the journal and backups folders as (st_dev, st_ino) only, so a journal folder
made again with the freed inode number (ext4 reuses it at once, the probe's `reuse.same_ino: True`) passes.
Copied from the verifier's repro (S0125 round 1, V-F1; owner: "B2"); it failed at 63b1a240 (the save landed)."""

from __future__ import annotations

from ledger_engine.ledger import save as sv
from ledger_engine.ledger.writer import NotSaved, Reason
from tests.ledger.test_save_outcome import (
    ALD,
    CHAIN,
    JD,
    A,
    B,
    Job,
    app_id,
    born_of,
    save_ports,
    save_world,
    two_file_plan,
)


def test_a_journal_folder_with_the_same_inode_but_another_creation_time_stops_the_save() -> None:
    fs = save_world(CHAIN[0])
    start = (*app_id(fs), born_of(fs, ALD))
    flipped: list[str] = []

    def flip(_: int, label: str) -> None:
        if not flipped and "backups" in label:  # after step 8, before the .part: the journal folder made again
            flipped.append(label)
            fs.outside_born(JD, born_of(fs, JD) + 1)

    fs.on_effect = flip
    out = sv.run(two_file_plan(fs, app_folder=start), save_ports(fs), Job())
    assert flipped
    assert isinstance(out, NotSaved), out
    assert out.reason is Reason.APP_FOLDER_MISSING
    assert (fs.file("/L/a.bean"), fs.file("/L/b.bean")) == (A, B)
