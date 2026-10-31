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

import beancount.loader as beancount_loader
import beancount.utils.encryption as beancount_encryption
import pytest

from ledger_engine.fs.darwin import DarwinLegacyLocks, DarwinLocks, for_this_os
from ledger_engine.fs.port import DirHandle, FileSystem, Resolved
from ledger_engine.ledger import beancount_port, overlay, r4
from ledger_engine.ledger import save as sv
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core.journal import Record
from ledger_engine.ledger.core.ledger_set import Fingerprint, decode, digest
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


def another_save(rec: Record, session: str, job: str, **kw: str) -> bytes:
    """`rec` as another save's record (its temp names follow its name)."""
    out = replace(rec, session=session, job=job, **kw)
    out = replace(out, backup_dir=f"{rec.backup_dir.rpartition('/')[0]}/{out.name}")
    files = tuple(replace(f, temp=journal.temp_name(f.canonical_path.rpartition("/")[2], out.name)) for f in rec.files)
    return journal.encode(replace(out, files=files))


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


def test_no_pickle_cache_is_read_or_written(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """@regression-guard (S0066 verifier r1 F3: the code was right, its test vacuous; this one kills the `loadfile`
    mutant). BL-0118, L1-09: R4's load is Beancount's own loader without `load_file`'s cache. A cache newer than the
    ledger is planted and Beancount is made to write one after any load (its threshold is a second): `load_file` would
    read the planted bytes (and fail) or replace them."""
    save(w)
    monkeypatch.setattr(beancount_loader, "PICKLE_CACHE_THRESHOLD", -1.0)
    planted = w.root / ".main.bean.picklecache"
    planted.write_bytes(b"not a pickle")
    os.utime(planted, (2_000_000_000, 2_000_000_000))
    assert check(w).outcome == "pass"
    assert planted.read_bytes() == b"not a pickle"
    assert sorted(os.listdir(w.root)) == [".main.bean.picklecache", "inc.bean", "main.bean"]


DOCS_MAIN = MAIN.replace(b'include "inc.bean"\n', b'include "inc.bean"\noption "documents" "."\n')
DATED = "2026-01-02.prices.bean"  # a dated name: the kind Beancount's documents plugin reads


@pytest.mark.parametrize("created", [DATED, f"new/{DATED}"], ids=["file", "file_in_a_new_folder"])
def test_a_file_created_under_a_documents_root_is_in_the_post_fingerprint(w: World, created: str) -> None:
    """Red team r1 F1: the documents listing the save's load read is from before the commit; the closed record's post
    fingerprint has the created file in it (`ledger_set.as_after`), so R4 compares: `pass`, or `differs` when
    Beancount's load disagrees (before: `superseded`, never compared)."""
    w.main.write_bytes(DOCS_MAIN)
    new = w.root / created
    main = DOCS_MAIN + f'include "{created}"\n'.encode()
    changes = (Change(str(w.main), sha256(DOCS_MAIN), main), Change(str(new), None, b"2026-01-02 price ABC 1 USD\n"))
    rec = save(w, changes)
    fp = decode(rec.post_fingerprint.encode("ascii"))
    assert (created, True) in (dict(fp.documents)[str(w.root)] or ())
    assert check(w, ports(load=fixed(OTHER))).outcome == "differs"
    assert check(w).outcome == "pass"


def test_a_change_beancount_never_reads_in_a_documents_folder_does_not_skip_the_check(w: World) -> None:
    """Red team r1 F2: Finder's `.DS_Store` (an undated name: Beancount's documents plugin skips it) leaves R4 to
    compare; a dated name, which Beancount turns into an entry, still supersedes it."""
    (w.root / "docs").mkdir()
    m = MAIN.replace(b'include "inc.bean"\n', b'include "inc.bean"\noption "documents" "docs"\n')
    w.main.write_bytes(m)
    save(w, (Change(str(w.main), sha256(m), m + TXN),))
    (w.root / "docs" / ".DS_Store").write_bytes(b"\0\0\0\1Bud1")
    (w.root / "docs" / "notes.txt").write_bytes(b"x")
    assert check(w, ports(load=fixed(OTHER))).outcome == "differs"
    (w.root / "docs" / "2026-01-03.receipt.pdf").write_bytes(b"%PDF")
    assert check(w, ports(load=fixed(OTHER))).outcome == "superseded"


def test_beancount_reads_keeps_only_dated_names_of_each_listing() -> None:
    fp = Fingerprint.of(
        "/L/main.bean",
        {},
        {},
        {"/L/docs": {".DS_Store": True, "Assets/Cash/2026-01-02.x.pdf": True, "Assets/a.txt": False}, "/L/no": None},
        {},
    )
    assert r4.beancount_reads(fp).documents == (("/L/docs", (("Assets/Cash/2026-01-02.x.pdf", True),)), ("/L/no", None))


READSELF = b"""
import hashlib, datetime
from beancount.core import data
__plugins__ = ["readself"]
def readself(entries, options_map):
    h = hashlib.sha256(open(options_map["filename"], "rb").read()).hexdigest()
    meta = data.new_metadata("<readself>", 0)
    return entries + [data.Custom(meta, datetime.date(2026, 1, 1), "filehash", [(h, str)])], []
"""

VARY = b"""import uuid
from beancount.core import data
__plugins__ = ["vary"]
def vary(entries, options_map):
    out = []
    for e in entries:
        if isinstance(e, data.Transaction):
            e = e._replace(meta={**e.meta, "run": str(uuid.uuid4())})
        out.append(e)
    return out, []
"""


@pytest.mark.parametrize(
    ("module", "source", "outcome", "state"),
    [
        ("r4_readself_plugin", READSELF, "nondeterministic", "done"),
        ("r4_vary_plugin", VARY, "nondeterministic", "done"),
    ],
    ids=["reads_a_ledger_file_itself", "varies_run_to_run"],
)
def test_a_plugin_whose_result_differs_from_the_prediction_end_to_end(
    w: World, module: str, source: bytes, outcome: str, state: str
) -> None:
    """Verifier r1 F10 and red team r1 F3, end to end with real plugins: a plugin that varies run to run is
    `nondeterministic`; so is one that reads the root file from disk itself (it sees the pre bytes at the save's load
    and the post bytes in R4's): a plugin difference shows a notice and does not pause saving (owner answer 4 of
    2026-10-26: "only a real loader difference pauses; plugin differences show a notice"; spec §15)."""
    (w.root / f"{module}.py").write_bytes(source)
    main = f'option "insert_pythonpath" "TRUE"\nplugin "{module}"\n'.encode() + MAIN
    w.main.write_bytes(main)
    try:
        save(w, (Change(str(w.main), sha256(main), main + TXN),))
        assert check(w).outcome == "differs"
        assert confirm(w).outcome == outcome
        assert w.states() == [f"{NAME}.{state}"]
    finally:
        sys.modules.pop(module, None)
        sys.path[:] = [p for p in sys.path if p != str(w.root)]


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
    """@regression-guard (S0066 round 1: now a readable record; an unreadable one is the next test's)."""
    rec = save(w)
    (w.journal() / "s2-j1.open").write_bytes(another_save(rec, "s2", "j1"))
    assert check(w).outcome == "superseded"


@pytest.mark.parametrize("key", [KEY, "k2"], ids=["own_key", "another_key"])
def test_an_open_journal_that_cannot_be_read_is_a_failure_to_run_never_a_lift(w: World, key: str) -> None:
    """Verifier r1 F1 = red team r1 F5: an `.open` that cannot be read (garbage, a newer app's format) cannot be
    matched by key or lineage; the save's `records` counts it as this workspace's to refuse, but in R4 that would lift
    the check. It is a failure to run (counted by the sidecar); the record stays `.closed`."""
    save(w)
    folder = w.journal(key)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "s9-j9.open").write_bytes(b'{"format":"finzytrack-save-journal","v":3}\n')
    with pytest.raises(r4.R4Error):
        check(w, ports(load=fixed(OTHER)))
    with pytest.raises(r4.R4Error):
        confirm(w, ports(parse_beancount=fixed(OTHER)))
    assert f"{NAME}.closed" in w.states()
    assert not any(n.startswith(NAME) and not n.endswith(".closed") for n in w.states())


def test_another_workspaces_readable_open_journal_does_not_lift_the_check(w: World) -> None:
    """@regression-guard (S0066 round 1: R4's own `.open` match keeps what `records` did for a readable record)."""
    rec = save(w)
    other = w.journal("k2")
    other.mkdir(parents=True)
    (other / "s9-j9.open").write_bytes(another_save(rec, "s9", "j9", workspace_key="k2", lineage="lin2"))
    assert check(w, ports(load=fixed(OTHER))).outcome == "differs"


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
    out = confirm(w)
    assert (out.outcome, out.loaded) == ("nondeterministic", False)
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
    rec = w.record()
    opened = another_save(rec, "s2", "j1")
    out = confirm(
        w, ports(parse_replica=around(p.parse_replica, lambda: (w.journal() / "s2-j1.open").write_bytes(opened)))
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


def test_a_truncate_and_rewrite_during_beancounts_parse_is_never_a_mismatch(w: World) -> None:
    """Red team r1 F4: `script > file` (truncated, then the same bytes written back) while Beancount's parser reads,
    done before the replica reads: nothing records what Beancount read and the fingerprint after is unchanged.
    Beancount parses once more and answers differently: `superseded` (before: a false `mismatch`)."""
    save(w)
    real = ports().parse_beancount
    calls: list[str] = []

    def once(root: str) -> str:
        calls.append(root)
        if len(calls) > 1:
            return real(root)
        w.inc.write_bytes(b"")
        try:
            return real(root)
        finally:
            w.inc.write_bytes(INC)

    out = confirm(w, ports(parse_beancount=once))
    assert (out.outcome, len(calls)) == ("superseded", 2)
    assert w.states() == [f"{NAME}.done"]


def test_a_replica_that_refuses_an_edit_made_during_the_confirm_is_superseded(w: World) -> None:
    """Verifier r1 O1: the replica refuses what an outside edit made of the disk: the disk moved (`superseded`), not a
    failure to run."""
    save(w)

    def edited(root: str) -> overlay.LoadRefused:
        w.inc.write_bytes(EDIT)
        return refuses(root)

    assert confirm(w, ports(parse_replica=edited)).outcome == "superseded"


def test_r4_never_decrypts_an_encrypted_file_added_during_the_check(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifier r1 F2: an encrypted include an outside program adds after step 1 is met by Beancount's load, which
    would decrypt it (`gpg --decrypt`); R4's loads refuse to (ADR-0059). The disk moved: `superseded`."""
    save(w)
    calls: list[str] = []

    def spy(name: str) -> str:
        calls.append(name)
        return ""

    monkeypatch.setattr(beancount_encryption, "read_encrypted_file", spy)
    first = [True]

    def observe(fp: Fingerprint) -> Fingerprint:
        out = r4._observe(fp)  # pyright: ignore[reportPrivateUsage]
        if first[0]:
            first[0] = False
            (w.root / "secret.gpg").write_bytes(b"-----BEGIN PGP MESSAGE-----\n")
            w.main.write_bytes(w.main.read_bytes() + b'include "secret.gpg"\n')
        return out

    assert check(w, ports(observe=observe)).outcome == "superseded"
    assert calls == []
    assert beancount_encryption.read_encrypted_file is spy  # put back after the load


@pytest.mark.parametrize("where", ["load", "parse"])
def test_the_reconciliation_loads_refuse_an_encrypted_file(w: World, where: str) -> None:
    (w.root / "secret.gpg").write_bytes(b"-----BEGIN PGP MESSAGE-----\n")
    w.main.write_bytes(MAIN + b'include "secret.gpg"\n')
    fn = beancount_port.reconcile_load if where == "load" else beancount_port.reconcile_parse
    with pytest.raises(beancount_port.EncryptedFileMet) as e:
        fn(str(w.main))
    assert e.value.path == str(w.root / "secret.gpg")


def test_an_encrypted_file_met_on_an_unchanged_disk_is_a_failure_to_run(w: World) -> None:
    save(w)

    def met(root: str) -> str:
        raise beancount_port.EncryptedFileMet(root)

    with pytest.raises(r4.R4Error):
        check(w, ports(load=met))
    with pytest.raises(r4.R4Error):
        confirm(w, ports(parse_beancount=met))
    assert w.states() == [f"{NAME}.closed"]


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


def test_a_version_1_record_is_never_a_pass(w: World) -> None:
    """Verifier r1 F10: a journal format 1 record (its post fingerprint a digest) is `unreadable`, left `.closed`;
    three counted failures make it "could not check"."""
    save(w)
    f = w.journal() / f"{NAME}.closed"
    data = f.read_bytes()
    assert b'"v":2' in data
    f.write_bytes(data.replace(b'"v":2', b'"v":1'))
    assert check(w).outcome == "unreadable"
    assert confirm(w).outcome == "unreadable"
    assert w.states() == [f"{NAME}.closed"]
    outs = [r4.attempt(for_this_os(), str(w.ald), KEY, NAME).outcome for _ in range(3)]
    assert outs == ["counted", "counted", "could_not_check"]
    assert f"{NAME}.mismatch" in w.states()


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
