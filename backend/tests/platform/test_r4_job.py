"""R4, the sidecar's side (`app.platform.engine_jobs.r4`, `save`, `save_text`; `design/specs/engine-jobs.md` §4,
§5.1, §5.3, §6.1 step 11, §6.7, §11; ADR-0067 decision 3; S0066, S0068), over the fake engine port with a real
scheduler. Written from the spec's lines:

- §6.1 step 11: "queue the `r4` as background work"; a landed save's R4 gates nothing (ADR-0067): "saving continues";
- §6.7: one `r4_check`, no confirm; "on a difference it logs and shows a short notice"; a failure to run, a death or a
  Cancel shows nothing and counts nothing; at most one R4 waits per workspace, the newest save's;
- §11's R4 row: the notice for a difference.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.platform.engine_jobs import save_text
from app.platform.engine_jobs.r4 import (
    Checked,
    NotChecked,
    R4Target,
    notice_of,
    r4_spec,
    r4_work,
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
from tests.platform.fake_job_engine import BLOCK, CRASH, CRASH_TO_BOUND, FakeEngine, Gated

DEADLINE = 20.0
W1 = "ws-1"
MAIN = "/L/main.bean"
ALD = Path("/app")
TARGET = R4Target("k1", "s0-j0")
REQUEST = SaveRequest(
    session="a1b2",
    workspace_key="k1",
    lineage="lin1",
    root=MAIN,
    changes=(ChangeText(MAIN, "0" * 64, "post\n"),),
    fingerprint='{"document_files":[],"documents":[],"files":[],"globs":[],"root":"/L/main.bean","v":1}',
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


def r4(outcome: str, *, loaded: bool = True) -> dict[str, object]:
    return {"outcome": outcome, "loaded": loaded}


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

    def r4_lines(self) -> list[tuple[str, dict[str, object]]]:
        return [(n, f) for n, f in self.logged if n.startswith("r4.")]

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

    def r4_queued(self) -> bool:
        q = self.jobs.status().queue
        return any(j.kind == "r4" for j in q.queued) or (q.running is not None and q.running.job.kind == "r4")


@pytest.fixture
def make() -> Iterator[Any]:
    rigs: list[Rig] = []

    def build(scripted: dict[str, list[object]]) -> Rig:
        rigs.append(Rig(scripted))
        return rigs[-1]

    yield build
    for r in rigs:
        r.jobs.close()


def background(rig: Any, target: R4Target = TARGET) -> object:
    out = rig.run(rig.jobs.submit(r4_spec(W1), r4_work(ALD, target)))
    assert isinstance(out, Done), out
    return out.value


# -- R4 gates nothing ------------------------------------------------------------------------------------------------


def test_after_a_difference_the_next_save_runs_with_no_r4_inside_it(make: Any) -> None:
    """ADR-0067 decision 3 (S0068): a save after an R4 that found a difference is one `save` request: no pause, no R4
    run first (until S0068: `WRITES_PAUSED {check_failed}`, or the owed R4 inside the save)."""
    rig = make({"save": [Gated(answer()), Gated(answer())], "r4_check": [r4("differs")]})
    first = rig.run(submit_save(rig.jobs, W1, REQUEST))
    assert isinstance(first, Done), first
    rig.called("r4_check")
    assert rig.save() == Saved(published=False)
    assert rig.ops() == ["save", "r4_check", "save"]


def test_a_save_is_one_request_whatever_records_stand(make: Any) -> None:
    """S0068: the save never asks for R4 first; the engine's answer is the save's value."""
    rig = make({"save": [Gated(answer())]})
    assert rig.save() == Saved(published=False)
    assert rig.ops() == ["save"]


# -- the R4 a landed save queues (§6.1 step 11) ------------------------------------------------------------------


def test_a_landed_save_queues_its_own_r4(make: Any) -> None:
    rig = make({"save": [Gated(answer())], "r4_check": [r4("pass")]})
    out = rig.run(submit_save(rig.jobs, W1, REQUEST))
    assert isinstance(out, Done), out
    assert out.value == Saved(published=False)
    rig.called("r4_check")
    save_params, r4_params = rig.engine.params[0][2], rig.engine.params[1][2]
    assert r4_params == {"app_local_data": "/app", "key": "k1", "name": f"a1b2-{save_params['job']}"}


def test_a_save_that_did_not_land_queues_no_r4(make: Any) -> None:
    rig = make({"save": [answer(outcome="refused", reason="ledger_changed")]})
    rig.run(submit_save(rig.jobs, W1, REQUEST))
    assert not rig.r4_queued()  # queued, if at all, before the save's work returned
    assert rig.ops() == ["save"]


def test_a_save_read_from_disk_as_saved_queues_its_r4_too(make: Any) -> None:
    from tests.platform.fake_job_engine import GATE_DIE

    facts = {"record": "closed", "unchanged": None, "path": None, "errno": None}
    rig = make({"save": [GATE_DIE], "save_outcome": [facts], "r4_check": [r4("pass")]})
    out = rig.run(submit_save(rig.jobs, W1, REQUEST))
    assert isinstance(out, Done), out
    assert out.value == Saved(published=False)
    rig.called("r4_check")


def test_a_newer_saves_r4_takes_the_queued_ones_place(make: Any) -> None:
    """S0068: one R4 waits per workspace (INV-10), and it is the newest save's: an older save's R4 would only find
    the disk moved by the newer save (`superseded`), leaving the newest save unchecked (the old gate caught it at the
    next save's step 1; nothing does now)."""
    rig = make({"r4_check": [BLOCK, r4("pass"), r4("pass")]})
    first = rig.jobs.submit(r4_spec(W1), r4_work(ALD, R4Target("k1", "s0-j0")))  # running, blocked
    assert isinstance(first, JobTicket)
    rig.called("r4_check")
    older = rig.jobs.submit(r4_spec(W1), r4_work(ALD, R4Target("k1", "s0-j1")))
    newer = rig.jobs.submit(r4_spec(W1), r4_work(ALD, R4Target("k1", "s0-j2")), replace_queued=True)
    assert isinstance(older, JobTicket)
    assert isinstance(newer, JobTicket)
    assert newer.id == older.id  # one queued R4
    rig.engine.release("r4_check")
    rig.run(first)
    rig.run(newer)
    names = [p[2]["name"] for p in rig.engine.params if p[1] == "r4_check"]
    assert names == ["s0-j0", "s0-j2"]


# -- the outcomes and the notice (§6.7, §11) -----------------------------------------------------------------------


@pytest.mark.parametrize("outcome", ["pass", "differs", "superseded", "gone", "unreadable"])
def test_the_background_r4_is_one_check_and_reports_its_outcome(make: Any, outcome: str) -> None:
    rig = make({"r4_check": [r4(outcome)]})
    value = background(rig)
    assert value == Checked(outcome)  # pyright: ignore[reportArgumentType]
    assert rig.ops() == ["r4_check"]  # no confirm, no count (S0068)


def test_only_a_difference_has_a_notice() -> None:
    """§11 (S0068): the notice is said for `differs` only, and says the change is saved."""
    assert notice_of(Checked("differs")) == save_text.R4_DIFFERS
    # S0068 verifier r1 F6: the one cause every file-creating save under a documents root gives is named (§15)
    assert "a new file in a documents folder" in save_text.R4_DIFFERS
    assert save_text.R4_DIFFERS.startswith("Your change is saved.")
    for other in ("pass", "superseded", "gone", "unreadable"):
        assert notice_of(Checked(other)) is None
    for why in ("engine_failed", "refused", "violation"):
        assert notice_of(NotChecked(why)) is None


@pytest.mark.parametrize("death", [CRASH, CRASH_TO_BOUND])
def test_a_death_of_r4s_engine_counts_nothing_and_shows_nothing(make: Any, death: str) -> None:
    """S0068: the queue loses an R4 whose engine died, as a read (`EngineLost`, §5.3): no fresh engine, no
    `r4_attempt`, no notice (until S0068 the job was kept to count the failure on disk)."""
    rig = make({"r4_check": [death]})
    out = rig.run(rig.jobs.submit(r4_spec(W1), r4_work(ALD, TARGET)))
    assert isinstance(out, Failed), out
    assert out.error.code == ErrorCode.ENGINE_STOPPED
    assert rig.ops() == ["r4_check"]


def test_a_refusal_from_the_engine_is_not_checked(make: Any) -> None:
    from app.platform.engine import EngineRefusal

    rig = make({"r4_check": [EngineRefusal("engine.internal", "an encrypted file met on the saved disk")]})
    assert background(rig) == NotChecked("refused")


def test_the_background_r4_ended_by_another_jobs_cancel_counts_nothing(make: Any) -> None:
    rig = make({"r4_check": [BLOCK, r4("pass")], "save": [Gated(answer())]})
    bg = rig.jobs.submit(r4_spec(W1), r4_work(ALD, TARGET))
    assert isinstance(bg, JobTicket)
    rig.called("r4_check")
    waiting = rig.jobs.submit(save_spec(W1), save_work(REQUEST))
    assert isinstance(waiting, JobTicket)
    rig.jobs.cancel(waiting.id)  # §4: a waiting user's Cancel ends a running R4 (nobody waits for it)
    rig.run(bg)
    assert rig.ops() == ["r4_check"]


def test_a_save_value_never_carries_r4(make: Any) -> None:
    """S0068: `Saved` has no `nondeterministic` (R4 never runs inside a save)."""
    rig = make({"save": [Gated(answer())]})
    value = rig.save()
    assert isinstance(value, Saved | NotDone)
    assert not hasattr(value, "nondeterministic")


# -- R4's result is logged (S0068 verifier r1 F1; ADR-0067 decision 3, §6.7) -------------------------------------------


def test_a_difference_is_logged_with_the_job_the_workspace_key_and_the_record_only(make: Any) -> None:
    rig = make({"r4_check": [r4("differs")]})
    assert background(rig) == Checked("differs")
    ((name, fields),) = rig.r4_lines()
    assert name == "r4.differs"
    assert fields["workspace_key"] == TARGET.key
    assert fields["record"] == TARGET.name
    assert set(fields) == {"job", "workspace_key", "record"}  # nothing of the ledger (INV-23)


@pytest.mark.parametrize("outcome", ["superseded", "gone", "unreadable"])
def test_an_outcome_that_compared_nothing_is_logged_as_not_compared(make: Any, outcome: str) -> None:
    rig = make({"r4_check": [r4(outcome)]})
    value = background(rig)
    assert isinstance(value, Checked)
    assert value.outcome == outcome
    ((name, fields),) = rig.r4_lines()
    assert name == "r4.not_compared"
    assert {k: v for k, v in fields.items() if k != "job"} == {
        "workspace_key": "k1", "record": "s0-j0", "outcome": outcome,
    }  # fmt: skip


def test_a_pass_logs_nothing(make: Any) -> None:
    rig = make({"r4_check": [r4("pass")]})
    assert background(rig) == Checked("pass")
    assert rig.r4_lines() == []


def test_an_r4_that_did_not_run_to_an_outcome_is_logged_with_why(make: Any) -> None:
    from app.platform.engine import EngineRefusal

    rig = make({"r4_check": [EngineRefusal("engine.internal", "an encrypted file met")]})
    assert background(rig) == NotChecked("refused")
    assert [(n, f["outcome"]) for n, f in rig.r4_lines()] == [("r4.not_compared", "refused")]


# -- A save right after a save (S0068 verifier r1 F4; spec §3, §14) ----------------------------------------------------


def test_a_save_waits_for_a_running_r4_and_never_runs_one_itself(make: Any) -> None:
    """§3: a running job is never interrupted, so a save asked for while the previous save's R4 runs waits for it;
    it never runs an R4 itself (one `save` request after the R4's one `r4_check`)."""
    rig = make({"r4_check": [BLOCK, r4("pass")], "save": [Gated(answer())]})
    bg = rig.jobs.submit(r4_spec(W1), r4_work(ALD, TARGET))
    assert isinstance(bg, JobTicket)
    rig.called("r4_check")
    t = rig.jobs.submit(save_spec(W1), save_work(REQUEST))
    assert isinstance(t, JobTicket)
    q = rig.jobs.status().queue
    assert q.running is not None
    assert q.running.job.kind == "r4"
    assert [j.kind for j in q.queued] == ["save"]  # waiting behind the running R4
    rig.engine.release("r4_check")
    assert isinstance(rig.run(t), Done)
    assert rig.ops() == ["r4_check", "save"]
