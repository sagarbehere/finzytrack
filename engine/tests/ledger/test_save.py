"""The save job inside the engine (`ledger_engine.ledger.save`; `design/specs/engine-jobs.md` §6.1 steps 1-7 and
9-11, §6.5, §11; ADR-0008 step 4, ADR-0013, ADR-0068; S0063, S0071), on real APFS in scratch folders
(the load and the fingerprint read the disk with the loader's own calls, so the fake file system cannot stand in for
them; the durable order's crash sweeps are `test_writer.py`'s and `test_restore.py`'s). Written from the spec's lines:

- step 1 "An `.open` journal of this workspace (re-read from disk, matched as §6.5 says) refuses the save with
  `WRITES_PAUSED {unfinished_save}` ... No other record gates a save" (ADR-0067); step 4 "this workspace has no `.open`
  journal (matched as §6.5 says)": by registry key or lineage id, in any `local/*/journal/`;
- steps 2 and 3 are removed (ADR-0068 D3, D4; S0071): the save takes no per-file lock and tries no legacy lock;
- step 4 "the disk's fingerprint equals the plan's; the published mirror is fresh against the disk (its own ledger set
  observed now gives its digest, `design/specs/mirror/publish.md` §7)"; publish.md §7's four gate tests (S0087,
  BL-0113): the generation is published by the real publish into a scratch cache folder;
- steps 5 and 6 are removed (ADR-0068 D1, S0070): "The save does no Beancount load"; the plan's fingerprint is what
  the preview's load read (BL-0106), so the save runs no plugin and refuses no candidate a load would refuse; it still
  refuses to write an encrypted file (ADR-0059);
- §11 "'Your ledger is unchanged' is said only after the targets were hashed again and equal their pre bytes".
"""

from __future__ import annotations

import gc
import os
import shutil
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

import pytest

from ledger_engine.fs.darwin import for_this_os
from ledger_engine.ledger import overlay, writer
from ledger_engine.ledger import save as sv
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core.journal import FileEntry, Record, State
from ledger_engine.ledger.core.ledger_set import Fingerprint, LedgerSet, as_before
from ledger_engine.ledger.fingerprint import observe as observe_set
from ledger_engine.ledger.policy import DesktopPolicy
from ledger_engine.ledger.writer import BadPlanError, Change, NotSaved, Reason, sha256
from ledger_engine.mirror import publish as pb
from tests.ledger.worlds import with_folders
from tests.mirror.publish_support import publish_job

pytestmark = [
    pytest.mark.inv("INV-01"),
    pytest.mark.inv("INV-22"),
    pytest.mark.inv("INV-26"),
    pytest.mark.skipif(sys.platform != "darwin", reason="the write path's OS parts are macOS-only until BL-0087"),
]

MAIN = b'option "operating_currency" "USD"\ninclude "inc.bean"\n2026-01-01 open Assets:Cash\n'
INC = b"2026-01-01 open Expenses:Food\n"
TXN = b'2026-01-02 * "Lunch"\n  Expenses:Food  5 USD\n  Assets:Cash\n'
EDIT = b"2026-01-01 open Expenses:Food\n2026-01-01 open Expenses:Rent\n"
KEY = "k1"
LINEAGE = "lin1"


class Cancelled(Exception):
    """Stands for the engine ended by a Cancel at its gate (the sidecar never sends `proceed`)."""


@dataclass
class FakeJob:
    events: list[str] = field(default_factory=list[str])
    cancel: bool = False
    on_gate: Callable[[], None] | None = None
    on_progress: Callable[[str], None] | None = None

    def progress(self, phase: sv.Phase) -> None:
        self.events.append(phase)
        if self.on_progress is not None:
            self.on_progress(phase)

    def gate(self) -> None:
        self.events.append("gate")
        if self.on_gate is not None:
            self.on_gate()
        if self.cancel:
            raise Cancelled

    def committed(self) -> None:
        self.events.append("committed")


@dataclass
class Ledger:
    tmp: Path
    root: Path
    main: Path
    inc: Path
    ald: Path
    cache: Path

    def tree(self) -> dict[str, bytes | None]:
        """Every entry under the scratch folder."""
        out: dict[str, bytes | None] = {}
        for folder, dirs, files in os.walk(self.tmp):
            for d in dirs:
                out[os.path.join(folder, d)] = None
            for f in files:
                p = os.path.join(folder, f)
                out[p] = Path(p).read_bytes() if Path(p).is_file() and not Path(p).is_symlink() else None
        return out

    def journal(self) -> Path:
        return self.ald / "local" / KEY / "journal"


@pytest.fixture
def led(tmp_path: Path) -> Ledger:
    root = tmp_path / "L"
    root.mkdir()
    (root / "main.bean").write_bytes(MAIN)
    (root / "inc.bean").write_bytes(INC)
    ald = tmp_path / "app"
    (ald / "locks").mkdir(parents=True)
    cache = tmp_path / "cache"
    cache.mkdir()
    led = Ledger(tmp_path, root, root / "main.bean", root / "inc.bean", ald, cache)
    publish_disk(led)
    return led


def publish_disk(led: Ledger) -> pb.Published:
    """The disk as it is now, published as the rebuild publishes it (§6.7; publish.md §4)."""
    out = publish_job(for_this_os(), led.cache, led.main, ledger=ledger_set_of(led.main))
    assert isinstance(out, pb.Published), out
    return out


def ledger_set_of(main: Path) -> LedgerSet:
    loaded = overlay.load(str(main), {}, POLICY)
    assert isinstance(loaded, overlay.Loaded)
    return loaded.fingerprint.ledger_set


POLICY = DesktopPolicy()


def plan_for(led: Ledger, changes: tuple[Change, ...] | None = None, /, **kw: Any) -> sv.SavePlan:
    """The plan a preview would make now (BL-0106; spec §6.1 step 4): its fingerprint is what the preview's load of the
    candidate read, as the disk must hold it before the save (`as_before`); the mirror published from it. A candidate
    the load refuses (the preview would refuse it) gets the disk's own load, so the save's refusals can be tested."""
    changes = changes or (Change(str(led.main), sha256(MAIN), MAIN + TXN),)
    loaded = overlay.load(str(led.main), {c.path: c.post for c in changes}, POLICY)
    if isinstance(loaded, overlay.Loaded):
        fp = as_before(loaded.fingerprint, {c.path: c.pre for c in changes})
    else:
        disk = overlay.load(str(led.main), {}, POLICY)
        assert isinstance(disk, overlay.Loaded)
        fp = disk.fingerprint
    plan = sv.SavePlan(
        session="s1",
        job="j7",
        workspace_key=KEY,
        lineage=LINEAGE,
        engine_build="test build",
        root=str(led.main),
        changes=changes,
        fingerprint=fp,
        app_cache=str(led.cache),
        app_local_data=str(led.ald),
    )
    return replace(plan, **kw)


def ports_for(
    led: Ledger,
    *,
    observe: sv.Observe | None = None,
) -> sv.Ports:
    ports = sv.Ports(for_this_os())
    return ports if observe is None else replace(ports, observe=observe)


def hooked(before: Callable[[], object] | None = None, after: Callable[[], object] | None = None) -> sv.Observe:
    """The real observation of the gate (step 4) with an outside program's act just before and just after it."""

    def observe(fp: Fingerprint) -> Fingerprint:
        if before is not None:
            before()
        out = observe_set(fp.ledger_set)
        if after is not None:
            after()
        return out

    return observe


def assert_nothing_written(led: Ledger, before: dict[str, bytes | None], out: object) -> None:
    assert led.tree() == before, out
    assert not (led.ald / "local").exists()  # no journal, no backup folder


# -- the save lands -----------------------------------------------------------------------------------------------


def test_a_save_lands_after_its_gate_with_no_load_and_its_record_carries_no_prediction(led: Ledger) -> None:
    plan = plan_for(led)
    job = FakeJob()
    out = sv.run(plan, ports_for(led), job)
    assert isinstance(out, sv.Saved), out
    assert not out.published  # the save never publishes: the rebuild after it does (§6.7; S0087)
    assert job.events == ["gate", "saving", "committed"]  # no "checking_change": the save does no load (S0070)
    assert (led.main.read_bytes(), led.inc.read_bytes()) == (MAIN + TXN, INC)
    assert sorted(os.listdir(led.journal())) == ["s1-j7.closed"]
    rec = journal.decode((led.journal() / "s1-j7.closed").read_bytes())
    assert isinstance(rec, Record)
    assert (rec.workspace_key, rec.lineage, rec.engine_build) == (KEY, LINEAGE, "test build")
    raw = (led.journal() / "s1-j7.closed").read_text()
    assert "post_fingerprint" not in raw  # ADR-0068 D1: no prediction (journal format 4)
    assert "adopted_digest" not in raw


def test_a_save_takes_no_lock_of_its_own(led: Ledger) -> None:
    """ADR-0068 D3, D4: no per-file lock file is made (the engine lock is the guard) and the plan names no legacy
    lock to try (only Copy tries one, INV-26)."""
    assert "legacy_locks" not in {f.name for f in fields(sv.SavePlan)}
    assert {f.name for f in fields(sv.Ports)} == {"fs", "observe", "store"}  # the store only reads (S0087)
    out = sv.run(plan_for(led), ports_for(led), FakeJob())
    assert isinstance(out, sv.Saved), out
    assert os.listdir(led.ald / "locks") == []


PLUGIN = """__plugins__ = ["mark"]


def mark(entries, options_map):
    import pathlib

    pathlib.Path(options_map["filename"]).with_name("plugin-ran").touch()
    return entries, []
"""


def test_the_save_runs_no_plugin_of_the_ledger(led: Ledger) -> None:
    """ADR-0068 D1: the save does no Beancount load, so no user code runs in it (the preview's load did, before)."""
    main = b'option "insert_pythonpath" "TRUE"\nplugin "ft_s0070_mark"\n' + MAIN
    led.main.write_bytes(main)
    (led.root / "ft_s0070_mark.py").write_text(PLUGIN)
    publish_disk(led)  # the rebuild's load runs the ledger's plugins; the save's gate only reads its result
    plan = plan_for(led, (Change(str(led.main), sha256(main), main + TXN),))
    mark = led.root / "plugin-ran"
    assert mark.exists()  # the preview's load ran it
    mark.unlink()
    out = sv.run(plan, ports_for(led), FakeJob())
    assert isinstance(out, sv.Saved), out
    assert not mark.exists()


PLUGIN_V1 = '__plugins__ = ["keep"]\n\n\ndef keep(entries, options_map):\n    return entries, []\n'
PLUGIN_V2 = PLUGIN_V1.replace("return entries, []", "return entries[:-1], []")


def with_plugin(led: Ledger, name: str, source: str | None = PLUGIN_V1) -> bytes:
    """The ledger names the plugin `name` from its own folder (`insert_pythonpath`); its source written unless None;
    the disk published (the rebuild after the last save)."""
    main = b'option "insert_pythonpath" "TRUE"\nplugin "' + name.encode() + b'"\n' + MAIN
    led.main.write_bytes(main)
    if source is not None:
        (led.root / f"{name}.py").write_text(source)
    publish_disk(led)
    return main


def fresh_engine(*names: str) -> None:
    """The next job's engine has imported none of the ledger's plugins (spec §5.1)."""
    for n in names:
        sys.modules.pop(n, None)


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_a_plugin_changed_after_the_preview_refuses_the_save(led: Ledger, change: str) -> None:
    """BL-0124, spec §6.1 step 4: the preview's new errors were those of the plugin code it ran; other code on disk
    is a moved ledger, refused at the gate with nothing written."""
    name = f"ft_s0099_save_{change}"
    try:
        main = with_plugin(led, name)
        fresh_engine(name)
        plan = plan_for(led, (Change(str(led.main), sha256(main), main + TXN),))
        src = led.root / f"{name}.py"
        if change == "edit":
            src.write_text(PLUGIN_V2)
        else:
            src.unlink()
        before = led.tree()
        job = FakeJob()
        out = sv.run(plan, ports_for(led), job)
        assert out == sv.Refused("ledger_changed", unchanged=True)
        assert "gate" not in job.events
        assert_nothing_written(led, before, out)
    finally:
        fresh_engine(name)


def test_a_plugin_that_appears_after_the_preview_refuses_the_save(led: Ledger) -> None:
    """The preview's load did not find the plugin (an import error); the next load would run it."""
    name = "ft_s0099_save_appears"
    try:
        main = with_plugin(led, name, None)
        plan = plan_for(led, (Change(str(led.main), sha256(main), main + TXN),))
        (led.root / f"{name}.py").write_text(PLUGIN_V1)
        out = sv.run(plan, ports_for(led), FakeJob())
        assert out == sv.Refused("ledger_changed", unchanged=True)
        assert led.main.read_bytes() == main
    finally:
        fresh_engine(name)


def test_a_plugin_edited_after_the_publish_refuses_the_save_as_a_stale_mirror(led: Ledger) -> None:
    """publish.md §7: the preview read the edited plugin (the disk equals the plan), but the published generation
    describes the old code, so the screens the user planned on are not the ledger's."""
    name = "ft_s0099_save_stale"
    try:
        main = with_plugin(led, name)
        (led.root / f"{name}.py").write_text(PLUGIN_V2)
        fresh_engine(name)
        plan = plan_for(led, (Change(str(led.main), sha256(main), main + TXN),))
        before = led.tree()
        out = sv.run(plan, ports_for(led), FakeJob())
        assert out == sv.Refused("mirror_stale", unchanged=True)
        assert_nothing_written(led, before, out)
    finally:
        fresh_engine(name)


def test_unchanged_plugin_code_saves(led: Ledger) -> None:
    name = "ft_s0099_save_same"
    try:
        main = with_plugin(led, name)
        fresh_engine(name)
        plan = plan_for(led, (Change(str(led.main), sha256(main), main + TXN),))
        assert any(k[0] == name for k, _ in plan.fingerprint.modules)
        (led.root / f"{name}.py").touch()
        out = sv.run(plan, ports_for(led), FakeJob())
        assert isinstance(out, sv.Saved), out
    finally:
        fresh_engine(name)


def test_a_candidate_a_load_would_refuse_is_saved_as_planned(led: Ledger) -> None:
    """The save trusts the plan: a file no include reaches (the overlay's `not-reached`) is the preview's to refuse
    (BL-0106), never the save's."""
    stray = led.root / "stray.bean"
    changes = (Change(str(stray), None, b"2026-01-02 open Assets:Stray\n"),)
    out = sv.run(plan_for(led, changes), ports_for(led), FakeJob())
    assert isinstance(out, sv.Saved), out
    assert stray.read_bytes() == changes[0].post


@pytest.mark.parametrize("name", ["new.gpg", "NEW.ASC", "inc.bean.gpg"])
def test_a_change_to_an_encrypted_file_is_refused_and_writes_nothing(led: Ledger, name: str) -> None:
    """ADR-0059: v0.4.0 never writes an encrypted file (the overlay's own candidate rule, by name)."""
    target = led.root / name
    plan = plan_for(led, (Change(str(led.main), sha256(MAIN), MAIN + TXN), Change(str(target), None, b"x\n")))
    before = led.tree()
    job = FakeJob()
    out = sv.run(plan, ports_for(led), job)
    assert out == sv.Refused("encrypted_file", unchanged=True, path=str(target))
    assert "gate" not in job.events
    assert_nothing_written(led, before, out)


def test_a_target_that_links_to_an_encrypted_file_is_refused(led: Ledger) -> None:
    """S0070 red team r1 F1 (their P6b): step 0 checks the name a change resolves to, so plaintext never goes into a
    `.gpg` file through a link."""
    secret = led.root / "secret.gpg"
    secret.write_bytes(MAIN)  # the same bytes, so the pre hash matches through the link
    led.main.unlink()
    led.main.symlink_to(secret)
    job = FakeJob()
    out = sv.run(plan_for(led), ports_for(led), job)
    assert out == sv.Refused("encrypted_file", unchanged=True, path=str(led.main))
    assert "gate" not in job.events
    assert secret.read_bytes() == MAIN


def test_a_link_to_an_encrypted_file_swapped_in_after_the_gate_is_not_written(led: Ledger) -> None:
    """S0070 red team r1 F1 (their P6): the writer checks the resolved name again before its first effect."""
    secret = led.root / "secret.gpg"
    secret.write_bytes(MAIN)

    def swap() -> None:
        led.main.unlink()
        led.main.symlink_to(secret)

    out = sv.run(plan_for(led), ports_for(led), FakeJob(on_gate=swap))
    assert isinstance(out, NotSaved), out
    assert out.reason is Reason.CHANGED
    assert secret.read_bytes() == MAIN
    assert led.main.is_symlink()


def test_the_writer_and_the_overlay_name_the_same_encrypted_suffixes() -> None:
    assert writer.ENCRYPTED_SUFFIXES == overlay.ENCRYPTED_SUFFIXES


def test_a_save_that_creates_a_file_named_by_a_new_include_lands(led: Ledger) -> None:
    new = led.root / "prices.bean"
    main = MAIN + b'include "prices.bean"\n'
    changes = (
        Change(str(led.main), sha256(MAIN), main),
        Change(str(new), None, b"2026-01-02 price USD 1 USD\n"),
    )
    out = sv.run(plan_for(led, changes), ports_for(led), FakeJob())
    assert isinstance(out, sv.Saved), out
    assert new.read_bytes() == changes[1].post


def test_the_garbage_collector_is_paused_for_the_job_and_back_after_it(led: Ledger) -> None:
    states: list[bool] = []
    out = sv.run(plan_for(led), ports_for(led, observe=hooked(before=lambda: states.append(gc.isenabled()))), FakeJob())
    assert isinstance(out, sv.Saved)
    assert states == [False, False]  # the plan's set, then the published generation's own set (S0087)
    assert gc.isenabled()


# -- the gate (step 4) ----------------------------------------------------------------------------------------------


def test_a_ledger_moved_since_the_plan_refuses(led: Ledger) -> None:
    plan = plan_for(led)
    led.inc.write_bytes(EDIT)
    before = led.tree()
    job = FakeJob()
    out = sv.run(plan, ports_for(led), job)
    assert out == sv.Refused("ledger_changed", unchanged=True)
    assert "gate" not in job.events
    assert_nothing_written(led, before, out)


def test_an_include_edited_right_up_to_the_gates_observation_refuses(led: Ledger) -> None:
    """Step 4 is the last check before the commit point (ADR-0068 D1): an edit just before it refuses."""
    plan = plan_for(led)
    out = sv.run(plan, ports_for(led, observe=hooked(before=lambda: led.inc.write_bytes(EDIT))), FakeJob())
    assert out == sv.Refused("ledger_changed", unchanged=True)
    assert (led.inc.read_bytes(), led.main.read_bytes()) == (EDIT, MAIN)


# -- the gate's mirror check (publish.md §7; S0087, BL-0113) -------------------------------------------------------


def test_a_save_that_creates_and_includes_a_file_passes_on_a_fresh_generation(led: Ledger) -> None:
    """The plan's set names `prices.bean`, which the published generation never read: only the generation's own set
    is compared with the disk (S0085 review, design F1)."""
    new = led.root / "prices.bean"
    changes = (
        Change(str(led.main), sha256(MAIN), MAIN + b'include "prices.bean"\n'),
        Change(str(new), None, b"2026-01-02 price USD 1 USD\n"),
    )
    plan = plan_for(led, changes)
    assert str(new) in dict(plan.fingerprint.files)
    out = sv.run(plan, ports_for(led), FakeJob())
    assert isinstance(out, sv.Saved), out


def test_a_save_that_adds_an_include_of_an_existing_file_passes(led: Ledger) -> None:
    other = led.root / "other.bean"
    other.write_bytes(b"2026-01-01 open Assets:Bank\n")
    publish_disk(led)  # the disk with `other.bean` beside it, not included: the generation's set does not name it
    changes = (Change(str(led.main), sha256(MAIN), MAIN + b'include "other.bean"\n'),)
    plan = plan_for(led, changes)
    assert str(other) in dict(plan.fingerprint.files)
    out = sv.run(plan, ports_for(led), FakeJob())
    assert isinstance(out, sv.Saved), out


def test_a_plan_made_after_an_outside_edit_on_a_generation_from_before_it_is_refused(led: Ledger) -> None:
    led.inc.write_bytes(EDIT)  # an outside edit; the published generation is from before it
    plan = plan_for(led)  # the preview read the edited disk: the disk equals the plan
    before = led.tree()
    job = FakeJob()
    out = sv.run(plan, ports_for(led), job)
    assert out == sv.Refused("mirror_stale", unchanged=True)
    assert "gate" not in job.events
    assert_nothing_written(led, before, out)


def test_a_generation_of_another_ledger_under_this_key_is_refused(led: Ledger, tmp_path: Path) -> None:
    """S0087 round 1 (red team F2): the gate ties the generation to this ledger's root by `path_key`."""
    other = tmp_path / "B"
    other.mkdir()
    (other / "main.bean").write_bytes(b'option "operating_currency" "EUR"\n2020-01-01 open Assets:Other\n')
    shutil.rmtree(led.cache / "workspaces")
    out = publish_job(for_this_os(), led.cache, other / "main.bean", ledger=ledger_set_of(other / "main.bean"))
    assert isinstance(out, pb.Published)
    plan = plan_for(led)
    before = led.tree()
    assert sv.run(plan, ports_for(led), FakeJob()) == sv.Refused("mirror_stale", unchanged=True)
    assert_nothing_written(led, before, None)


def test_a_fresh_generation_after_the_outside_edit_lets_the_same_plan_pass(led: Ledger) -> None:
    led.inc.write_bytes(EDIT)
    plan = plan_for(led)
    publish_disk(led)
    assert isinstance(sv.run(plan, ports_for(led), FakeJob()), sv.Saved)


@pytest.mark.parametrize(
    "damage", ["no_folder", "no_link", "garbage", "journal", "link_at_mirror", "missing_file", "incomplete"]
)
def test_no_published_generation_or_an_unreadable_one_is_refused(led: Ledger, damage: str) -> None:
    m = led.cache / "workspaces" / KEY / "mirror"
    name = os.readlink(m / "current.db")
    if damage == "no_folder":
        for p in m.iterdir():
            p.unlink()
        m.rmdir()
    elif damage == "no_link":
        (m / "current.db").unlink()
    elif damage == "garbage":
        (m / name).write_bytes(b"SQLite format 3\x00" + bytes(200))
    elif damage == "journal":
        (m / f"{name}-journal").write_bytes(b"hot")
    elif damage == "missing_file":
        (m / name).unlink()
    elif damage == "incomplete":
        con = sqlite3.connect(m / name)
        con.execute("UPDATE m_meta SET value = '0' WHERE key = 'build_complete'")
        con.commit()
        con.close()
    else:
        m.rename(m.parent / "away")
        m.symlink_to(m.parent / "away")  # a fresh generation behind a link at `mirror/`: never followed
    plan = plan_for(led)
    before = led.tree()
    out = sv.run(plan, ports_for(led), FakeJob())
    assert out == sv.Refused("mirror_stale", unchanged=True)
    assert_nothing_written(led, before, out)


def test_the_gate_reads_the_published_generation_once_after_the_disk_check(led: Ledger) -> None:
    """One job at a time (ADR-0064): no publish runs between the gate's read and the commit point; the read comes after
    the disk's check, so an edit just before the check is the disk's refusal, not the mirror's."""
    plan = plan_for(led)
    calls: list[str] = []

    def observe(fp: Fingerprint) -> Fingerprint:
        calls.append(fp.root)
        return observe_set(fp.ledger_set)

    out = sv.run(plan, ports_for(led, observe=observe), FakeJob())
    assert isinstance(out, sv.Saved)
    assert len(calls) == 2  # the plan's set, then the generation's own set


def _record(led: Ledger, key: str, name: str, state: State, *, lineage: str = LINEAGE, owner: str = KEY) -> Path:
    """A record of workspace `owner` (lineage `lineage`) in `local/<key>/journal/`, in `state`."""
    folder = led.ald / "local" / key / "journal"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / journal.file_name(name, state)
    session, job = name.split("-")
    rec = with_folders(
        session=session,
        job=job,
        workspace_key=owner,
        lineage=lineage,
        engine_build="b",
        backup_dir="/b/" + name,
        files=(
            FileEntry(
                "/x/a.bean",
                "/x/a.bean",
                "1" * 64,
                "2" * 64,
                "0-a.bean",
                f".a.bean.ft-{name}.tmp",
            ),
        ),
        created_dirs=(),
    )
    path.write_bytes(journal.encode(rec))
    return path


def test_an_open_journal_of_this_workspace_refuses_and_writes_nothing(led: Ledger) -> None:
    """@regression-guard (S0068: the `.open` refusal stays, ADR-0012; spec §6.1 step 1)."""
    path = _record(led, KEY, "s0-j1", State.OPEN)
    before = led.tree()
    out = sv.run(plan_for(led), ports_for(led), FakeJob())
    assert out == sv.Refused("unfinished_save", unchanged=True, path=str(path))
    assert led.tree() == before


def test_a_closed_record_of_this_workspace_does_not_refuse_the_next_save(led: Ledger) -> None:
    """ADR-0067 decision 3: a landed save's `.closed` record, its rebuild run or not, lets the next save of the
    workspace land."""
    closed = _record(led, KEY, "s0-j1", State.CLOSED)
    out = sv.run(plan_for(led), ports_for(led), FakeJob())
    assert isinstance(out, sv.Saved), out
    assert closed.read_bytes()  # left as it was: the save reads no record but `.open` ones


@pytest.mark.parametrize("suffix", ["mismatch", "done", "r4-attempt-3"])
def test_records_a_development_build_left_do_not_refuse(led: Ledger, suffix: str) -> None:
    """S0068: `.mismatch`, `.done` and `.r4-attempt-<n>` files were written only by S0066-S0067 development builds;
    they are not records any more, so they fail safe: nothing gates on them (until S0068 a `.mismatch` refused
    `check_failed` until Resume saving). The `done` and `r4-attempt-3` cases are @regression-guard: they pass on
    cc0b27db too, which never gated on those names (S0068 verifier r1 F8); `mismatch` fails there."""
    folder = led.ald / "local" / KEY / "journal"
    folder.mkdir(parents=True)
    (folder / f"s0-j1.{suffix}").write_bytes(
        journal.encode(
            with_folders(
                session="s0",
                job="j1",
                workspace_key=KEY,
                lineage=LINEAGE,
                engine_build="b",
                backup_dir="/b/s0-j1",
                files=(
                    FileEntry(
                        "/x/a.bean",
                        "/x/a.bean",
                        "1" * 64,
                        "2" * 64,
                        "0-a.bean",
                        ".a.bean.ft-s0-j1.tmp",
                    ),
                ),
                created_dirs=(),
            )
        )
    )
    out = sv.run(plan_for(led), ports_for(led), FakeJob())
    assert isinstance(out, sv.Saved), out


def test_an_open_journal_under_another_key_with_this_lineage_refuses(led: Ledger) -> None:
    """§6.5: forgetting a folder and adding it again under a new key cannot skip an unfinished save."""
    path = _record(led, "old", "s0-j1", State.OPEN, owner="old")
    out = sv.run(plan_for(led), ports_for(led), FakeJob())
    assert out == sv.Refused("unfinished_save", unchanged=True, path=str(path))


def test_a_record_under_another_key_naming_this_key_refuses(led: Ledger) -> None:
    path = _record(led, "old", "s0-j1", State.OPEN, owner=KEY, lineage="other")
    out = sv.run(plan_for(led), ports_for(led), FakeJob())
    assert out == sv.Refused("unfinished_save", unchanged=True, path=str(path))


def test_another_workspaces_records_and_this_workspaces_history_do_not_refuse(led: Ledger) -> None:
    _record(led, "other", "s0-j1", State.OPEN, owner="other", lineage="lin9")
    _record(led, KEY, "s0-j2", State.CLOSED)
    _record(led, KEY, "s0-j3", State.RESOLVED)
    _record(led, KEY, "s0-j4", State.PART)
    out = sv.run(plan_for(led), ports_for(led), FakeJob())
    assert isinstance(out, sv.Saved), out


def test_an_unreadable_gating_record_under_another_key_refuses(led: Ledger) -> None:
    folder = led.ald / "local" / "other" / "journal"
    folder.mkdir(parents=True)
    (folder / "s0-j1.open").write_bytes(b"{torn")
    out = sv.run(plan_for(led), ports_for(led), FakeJob())
    assert out == sv.Refused("unfinished_save", unchanged=True, path=str(folder / "s0-j1.open"))


def test_a_journal_folder_that_cannot_be_listed_refuses(led: Ledger) -> None:
    folder = led.ald / "local" / "other" / "journal"
    folder.mkdir(parents=True)
    folder.chmod(0o000)
    try:
        out = sv.run(plan_for(led), ports_for(led), FakeJob())
    finally:
        folder.chmod(0o700)
    assert isinstance(out, sv.Refused)
    assert out.reason == "unfinished_save"


@pytest.mark.parametrize("folder", ["key", "journal", "backups"])
def test_a_link_in_place_of_the_workspaces_own_folder_is_not_saved_through(led: Ledger, folder: str) -> None:
    """S0077 (BL-0193): recover-all reads no record through a link in place of `local/<key>`, its `journal` or its
    `backups` (nor does the history trim), so the save does not write through one: its record or its backups would be
    where recovery never undoes them. Not saved, naming the link; the ledger unchanged; nothing written there."""
    key_dir = led.ald / "local" / KEY
    elsewhere = led.tmp / "elsewhere"
    elsewhere.mkdir()
    if folder == "key":
        link = key_dir
        key_dir.parent.mkdir(parents=True)
    else:
        link = key_dir / folder
        key_dir.mkdir(parents=True)
    link.symlink_to(elsewhere)
    out = sv.run(plan_for(led), ports_for(led), FakeJob())
    assert isinstance(out, NotSaved), out
    assert (out.reason, out.entry, out.unchanged, out.open_journal) == (Reason.LINKED_FOLDER, str(link), True, False)
    assert (led.main.read_bytes(), led.inc.read_bytes()) == (MAIN, INC)
    assert os.listdir(elsewhere) == []


def test_a_record_written_after_step_1_refuses_at_the_gate(led: Ledger) -> None:
    """@regression-guard (spec §6.1 step 4): the gate reads the `.open` journals again; the step-1 read is not
    trusted."""
    out = sv.run(
        plan_for(led), ports_for(led, observe=hooked(before=lambda: _record(led, KEY, "s0-j9", State.OPEN))), FakeJob()
    )
    assert isinstance(out, sv.Refused)
    assert out.reason == "unfinished_save"


# -- Cancel, the writer's refusals, a bad plan ----------------------------------------------------------------------


def test_a_cancel_at_the_gate_writes_nothing(led: Ledger) -> None:
    plan = plan_for(led)
    before = led.tree()
    with pytest.raises(Cancelled):
        sv.run(plan, ports_for(led), FakeJob(cancel=True))
    assert_nothing_written(led, before, None)


def test_a_writer_refusal_comes_back_as_it_is_with_its_file(led: Ledger) -> None:
    os.link(led.main, led.tmp / "second-name.bean")
    out = sv.run(plan_for(led), ports_for(led), FakeJob())
    assert isinstance(out, NotSaved), out
    assert (out.reason, out.path, out.unchanged, out.open_journal) == (Reason.HARD_LINKED, str(led.main), True, False)
    assert led.main.read_bytes() == MAIN


@pytest.mark.parametrize(
    "bad",
    [
        {"session": "S1"},  # a session id in upper case could differ from another only in case
        {"root": "/elsewhere/main.bean"},
    ],
)
def test_a_malformed_plan_raises_before_anything_is_read(led: Ledger, bad: dict[str, object]) -> None:
    with pytest.raises(BadPlanError):
        sv.run(plan_for(led, **bad), ports_for(led), FakeJob())


def test_a_change_whose_pre_is_not_the_plans_raises(led: Ledger) -> None:
    changes = (Change(str(led.main), sha256(b"other"), MAIN + TXN),)
    with pytest.raises(BadPlanError):
        sv.run(replace(plan_for(led), changes=changes), ports_for(led), FakeJob())


def test_a_ledger_folder_that_cannot_be_searched_is_not_saved_and_names_the_file(led: Ledger) -> None:
    """Spec §15: a folder that denies looking names up in it stops even the resolution of the file: not saved, as an
    I/O error, before anything is written (the mode is restored after; no ACL or flag is set)."""
    sub = led.root / "y2026"
    sub.mkdir()
    (sub / "a.bean").write_bytes(b"")
    led.main.write_bytes(MAIN + b'include "y2026/a.bean"\n')
    with_include = MAIN + b'include "y2026/a.bean"\n'
    plan = plan_for(led, (Change(str(led.main), sha256(with_include), with_include + TXN),))
    sub.chmod(0o600)  # no search right
    try:
        out = sv.run(plan, ports_for(led), FakeJob())
    finally:
        sub.chmod(0o700)
    assert isinstance(out, NotSaved), out
    assert (out.reason, out.path) == (Reason.IO_ERROR, str(sub / "a.bean"))
    assert not (led.ald / "local").exists()


# -- round 1: one save, one name; an I/O error at a lock or in the load --------------------------------------------


@pytest.mark.parametrize(
    "taken",
    [
        "journal/s1-j7.closed",  # an earlier save of this name landed
        "journal/s1-j7.resolved",
        "journal/s1-j7.part",
        "journal/s1-j7.mismatch",  # any suffix: a development build's file too
        "journal/S1-J7.closed",  # the same name in another case (APFS is case-insensitive)
        "backups/s1-j7",
    ],
)
def test_a_save_whose_name_already_has_a_record_or_backups_raises_and_writes_nothing(led: Ledger, taken: str) -> None:
    """Spec §6.5 "One save, one name": §6.6 tells a dead save's outcome by its record's name alone, so a name that is
    already on disk is a caller's error, refused before step 1 (verifier r1 F2)."""
    entry = led.ald / "local" / KEY / taken
    entry.parent.mkdir(parents=True)
    if taken.startswith("backups/"):
        entry.mkdir()
    else:
        entry.write_bytes(b"{}")
    before = led.tree()
    with pytest.raises(BadPlanError, match="name_taken"):
        sv.run(plan_for(led), ports_for(led), FakeJob())
    assert led.tree() == before
    assert led.main.read_bytes() == MAIN


def test_another_saves_name_does_not_take_this_one(led: Ledger) -> None:
    jd = led.journal()
    jd.mkdir(parents=True)
    (jd / "s1-j70.done").write_bytes(b"{}")  # `s1-j7` is a prefix of its name, not its name
    (jd / "s1-j8.done").write_bytes(b"{}")
    (led.ald / "local" / KEY / "backups" / "s1-j70").mkdir(parents=True)
    out = sv.run(plan_for(led), ports_for(led), FakeJob())
    assert isinstance(out, sv.Saved), out
