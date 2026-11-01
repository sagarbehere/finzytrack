"""The rebuild after a save, inside the engine (`ledger_engine.ledger.rebuild`; `design/specs/engine-jobs.md` §6.7,
§7; ADR-0068 D1; BL-0118; S0070), on real APFS in scratch folders (Beancount's own loader reads the disk). Written from
the spec's lines:

- §6.7 "After every landed save the sidecar queues a background rebuild of the mirror with Beancount's real loader";
  BL-0118: Beancount's own loader, never the replica, and no pickle cache read or written;
- §7 "record the fingerprint of the bytes they read ... A moved fingerprint discards the result and runs the job
  again": the set observed before and after the load; a load that used a file outside the set is a moved disk too;
- ADR-0059: no encrypted file is decrypted.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import overlay, rebuild
from ledger_engine.ledger.core.ledger_set import Fingerprint, LedgerSet
from ledger_engine.ledger.fingerprint import observe
from ledger_engine.ledger.policy import DesktopPolicy

pytestmark = [
    pytest.mark.inv("INV-22"),
    pytest.mark.skipif(sys.platform != "darwin", reason="the write path's OS parts are macOS-only until BL-0087"),
]

MAIN = b'option "operating_currency" "USD"\ninclude "inc.bean"\n2026-01-01 open Assets:Cash\n'
INC = b'2026-01-01 open Expenses:Food\n2026-01-02 * "Lunch"\n  Expenses:Food  5 USD\n  Assets:Cash\n'


@dataclass
class FakeJob:
    events: list[str] = field(default_factory=list[str])

    def progress(self, phase: rebuild.Phase) -> None:
        self.events.append(phase)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    led = tmp_path / "L"
    led.mkdir()
    (led / "main.bean").write_bytes(MAIN)
    (led / "inc.bean").write_bytes(INC)
    return led / "main.bean"


def hint_of(root: Path) -> Fingerprint:
    """The saved plan's fingerprint: what the preview's load read."""
    loaded = overlay.load(str(root), {}, DesktopPolicy())
    assert isinstance(loaded, overlay.Loaded)
    return loaded.fingerprint


def with_load(around: Callable[[], object]) -> rebuild.Ports:
    """The real load, with an outside program's act right after it."""

    def load(r: str) -> port.ParseResult:
        out = port.real_load(r)
        around()
        return out

    return rebuild.Ports(load=load)


def test_the_rebuild_is_beancounts_own_load_and_writes_nothing(root: Path) -> None:
    before = sorted(p.name for p in root.parent.iterdir())
    job = FakeJob()
    a = rebuild.run(rebuild.Ports(), hint_of(root), job)
    real_entries, real_errors, _ = port.real_load(str(root))
    assert (a.outcome, a.entries, a.errors) == ("loaded", len(real_entries), len(real_errors))
    assert a.entries > 0
    assert a.fingerprint == observe(hint_of(root).ledger_set)
    assert job.events == ["updating_views"]
    assert sorted(p.name for p in root.parent.iterdir()) == before  # no `.main.bean.picklecache` (BL-0118)


def test_the_load_is_never_the_replica(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_replica(*_: object, **__: object) -> object:
        msg = "the overlay replica ran"
        raise AssertionError(msg)

    monkeypatch.setattr(overlay, "load", no_replica)
    hint = observe(LedgerSet(str(root), (str(root), str(root.parent / "inc.bean")), (), (), ()))
    assert rebuild.run(rebuild.Ports(), hint, FakeJob()).outcome == "loaded"


def test_beancounts_errors_are_counted(root: Path) -> None:
    (root.parent / "inc.bean").write_bytes(INC + b'2026-01-03 * "Bad"\n  Expenses:Unknown  1 USD\n  Assets:Cash\n')
    a = rebuild.run(rebuild.Ports(), hint_of(root), FakeJob())
    assert a.outcome == "loaded"
    assert a.errors == 1


def test_a_file_edited_during_the_load_is_a_moved_disk(root: Path) -> None:
    inc = root.parent / "inc.bean"
    a = rebuild.run(with_load(lambda: inc.write_bytes(INC + b"; edited\n")), hint_of(root), FakeJob())
    assert a.outcome == "moved"
    assert a.fingerprint == observe(hint_of(root).ledger_set)  # the next try observes the disk as it is now
    assert (a.entries, a.errors) == (0, 0)


def test_an_edit_reverted_during_the_load_is_not_seen_and_the_disk_is_the_same(root: Path) -> None:
    """Content hashes: an edit undone before the second observation leaves equal observations (the A-B-A limit, §15)."""
    inc = root.parent / "inc.bean"

    def edit_and_revert() -> None:
        inc.write_bytes(INC + b"; edited\n")
        inc.write_bytes(INC)

    assert rebuild.run(with_load(edit_and_revert), hint_of(root), FakeJob()).outcome == "loaded"


def test_a_load_that_read_a_file_outside_the_set_is_moved_with_the_wider_set(root: Path) -> None:
    """An include added after the plan (an outside edit before the rebuild): the set did not name the new file, so its
    bytes were not observed; the next try observes it."""
    hint = hint_of(root)
    new = root.parent / "more.bean"
    new.write_bytes(b"2026-01-01 open Assets:Bank\n")
    root.write_bytes(MAIN + b'include "more.bean"\n')
    a = rebuild.run(rebuild.Ports(), hint, FakeJob())
    assert a.outcome == "moved"
    assert str(new) in dict(a.fingerprint.files)
    assert rebuild.run(rebuild.Ports(), a.fingerprint, FakeJob()).outcome == "loaded"


def test_a_documents_root_outside_the_set_is_moved_with_it(root: Path) -> None:
    hint = hint_of(root)
    docs = root.parent / "docs"
    (docs / "Assets" / "Cash").mkdir(parents=True)
    root.write_bytes(MAIN + b'option "documents" "docs"\n')
    a = rebuild.run(rebuild.Ports(), hint, FakeJob())
    assert a.outcome == "moved"
    assert str(docs) in dict(a.fingerprint.documents)
    assert rebuild.run(rebuild.Ports(), a.fingerprint, FakeJob()).outcome == "loaded"


def test_a_document_file_outside_the_set_is_moved_with_it(root: Path) -> None:
    """S0070 mutant `wider-document-files`: a `document` entry an outside edit added after the save names a file the
    set does not; the load used it, so the rebuild tries again with it in the set."""
    hint = hint_of(root)
    receipt = root.parent / "receipt.pdf"
    receipt.write_bytes(b"%PDF-1.4\n")
    root.write_bytes(MAIN + b'2026-01-03 document Assets:Cash "receipt.pdf"\n')
    a = rebuild.run(rebuild.Ports(), hint, FakeJob())
    assert a.outcome == "moved"
    assert str(receipt) in dict(a.fingerprint.document_files)
    assert rebuild.run(rebuild.Ports(), a.fingerprint, FakeJob()).outcome == "loaded"


def test_an_encrypted_file_is_never_decrypted(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0059: the real load meets an encrypted include and raises; no `gpg` runs."""
    (root.parent / "secret.bean.gpg").write_bytes(b"\x85\x01not really")
    root.write_bytes(MAIN + b'include "secret.bean.gpg"\n')
    hint = observe(LedgerSet(str(root), (str(root),), (), (), ()))
    with pytest.raises(port.EncryptedFileMet):
        rebuild.run(rebuild.Ports(), hint, FakeJob())
