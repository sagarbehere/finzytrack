"""R4 inside the engine (`ledger_engine.ledger.r4`; `design/specs/engine-jobs.md` §6.5, §6.7; ADR-0008 amendments,
ADR-0010 guardrail 3; BL-0118; S0066), on real APFS scratch folders after a real save. Written from the spec's lines:

- step 1 "the disk's fingerprint equals the record's post fingerprint and no `.open` journal exists; otherwise
  `superseded`"; an engine-build change: "R4 runs step 2 with the new build, then step 3's fingerprint check (moved →
  `superseded`) … the record becomes `.done {superseded_build}`";
- step 2 "a full `load_file` of the disk … Beancount's real loader, never the overlay's replica … never
  `loader.load_file` itself, which reads, deletes and writes a pickle cache";
- step 3 "The fingerprint again: moved → `superseded`; digest equal → `pass`";
- step 4 "a fresh engine that runs no user code parses the disk with Beancount's parser and with the overlay's replica
  on the same bytes. Then the fingerprint once more: moved since step 3 → `superseded`, never a false mismatch … both
  parse digests equal each other and the record's → `nondeterministic` … Anything else → `mismatch`: the record becomes
  `.mismatch`";
- "A failure to run … adds one to its `.r4-attempts` count (written and synced). After the third failure of the same
  R4 … the record becomes `.mismatch {could_not_check}` … The gate is lifted only by that explicit action";
- "Every rename of the record … is followed by a `durable_sync` of the journal directory before the outcome is acted
  on or shown".

The world's actors: an outside editor that edits, edits and reverts (A-B-A) or truncates and then writes during the
load and during the confirm; an `.open` journal of the workspace; a record matched by lineage in another key folder.
The renames' syncs under power loss are `test_r4_power_loss` (over the fake file system) below.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from ledger_engine.fs.darwin import DarwinLegacyLocks, DarwinLocks, for_this_os
from ledger_engine.fs.port import DirHandle, FileSystem, Resolved
from ledger_engine.ledger import overlay, r4
from ledger_engine.ledger import save as sv
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core.journal import Record
from ledger_engine.ledger.core.ledger_set import decode, digest
from ledger_engine.ledger.policy import DesktopPolicy
from ledger_engine.ledger.writer import Change, sha256

pytestmark = [
    pytest.mark.inv("INV-01"),
    pytest.mark.inv("INV-22"),
    pytest.mark.skipif(sys.platform != "darwin", reason="the write path's OS parts are macOS-only until BL-0087"),
]

POLICY = DesktopPolicy()
BUILD = "test build"
MAIN = b'option "operating_currency" "USD"\ninclude "inc.bean"\n2026-01-01 open Assets:Cash\n'
INC = b"2026-01-01 open Expenses:Food\n"
TXN = b'2026-01-02 * "Lunch"\n  Expenses:Food  5 USD\n  Assets:Cash\n'
EDIT = b"2026-01-01 open Expenses:Food\n2026-01-01 open Expenses:Rent\n"
KEY = "k1"
LINEAGE = "lin1"
NAME = "s1-j7"
OTHER = "sha256:" + "0" * 64


@dataclass
class Job:
    events: list[str] = field(default_factory=list[str])

    def progress(self, phase: str) -> None:
        self.events.append(phase)

    def gate(self) -> None:
        self.events.append("gate")

    def committed(self) -> None:
        self.events.append("committed")


@dataclass
class World:
    tmp: Path
    root: Path
    main: Path
    inc: Path
    ald: Path

    def journal(self, key: str = KEY) -> Path:
        return self.ald / "local" / key / "journal"

    def states(self, key: str = KEY) -> list[str]:
        return sorted(os.listdir(self.journal(key)))

    def record(self, state: str = "closed", key: str = KEY) -> Record:
        rec = journal.decode((self.journal(key) / f"{NAME}.{state}").read_bytes())
        assert isinstance(rec, Record)
        return rec


@pytest.fixture
def w(tmp_path: Path) -> World:
    root = tmp_path / "L"
    root.mkdir()
    (root / "main.bean").write_bytes(MAIN)
    (root / "inc.bean").write_bytes(INC)
    ald = tmp_path / "app"
    (ald / "locks").mkdir(parents=True)
    return World(tmp_path, root, root / "main.bean", root / "inc.bean", ald)


def save(w: World, changes: tuple[Change, ...] | None = None, *, load: sv.Load | None = None) -> Record:
    """A real save of one appended transaction (or `changes`), leaving its `.closed` record."""
    loaded = overlay.load(str(w.main), {}, POLICY)
    assert isinstance(loaded, overlay.Loaded)
    fs = for_this_os()
    plan = sv.SavePlan(
        session="s1",
        job="j7",
        workspace_key=KEY,
        lineage=LINEAGE,
        engine_build=BUILD,
        root=str(w.main),
        changes=changes or (Change(str(w.main), sha256(MAIN), MAIN + TXN),),
        fingerprint=loaded.fingerprint,
        mirror=digest(loaded.fingerprint),
        legacy_locks=(),
        app_local_data=str(w.ald),
    )
    ports = sv.Ports(fs, DarwinLocks(str(w.ald / "locks")), DarwinLegacyLocks(), load or sv.desktop_load(POLICY, fs))
    out = sv.run(plan, ports, Job())
    assert isinstance(out, sv.Saved), out
    return w.record()


def fixed(value: str) -> Callable[[str], str]:
    def give(root: str) -> str:
        return value

    return give


def logged(log: list[str], fn: Callable[[str], str]) -> Callable[[str], str]:
    def call(root: str) -> str:
        log.append(root)
        return fn(root)

    return call


def refuses(root: str) -> overlay.LoadRefused:
    return overlay.LoadRefused("encrypted", root)


def ports(**kw: object) -> r4.Ports:
    return replace(r4.desktop(for_this_os(), BUILD, POLICY), **kw)


def check(w: World, p: r4.Ports | None = None, key: str = KEY) -> r4.Answer:
    return r4.check(p or ports(), str(w.ald), key, NAME, Job())


def confirm(w: World, p: r4.Ports | None = None, key: str = KEY) -> r4.Answer:
    return r4.confirm(p or ports(), str(w.ald), key, NAME, Job())


def around(real: Callable[[str], object], before: Callable[[], object], after: Callable[[], object] | None = None):
    def wrapped(root: str) -> object:
        before()
        try:
            return real(root)
        finally:
            if after is not None:
                after()

    return wrapped


# -- the record a save leaves ------------------------------------------------------------------------------------


def test_the_closed_record_carries_the_post_fingerprint_and_the_digests_r4_computes(w: World) -> None:
    rec = save(w)
    fp = decode(rec.post_fingerprint.encode("ascii"))
    assert dict(fp.files)[str(w.main)] == overlay.fp.content(MAIN + TXN)  # the post state, not the plan's
    assert rec.adopted_digest == ports().load(str(w.main))  # Beancount's real loader on the disk after the save
    assert rec.parse_digest == ports().parse_beancount(str(w.main))
    assert rec.adopted_digest != rec.parse_digest


# -- the outcomes ---------------------------------------------------------------------------------------------------


def test_a_saved_ledger_passes_and_its_record_is_done(w: World) -> None:
    save(w)
    out = check(w)
    assert (out.outcome, out.loaded) == ("pass", True)
    assert w.states() == [f"{NAME}.done"]
    assert out.record == str(w.journal() / f"{NAME}.done")
    assert out.backup == str(w.ald / "local" / KEY / "backups" / NAME)


def test_a_save_that_creates_a_file_passes(w: World) -> None:
    """§14: "a file-creating save ends `pass` (its created file's `path_key` is the same before and after)"."""
    new = w.root / "Prices.bean"
    main = MAIN + b'include "Prices.bean"\n'
    save(w, (Change(str(w.main), sha256(MAIN), main), Change(str(new), None, b"2026-01-02 price ABC 1 USD\n")))
    assert check(w).outcome == "pass"


def test_no_pickle_cache_is_read_or_written(w: World) -> None:
    """BL-0118, L1-09: R4's load is Beancount's own loader without `load_file`'s cache."""
    save(w)
    planted = w.root / ".main.bean.picklecache"
    before = sorted(os.listdir(w.root))
    assert check(w).outcome == "pass"
    assert sorted(os.listdir(w.root)) == before
    assert not planted.exists()


def test_an_outside_edit_before_the_check_supersedes_it_without_loading(w: World) -> None:
    save(w)
    w.inc.write_bytes(EDIT)
    loads: list[str] = []
    out = check(w, ports(load=logged(loads, fixed(OTHER))))
    assert (out.outcome, out.loaded, loads) == ("superseded", False, [])
    assert w.states() == [f"{NAME}.done"]


@pytest.mark.parametrize("edit", [EDIT, b""], ids=["edit", "truncate"])
def test_an_outside_edit_during_the_load_supersedes_it(w: World, edit: bytes) -> None:
    save(w)
    p = ports()
    out = check(w, ports(load=around(p.load, lambda: None, lambda: w.inc.write_bytes(edit))))
    assert (out.outcome, out.loaded) == ("superseded", True)
    assert w.states() == [f"{NAME}.done"]


@pytest.mark.parametrize("edit", [EDIT, b""], ids=["edit_revert", "truncate_then_write"])
def test_an_edit_reverted_during_the_load_is_never_a_mismatch(w: World, edit: bytes) -> None:
    """The load read other bytes (A-B-A, or `script > file` caught empty): its digest differs, but the confirm reads
    the saved bytes again: `nondeterministic`, saving goes on; never `mismatch`."""
    save(w)
    p = ports()
    out = check(w, ports(load=around(p.load, lambda: w.inc.write_bytes(edit), lambda: w.inc.write_bytes(INC))))
    assert out.outcome == "differs"
    assert w.states() == [f"{NAME}.closed"]
    assert confirm(w).outcome == "nondeterministic"
    assert w.states() == [f"{NAME}.done"]


def test_an_open_journal_of_the_workspace_supersedes_it(w: World) -> None:
    save(w)
    (w.journal() / "s2-j1.open").write_bytes(b"anything")
    assert check(w).outcome == "superseded"


def test_an_open_journal_matched_by_lineage_in_another_key_supersedes_it(w: World) -> None:
    rec = save(w)
    other = w.journal("k2")
    other.mkdir(parents=True)
    # another save's journal, left `.open` under another key by a forgotten and re-added folder: same lineage
    (other / f"{NAME}.open").write_bytes(journal.encode(replace(rec, workspace_key="k2")))
    assert check(w).outcome == "superseded"


def test_a_new_engine_build_is_superseded_after_the_real_load(w: World) -> None:
    save(w)
    loads: list[str] = []
    real = ports().load
    out = check(w, ports(build="another build", load=logged(loads, real)))
    assert (out.outcome, out.loaded, loads) == ("superseded_build", True, [str(w.main)])
    assert w.states() == [f"{NAME}.done"]


def test_a_record_without_digests_is_treated_as_a_build_change(w: World) -> None:
    def plain(root: str, candidates: object) -> overlay.LoadResult:
        return overlay.load(root, candidates, POLICY)  # pyright: ignore[reportArgumentType] (no parse digest)

    rec = save(w, load=plain)
    assert rec.adopted_digest == rec.parse_digest == sv.NOT_COMPUTED
    assert check(w).outcome == "superseded_build"


def test_a_different_digest_is_confirmed_in_a_parse_only_step_and_is_a_mismatch_when_the_parsers_differ(
    w: World,
) -> None:
    save(w)
    out = check(w, ports(load=fixed(OTHER)))
    assert (out.outcome, out.loaded) == ("differs", True)
    assert w.states() == [f"{NAME}.closed"]
    loads: list[str] = []
    out = confirm(w, ports(parse_beancount=fixed(OTHER), load=logged(loads, fixed(OTHER))))
    assert (out.outcome, out.loaded, loads) == ("mismatch", False, [])  # the confirm runs no load (no user code)
    assert w.states() == [f"{NAME}.mismatch"]
    assert out.record == str(w.journal() / f"{NAME}.mismatch")


def test_a_replica_that_differs_from_the_record_is_a_mismatch(w: World) -> None:
    save(w)
    real = ports().parse_replica

    def drifted(root: str) -> overlay.Parsed | overlay.LoadRefused:
        out = real(root)
        assert isinstance(out, overlay.Parsed)
        return overlay.Parsed(OTHER, out.files)

    assert confirm(w, ports(parse_replica=drifted)).outcome == "mismatch"


def test_equal_parses_with_a_different_load_are_nondeterministic(w: World) -> None:
    save(w)
    assert confirm(w).outcome == "nondeterministic"
    assert w.states() == [f"{NAME}.done"]


@pytest.mark.parametrize("edit", [EDIT, b""], ids=["edit", "truncate"])
def test_an_edit_during_the_confirm_is_superseded_never_a_mismatch(w: World, edit: bytes) -> None:
    """O1: "moved since step 3 → `superseded`, never a false mismatch from an edit made during the confirm"."""
    save(w)
    p = ports()
    out = confirm(w, ports(parse_beancount=around(p.parse_beancount, lambda: w.inc.write_bytes(edit))))
    assert out.outcome == "superseded"
    assert w.states() == [f"{NAME}.done"]


def test_an_edit_after_both_parses_of_the_confirm_is_superseded(w: World) -> None:
    """O1: the fingerprint once more, after the parses: an edit neither parser read is still `superseded`."""
    save(w)
    p = ports()
    out = confirm(w, ports(parse_replica=around(p.parse_replica, lambda: None, lambda: w.inc.write_bytes(EDIT))))
    assert out.outcome == "superseded"
    assert w.states() == [f"{NAME}.done"]


def test_an_open_journal_that_appears_during_the_confirm_supersedes_it(w: World) -> None:
    save(w)
    p = ports()
    out = confirm(
        w, ports(parse_replica=around(p.parse_replica, lambda: (w.journal() / "s2-j1.open").write_bytes(b"x")))
    )
    assert out.outcome == "superseded"


def test_parses_that_agree_with_each_other_but_not_with_the_saves_prediction_are_a_mismatch(w: World) -> None:
    """§6.7 step 4: "both parse digests equal each other and the record's → `nondeterministic` … Anything else →
    `mismatch`": the replica that parsed the candidate at the save is the one being checked."""
    rec = save(w)
    (w.journal() / f"{NAME}.closed").write_bytes(journal.encode(replace(rec, parse_digest=OTHER)))
    assert confirm(w).outcome == "mismatch"
    assert w.states() == [f"{NAME}.mismatch"]


def test_an_edit_the_replica_read_and_was_reverted_during_the_confirm_is_superseded(w: World) -> None:
    """The replica parsed other bytes than the record's (it says which it read): never a mismatch."""
    save(w)
    p = ports()
    out = confirm(
        w,
        ports(parse_replica=around(p.parse_replica, lambda: w.inc.write_bytes(EDIT), lambda: w.inc.write_bytes(INC))),
    )
    assert out.outcome == "superseded"


def test_a_replica_that_refuses_is_a_failure_to_run(w: World) -> None:
    save(w)
    with pytest.raises(r4.R4Error):
        confirm(w, ports(parse_replica=refuses))
    assert w.states() == [f"{NAME}.closed"]


# -- what is not `.closed` is never touched --------------------------------------------------------------------


@pytest.mark.parametrize("state", ["mismatch", "done", "open"])
def test_r4_never_touches_a_record_that_is_not_closed(w: World, state: str) -> None:
    save(w)
    (w.journal() / f"{NAME}.closed").rename(w.journal() / f"{NAME}.{state}")
    for op in (check, confirm):
        assert op(w).outcome == "gone"
    assert r4.attempt(for_this_os(), str(w.ald), KEY, NAME).outcome == "gone"
    assert w.states() == [f"{NAME}.{state}"]


def test_an_unreadable_record_is_reported_and_left(w: World) -> None:
    save(w)
    (w.journal() / f"{NAME}.closed").write_bytes(b"garbage")
    assert check(w).outcome == "unreadable"
    assert confirm(w).outcome == "unreadable"
    assert w.states() == [f"{NAME}.closed"]


def test_a_record_with_an_old_style_fingerprint_is_unreadable(w: World) -> None:
    rec = save(w)
    old = replace(rec, post_fingerprint=digest(decode(rec.post_fingerprint.encode("ascii"))))
    (w.journal() / f"{NAME}.closed").write_bytes(journal.encode(old))
    assert check(w).outcome == "unreadable"


def test_a_record_renamed_by_hand_is_unreadable(w: World) -> None:
    save(w)
    (w.journal() / f"{NAME}.closed").rename(w.journal() / "s9-j9.closed")
    assert r4.check(ports(), str(w.ald), KEY, "s9-j9", Job()).outcome == "unreadable"


# -- attempts and Resume saving ---------------------------------------------------------------------------------


def test_the_third_failure_to_run_makes_the_record_could_not_check(w: World) -> None:
    save(w)
    fs = for_this_os()
    outs = [r4.attempt(fs, str(w.ald), KEY, NAME).outcome for _ in range(3)]
    assert outs == ["counted", "counted", "could_not_check"]
    assert w.states() == [f"{NAME}.mismatch", *(f"{NAME}.r4-attempt-{n}" for n in (1, 2, 3))]
    assert sv.paused_by(fs, str(w.journal() / f"{NAME}.mismatch")) == (
        str(w.ald / "local" / KEY / "backups" / NAME),
        True,
    )


def test_a_mismatch_found_by_the_confirm_is_not_could_not_check(w: World) -> None:
    save(w)
    fs = for_this_os()
    r4.attempt(fs, str(w.ald), KEY, NAME)
    r4.attempt(fs, str(w.ald), KEY, NAME)
    assert confirm(w, ports(parse_beancount=fixed(OTHER))).outcome == "mismatch"
    assert sv.paused_by(fs, str(w.journal() / f"{NAME}.mismatch"))[1] is False


def test_a_count_left_by_a_cut_try_counts_once(w: World) -> None:
    save(w)
    (w.journal() / f"{NAME}.r4-attempt-1").write_bytes(b"")
    assert r4.attempt(for_this_os(), str(w.ald), KEY, NAME).outcome == "counted"
    assert (w.journal() / f"{NAME}.r4-attempt-2").exists()


def test_resume_saving_lifts_only_a_mismatch(w: World) -> None:
    save(w)
    fs = for_this_os()
    assert r4.resume(fs, str(w.ald), KEY, NAME).outcome == "gone"  # a pending check is never lifted by Resume
    assert w.states() == [f"{NAME}.closed"]
    for _ in range(3):
        r4.attempt(fs, str(w.ald), KEY, NAME)
    out = r4.resume(fs, str(w.ald), KEY, NAME)
    assert (out.outcome, out.record) == ("done", str(w.journal() / f"{NAME}.done"))
    assert f"{NAME}.done" in w.states()
    assert r4.resume(fs, str(w.ald), KEY, NAME).outcome == "gone"


def test_the_save_is_refused_check_failed_with_the_backup_while_a_mismatch_stands(w: World) -> None:
    save(w)
    assert confirm(w, ports(parse_beancount=fixed(OTHER))).outcome == "mismatch"
    fs = for_this_os()
    loaded = overlay.load(str(w.main), {}, POLICY)
    assert isinstance(loaded, overlay.Loaded)
    plan = sv.SavePlan(
        session="s1",
        job="j8",
        workspace_key=KEY,
        lineage=LINEAGE,
        engine_build=BUILD,
        root=str(w.main),
        changes=(Change(str(w.main), sha256(MAIN + TXN), MAIN + TXN + TXN),),
        fingerprint=loaded.fingerprint,
        mirror=digest(loaded.fingerprint),
        legacy_locks=(),
        app_local_data=str(w.ald),
    )
    ports_ = sv.Ports(fs, DarwinLocks(str(w.ald / "locks")), DarwinLegacyLocks(), sv.desktop_load(POLICY, fs))
    out = sv.run(plan, ports_, Job())
    assert out == sv.Refused(
        "check_failed",
        True,
        str(w.journal() / f"{NAME}.mismatch"),
        str(w.ald / "local" / KEY / "backups" / NAME),
        False,
    )


# -- every rename is synced before the answer (over a spy of the real port) --------------------------------------


@dataclass
class SpyDir:
    inner: DirHandle
    log: list[str]

    def __getattr__(self, name: str) -> object:
        return getattr(self.inner, name)

    def rename_excl(self, src: str, dst: str) -> None:
        self.inner.rename_excl(src, dst)
        self.log.append(f"rename {src} {dst}")

    def create(self, name: str, data: bytes, mode: int, like: str | None = None) -> None:
        self.inner.create(name, data, mode, like)
        self.log.append(f"create {name}")

    def sync(self) -> None:
        self.inner.sync()
        self.log.append("sync")

    def __enter__(self) -> SpyDir:
        return self

    def __exit__(self, *exc: object) -> None:
        self.inner.close()


@dataclass
class SpyFs:
    inner: FileSystem
    log: list[str] = field(default_factory=list[str])

    def resolve(self, path: str) -> Resolved:
        return self.inner.resolve(path)

    def open_dir(self, path: str) -> DirHandle:
        return SpyDir(self.inner.open_dir(path), self.log)  # pyright: ignore[reportReturnType]


def test_every_rename_and_count_is_followed_by_a_sync_of_the_journal_folder(w: World) -> None:
    save(w)
    fs = SpyFs(for_this_os())
    assert r4.attempt(fs, str(w.ald), KEY, NAME).outcome == "counted"
    assert confirm(w, ports(fs=fs, parse_beancount=fixed(OTHER))).outcome == "mismatch"
    assert r4.resume(fs, str(w.ald), KEY, NAME).outcome == "done"
    assert fs.log == [
        f"create {NAME}.r4-attempt-1",
        "sync",
        f"rename {NAME}.closed {NAME}.mismatch",
        "sync",
        f"rename {NAME}.mismatch {NAME}.done",
        "sync",
    ]
