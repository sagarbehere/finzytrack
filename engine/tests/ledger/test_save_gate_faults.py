"""INV-22 at the save's gate (ADR-0008 step 4, `ledger.save._gated`; engine-jobs §6.1 step 4; BL-0022, S0156): a fault
injected into any of the gate's calls, before the commit point, refuses the save with nothing written.

The gate's calls, in order: step 4's observation of the disk (`Ports.observe`), the published generation's read
(`publish.read_published`, through the store's `meta`), the freshness check's observation of that generation's ledger
set (`Ports.observe` again, inside `publish.fresh_on_disk`), and the `.open` re-check (`save.records`). A fault there
either becomes a decided refusal (an `OSError` where the call states one: `mirror_stale`, `unfinished_save`) or
escapes `save.run`, which the engine's request boundary answers `engine.internal` (`protocol.server`), and the
sidecar's scheduler ends the job `INTERNAL` with no `gate` seen (`app.workspaces.writes`). Either way the job never
sends `gate`, so it never reaches the commit point, and the disk is as it was: no effect at all, journal, backups and
ledger included.

Over the fake (`tests/fakes/memfs.py`) with the save world of `test_save_outcome.py`, so no test here skips on any OS
(the pre-push run proof, PROCESS.md §6). Each test is a `@regression-guard`: the gate failed closed on the base
(0e68df4a) too; each kills a fail-open mutant of `_gated` seeded in S0156 (its journal's Hand-off).
"""

from __future__ import annotations

import errno
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import override

import pytest

from ledger_engine.fs.port import DirHandle
from ledger_engine.ledger import save as sv
from ledger_engine.ledger.core.ledger_set import Fingerprint
from ledger_engine.mirror import publish
from tests.fakes.memfs import MemFS
from tests.ledger.test_save_outcome import ALD, CHAIN, A, B, app_id, born_of, save_world, two_file_plan
from tests.mirror.fake_store import FakeStore

pytestmark = [pytest.mark.inv("INV-22"), pytest.mark.inv("INV-01")]


class GateJob:
    """`save.Job`, recording whether the save asked to pass its commit point."""

    def __init__(self) -> None:
        self.gated = 0
        self.commits = 0

    def progress(self, phase: str) -> None: ...

    def gate(self) -> None:
        self.gated += 1

    def committed(self) -> None:
        self.commits += 1


def _eio() -> OSError:
    return OSError(errno.EIO, "injected I/O error")


def _bug() -> Exception:
    return RuntimeError("injected fault")


@dataclass(frozen=True)
class Fault:
    """Where the fault is injected, what it raises, and what the save must answer: the exception type it lets escape,
    or the refusal (`Refused`) it decides."""

    at: str
    make: Callable[[], Exception]
    escapes: type[BaseException] | None = None
    refused: sv.Refused | None = None


def _observe_faulting(n: int, make: Callable[[], Exception]) -> Callable[[Fingerprint], Fingerprint]:
    """`Ports.observe` that answers the disk as the plan saw it, except at its `n`th call (1: step 4's observation;
    2: the freshness check's), which raises."""
    calls = 0

    def observe(fp: Fingerprint) -> Fingerprint:
        nonlocal calls
        calls += 1
        if calls == n:
            raise make()
        return fp

    return observe


class _FaultingStore(FakeStore):
    """The fake mirror store whose `meta` read raises (the published generation's read)."""

    def __init__(self, make: Callable[[], Exception]) -> None:
        super().__init__()
        self._make = make

    @override
    def meta(self, d: DirHandle, name: str) -> publish.Meta | None:
        raise self._make()


def _inject(fault: Fault, fs: MemFS, monkeypatch: pytest.MonkeyPatch) -> sv.Ports:
    observe: Callable[[Fingerprint], Fingerprint] = lambda fp: fp  # noqa: E731 (the disk is as the plan saw it)
    store: publish.Store = FakeStore()
    if fault.at.startswith("observe_step4"):
        observe = _observe_faulting(1, fault.make)
    elif fault.at.startswith("observe_freshness"):
        observe = _observe_faulting(2, fault.make)
    elif fault.at.startswith("store_meta"):
        store = _FaultingStore(fault.make)
    elif fault.at == "read_published_bug":

        def read_published(*_: object) -> None:
            raise fault.make()

        monkeypatch.setattr(publish, "read_published", read_published)
    elif fault.at.startswith("open_recheck"):
        real = sv.records
        calls = 0

        def records(fs_: MemFS, app_local_data: str, workspace_key: str, lineage: str) -> str | None:
            nonlocal calls
            calls += 1
            if calls == 2:  # the first is step 1's look; the second the gate's re-check, just before `gate()`
                raise fault.make()
            return real(fs_, app_local_data, workspace_key, lineage)

        monkeypatch.setattr(sv, "records", records)
    else:
        raise AssertionError(fault.at)
    return sv.Ports(fs, observe=observe, store=store)


def _world() -> MemFS:
    """The save world: `a.bean` and `b.bean` at their pre bytes, a fresh published mirror, the app's `local/` made."""
    fs = save_world(CHAIN[0])
    with fs.open_dir(ALD) as h:
        h.sync()
    return fs


@pytest.mark.parametrize(
    "fault",
    [
        Fault("observe_step4_oserror", _eio, escapes=OSError),
        Fault("observe_step4_bug", _bug, escapes=RuntimeError),
        Fault("observe_freshness_oserror", _eio, refused=sv.Refused("mirror_stale", True)),
        Fault("observe_freshness_bug", _bug, escapes=RuntimeError),
        Fault("store_meta_oserror", _eio, refused=sv.Refused("mirror_stale", True)),
        Fault("store_meta_database_error", lambda: sqlite3.DatabaseError("injected"), escapes=sqlite3.DatabaseError),
        Fault("read_published_bug", _bug, escapes=RuntimeError),
        Fault("open_recheck_oserror", _eio, refused=sv.Refused("unfinished_save", True, f"{ALD}/local/k1/journal")),
        Fault("open_recheck_bug", _bug, escapes=RuntimeError),
    ],
    ids=lambda f: f.at,
)
def test_a_fault_at_the_gate_refuses_before_the_commit_point_with_nothing_written(
    fault: Fault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """@regression-guard (BL-0022: the gate failed closed on the base; pinned for INV-22). Must not pass a gate that
    swallows the fault and goes on: it would send `gate`, and the save would write its backups, journal and files."""
    fs = _world()
    before, effects = fs.snapshot(), len(fs.effects)
    plan = two_file_plan(fs, app_folder=(*app_id(fs), born_of(fs, ALD)))
    ports = _inject(fault, fs, monkeypatch)
    job = GateJob()
    if fault.escapes is not None:
        with pytest.raises(fault.escapes):
            sv.run(plan, ports, job)
    else:
        assert sv.run(plan, ports, job) == fault.refused
    assert job.gated == 0, "the save asked to pass its commit point after a fault at its gate"
    assert job.commits == 0
    assert fs.effects[effects:] == [], f"the save wrote after a fault at its gate: {fs.effects[effects:]}"
    assert fs.snapshot() == before
    assert (fs.file("/L/a.bean"), fs.file("/L/b.bean")) == (A, B)


def test_the_same_world_with_no_fault_passes_the_gate_and_lands() -> None:
    """@regression-guard: the companion of the fault tests, so their world is one the gate passes (a fault test that
    refused for another reason would prove nothing)."""
    fs = _world()
    plan = two_file_plan(fs, app_folder=(*app_id(fs), born_of(fs, ALD)))
    job = GateJob()
    out = sv.run(plan, sv.Ports(fs, observe=lambda fp: fp, store=FakeStore()), job)
    assert isinstance(out, sv.Saved), out
    assert (job.gated, job.commits) == (1, 1)
    assert fs.file("/L/a.bean") == b"a post\n"
