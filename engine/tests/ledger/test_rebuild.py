"""The rebuild after a save, inside the engine (`ledger_engine.ledger.rebuild`; `design/specs/engine-jobs.md` §6.7,
§7; ADR-0068 D1; BL-0118; S0070), on real APFS in scratch folders (Beancount's own loader reads the disk). Written from
the spec's lines:

- §6.7 "After every landed save the sidecar queues a background rebuild of the mirror with Beancount's real loader";
  BL-0118: Beancount's own loader, never the replica, and no pickle cache read or written;
- §7 "record the fingerprint of the bytes they read ... A moved fingerprint discards the result and runs the job
  again": the set observed before and after the load; a load that used a file outside the set is a moved disk too;
- ADR-0059: no encrypted file is decrypted;
- §6.7 "Its publish ... follows `design/specs/mirror/publish.md` §4" (S0087, BL-0113): the folder and the cleanup
  first, then the build, the re-check just before the swap (§7: the set observed again, no `.open` journal), the swap;
  a job that finds the published generation fresh (§8) builds nothing.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any, override

import pytest

from ledger_engine.fs.darwin import for_this_os
from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import overlay, rebuild
from ledger_engine.ledger.core.ledger_set import Fingerprint, LedgerSet, decode, digest
from ledger_engine.ledger.fingerprint import observe
from ledger_engine.ledger.policy import DesktopPolicy
from ledger_engine.mirror import publish as pb
from ledger_engine.mirror.store import SqliteStore
from tests.mirror.publish_support import BUILT_AT, ENGINE_BUILD, KEY, TODAY, fs_now, meta

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


CACHE: list[Path] = []
"""The current test's cache folder (`go`'s target), set by the autouse fixture below."""


@pytest.fixture(autouse=True)
def cache(tmp_path: Path) -> Iterator[Path]:
    c = tmp_path / "cache"
    c.mkdir()
    CACHE.append(c)
    yield c
    CACHE.pop()


def target(**kw: Any) -> rebuild.Target:
    t = rebuild.Target(
        app_cache=str(CACHE[-1]),
        workspace_key=KEY,
        app_local_data=str(CACHE[-1].parent / "app"),
        lineage="lin1",
        floor=0,
        today=TODAY,
        built_at=BUILT_AT,
        now_ns=fs_now(CACHE[-1]),
        engine_build=ENGINE_BUILD,
    )
    return replace(t, **kw)


def go(ports: rebuild.Ports, hint: Fingerprint, job: FakeJob, **kw: Any) -> rebuild.Answer:
    """The rebuild with its publish into the test's cache folder (publish.md §4)."""
    return rebuild.run(ports, hint, job, target(**kw))


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
    a = go(rebuild.Ports(), hint_of(root), job)
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
    assert go(rebuild.Ports(), hint, FakeJob()).outcome == "loaded"


def test_beancounts_errors_are_counted(root: Path) -> None:
    (root.parent / "inc.bean").write_bytes(INC + b'2026-01-03 * "Bad"\n  Expenses:Unknown  1 USD\n  Assets:Cash\n')
    a = go(rebuild.Ports(), hint_of(root), FakeJob())
    assert a.outcome == "loaded"
    assert a.errors == 1


def test_a_file_edited_during_the_load_is_a_moved_disk(root: Path) -> None:
    inc = root.parent / "inc.bean"
    a = go(with_load(lambda: inc.write_bytes(INC + b"; edited\n")), hint_of(root), FakeJob())
    assert a.outcome == "moved"
    assert a.fingerprint == observe(hint_of(root).ledger_set)  # the next try observes the disk as it is now
    assert (a.entries, a.errors) == (0, 0)


def test_an_edit_reverted_during_the_load_is_not_seen_and_the_disk_is_the_same(root: Path) -> None:
    """Content hashes: an edit undone before the second observation leaves equal observations (the A-B-A limit, §15)."""
    inc = root.parent / "inc.bean"

    def edit_and_revert() -> None:
        inc.write_bytes(INC + b"; edited\n")
        inc.write_bytes(INC)

    assert go(with_load(edit_and_revert), hint_of(root), FakeJob()).outcome == "loaded"


def test_a_load_that_read_a_file_outside_the_set_is_moved_with_the_wider_set(root: Path) -> None:
    """An include added after the plan (an outside edit before the rebuild): the set did not name the new file, so its
    bytes were not observed; the next try observes it."""
    hint = hint_of(root)
    new = root.parent / "more.bean"
    new.write_bytes(b"2026-01-01 open Assets:Bank\n")
    root.write_bytes(MAIN + b'include "more.bean"\n')
    a = go(rebuild.Ports(), hint, FakeJob())
    assert a.outcome == "moved"
    assert str(new) in dict(a.fingerprint.files)
    assert go(rebuild.Ports(), a.fingerprint, FakeJob()).outcome == "loaded"


def test_a_documents_root_outside_the_set_is_moved_with_it(root: Path) -> None:
    hint = hint_of(root)
    docs = root.parent / "docs"
    (docs / "Assets" / "Cash").mkdir(parents=True)
    root.write_bytes(MAIN + b'option "documents" "docs"\n')
    a = go(rebuild.Ports(), hint, FakeJob())
    assert a.outcome == "moved"
    assert str(docs) in dict(a.fingerprint.documents)
    assert go(rebuild.Ports(), a.fingerprint, FakeJob()).outcome == "loaded"


def test_a_document_file_outside_the_set_is_moved_with_it(root: Path) -> None:
    """S0070 mutant `wider-document-files`: a `document` entry an outside edit added after the save names a file the
    set does not; the load used it, so the rebuild tries again with it in the set."""
    hint = hint_of(root)
    receipt = root.parent / "receipt.pdf"
    receipt.write_bytes(b"%PDF-1.4\n")
    root.write_bytes(MAIN + b'2026-01-03 document Assets:Cash "receipt.pdf"\n')
    a = go(rebuild.Ports(), hint, FakeJob())
    assert a.outcome == "moved"
    assert str(receipt) in dict(a.fingerprint.document_files)
    assert go(rebuild.Ports(), a.fingerprint, FakeJob()).outcome == "loaded"


def test_an_encrypted_file_is_never_decrypted(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0059: the real load meets an encrypted include and raises; no `gpg` runs."""
    (root.parent / "secret.bean.gpg").write_bytes(b"\x85\x01not really")
    root.write_bytes(MAIN + b'include "secret.bean.gpg"\n')
    hint = observe(LedgerSet(str(root), (str(root),), (), (), ()))
    with pytest.raises(port.EncryptedFileMet):
        go(rebuild.Ports(), hint, FakeJob())


def test_a_dated_file_under_a_documents_root_in_the_set_is_loaded_on_the_first_try(root: Path) -> None:
    """S0070 r1 F1 (verifier F1, red team F2): Beancount's documents step makes a `Document` entry for each dated file
    under a documents root; the root's listing already holds it, so it is not a file outside the set."""
    cash = root.parent / "docs" / "Assets" / "Cash"
    cash.mkdir(parents=True)
    (cash / "2026-01-02.receipt.pdf").write_bytes(b"x")
    root.write_bytes(MAIN + b'option "documents" "docs"\n')
    loads: list[str] = []

    def load(r: str) -> port.ParseResult:
        loads.append(r)
        return port.real_load(r)

    a = go(rebuild.Ports(load=load), hint_of(root), FakeJob())
    assert (a.outcome, len(loads)) == ("loaded", 1)
    assert a.entries > 0


# -- the publish (S0087, BL-0113; publish.md §4, §8) ------------------------------------------------------------------


def mirror(cache: Path) -> Path:
    return cache / "workspaces" / KEY / "mirror"


def generations(cache: Path) -> list[str]:
    return sorted(p.name for p in mirror(cache).iterdir() if pb.gen_of(p.name) is not None)


def test_a_loaded_result_is_published_with_its_number_and_ledger_version(root: Path, cache: Path) -> None:
    a = go(rebuild.Ports(), hint_of(root), FakeJob())
    assert a.outcome == "loaded"
    assert isinstance(a.published, pb.Published), a
    name = os.readlink(mirror(cache) / pb.CURRENT)
    assert a.published.gen == pb.Gen(1, name)
    facts = meta(mirror(cache) / name)
    assert facts["ledger_version"] == a.published.ledger_version == f"1/{digest(a.fingerprint)}"
    assert facts["as_of"] == TODAY.isoformat()


def test_a_rebuild_that_finds_the_published_generation_fresh_builds_nothing(root: Path, cache: Path) -> None:
    first = go(rebuild.Ports(), hint_of(root), FakeJob())
    assert isinstance(first.published, pb.Published)
    loads: list[str] = []

    def load(r: str) -> port.ParseResult:
        loads.append(r)
        return port.real_load(r)

    again = go(rebuild.Ports(load=load), hint_of(root), FakeJob())
    assert again.outcome == "fresh"
    assert again.published == first.published
    assert loads == []
    assert len(generations(cache)) == 1


@pytest.mark.parametrize("change", ["today", "engine_build", "disk"])
def test_a_generation_that_is_not_fresh_is_rebuilt(root: Path, cache: Path, change: str) -> None:
    go(rebuild.Ports(), hint_of(root), FakeJob())
    kw: dict[str, Any] = {}
    if change == "today":
        kw["today"] = date(2026, 11, 9)
    elif change == "engine_build":
        kw["engine_build"] = "a newer engine"
    else:
        (root.parent / "inc.bean").write_bytes(INC + b"; edited\n")
    a = go(rebuild.Ports(), hint_of(root), FakeJob(), **kw)
    assert a.outcome == "loaded"
    assert isinstance(a.published, pb.Published)
    assert a.published.gen.number == 2


def test_the_number_is_above_the_sidecars_floor(root: Path) -> None:
    a = go(rebuild.Ports(), hint_of(root), FakeJob(), floor=41)
    assert isinstance(a.published, pb.Published)
    assert a.published.gen.number == 42


def test_an_open_journal_seen_at_the_recheck_publishes_nothing(root: Path, cache: Path) -> None:
    """engine-jobs §7: "A `.open` journal refuses the job"; publish.md §4 step 7."""
    journal = cache.parent / "app" / "local" / KEY / "journal"

    def cut_save() -> None:
        journal.mkdir(parents=True)
        (journal / "s1-j1.open").write_bytes(b"{}")

    a = go(with_load(cut_save), hint_of(root), FakeJob())
    assert a.outcome == "paused"
    assert a.published is None
    assert not (mirror(cache) / pb.CURRENT).exists()


class EditingStore(SqliteStore):
    """An outside edit while the build writes the generation."""

    def __init__(self, edit: Callable[[], object]) -> None:
        self._edit = edit

    @override
    def build(self, *args: Any, **kw: Any) -> Any:
        out = super().build(*args, **kw)
        self._edit()
        return out


def test_an_edit_during_the_build_is_moved_and_not_published(root: Path, cache: Path) -> None:
    inc = root.parent / "inc.bean"
    first = go(rebuild.Ports(), hint_of(root), FakeJob())
    assert isinstance(first.published, pb.Published)
    inc.write_bytes(INC + b"; one\n")
    ports = rebuild.Ports(store=EditingStore(lambda: inc.write_bytes(INC + b"; two\n")))
    a = go(ports, hint_of(root), FakeJob())
    assert a.outcome == "moved"
    assert a.fingerprint == observe(hint_of(root).ledger_set)  # the next try starts from the disk as it is now
    assert os.readlink(mirror(cache) / pb.CURRENT) == first.published.gen.name
    assert len(generations(cache)) == 2  # the unpublished build waits for the next cleanup


def test_the_cleanup_runs_first_even_when_the_load_then_moves(root: Path, cache: Path) -> None:
    m = mirror(cache)
    m.mkdir(parents=True)
    (m / "junk").write_bytes(b"x")
    inc = root.parent / "inc.bean"
    a = go(with_load(lambda: inc.write_bytes(INC + b"; edited\n")), hint_of(root), FakeJob())
    assert a.outcome == "moved"
    assert a.cleaned is not None
    assert a.cleaned.removed == 1
    assert not (m / "junk").exists()


def test_after_the_ledger_is_located_elsewhere_the_old_places_generation_is_not_fresh(
    root: Path, cache: Path, tmp_path: Path
) -> None:
    """S0087 round 1 (red team F2): a copy of the ledger located under the same key, saved there: the generation of
    the old place is another ledger's, so the rebuild loads and publishes the new place."""
    assert go(rebuild.Ports(), hint_of(root), FakeJob()).outcome == "loaded"
    new = tmp_path / "New"
    shutil.copytree(root.parent, new)
    (new / "main.bean").write_bytes(MAIN + b'2026-02-01 * "Saved there"\n  Expenses:Food  9 USD\n  Assets:Cash\n')
    b = go(rebuild.Ports(), hint_of(new / "main.bean"), FakeJob())
    assert b.outcome == "loaded"
    assert isinstance(b.published, pb.Published)
    assert b.published.gen.number == 2


def test_the_generation_is_stamped_with_what_the_load_read_not_the_hint(root: Path, cache: Path) -> None:
    """S0087 round 1 (verifier V1; publish.md §8): a hint that names a file the ledger no longer includes (the queued
    rebuild of an earlier save) does not put that file in the generation's fingerprint, so deleting it later does not
    make every save refused."""
    hint = hint_of(root)  # names inc.bean
    root.write_bytes(b'option "operating_currency" "USD"\n2026-01-01 open Assets:Cash\n')  # a save dropped the include
    a = go(rebuild.Ports(), hint, FakeJob())
    assert a.outcome == "loaded"
    got = pb.read_published(for_this_os(), str(cache), KEY, SqliteStore())
    assert got is not None
    stamped = decode(got.meta.fingerprint.encode("ascii"))
    assert str(root.parent / "inc.bean") not in dict(stamped.files)
    assert str(root) in dict(stamped.files)
    (root.parent / "inc.bean").unlink()  # no longer part of the ledger
    assert pb.fresh_on_disk(
        got.meta, lambda fp: observe(fp.ledger_set), lambda r: pb.same_ledger(for_this_os(), r, str(root))
    )


def test_a_glob_the_ledger_still_includes_stays_in_the_stamp(root: Path, cache: Path) -> None:
    """The other side of V1: a file appearing under a glob the ledger includes makes the generation not fresh."""
    sub = root.parent / "sub"
    sub.mkdir()
    (sub / "a.bean").write_bytes(b"2026-01-01 open Assets:A\n")
    root.write_bytes(MAIN + b'include "sub/*.bean"\n')
    assert go(rebuild.Ports(), hint_of(root), FakeJob()).outcome == "loaded"
    got = pb.read_published(for_this_os(), str(cache), KEY, SqliteStore())
    assert got is not None

    def ours(r: str) -> bool:
        return pb.same_ledger(for_this_os(), r, str(root))

    def obs(fp: Fingerprint) -> Fingerprint:
        return observe(fp.ledger_set)

    assert pb.fresh_on_disk(got.meta, obs, ours)
    (sub / "b.bean").write_bytes(b"2026-01-01 open Assets:B\n")
    assert not pb.fresh_on_disk(got.meta, obs, ours)


def test_an_edit_between_the_load_and_the_stamp_is_moved_never_published(root: Path, cache: Path) -> None:
    """Round 1 (the stamp is observed after the load): an edit just before that observation would stamp the edited
    state on rows of the earlier one; the hint's set observed once more sees it, and nothing is published."""
    calls: list[int] = []
    inc = root.parent / "inc.bean"

    def observe_with_edit(s: LedgerSet) -> Fingerprint:
        calls.append(1)
        if len(calls) == 3:  # before, after, then the stamp's observation (no generation yet: no freshness check)
            inc.write_bytes(INC + b"; edited\n")
        return observe(s)

    a = go(rebuild.Ports(observe=observe_with_edit), hint_of(root), FakeJob())
    assert a.outcome == "moved"
    assert a.published is None
    assert not (mirror(cache) / pb.CURRENT).exists()


# -- S0088: BL-0212 (the stamp and the fresh shortcut) and §7 before a fresh answer ------------------------------------


def test_a_glob_the_hint_names_and_the_stamp_lacks_is_not_fresh(root: Path, cache: Path) -> None:
    """BL-0212 V1 (the S0087 final verifier's repro): a glob `include` an outside edit added before the queued rebuild
    is not in that rebuild's stamp (its hint did not name it); once a file matches it, a later rebuild whose hint names
    the glob must not take the generation for fresh: it loads, and the new generation shows the file."""
    hint = hint_of(root)  # the save's plan: no glob
    sub = root.parent / "extra"
    sub.mkdir()
    root.write_bytes(MAIN + b'include "extra/*.bean"\n')  # an outside edit before the queued rebuild
    assert go(rebuild.Ports(), hint, FakeJob()).outcome == "loaded"
    (sub / "a.bean").write_bytes(b"2026-03-01 open Assets:Bank\n")
    b = go(rebuild.Ports(), hint_of(root), FakeJob())  # the next save's plan names the glob and its file
    assert b.outcome == "loaded"
    assert isinstance(b.published, pb.Published)
    got = pb.read_published(for_this_os(), str(cache), KEY, SqliteStore())
    assert got is not None
    assert str(sub / "a.bean") in dict(decode(got.meta.fingerprint.encode("ascii")).files)


def test_a_hint_naming_only_what_the_stamp_names_still_finds_it_fresh(root: Path) -> None:
    """@regression-guard (it held on 5893da6f): the other side of V1, the condition costs nothing when the hint is
    within the stamp."""
    assert go(rebuild.Ports(), hint_of(root), FakeJob()).outcome == "loaded"
    assert go(rebuild.Ports(), hint_of(root), FakeJob()).outcome == "fresh"


def test_a_glob_of_the_hint_that_matches_nothing_stays_in_the_stamp(root: Path, cache: Path) -> None:
    """@regression-guard (it held on 5893da6f; it kills mutant `if read & set(matches)`). BL-0212 V2 (the clause "or
    that match nothing" of `rebuild._read_by`): such a glob may still be in the ledger,
    so a file appearing under it later makes the generation not fresh."""
    (root.parent / "extra").mkdir()
    root.write_bytes(MAIN + b'include "extra/*.bean"\n')
    assert go(rebuild.Ports(), hint_of(root), FakeJob()).outcome == "loaded"
    got = pb.read_published(for_this_os(), str(cache), KEY, SqliteStore())
    assert got is not None

    def ours(r: str) -> bool:
        return pb.same_ledger(for_this_os(), r, str(root))

    def obs(fp: Fingerprint) -> Fingerprint:
        return observe(fp.ledger_set)

    assert pb.fresh_on_disk(got.meta, obs, ours)
    (root.parent / "extra" / "a.bean").write_bytes(b"2026-01-01 open Assets:A\n")
    assert not pb.fresh_on_disk(got.meta, obs, ours)


def test_a_file_the_ledger_no_longer_reads_changing_during_the_build_does_not_stop_the_publish(
    root: Path, cache: Path
) -> None:
    """@regression-guard (it held on 5893da6f). BL-0212 V3: the re-check before the swap observes the stamped set
    (what the load read), not the hint: a file the hint names that the ledger dropped may change during the build, and
    the generation is still published."""
    hint = hint_of(root)  # names inc.bean
    root.write_bytes(b'option "operating_currency" "USD"\n2026-01-01 open Assets:Cash\n')  # a save dropped the include
    inc = root.parent / "inc.bean"
    a = go(rebuild.Ports(store=EditingStore(lambda: inc.write_bytes(INC + b"; edited\n"))), hint, FakeJob())
    assert a.outcome == "loaded"
    assert isinstance(a.published, pb.Published)
    assert os.readlink(mirror(cache) / pb.CURRENT) == a.published.gen.name


def test_an_open_journal_beside_a_fresh_generation_is_paused_not_fresh(root: Path, cache: Path) -> None:
    """engine-jobs §7: "Before returning a result ... the job checks that this workspace has no `.open` journal", a
    fresh answer included (S0088)."""
    assert go(rebuild.Ports(), hint_of(root), FakeJob()).outcome == "loaded"
    journal = cache.parent / "app" / "local" / KEY / "journal"
    journal.mkdir(parents=True)
    (journal / "s1-j1.open").write_bytes(b"{}")
    a = go(rebuild.Ports(), hint_of(root), FakeJob())
    assert a.outcome == "paused"
    assert a.published is None


def test_a_glob_the_hint_names_that_matches_nothing_and_the_stamp_lacks_is_not_fresh(root: Path, cache: Path) -> None:
    """BL-0212 V1, its glob half (S0088 mutant `within-ignores-globs`): the pattern matches nothing yet, so only the
    pattern itself tells the stamp lacks it; the rebuild loads and the new stamp names it, so a file appearing under
    it later makes the generation not fresh."""
    hint = hint_of(root)  # no glob
    (root.parent / "extra").mkdir()
    root.write_bytes(MAIN + b'include "extra/*.bean"\n')  # an outside edit before the queued rebuild
    assert go(rebuild.Ports(), hint, FakeJob()).outcome == "loaded"  # its stamp lacks the pattern
    b = go(rebuild.Ports(), hint_of(root), FakeJob())  # a hint that names the pattern (still matching nothing)
    assert b.outcome == "loaded"
    got = pb.read_published(for_this_os(), str(cache), KEY, SqliteStore())
    assert got is not None
    stamped = decode(got.meta.fingerprint.encode("ascii"))
    assert any(g.endswith("extra/*.bean") for g, _ in stamped.globs)
