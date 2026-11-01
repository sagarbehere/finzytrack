"""The save job inside the engine (`ledger_engine.ledger.save`; `design/specs/engine-jobs.md` §6.1 steps 1-7 and
9-11, §6.5, §11; ADR-0008 step 4, ADR-0012 Q29, ADR-0013, ADR-0062 decision 2; S0063), on real APFS in scratch folders
(the load and the fingerprint read the disk with the loader's own calls, so the fake file system cannot stand in for
them; the durable order's crash sweeps are `test_writer.py`'s and `test_restore.py`'s). Written from the spec's lines:

- step 1 "An `.open` journal of this workspace (re-read from disk, matched as §6.5 says) refuses the save with
  `WRITES_PAUSED {unfinished_save}` ... No other record gates a save" (ADR-0067); step 4 "this workspace has no `.open`
  journal (matched as §6.5 says)": by registry key or lineage id, in any `local/*/journal/`;
- step 2 "every `data/.write.lock` ... deduplicated by `path_key`, tried once each without waiting, before any
  ledger-set lock. Free: held until the save ends. Absent: go on. Held, or any other outcome: refused with
  `LEGACY_APP_SAVING`, nothing written";
- step 3 "taken in `path_key` order, waiting (the user sees "Waiting for your ledger files…")";
- step 4 "the disk's fingerprint equals the plan's; the published mirror's equals the plan's";
- steps 5 and 6 are removed (ADR-0068 D1, S0070): "The save does no Beancount load"; the plan's fingerprint is what
  the preview's load read (BL-0106), so the save runs no plugin and refuses no candidate a load would refuse; it still
  refuses to write an encrypted file (ADR-0059);
- §11 "'Your ledger is unchanged' is said only after the targets were hashed again and equal their pre bytes".
"""

from __future__ import annotations

import gc
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from ledger_engine.fs.darwin import DarwinLegacyLocks, DarwinLocks, for_this_os
from ledger_engine.fs.port import LegacyAbsent, LegacyHeld, LegacyLocks, Lock, Locks
from ledger_engine.ledger import overlay
from ledger_engine.ledger import save as sv
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core.journal import FileEntry, Record, State
from ledger_engine.ledger.core.ledger_set import Fingerprint, as_before, digest
from ledger_engine.ledger.fingerprint import observe as observe_set
from ledger_engine.ledger.policy import DesktopPolicy
from ledger_engine.ledger.writer import BadPlanError, Change, NotSaved, Reason, sha256

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
class SpyLocks:
    inner: Locks
    tried: list[str] = field(default_factory=list[str])
    waited: list[str] = field(default_factory=list[str])

    def try_lock(self, key: str) -> Lock | None:
        self.tried.append(key)
        return self.inner.try_lock(key)

    def lock(self, key: str) -> Lock:
        self.waited.append(key)
        return self.inner.lock(key)


@dataclass
class SpyLegacy:
    inner: LegacyLocks
    tried: list[str] = field(default_factory=list[str])
    locks: SpyLocks | None = None
    ledger_locks_before: list[int] = field(default_factory=list[int])

    def try_lock(self, path: str) -> Lock | LegacyAbsent | LegacyHeld:
        self.tried.append(path)
        if self.locks is not None:
            self.ledger_locks_before.append(len(self.locks.tried))
        return self.inner.try_lock(path)


@dataclass
class Ledger:
    tmp: Path
    root: Path
    main: Path
    inc: Path
    ald: Path

    def tree(self) -> dict[str, bytes | None]:
        """Every entry under the scratch folder, but the lock files (they are never deleted, §6.1 step 3)."""
        out: dict[str, bytes | None] = {}
        for folder, dirs, files in os.walk(self.tmp):
            for d in dirs:
                out[os.path.join(folder, d)] = None
            for f in files:
                p = os.path.join(folder, f)
                if os.path.dirname(p) != str(self.ald / "locks"):
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
    return Ledger(tmp_path, root, root / "main.bean", root / "inc.bean", ald)


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
        mirror=digest(fp),
        legacy_locks=(),
        app_local_data=str(led.ald),
    )
    return replace(plan, **kw)


def ports_for(
    led: Ledger,
    *,
    observe: sv.Observe | None = None,
    locks: Locks | None = None,
    legacy: LegacyLocks | None = None,
) -> sv.Ports:
    ports = sv.Ports(for_this_os(), locks or DarwinLocks(str(led.ald / "locks")), legacy or DarwinLegacyLocks())
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


def assert_locks_free(led: Ledger, plan: sv.SavePlan) -> None:
    locks = DarwinLocks(str(led.ald / "locks"))
    fs = for_this_os()
    for p in {*(p for p, _ in plan.fingerprint.files), *(c.path for c in plan.changes)}:
        lock = locks.try_lock(sv._key(fs, p))  # pyright: ignore[reportPrivateUsage]
        assert lock is not None, p
        lock.release()


# -- the save lands -----------------------------------------------------------------------------------------------


def test_a_save_lands_after_its_gate_with_no_load_and_its_record_carries_no_prediction(led: Ledger) -> None:
    plan = plan_for(led)
    job = FakeJob()
    out = sv.run(plan, ports_for(led), job)
    assert isinstance(out, sv.Saved), out
    assert not out.published  # TODO(BL-0113)
    assert job.events == ["gate", "saving", "committed"]  # no "checking_change": the save does no load (S0070)
    assert (led.main.read_bytes(), led.inc.read_bytes()) == (MAIN + TXN, INC)
    assert sorted(os.listdir(led.journal())) == ["s1-j7.closed"]
    rec = journal.decode((led.journal() / "s1-j7.closed").read_bytes())
    assert isinstance(rec, Record)
    assert (rec.workspace_key, rec.lineage, rec.engine_build) == (KEY, LINEAGE, "test build")
    raw = (led.journal() / "s1-j7.closed").read_text()
    assert "post_fingerprint" not in raw  # ADR-0068 D1: no prediction (journal format 4)
    assert "adopted_digest" not in raw
    assert_locks_free(led, plan)


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
    plan = plan_for(led, (Change(str(led.main), sha256(main), main + TXN),))
    mark = led.root / "plugin-ran"
    assert mark.exists()  # the preview's load ran it
    mark.unlink()
    out = sv.run(plan, ports_for(led), FakeJob())
    assert isinstance(out, sv.Saved), out
    assert not mark.exists()


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
    assert states == [False]
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


@pytest.mark.parametrize("mirror", [None, "sha256:" + "0" * 64])
def test_a_mirror_that_is_not_the_plans_refuses(led: Ledger, mirror: str | None) -> None:
    plan = plan_for(led, mirror=mirror)
    before = led.tree()
    out = sv.run(plan, ports_for(led), FakeJob())
    assert out == sv.Refused("mirror_stale", unchanged=True)
    assert_nothing_written(led, before, out)


def _record(led: Ledger, key: str, name: str, state: State, *, lineage: str = LINEAGE, owner: str = KEY) -> Path:
    """A record of workspace `owner` (lineage `lineage`) in `local/<key>/journal/`, in `state`."""
    folder = led.ald / "local" / key / "journal"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / journal.file_name(name, state)
    session, job = name.split("-")
    rec = Record(
        session=session,
        job=job,
        workspace_key=owner,
        lineage=lineage,
        engine_build="b",
        backup_dir="/b/" + name,
        files=(FileEntry("/x/a.bean", "/x/a.bean", "1" * 64, "2" * 64, "0-a.bean", f".a.bean.ft-{name}.tmp"),),
        created_dirs=(),
    )
    path.write_bytes(journal.encode(rec))
    return path


def test_an_open_journal_of_this_workspace_refuses_before_any_lock(led: Ledger) -> None:
    """@regression-guard (S0068: the `.open` refusal stays, ADR-0012; spec §6.1 step 1)."""
    path = _record(led, KEY, "s0-j1", State.OPEN)
    locks = SpyLocks(DarwinLocks(str(led.ald / "locks")))
    legacy = SpyLegacy(DarwinLegacyLocks())
    plan = plan_for(led, legacy_locks=(str(led.tmp / "legacy" / ".write.lock"),))
    before = led.tree()
    out = sv.run(plan, ports_for(led, locks=locks, legacy=legacy), FakeJob())
    assert out == sv.Refused("unfinished_save", unchanged=True, path=str(path))
    assert (locks.tried, legacy.tried) == ([], [])  # step 1 comes first
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
            Record(
                session="s0",
                job="j1",
                workspace_key=KEY,
                lineage=LINEAGE,
                engine_build="b",
                backup_dir="/b/s0-j1",
                files=(FileEntry("/x/a.bean", "/x/a.bean", "1" * 64, "2" * 64, "0-a.bean", ".a.bean.ft-s0-j1.tmp"),),
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


def test_a_record_written_while_the_save_waits_for_its_locks_refuses_at_the_gate(led: Ledger) -> None:
    """Step 4 reads the `.open` journals again under the locks: the step-1 read is not trusted across the wait."""
    plan = plan_for(led)
    holder = DarwinLocks(str(led.ald / "locks")).lock(sv._key(for_this_os(), str(led.inc)))  # pyright: ignore[reportPrivateUsage]

    def waiting(phase: str) -> None:
        if phase == "waiting_for_files":
            _record(led, KEY, "s0-j9", State.OPEN)
            holder.release()

    job = FakeJob(on_progress=waiting)
    out = sv.run(plan, ports_for(led), job)
    assert isinstance(out, sv.Refused)
    assert out.reason == "unfinished_save"
    assert job.events == ["waiting_for_files"]


# -- the locks (steps 2, 3) ---------------------------------------------------------------------------------------


def test_the_ledger_set_locks_are_taken_in_path_key_order_and_released(led: Ledger) -> None:
    locks = SpyLocks(DarwinLocks(str(led.ald / "locks")))
    plan = plan_for(led)
    out = sv.run(plan, ports_for(led, locks=locks), FakeJob())
    assert isinstance(out, sv.Saved)
    fs = for_this_os()
    want = sorted({sv._key(fs, p) for p, _ in plan.fingerprint.files})  # pyright: ignore[reportPrivateUsage]
    assert locks.tried == want
    assert len(want) == 2
    assert_locks_free(led, plan)


def test_a_held_ledger_set_lock_is_waited_for_with_its_phase_shown(led: Ledger) -> None:
    key = sv._key(for_this_os(), str(led.main))  # pyright: ignore[reportPrivateUsage]
    holder = DarwinLocks(str(led.ald / "locks")).lock(key)
    released = threading.Event()

    def release_later() -> None:
        released.wait()
        holder.release()

    t = threading.Thread(target=release_later)
    t.start()
    locks = SpyLocks(DarwinLocks(str(led.ald / "locks")))
    held_at_the_gate: list[bool] = []

    def probe() -> None:
        other = DarwinLocks(str(led.ald / "locks")).try_lock(key)
        held_at_the_gate.append(other is None)
        if other is not None:
            other.release()

    job = FakeJob(on_progress=lambda phase: released.set() if phase == "waiting_for_files" else None)
    out = sv.run(plan_for(led), ports_for(led, locks=locks, observe=hooked(before=probe)), job)
    t.join()
    assert isinstance(out, sv.Saved), out
    assert job.events[0] == "waiting_for_files"
    assert locks.waited == [key]  # it waited for that lock, and only that one
    assert held_at_the_gate == [True]  # and held it at the gate


def _legacy_root(led: Ledger) -> Path:
    data = led.tmp / "legacy" / "data"
    data.mkdir(parents=True)
    lock = data / ".write.lock"
    lock.write_bytes(b"")
    return lock


HOLDER = """
import sys, portalocker
fh = open(sys.argv[1], "a")
portalocker.lock(fh, portalocker.LOCK_EX)
print("held", flush=True)
sys.stdin.readline()
"""
TRY = """
import sys, portalocker
fh = open(sys.argv[1], "rb")
try:
    portalocker.lock(fh, getattr(portalocker, sys.argv[2]) | portalocker.LOCK_NB)
except portalocker.AlreadyLocked:
    print("held")
else:
    print("free")
"""


@contextmanager
def legacy_app_saving(lock: Path) -> Generator[None]:
    """The legacy app saving: another process holds its write lock as v0.2.2/v0.3.0 do (portalocker 3.2.0,
    `portalocker.lock(fp, LOCK_EX)` in its `write_lock.py`; ADR-0062)."""
    proc = subprocess.Popen(  # noqa: S603 (our own interpreter and script)
        [sys.executable, "-c", HOLDER, str(lock)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "held"
        yield
    finally:
        assert proc.stdin is not None
        proc.stdin.write("\n")
        proc.stdin.close()
        proc.wait()


def legacy_state(lock: Path, kind: str = "LOCK_EX") -> str:
    """Whether another process could take the legacy lock now, with portalocker 3.2.0 (exclusive, or shared)."""
    return subprocess.run(  # noqa: S603 (our own interpreter and script)
        [sys.executable, "-c", TRY, str(lock), kind], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_a_held_legacy_lock_refuses_before_any_ledger_set_lock_and_writes_nothing(led: Ledger) -> None:
    lock = _legacy_root(led)
    locks = SpyLocks(DarwinLocks(str(led.ald / "locks")))
    plan = plan_for(led, legacy_locks=(str(lock),))
    before = led.tree()
    with legacy_app_saving(lock):
        out = sv.run(plan, ports_for(led, locks=locks), FakeJob())
    assert out == sv.Refused("legacy_app_saving", unchanged=True, path=str(lock))
    assert locks.tried == []
    assert_nothing_written(led, before, out)
    assert lock.read_bytes() == b""


def test_a_free_legacy_lock_is_held_until_the_save_ends_and_never_written(led: Ledger) -> None:
    lock = _legacy_root(led)
    seen: list[str] = []
    job = FakeJob(on_gate=lambda: seen.extend((legacy_state(lock), legacy_state(lock, "LOCK_SH"))))
    out = sv.run(plan_for(led, legacy_locks=(str(lock),)), ports_for(led), job)
    assert isinstance(out, sv.Saved), out
    assert seen == ["held", "held"]  # held exclusively: the old app could not start a save half-way through ours
    assert legacy_state(lock) == "free"
    assert lock.read_bytes() == b""


def test_an_absent_legacy_lock_lets_the_save_go_on_and_is_never_created(led: Ledger) -> None:
    lock = led.tmp / "legacy" / "data" / ".write.lock"
    lock.parent.mkdir(parents=True)
    out = sv.run(plan_for(led, legacy_locks=(str(lock),)), ports_for(led), FakeJob())
    assert isinstance(out, sv.Saved), out
    assert os.listdir(lock.parent) == []


@pytest.mark.parametrize("kind", ["folder", "fifo", "link", "unreadable"])
def test_a_legacy_lock_that_cannot_be_tried_refuses(led: Ledger, kind: str) -> None:
    lock = led.tmp / "legacy" / "data" / ".write.lock"
    lock.parent.mkdir(parents=True)
    match kind:
        case "folder":
            lock.mkdir()
        case "fifo":
            os.mkfifo(lock)
        case "link":
            (lock.parent / "real").write_bytes(b"")
            lock.symlink_to(lock.parent / "real")
        case _:
            lock.write_bytes(b"")
            lock.chmod(0o000)
    before = led.tree() if kind != "unreadable" else None
    try:
        out = sv.run(plan_for(led, legacy_locks=(str(lock),)), ports_for(led), FakeJob())
    finally:
        if kind == "unreadable":
            lock.chmod(0o600)
    assert out == sv.Refused("legacy_app_saving", unchanged=True, path=str(lock))
    if before is not None:
        assert led.tree() == before


def test_one_legacy_lock_named_twice_is_tried_once(led: Ledger) -> None:
    """Deduplicated by `path_key`: the scratch volume is case-insensitive APFS, so two spellings are one file."""
    lock = _legacy_root(led)
    other = str(lock.parent.parent / "DATA" / ".write.lock")
    legacy = SpyLegacy(DarwinLegacyLocks())
    out = sv.run(plan_for(led, legacy_locks=(str(lock), other)), ports_for(led, legacy=legacy), FakeJob())
    assert isinstance(out, sv.Saved), out
    assert len(legacy.tried) == 1


# -- Cancel, the writer's refusals, a bad plan ----------------------------------------------------------------------


def test_a_cancel_at_the_gate_writes_nothing_and_releases_every_lock(led: Ledger) -> None:
    lock = _legacy_root(led)
    plan = plan_for(led, legacy_locks=(str(lock),))
    before = led.tree()
    with pytest.raises(Cancelled):
        sv.run(plan, ports_for(led), FakeJob(cancel=True))
    assert_nothing_written(led, before, None)
    assert_locks_free(led, plan)
    assert legacy_state(lock) == "free"


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


@dataclass
class FailingLocks:
    inner: Locks
    fails_on: str  # the ledger file whose lock cannot be taken

    def try_lock(self, key: str) -> Lock | None:
        if key == sv._key(for_this_os(), self.fails_on):  # pyright: ignore[reportPrivateUsage]
            raise PermissionError(13, "Permission denied")
        return self.inner.try_lock(key)

    def lock(self, key: str) -> Lock:
        return self.inner.lock(key)


def test_a_ledger_set_lock_that_cannot_be_taken_is_not_saved_and_names_its_file(led: Ledger) -> None:
    """Red team r1 L2: an I/O error taking a lock is "not saved", with the re-hash, as a name that cannot be resolved
    is (spec §11's rule), never an internal error."""
    before = led.tree()
    plan = plan_for(led)
    out = sv.run(plan, ports_for(led, locks=FailingLocks(DarwinLocks(str(led.ald / "locks")), str(led.inc))), FakeJob())
    assert isinstance(out, NotSaved), out
    assert (out.reason, out.path, out.unchanged, out.open_journal) == (Reason.IO_ERROR, str(led.inc), True, False)
    assert_nothing_written(led, before, out)
    assert_locks_free(led, plan)
