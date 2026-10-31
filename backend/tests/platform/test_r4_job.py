"""R4, the sidecar's side (`app.platform.engine_jobs.r4`, `save`'s step 1, `save_text`; `design/specs/engine-jobs.md`
§4, §5.1, §5.3, §6.1 steps 1 and 11, §6.7, §11; ADR-0008 amendments; S0066), over the fake engine port with a real
scheduler. Written from the spec's lines:

- §6.1 step 1: "If this workspace has a closed record (re-read from disk), the save first runs that `r4` (§6.7) inside
  this job. A `pass`, `superseded` or `nondeterministic` outcome lets the save go on. A `mismatch` refuses it with
  `WRITES_PAUSED {check_failed}`; an R4 that could not finish (its engine stopped) refuses it with `WRITES_PAUSED
  {check_pending}` … after the third such failure of the same R4 it becomes `check_failed`. A Cancel during this step
  cancels the save"; "Steps 2-11 run in one fresh engine";
- §6.1 step 11: "queue the `r4` as background work";
- §6.7: "(4) Digest different, fingerprint unchanged → confirm: that engine ends, and a fresh engine that runs no user
  code parses"; "A Cancel leaves it `.closed` … A failure to run (its engine crashed or failed to start) also leaves
  it `.closed` and adds one to its `.r4-attempts` count"; "Resume saving is offered … The gate is lifted only by that
  explicit action";
- §11's rows: "a save's R4 could not finish", "the same R4 failed to run three times", "R4 mismatch".
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.platform.engine import EngineFailure
from app.platform.engine_jobs import save_text
from app.platform.engine_jobs.r4 import (
    Checked,
    NotChecked,
    R4Target,
    resume_spec,
    resume_work,
    target_of,
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
from app.platform.errors import ErrorCode, WritesPausedDetails
from tests.platform.fake_job_engine import BLOCK, CRASH, CRASH_TO_BOUND, FakeEngine, Gated

DEADLINE = 20.0
W1 = "ws-1"
MAIN = "/L/main.bean"
ALD = Path("/app")
JD = "/app/local/k1/journal"
OWED = f"{JD}/s0-j0.closed"
MISMATCH = f"{JD}/s0-j0.mismatch"
BACKUP = "/app/local/k1/backups/s0-j0"
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
    "backup": None,
    "could_not_check": False,
}
OWES = {**BLANK, "outcome": "refused", "reason": "check_pending", "path": OWED, "unchanged": True}


def answer(**kw: object) -> dict[str, object]:
    return {**BLANK, **kw}


def r4(
    outcome: str, *, loaded: bool = False, record: str | None = None, backup: str | None = BACKUP
) -> dict[str, object]:
    return {"outcome": outcome, "loaded": loaded, "record": record, "backup": backup}


class Rig:
    def __init__(self, scripted: dict[str, list[object]]) -> None:
        self.engine = FakeEngine(scripted=scripted)
        self.jobs = EngineJobs(self.engine, wait_engine_lock=lambda: None)
        self.engine.spawned = self.jobs.engine_spawned
        self.engine.ended = self.jobs.engine_ended
        boot = self.jobs.start()
        assert isinstance(boot, JobTicket)
        assert boot.wait(DEADLINE) is not None
        self.jobs.workspace_opened(W1)

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


def paused(value: SaveValue) -> WritesPausedDetails:
    assert isinstance(value, NotDone), value
    assert value.error.code == ErrorCode.WRITES_PAUSED, value.error
    assert isinstance(value.error.details, WritesPausedDetails)
    return value.error.details


# -- the record a path names -----------------------------------------------------------------------------------------


def test_the_target_is_read_from_the_records_path_only() -> None:
    assert target_of(OWED) == R4Target("k1", "s0-j0")
    assert target_of(MISMATCH) == R4Target("k1", "s0-j0")
    for bad in (
        "/app/local/k1/journal/s0-j0.open",  # not a state R4 acts on
        "/app/local/k1/other/s0-j0.closed",
        "/app/x/k1/journal/s0-j0.closed",
        "/app/local/k1/journal/s0.closed",  # no job part
        "/app/local/k1/journal/s0-j/0.closed",
        "/app/local/../journal/s0-j0.closed",
        "s0-j0.closed",
    ):
        assert target_of(bad) is None, bad


# -- R4 first, inside the save (§6.1 step 1) -----------------------------------------------------------------------


@pytest.mark.parametrize("outcome", ["pass", "superseded", "superseded_build"])
def test_an_owed_r4_that_lets_the_save_go_on_is_run_first_and_the_save_is_asked_again_in_a_fresh_engine(
    make: Any, outcome: str
) -> None:
    rig = make({"save": [OWES, Gated(answer())], "r4_check": [r4(outcome, loaded=True, record=f"{JD}/s0-j0.done")]})
    assert rig.save() == Saved(published=False)
    assert rig.ops() == ["save", "r4_check", "save"]
    calls = rig.serials()
    assert calls[0][0] == calls[1][0], "R4 runs in the save's own engine: it ran no plugin before refusing"
    assert calls[2][0] != calls[1][0], "steps 2-11 run in a fresh engine after R4's load (§6.1, §5.1)"
    assert rig.engine.params[1][2] == {"app_local_data": "/app", "key": "k1", "name": "s0-j0"}


def test_r4_that_did_not_load_leaves_the_engine_to_the_save(make: Any) -> None:
    rig = make({"save": [OWES, Gated(answer())], "r4_check": [r4("superseded", loaded=False)]})
    assert rig.save() == Saved(published=False)
    calls = rig.serials()
    assert calls[0][0] == calls[1][0] == calls[2][0]  # parse-only work may serve the next part (§5.1)


def test_a_mismatch_refuses_check_failed_with_the_record_and_the_backup(make: Any) -> None:
    rig = make(
        {
            "save": [OWES],
            "r4_check": [r4("differs", loaded=True, record=OWED)],
            "r4_confirm": [r4("mismatch", record=MISMATCH)],
        }
    )
    d = paused(rig.save())
    assert d == WritesPausedDetails("check_failed", None, MISMATCH, BACKUP, False)
    assert save_text.paused_text(d) == f"{save_text.CHECK_FAILED} {save_text.BACKUP_HERE}"
    assert rig.ops() == ["save", "r4_check", "r4_confirm"]
    calls = rig.serials()
    assert calls[2][0] != calls[1][0], "the confirm runs in a fresh engine that ran no user code (§6.7 step 4)"


def test_a_passed_r4_then_no_engine_for_the_save_is_not_could_not_check(make: Any) -> None:
    """Verifier r1 F8: the owed R4 passed (the record is `.done`), then no engine could start for the save itself:
    the engine's answer (`ENGINE_UNAVAILABLE`), never "could not check your last change"."""
    import time

    rig = make({"save": [OWES, Gated(answer())], "r4_check": [r4("pass", loaded=True, record=f"{JD}/s0-j0.done")]})
    real = rig.engine.call

    def call(op: str, params: Any, **kw: Any) -> Any:
        out = real(op, params, **kw)
        if op == "r4_check":
            now = time.monotonic()
            rig.engine.unavailable((now, now, now))  # no engine can start after R4's load
        return out

    rig.engine.call = call
    value = rig.save()
    assert isinstance(value, NotDone)
    assert value.error.code == ErrorCode.ENGINE_UNAVAILABLE
    assert rig.ops() == ["save", "r4_check"]


@pytest.mark.parametrize(
    ("kind", "code", "reason"),
    [
        ("incompatible", ErrorCode.ENGINE_UNAVAILABLE, "incompatible"),
        ("start_failed", ErrorCode.ENGINE_UNAVAILABLE, "cannot_start"),
        ("crashed", ErrorCode.ENGINE_STOPPED, "crashed"),
        ("protocol_violation", ErrorCode.ENGINE_STOPPED, "crashed"),
        ("unavailable", ErrorCode.ENGINE_UNAVAILABLE, "stopped_often"),
    ],
)
def test_a_passed_r4_then_no_engine_for_the_save_is_worded_by_its_cause(
    make: Any, kind: str, code: ErrorCode, reason: str
) -> None:
    """BL-0172 (S0066 red team r2): the fresh engine for the save after its owed R4 passed did not come; the text says
    why (§11): "stopped several times" only when the restart bound was reached; an engine that crashed or broke the
    protocol while it started is `ENGINE_STOPPED` (nothing written: the save never ran); an incompatible one says the
    install is damaged (before S0067: every cause but three read "stopped several times")."""
    import time

    rig = make({"save": [OWES, Gated(answer())], "r4_check": [r4("pass", loaded=True, record=f"{JD}/s0-j0.done")]})
    real = rig.engine.call

    def call(op: str, params: Any, **kw: Any) -> Any:
        out = real(op, params, **kw)
        if op == "r4_check":
            if kind == "incompatible":
                rig.engine.incompatible()
            elif kind == "unavailable":
                now = time.monotonic()
                rig.engine.unavailable((now, now, now))
            else:
                rig.engine.start_failure = EngineFailure(kind, "test")  # type: ignore[arg-type]
        return out

    rig.engine.call = call
    value = rig.save()
    assert isinstance(value, NotDone)
    assert value.error.code == code
    assert getattr(value.error.details, "reason", None) == reason
    assert rig.ops() == ["save", "r4_check"]


def test_no_backup_folder_no_promise_of_one() -> None:
    """Verifier r1 F9: a pause whose record could not be read has no backup folder to show."""
    for could_not_check in (True, False):
        d = WritesPausedDetails("check_failed", None, MISMATCH, None, could_not_check)
        assert save_text.BACKUP_HERE not in save_text.paused_text(d)


def test_a_nondeterministic_outcome_saves_and_says_so(make: Any) -> None:
    rig = make(
        {
            "save": [OWES, Gated(answer())],
            "r4_check": [r4("differs", loaded=True, record=OWED)],
            "r4_confirm": [r4("nondeterministic", record=f"{JD}/s0-j0.done")],
        }
    )
    assert rig.save() == Saved(published=False, nondeterministic=True)
    assert rig.ops() == ["save", "r4_check", "r4_confirm", "save"]


def test_an_r4_whose_engine_died_counts_one_failure_in_a_fresh_engine_and_refuses_check_pending(make: Any) -> None:
    rig = make({"save": [OWES], "r4_check": [CRASH], "r4_attempt": [r4("counted", record=OWED)]})
    d = paused(rig.save())
    assert d == WritesPausedDetails("check_pending", None, OWED, None, False)
    assert save_text.paused_text(d) == save_text.CHECK_PENDING
    assert rig.ops() == ["save", "r4_check", "r4_attempt"]
    assert rig.serials()[2][0] != rig.serials()[1][0]


@pytest.mark.parametrize("failure", ["confirm", "refusal", "unreadable"])
def test_other_failures_to_run_are_counted_too(make: Any, failure: str) -> None:
    from app.platform.engine import EngineRefusal

    scripted: dict[str, list[object]] = {"save": [OWES], "r4_attempt": [r4("counted", record=OWED)]}
    if failure == "confirm":
        scripted["r4_check"] = [r4("differs", loaded=True, record=OWED)]
        scripted["r4_confirm"] = [CRASH]
    elif failure == "refusal":  # an exception in the engine (the replica refused): its plugins may have run
        scripted["r4_check"] = [EngineRefusal("engine.internal", "failed")]
    else:
        scripted["r4_check"] = [r4("unreadable", backup=None)]
    rig = make(scripted)
    assert paused(rig.save()).reason == "check_pending"
    assert rig.ops()[-1] == "r4_attempt"
    if failure == "unreadable":  # no plugin ran: the same engine counts it
        assert rig.serials()[-1][0] == rig.serials()[-2][0]
    else:  # a refusal may come after the plugins ran, a death leaves no engine: a fresh one counts it (§5.1)
        assert rig.serials()[-1][0] != rig.serials()[-2][0]


def test_the_third_failure_refuses_check_failed_could_not_check(make: Any) -> None:
    rig = make({"save": [OWES], "r4_check": [CRASH], "r4_attempt": [r4("could_not_check", record=MISMATCH)]})
    d = paused(rig.save())
    assert d == WritesPausedDetails("check_failed", None, MISMATCH, BACKUP, True)
    assert save_text.paused_text(d) == f"{save_text.COULD_NOT_CHECK} {save_text.BACKUP_HERE}"


def test_a_cancel_during_the_owed_r4_cancels_the_save_and_counts_nothing(make: Any) -> None:
    rig = make({"save": [OWES], "r4_check": [BLOCK, r4("pass", loaded=True)]})
    ticket = rig.jobs.submit(save_spec(W1), save_work(REQUEST))
    assert isinstance(ticket, JobTicket)
    rig.called("r4_check")
    rig.jobs.cancel(ticket.id)
    out = rig.run(ticket)
    assert isinstance(out, Failed), out
    assert out.error.code == ErrorCode.JOB_CANCELLED
    assert "r4_attempt" not in rig.ops()


def test_no_engine_to_count_in_counts_nothing_and_refuses_check_pending(make: Any) -> None:
    rig = make({"save": [OWES], "r4_check": [CRASH_TO_BOUND]})
    value = rig.save()
    assert paused(value).reason == "check_pending"
    assert "r4_attempt" not in rig.ops()


def test_a_record_met_twice_in_one_save_is_not_checked_again(make: Any) -> None:
    rig = make({"save": [OWES, OWES], "r4_check": [r4("pass", loaded=True)]})
    assert paused(rig.save()) == WritesPausedDetails("check_pending", None, OWED, None, False)
    assert rig.ops() == ["save", "r4_check", "save"]


def test_a_check_failed_refusal_from_the_engine_carries_its_record_and_backup(make: Any) -> None:
    rig = make(
        {"save": [answer(outcome="refused", reason="check_failed", path=MISMATCH, backup=BACKUP, could_not_check=True)]}
    )
    assert paused(rig.save()) == WritesPausedDetails("check_failed", None, MISMATCH, BACKUP, True)


# -- the R4 a landed save queues (§6.1 step 11) ------------------------------------------------------------------


def test_a_landed_save_queues_its_own_r4(make: Any) -> None:
    rig = make({"save": [Gated(answer())], "r4_check": [r4("pass", loaded=True)]})
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
    rig = make({"save": [GATE_DIE], "save_outcome": [facts], "r4_check": [r4("pass", loaded=True)]})
    out = rig.run(submit_save(rig.jobs, W1, REQUEST))
    assert isinstance(out, Done), out
    assert out.value == Saved(published=False)
    rig.called("r4_check")


def test_the_background_r4_counts_its_engines_death_in_a_fresh_engine(make: Any) -> None:
    from app.platform.engine_jobs.r4 import r4_spec, r4_work

    rig = make({"r4_check": [CRASH], "r4_attempt": [r4("counted", record=OWED)]})
    out = rig.run(rig.jobs.submit(r4_spec(W1), r4_work(ALD, R4Target("k1", "s0-j0"))))
    assert isinstance(out, Done), out
    assert out.value == NotChecked("counted", OWED, BACKUP)
    assert rig.ops() == ["r4_check", "r4_attempt"]
    assert rig.serials()[0][0] != rig.serials()[1][0]


def test_the_background_r4_ended_by_another_jobs_cancel_counts_nothing(make: Any) -> None:
    from app.platform.engine_jobs.r4 import r4_spec, r4_work

    rig = make({"r4_check": [BLOCK, r4("pass", loaded=True)], "save": [Gated(answer())]})
    bg = rig.jobs.submit(r4_spec(W1), r4_work(ALD, R4Target("k1", "s0-j0")))
    assert isinstance(bg, JobTicket)
    rig.called("r4_check")
    waiting = rig.jobs.submit(save_spec(W1), save_work(REQUEST))
    assert isinstance(waiting, JobTicket)
    rig.jobs.cancel(waiting.id)  # §4: a waiting user's Cancel ends a running R4 (nobody waits for it)
    rig.run(bg)
    assert "r4_attempt" not in rig.ops()


def test_the_background_r4_reports_a_mismatch(make: Any) -> None:
    from app.platform.engine_jobs.r4 import r4_spec, r4_work

    rig = make({"r4_check": [r4("differs", loaded=True, record=OWED)], "r4_confirm": [r4("mismatch", record=MISMATCH)]})
    out = rig.run(rig.jobs.submit(r4_spec(W1), r4_work(ALD, R4Target("k1", "s0-j0"))))
    assert isinstance(out, Done), out
    assert out.value == Checked("mismatch", MISMATCH, BACKUP)


# -- Resume saving -------------------------------------------------------------------------------------------------


def test_resume_saving_renames_the_record_it_was_shown(make: Any) -> None:
    rig = make({"r4_resume": [r4("done", record=f"{JD}/s0-j0.done", backup=None)]})
    out = rig.run(rig.jobs.submit(resume_spec(W1), resume_work(ALD, MISMATCH)))
    assert isinstance(out, Done), out
    assert out.value == "done"
    assert rig.engine.params[0][2] == {"app_local_data": "/app", "key": "k1", "name": "s0-j0"}


@pytest.mark.parametrize("record", [OWED, f"{JD}/s0-j0.done", "/elsewhere/s0-j0.mismatch"])
def test_resume_saving_never_acts_on_anything_but_a_mismatch_record(make: Any, record: str) -> None:
    rig = make({})
    out = rig.run(rig.jobs.submit(resume_spec(W1), resume_work(ALD, record)))
    assert isinstance(out, Done), out
    assert out.value == "gone"
    assert rig.ops() == []


def test_resume_saving_that_gets_no_answer_says_it_failed(make: Any) -> None:
    rig = make({"r4_resume": [CRASH]})
    out = rig.run(rig.jobs.submit(resume_spec(W1), resume_work(ALD, MISMATCH)))
    assert isinstance(out, Done), out
    assert out.value == "failed"
