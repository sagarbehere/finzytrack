"""The rebuild after a save, the sidecar's side (`app.platform.engine_jobs.rebuild`, `save`;
`design/specs/engine-jobs.md` §2, §3, §4, §5.1, §5.3, §6.1 step 10, §6.6, §6.7, §7; ADR-0068 D1; S0070), over the fake
engine port with a real
scheduler. Written from the spec's lines:

- §6.1 step 10: "after the save has landed, the sidecar queues a background rebuild of the mirror with Beancount's
  real loader"; §6.6: a save read from disk as saved has its rebuild queued too; a save that did not land has none;
- §7: "A moved fingerprint discards the result and runs the job again, at most 3 times in a row";
- §5.3: a rebuild's engine death is not a stop toward the bound (ADR-0068 D1; the step-back area review F2), tells
  no one "saved" or "not saved", and owes nothing; §5.1: each try after the first gets a fresh engine;
- §3: at most one background rebuild waits per workspace; §4: another job's Cancel may end a running one.
R4 is gone: no `r4_check`, no notice, no `r4.*` log line.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.platform.engine import EngineRefusal
from app.platform.engine_jobs.rebuild import (
    MAX_TRIES,
    Loaded,
    NotLoaded,
    RebuildTarget,
    rebuild_spec,
    rebuild_work,
)
from app.platform.engine_jobs.save import (
    ChangeText,
    NotDone,
    Saved,
    SaveRequest,
    SaveValue,
    save_spec,
    save_work,
    submit_save,
)
from app.platform.engine_jobs.scheduler import Done, EngineJobs, Failed, JobTicket
from app.platform.errors import ErrorCode
from app.platform.observability import Event as LogEvent
from tests.platform.fake_job_engine import BLOCK, CRASH, CRASH_TO_BOUND, GATE_DIE, FakeEngine, Gated

DEADLINE = 20.0
W1 = "ws-1"
MAIN = "/L/main.bean"
ALD = Path("/app")
FP = '{"document_files":[],"documents":[],"files":[],"globs":[],"root":"/L/main.bean","v":1}'
FP2 = (
    '{"document_files":[],"documents":[],"files":[["/L/main.bean",["absent"]]],"globs":[],"root":"/L/main.bean","v":1}'
)
TARGET = RebuildTarget("k1", MAIN, FP)
REQUEST = SaveRequest(
    session="a1b2",
    workspace_key="k1",
    lineage="lin1",
    root=MAIN,
    changes=(ChangeText(MAIN, "0" * 64, "post\n"),),
    fingerprint=FP,
    mirror=None,
    legacy_locks=(),
    app_local_data=ALD,
)
BLANK: dict[str, object] = {
    "outcome": "saved",
    "reason": None,
    "path": None,
    "entry": None,
    "detail": None,
    "unchanged": False,
    "open_journal": False,
    "cause": None,
    "cause_path": None,
    "published": False,
}


def answer(**kw: object) -> dict[str, object]:
    return {**BLANK, **kw}


def loaded(entries: int = 7, errors: int = 0, fingerprint: str = FP) -> dict[str, object]:
    return {"outcome": "loaded", "fingerprint": fingerprint, "entries": entries, "errors": errors}


def moved(fingerprint: str = FP2) -> dict[str, object]:
    return {"outcome": "moved", "fingerprint": fingerprint, "entries": 0, "errors": 0}


class Rig:
    def __init__(self, scripted: dict[str, list[object]]) -> None:
        self.engine = FakeEngine(scripted=scripted)
        self.logged: list[tuple[str, dict[str, object]]] = []
        self.jobs = EngineJobs(self.engine, wait_engine_lock=lambda: None, log=self._sink)
        self.engine.spawned = self.jobs.engine_spawned
        self.engine.ended = self.jobs.engine_ended
        boot = self.jobs.start()
        assert isinstance(boot, JobTicket)
        assert boot.wait(DEADLINE) is not None
        self.jobs.workspace_opened(W1)

    def _sink(self, event: LogEvent, /, **fields: object) -> None:
        self.logged.append((event.name, fields))

    def lines(self) -> list[tuple[str, dict[str, object]]]:
        return [(n, f) for n, f in self.logged if n.startswith("rebuild.")]

    def run(self, ticket: JobTicket | Failed) -> object:
        assert isinstance(ticket, JobTicket), ticket
        out = ticket.wait(DEADLINE)
        assert out is not None, "the job never ended"
        return out

    def save(self) -> SaveValue:
        out = self.run(self.jobs.submit(save_spec(W1), save_work(REQUEST)))
        assert isinstance(out, Done), out
        assert isinstance(out.value, Saved | NotDone), out.value
        return out.value

    def ops(self) -> list[str]:
        return [op for _, op in self.engine.calls]

    def serials(self) -> list[tuple[int, str]]:
        return list(self.engine.calls)

    def called(self, op: str) -> None:
        """Wait until the engine got `op` (the fake notifies its condition on every call; EP-28)."""
        cond = self.engine._cond  # pyright: ignore[reportPrivateUsage]
        with cond:
            assert cond.wait_for(lambda: op in self.ops(), DEADLINE), f"{op} was never called"

    def rebuild_queued(self) -> bool:
        q = self.jobs.status().queue
        return any(j.kind == "rebuild" for j in q.queued) or (q.running is not None and q.running.job.kind == "rebuild")


@pytest.fixture
def make() -> Iterator[Any]:
    rigs: list[Rig] = []

    def build(scripted: dict[str, list[object]]) -> Rig:
        rigs.append(Rig(scripted))
        return rigs[-1]

    yield build
    for r in rigs:
        r.jobs.close()


def background(rig: Any, target: RebuildTarget = TARGET) -> object:
    out = rig.run(rig.jobs.submit(rebuild_spec(W1), rebuild_work(target)))
    assert isinstance(out, Done), out
    return out.value


# -- the rebuild a landed save queues (§6.1 step 10) -----------------------------------------------------------------


def test_a_landed_save_queues_a_background_rebuild_of_its_ledger(make: Any) -> None:
    rig = make({"save": [Gated(answer())], "rebuild": [loaded()]})
    out = rig.run(submit_save(rig.jobs, W1, REQUEST))
    assert isinstance(out, Done), out
    assert out.value == Saved(published=False)
    rig.called("rebuild")
    assert rig.engine.params[1][1:] == ("rebuild", {"root": MAIN, "fingerprint": FP})
    assert rig.ops() == ["save", "rebuild"]  # no `r4_check` (ADR-0068 D1)


def test_the_rebuild_is_background_unconfirmed_and_the_apps() -> None:
    spec = rebuild_spec(W1)
    assert (spec.kind, spec.waited_for, spec.confirmed, spec.owner) == ("rebuild", False, False, "app")


def test_a_save_that_did_not_land_queues_no_rebuild(make: Any) -> None:
    rig = make({"save": [answer(outcome="refused", reason="ledger_changed")]})
    rig.run(submit_save(rig.jobs, W1, REQUEST))
    assert not rig.rebuild_queued()  # queued, if at all, before the save's work returned
    assert rig.ops() == ["save"]


def test_a_save_read_from_disk_as_saved_queues_its_rebuild_too(make: Any) -> None:
    facts = {"record": "closed", "unchanged": None, "path": None, "errno": None}
    rig = make({"save": [GATE_DIE], "save_outcome": [facts], "rebuild": [loaded()]})
    out = rig.run(submit_save(rig.jobs, W1, REQUEST))
    assert isinstance(out, Done), out
    assert out.value == Saved(published=False)
    rig.called("rebuild")


def test_a_save_read_from_disk_as_not_saved_queues_none(make: Any) -> None:
    facts = {"record": "none", "unchanged": True, "path": None, "errno": None}
    rig = make({"save": [GATE_DIE], "save_outcome": [facts]})
    out = rig.run(submit_save(rig.jobs, W1, REQUEST))
    assert isinstance(out, Done), out
    assert isinstance(out.value, NotDone)
    assert not rig.rebuild_queued()


def test_one_background_rebuild_waits_per_workspace(make: Any) -> None:
    """§3, INV-10: a newer save's rebuild finds the queued one and gets its ticket; the queued one loads the disk
    when it runs, so it shows the newer save too."""
    rig = make({"rebuild": [BLOCK, loaded(), loaded()]})
    first = rig.jobs.submit(rebuild_spec(W1), rebuild_work(TARGET))  # running, blocked
    assert isinstance(first, JobTicket)
    rig.called("rebuild")
    older = rig.jobs.submit(rebuild_spec(W1), rebuild_work(TARGET))
    newer = rig.jobs.submit(rebuild_spec(W1), rebuild_work(RebuildTarget("k1", MAIN, FP2)))
    assert isinstance(older, JobTicket)
    assert isinstance(newer, JobTicket)
    assert newer.id == older.id
    rig.engine.release("rebuild")
    rig.run(first)
    rig.run(newer)
    assert rig.ops() == ["rebuild", "rebuild"]


# -- the tries (§7) ----------------------------------------------------------------------------------------------------


def test_a_loaded_rebuild_is_one_load_and_logs_counts_only(make: Any) -> None:
    rig = make({"rebuild": [loaded(entries=9, errors=2)]})
    assert background(rig) == Loaded(9, 2, 1)
    ((name, fields),) = rig.lines()
    assert name == "rebuild.loaded"
    assert {k: v for k, v in fields.items() if k != "job"} == {
        "workspace_key": "k1",
        "entries": 9,
        "errors": 2,
        "tries": 1,
    }
    assert set(fields) == {"job", "workspace_key", "entries", "errors", "tries"}  # nothing of the ledger (INV-23)


def test_a_moved_disk_tries_again_with_the_answers_fingerprint_in_a_fresh_engine(make: Any) -> None:
    rig = make({"rebuild": [moved(FP2), loaded()]})
    assert background(rig) == Loaded(7, 0, 2)
    (s1, _, p1), (s2, _, p2) = rig.engine.params
    assert (p1["fingerprint"], p2["fingerprint"]) == (FP, FP2)
    assert s2 != s1  # §5.1: the first try ran user code, the second gets a fresh engine


def test_a_disk_that_keeps_moving_stops_after_three_tries_and_is_logged(make: Any) -> None:
    rig = make({"rebuild": [moved(), moved(), moved(), loaded()]})
    assert MAX_TRIES == 3
    assert background(rig) == NotLoaded("changing")
    assert rig.ops() == ["rebuild"] * 3
    assert [(n, f["why"]) for n, f in rig.lines()] == [("rebuild.not_loaded", "changing")]


def test_every_try_is_sent_uncounted(make: Any) -> None:
    """§5.3 (ADR-0068 D1, area review F2): a rebuild's engine death is no stop toward the bound."""
    rig = make({"rebuild": [moved(), loaded()]})
    background(rig)
    assert rig.engine.uncounted == ["rebuild", "rebuild"]


def test_a_save_is_sent_counted(make: Any) -> None:
    rig = make({"save": [Gated(answer())]})
    rig.save()
    assert rig.engine.uncounted == []


# -- a rebuild that does not load: logged, nothing owed (§5.3, §6.7) --------------------------------------------------


@pytest.mark.parametrize("death", [CRASH, CRASH_TO_BOUND])
def test_a_death_of_the_rebuilds_engine_is_lost_like_a_read(make: Any, death: str) -> None:
    """The queue loses it (`EngineLost`, §5.3): no outcome read from disk, nobody told "saved" or "not saved"."""
    rig = make({"rebuild": [death]})
    out = rig.run(rig.jobs.submit(rebuild_spec(W1), rebuild_work(TARGET)))
    assert isinstance(out, Failed), out
    assert out.error.code == ErrorCode.ENGINE_STOPPED
    assert rig.ops() == ["rebuild"]
    assert "save_outcome" not in rig.ops()


def test_a_refusal_from_the_engine_is_logged_as_not_loaded(make: Any) -> None:
    rig = make({"rebuild": [EngineRefusal("engine.internal", "an encrypted file met")]})
    assert background(rig) == NotLoaded("refused")
    assert [(n, f["why"]) for n, f in rig.lines()] == [("rebuild.not_loaded", "refused")]


def test_a_reply_of_the_wrong_shape_is_not_loaded(make: Any) -> None:
    rig = make({"rebuild": [{"outcome": "pass", "loaded": True}]})  # R4's old answer
    assert background(rig) == NotLoaded("violation")


def test_the_background_rebuild_is_ended_by_another_jobs_cancel(make: Any) -> None:
    """§4: nobody waits for it and nobody confirmed it, so a waiting user's Cancel of a queued job ends it."""
    rig = make({"rebuild": [BLOCK, loaded()], "save": [Gated(answer())]})
    bg = rig.jobs.submit(rebuild_spec(W1), rebuild_work(TARGET))
    assert isinstance(bg, JobTicket)
    rig.called("rebuild")
    waiting = rig.jobs.submit(save_spec(W1), save_work(REQUEST))
    assert isinstance(waiting, JobTicket)
    rig.jobs.cancel(waiting.id)
    rig.run(bg)
    assert rig.ops() == ["rebuild"]
    assert rig.lines() == []  # a Cancel logs nothing


def test_a_save_waits_for_a_running_rebuild(make: Any) -> None:
    """§3: a running job is never interrupted; the save asked for while the previous save's rebuild runs waits."""
    rig = make({"rebuild": [BLOCK, loaded()], "save": [Gated(answer())]})
    bg = rig.jobs.submit(rebuild_spec(W1), rebuild_work(TARGET))
    assert isinstance(bg, JobTicket)
    rig.called("rebuild")
    t = rig.jobs.submit(save_spec(W1), save_work(REQUEST))
    assert isinstance(t, JobTicket)
    q = rig.jobs.status().queue
    assert q.running is not None
    assert q.running.job.kind == "rebuild"
    assert [j.kind for j in q.queued] == ["save"]
    rig.engine.release("rebuild")
    assert isinstance(rig.run(t), Done)
    assert rig.ops() == ["rebuild", "save"]
