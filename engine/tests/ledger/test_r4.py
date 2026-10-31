"""R4 inside the engine (`ledger_engine.ledger.r4`; `design/specs/engine-jobs.md` §6.7; ADR-0067 decision 3, ADR-0010
guardrail 3; BL-0118; S0066, S0068), on real APFS scratch folders after a real save. Written from the spec's lines:

- "the disk's fingerprint equals the record's post fingerprint; otherwise `superseded`" (before and after the load);
- "a full load of the disk … Beancount's real loader, never the overlay's replica … never `loader.load_file` itself,
  which reads, deletes and writes a pickle cache";
- "digest equal → `pass`; different → `differs`: logged and a notice; saving goes on";
- "an engine build other than the record's → `superseded`";
- "R4 reads the closed record and writes nothing" (ADR-0067: `.closed` is terminal; no `.done`, `.mismatch`, attempt
  count or Resume saving).

The world's actors: an outside editor that edits, edits and reverts (A-B-A) or truncates and then writes during the
load; an `.open` journal of the workspace.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping
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
    ports = sv.Ports(fs, DarwinLocks(str(w.ald / "locks")), DarwinLegacyLocks(), load or sv.desktop_load(POLICY))
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


def ports(**kw: object) -> r4.Ports:
    return replace(r4.desktop(for_this_os(), BUILD), **kw)


def check(w: World, p: r4.Ports | None = None, key: str = KEY) -> r4.Answer:
    return r4.check(p or ports(), str(w.ald), key, NAME, Job())


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


def test_the_closed_record_carries_the_post_fingerprint_and_the_adopted_digest_r4_computes(w: World) -> None:
    """BL-0173 (owner, 2026-10-31: "Drop digest; 2 jobs (Recommended)"): the record carries the adopted digest and no
    parse digest (journal format 3)."""
    rec = save(w)
    fp = decode(rec.post_fingerprint.encode("ascii"))
    assert dict(fp.files)[str(w.main)] == overlay.fp.content(MAIN + TXN)  # the post state, not the plan's
    assert rec.adopted_digest == ports().load(str(w.main))  # Beancount's real loader on the disk after the save
    raw = json.loads((w.journal() / f"{NAME}.closed").read_bytes())
    assert (raw["v"], "parse_digest" in raw) == (3, False)


# -- the outcomes ---------------------------------------------------------------------------------------------------


def test_a_saved_ledger_passes_and_its_record_stays_closed(w: World) -> None:
    """S0068 (ADR-0067): R4 renames nothing; the record stays `.closed` ("saved") after `pass` (before: `.done`)."""
    save(w)
    before = (w.journal() / f"{NAME}.closed").read_bytes()
    out = check(w)
    assert (out.outcome, out.loaded) == ("pass", True)
    assert w.states() == [f"{NAME}.closed"]
    assert (w.journal() / f"{NAME}.closed").read_bytes() == before


def test_a_different_digest_is_differs_at_once_and_the_record_stays_closed(w: World) -> None:
    """@regression-guard (S0068: `check` already answered so). ADR-0067 decision 3: no confirm, no `.mismatch`: a
    difference with the disk unchanged is `differs` (the sidecar logs it and shows the notice); asked again, it compares
    again."""
    save(w)
    out = check(w, ports(load=fixed(OTHER)))
    assert (out.outcome, out.loaded) == ("differs", True)
    assert w.states() == [f"{NAME}.closed"]
    assert check(w).outcome == "pass"


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
PRICES = b"2026-01-02 price ABC 1 USD\n"


def documents_of(w: World) -> list[str]:
    """The files of the `document` entries Beancount's own loader makes of the disk now."""
    entries, _, _ = beancount_port.reconcile_load(str(w.main))
    return [e.filename for e in entries if isinstance(e, beancount_port.Document)]


def save_creating(w: World, main: bytes, include: str, created: Path) -> Record:
    """A save that adds `include "<include>"` to `main` (already on disk) and creates `created` with a price."""
    new_main = main + f'include "{include}"\n'.encode()
    return save(w, (Change(str(w.main), sha256(main), new_main), Change(str(created), None, PRICES)))


@pytest.mark.parametrize(
    ("created", "outcome"),
    [
        (DATED, "pass"),
        (f"new/{DATED}", "pass"),
        (f"Assets/Cash/{DATED}", "differs"),
        (f"Expenses/Food/{DATED}", "differs"),
    ],
    ids=["file", "file_in_a_new_folder", "file_in_an_account_folder", "file_in_a_new_account_folder"],
)
def test_a_file_created_under_a_documents_root_is_compared(w: World, created: str, outcome: str) -> None:
    """Red team r1 F1 (S0066); S0068 (ADR-0067, spec §15): the closed record's post fingerprint has the created file in
    the root's listing (`ledger_set.as_after`), so R4 compares. The save's preview read the documents root before the
    write (S0067's listing swap is gone), so a dated name in an account folder, which Beancount turns into a
    `document` entry after the write, is missing from the prediction: `differs`, the notice; saving goes on."""
    (w.root / "Assets" / "Cash").mkdir(parents=True)
    w.main.write_bytes(DOCS_MAIN)
    rec = save_creating(w, DOCS_MAIN, created, w.root / created)
    fp = decode(rec.post_fingerprint.encode("ascii"))
    assert (created, True) in (dict(fp.documents)[str(w.root)] or ())
    assert (str(w.root / created) in documents_of(w)) is (outcome == "differs")
    assert check(w).outcome == outcome
    assert w.states() == [f"{NAME}.closed"]


def test_a_created_file_a_glob_also_matches_is_superseded_never_a_pass(w: World) -> None:
    """@regression-guard (S0067: unchanged behaviour, pinned). A file the save creates, reached by a plain include,
    that an `include` glob also matches after the write: the prediction parsed it once, Beancount parses it twice (a
    duplicate). The glob's answer moved: R4 is `superseded`, never a `pass` on a prediction that was not compared
    (predicting the glob is BL-0117's)."""
    (w.root / "sub").mkdir()
    (w.root / "sub" / "a.bean").write_bytes(b"2026-01-01 open Assets:Bank\n")
    main = MAIN + b'include "sub/*.bean"\n'
    w.main.write_bytes(main)
    save_creating(w, main, "sub/new.bean", w.root / "sub" / "new.bean")
    assert check(w).outcome == "superseded"


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
    ("module", "source"),
    [("r4_readself_plugin", READSELF), ("r4_vary_plugin", VARY)],
    ids=["reads_a_ledger_file_itself", "varies_run_to_run"],
)
def test_a_plugin_whose_result_differs_from_the_prediction_end_to_end(w: World, module: str, source: bytes) -> None:
    """Verifier r1 F10 and red team r1 F3 (S0066), end to end with real plugins; S0068 (ADR-0067): a plugin that varies
    run to run, or reads the root file from disk itself (the pre bytes at the save's load, the post bytes in R4's), is
    `differs`: the notice, saving goes on; the record stays `.closed`."""
    (w.root / f"{module}.py").write_bytes(source)
    main = f'option "insert_pythonpath" "TRUE"\nplugin "{module}"\n'.encode() + MAIN
    w.main.write_bytes(main)
    try:
        save(w, (Change(str(w.main), sha256(main), main + TXN),))
        assert check(w).outcome == "differs"
        assert w.states() == [f"{NAME}.closed"]
    finally:
        sys.modules.pop(module, None)
        sys.path[:] = [p for p in sys.path if p != str(w.root)]


SIDE = b"""
from beancount.utils import encryption
__plugins__ = ["side"]
def side(entries, options_map):
    encryption.read_encrypted_file(options_map["filename"].rsplit("/", 1)[0] + "/side.gpg")
    return entries, []
"""


def test_a_plugin_that_decrypts_a_side_file_passes_r4(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """BL-0172 (S0066 red team r2): the save's load and R4's both refuse the plugin's decryption (ADR-0059), so they
    agree: `pass`, never decrypted (before S0067: the save's load decrypted, R4 refused; every such save was
    `differs`, then `nondeterministic`)."""
    calls: list[str] = []

    def spy(name: str) -> str:
        calls.append(name)
        return ""

    monkeypatch.setattr(beancount_encryption, "read_encrypted_file", spy)
    module = "r4_side_decrypt_plugin"
    (w.root / f"{module}.py").write_bytes(SIDE)
    (w.root / "side.gpg").write_bytes(b"-----BEGIN PGP MESSAGE-----\n")
    main = f'option "insert_pythonpath" "TRUE"\nplugin "{module}"\n'.encode() + MAIN
    w.main.write_bytes(main)
    try:
        save(w, (Change(str(w.main), sha256(main), main + TXN),))
        assert check(w).outcome == "pass"
        assert calls == []
    finally:
        sys.modules.pop(module, None)
        sys.path[:] = [p for p in sys.path if p != str(w.root)]


CHECKDIRS = b"""
import os
from beancount.core import getters
from beancount.scripts.directories import validate_directory
__plugins__ = ["checkdirs"]
def checkdirs(entries, options_map):
    root = os.path.join(os.path.dirname(options_map["filename"]), "docs")
    return entries, validate_directory(getters.get_accounts(entries), root)
"""

WALKS = b"""
import datetime, os
from beancount.core import account, data
__plugins__ = ["walks"]
def walks(entries, options_map):
    root = os.path.join(os.path.dirname(options_map["filename"]), "docs")
    seen = [f"{name}:{len(dirs)}" for _, name, dirs, _ in account.walk(root, followlinks=FOLLOW)]
    meta = data.new_metadata("<walks>", 0)
    return entries + [data.Note(meta, datetime.date(2026, 1, 1), "Assets:Cash", ",".join(seen), None, None)], []
"""


@pytest.mark.parametrize(
    "case", ["validate_directory_empty_folder", "walk_empty_account_folder", "walk_not_following_links"]
)
def test_a_users_plugin_walking_a_documents_root_sees_the_disk_in_the_prediction(w: World, case: str) -> None:
    """S0067 red team r1 F1 (High): only Beancount's own documents step reads the overlay's listing of a documents
    root; a user plugin that walks the root (`account.walk`, Beancount's `validate_directory`) sees the disk, empty
    folders, their `dirs` and `followlinks` included, as Beancount's real load does: R4 `pass` (before the fix: the
    prediction differed)."""
    module = f"r4_{case}_plugin"
    if case == "validate_directory_empty_folder":
        (w.root / "docs" / "Assets" / "Old").mkdir(parents=True)  # no `open Assets:Old`: Beancount reports it
        source = CHECKDIRS
    else:
        (w.root / "docs" / "Assets" / "Cash").mkdir(parents=True)  # empty
        follow = b"True"
        if case == "walk_not_following_links":
            follow = b"False"
            (w.root / "real").mkdir()
            (w.root / "real" / "2026-01-01.pdf").write_bytes(b"x")
            (w.root / "docs" / "Assets" / "Bank").symlink_to(w.root / "real")
        source = WALKS.replace(b"FOLLOW", follow)
    (w.root / f"{module}.py").write_bytes(source)
    main = f'option "insert_pythonpath" "TRUE"\noption "documents" "docs"\nplugin "{module}"\n'.encode() + MAIN
    w.main.write_bytes(main)
    try:
        save(w, (Change(str(w.main), sha256(main), main + TXN),))
        assert check(w).outcome == "pass"
    finally:
        sys.modules.pop(module, None)
        sys.path[:] = [p for p in sys.path if p != str(w.root)]


def test_an_outside_edit_before_the_check_supersedes_it_without_loading(w: World) -> None:
    save(w)
    w.inc.write_bytes(EDIT)
    loads: list[str] = []
    out = check(w, ports(load=logged(loads, fixed(OTHER))))
    assert (out.outcome, out.loaded, loads) == ("superseded", False, [])
    assert w.states() == [f"{NAME}.closed"]


@pytest.mark.parametrize("edit", [EDIT, b""], ids=["edit", "truncate"])
def test_an_outside_edit_during_the_load_supersedes_it(w: World, edit: bytes) -> None:
    save(w)
    p = ports()
    out = check(w, ports(load=around(p.load, lambda: None, lambda: w.inc.write_bytes(edit))))
    assert (out.outcome, out.loaded) == ("superseded", True)
    assert w.states() == [f"{NAME}.closed"]


@pytest.mark.parametrize("edit", [EDIT, b""], ids=["edit_revert", "truncate_then_write"])
def test_an_edit_reverted_during_the_load_is_differs_and_gates_nothing(w: World, edit: bytes) -> None:
    """@regression-guard (`check` already answered so). S0068 (ADR-0067; spec §15): the load read other bytes (A-B-A, or
    `script > file` caught empty) and the fingerprint is the same before and after it: R4 cannot tell this from a
    prediction that differs (the parse-only confirm that told them apart is gone), so it is `differs`; the notice names
    both causes and nothing is gated."""
    save(w)
    p = ports()
    out = check(w, ports(load=around(p.load, lambda: w.inc.write_bytes(edit), lambda: w.inc.write_bytes(INC))))
    assert out.outcome == "differs"
    assert w.states() == [f"{NAME}.closed"]


def test_an_open_journal_of_the_workspace_does_not_stop_the_compare(w: World) -> None:
    """S0068: R4's own `.open` match is gone; the fingerprint alone decides. A later save's journal left `.open` with
    the files at the record's post state (its rollback failed before any replace) leaves the disk the saved state:
    R4 compares (until S0068: `superseded`). A half-applied one moves the fingerprint (the next test)."""
    rec = save(w)
    (w.journal() / "s2-j1.open").write_bytes(another_save(rec, "s2", "j1"))
    assert check(w).outcome == "pass"
    (w.journal() / "s2-j1.open").write_bytes(b"garbage")  # unreadable: nothing to read in it either
    assert check(w).outcome == "pass"
    w.inc.write_bytes(EDIT)  # a half-applied later save: the disk is no longer the saved state
    assert check(w).outcome == "superseded"


def test_a_new_engine_build_is_superseded_after_the_real_load(w: World) -> None:
    save(w)
    loads: list[str] = []
    real = ports().load
    out = check(w, ports(build="another build", load=logged(loads, real)))
    assert (out.outcome, out.loaded, loads) == ("superseded", True, [str(w.main)])
    assert w.states() == [f"{NAME}.closed"]


def test_a_save_through_any_load_records_its_adopted_digest(w: World) -> None:
    """BL-0173: the save always computes the adopted digest of its load, so every record can be compared (before
    S0067 a load without a parse digest left `not_computed`, which R4 treated as a build change)."""

    def plain(root: str, candidates: Mapping[str, bytes]) -> overlay.LoadResult:
        return overlay.load(root, candidates, POLICY)

    rec = save(w, load=plain)
    assert rec.adopted_digest == ports().load(str(w.main))
    assert check(w).outcome == "pass"


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


def test_the_reconciliation_load_refuses_an_encrypted_file(w: World) -> None:
    (w.root / "secret.gpg").write_bytes(b"-----BEGIN PGP MESSAGE-----\n")
    w.main.write_bytes(MAIN + b'include "secret.gpg"\n')
    with pytest.raises(beancount_port.EncryptedFileMet) as e:
        beancount_port.reconcile_load(str(w.main))
    assert e.value.path == str(w.root / "secret.gpg")


def test_an_encrypted_file_met_on_an_unchanged_disk_is_a_failure_to_run(w: World) -> None:
    save(w)

    def met(root: str) -> str:
        raise beancount_port.EncryptedFileMet(root)

    with pytest.raises(r4.R4Error):
        check(w, ports(load=met))
    assert w.states() == [f"{NAME}.closed"]


# -- what is not `.closed` is never touched --------------------------------------------------------------------


@pytest.mark.parametrize("state", ["mismatch", "done", "open"])
def test_r4_never_touches_a_record_that_is_not_closed(w: World, state: str) -> None:
    save(w)
    (w.journal() / f"{NAME}.closed").rename(w.journal() / f"{NAME}.{state}")
    assert check(w).outcome == "gone"
    assert w.states() == [f"{NAME}.{state}"]


def test_an_unreadable_record_is_reported_and_left(w: World) -> None:
    save(w)
    (w.journal() / f"{NAME}.closed").write_bytes(b"garbage")
    assert check(w).outcome == "unreadable"
    assert w.states() == [f"{NAME}.closed"]


def test_a_record_with_an_old_style_fingerprint_is_unreadable(w: World) -> None:
    rec = save(w)
    old = replace(rec, post_fingerprint=digest(decode(rec.post_fingerprint.encode("ascii"))))
    (w.journal() / f"{NAME}.closed").write_bytes(journal.encode(old))
    assert check(w).outcome == "unreadable"


@pytest.mark.parametrize("version", [1, 2])
def test_an_older_record_is_unreadable_never_compared(w: World, version: int) -> None:
    """@regression-guard. Verifier r1 F10 (S0066), BL-0173 (S0067): a journal format 1 or 2 record is `unreadable`: R4
    compares nothing (logged, no notice), and leaves it."""
    save(w)
    f = w.journal() / f"{NAME}.closed"
    raw = json.loads(f.read_bytes())
    assert raw["v"] == 3
    raw["v"] = version
    raw["parse_digest"] = "sha256:" + "b" * 64
    f.write_bytes((json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode())
    assert check(w).outcome == "unreadable"
    assert w.states() == [f"{NAME}.closed"]


def test_a_record_renamed_by_hand_is_unreadable(w: World) -> None:
    save(w)
    (w.journal() / f"{NAME}.closed").rename(w.journal() / "s9-j9.closed")
    assert r4.check(ports(), str(w.ald), KEY, "s9-j9", Job()).outcome == "unreadable"


# -- R4 writes nothing ------------------------------------------------------------------------------------------


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


def test_r4_writes_nothing_on_any_outcome(w: World) -> None:
    """S0068 (ADR-0067 decision 3): no rename, no count, no sync of the journal folder, whatever the outcome (until
    S0068 every outcome but `differs` and a Cancel renamed the record, and a failure created an attempt count)."""
    save(w)
    fs = SpyFs(for_this_os())
    assert check(w, ports(fs=fs)).outcome == "pass"
    assert check(w, ports(fs=fs, load=fixed(OTHER))).outcome == "differs"
    assert check(w, ports(fs=fs, build="another build")).outcome == "superseded"
    assert fs.log == []
    assert w.states() == [f"{NAME}.closed"]
