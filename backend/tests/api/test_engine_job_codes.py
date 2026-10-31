"""The engine-job codes through the API (S0058; `design/specs/engine-jobs.md` §11; ARCHITECTURE.md §6.1, §6.7).

No product route runs an engine job yet (the first ones come with BL-0151 and BL-0152), so a test-only route runs a
real scheduler (`app.platform.engine_jobs.scheduler.EngineJobs`) over the fake engine port and raises the job's
`Failed` error, as a product route will: the edge must answer each code with its class, status, plain message and
typed details, in the envelope the contract declares.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from jsonschema import Draft202012Validator

from app.api.envelope import Envelope
from app.api.routes import declare
from app.platform.engine_jobs.queue import JobSpec, QueuePolicy
from app.platform.engine_jobs.save import ChangeText, NotDone, SaveRequest, save_spec, save_work
from app.platform.engine_jobs.scheduler import Done, EngineJobs, Failed, JobContext, JobTicket, Work, WorkResult
from app.platform.errors import ErrorCode, spec_for
from app.workspaces.access import Unknown
from tests.api.support import Counted, body, client, make_app
from tests.platform.fake_job_engine import FakeEngine

DEADLINE = 20.0
W1 = "ws-1"
SAVE_ANSWER: dict[str, object] = {
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
REQUEST = SaveRequest(
    session="s1",
    workspace_key="k1",
    lineage="lin1",
    root="/L/main.bean",
    changes=(ChangeText("/L/main.bean", "0" * 64, "x"),),
    fingerprint="{}",
    mirror=None,
    legacy_locks=(),
    app_local_data=Path("/app"),
)


class Scenarios:
    """A booted scheduler over the fake engine, and the failure each scenario's job ends with."""

    def __init__(self) -> None:
        self.engine = FakeEngine()
        self.jobs = EngineJobs(self.engine, wait_engine_lock=lambda: None, policy=QueuePolicy(max_queued=1))
        self.engine.spawned = self.jobs.engine_spawned
        self.engine.ended = self.jobs.engine_ended
        boot = self.jobs.start()
        assert isinstance(boot, JobTicket)
        assert boot.wait(DEADLINE) is not None
        self.jobs.workspace_opened(W1)

    def close(self) -> None:
        self.jobs.close()

    def run(self, name: str) -> Failed | NotDone:
        out = getattr(self, name)()
        if isinstance(out, Done):
            out = out.value
        assert isinstance(out, Failed | NotDone), out
        return out

    def _save(self, answer: dict[str, object]) -> object:
        """A save job (S0063) whose engine answers `answer`: its value is the error the route raises."""
        self.engine.scripted["save"] = [{**SAVE_ANSWER, **answer}]
        ticket = self.jobs.submit(save_spec(W1), save_work(REQUEST))
        assert isinstance(ticket, JobTicket)
        return self._outcome(ticket)

    def ledger_changed(self) -> object:
        return self._save({"outcome": "refused", "reason": "ledger_changed", "unchanged": True})

    def writes_paused(self) -> object:
        return self._save({"outcome": "refused", "reason": "check_pending", "unchanged": True})

    def legacy_app_saving(self) -> object:
        return self._save({"outcome": "refused", "reason": "legacy_app_saving", "unchanged": True})

    def not_saved(self) -> object:
        return self._save({"outcome": "not_saved", "reason": "hard_linked", "path": "/L/main.bean", "unchanged": True})

    def _submit(self, work: Work, kind: str = "preview") -> JobTicket:
        spec = JobSpec(kind=kind, workspace=W1, waited_for=True, confirmed=False, owner="user")  # type: ignore[arg-type]
        ticket = self.jobs.submit(spec, work)
        assert isinstance(ticket, JobTicket), ticket
        return ticket

    def _outcome(self, ticket: JobTicket) -> object:
        out = ticket.wait(DEADLINE)
        assert out is not None
        return out

    def queue_full(self) -> object:
        running = self._submit(lambda ctx: WorkResult(ctx.call("block", {})))
        threading_wait(lambda: any(op == "block" for _, op in self.engine.calls))
        self._submit(lambda ctx: WorkResult(ctx.call("ok", {})))
        spec = JobSpec(kind="bql", workspace=W1, waited_for=True, confirmed=False, owner="user")
        out = self.jobs.submit(spec, lambda ctx: WorkResult(None))
        self.engine.release()
        self._outcome(running)
        return out

    def cancelled(self) -> object:
        def work(ctx: JobContext) -> WorkResult:
            return WorkResult(ctx.call("block", {}))

        ticket = self._submit(work)
        threading_wait(lambda: any(op == "block" for _, op in self.engine.calls))
        assert self.jobs.cancel(ticket.id) == "cancelled"
        return self._outcome(ticket)

    def stopped(self) -> object:
        return self._outcome(self._submit(lambda ctx: WorkResult(ctx.call("crash", {}))))

    def outcome_unknown(self) -> object:
        # a save reads its outcome from disk since S0063; a `create` cut past its commit point does not yet (BL-0153)
        spec = JobSpec(kind="create", workspace=None, waited_for=True, confirmed=True, owner="user")
        ticket = self.jobs.submit(spec, lambda ctx: WorkResult(ctx.call("gate_die", {})))
        assert isinstance(ticket, JobTicket)
        return self._outcome(ticket)

    def unavailable(self) -> object:
        self.engine.incompatible()
        self.engine.die()
        threading_wait(lambda: self.jobs.status().queue.slot == "none")
        return self._outcome(self._submit(lambda ctx: WorkResult(ctx.call("ok", {}))))


def threading_wait(cond: Callable[[], bool]) -> None:
    done = threading.Event()
    for _ in range(int(DEADLINE / 0.01)):
        if cond():
            return
        done.wait(0.01)
    assert cond()


@pytest.fixture
def scenarios() -> Iterator[Scenarios]:
    s = Scenarios()
    yield s
    s.close()


def job_router(scenarios: Scenarios) -> APIRouter:
    router = APIRouter(prefix="/api/test")

    @router.get("/job/{name}", openapi_extra=declare("app"))
    async def job(name: str) -> Envelope[Counted]:
        raise scenarios.run(name).error

    return router


def app_for(scenarios: Scenarios) -> FastAPI:
    app, _ = make_app(Unknown(), routers=(job_router(scenarios),))
    return app


def conforms(app: FastAPI, payload: dict[str, Any]) -> None:
    doc = app.openapi()
    Draft202012Validator({"$ref": "#/components/schemas/ErrorEnvelope", "components": doc["components"]}).validate(  # pyright: ignore[reportUnknownMemberType] (jsonschema's stubs)
        payload
    )


def answer(scenarios: Scenarios, name: str) -> tuple[int, dict[str, Any], FastAPI]:
    app = app_for(scenarios)
    r = client(app).get(f"/api/test/job/{name}")
    return r.status_code, body(r), app


@pytest.mark.error_code("ENGINE_QUEUE_FULL")
def test_a_full_queue_is_engine_queue_full_and_retryable(scenarios: Scenarios) -> None:
    """§11 "queue full": "Finzytrack is busy with other ledger work. Please try again in a moment." (Try again)."""
    status, payload, app = answer(scenarios, "queue_full")
    assert status == 503
    error = payload["error"]
    assert error["code"] == "ENGINE_QUEUE_FULL"
    assert error["class"] == "unavailable"
    assert error["retryable"] is True
    full = spec_for(ErrorCode.ENGINE_QUEUE_FULL)
    assert full is not None
    assert error["message"] == full.message == "Finzytrack is busy with other ledger work."
    assert full.next_step == "Please try again in a moment."
    conforms(app, payload)


@pytest.mark.error_code("JOB_CANCELLED")
def test_a_cancelled_job_is_job_cancelled_with_whether_the_work_ahead_could_be_stopped(scenarios: Scenarios) -> None:
    status, payload, app = answer(scenarios, "cancelled")
    assert status == 409
    error = payload["error"]
    assert error["code"] == "JOB_CANCELLED"
    assert error["class"] == "conflict"
    assert error["retryable"] is False
    assert error["details"] == {"cannot_stop_ahead": False}
    conforms(app, payload)


@pytest.mark.error_code("ENGINE_STOPPED")
def test_a_read_whose_engine_stopped_is_engine_stopped_crashed(scenarios: Scenarios) -> None:
    """§11 "a read job's engine stopped": "The ledger engine stopped while working on this. Your ledger files are
    safe." with Try again."""
    status, payload, app = answer(scenarios, "stopped")
    assert status == 503
    error = payload["error"]
    assert error["code"] == "ENGINE_STOPPED"
    assert error["retryable"] is True
    assert error["details"] == {"reason": "crashed"}
    stopped = spec_for(ErrorCode.ENGINE_STOPPED)
    assert stopped is not None
    assert stopped.message == "The ledger engine stopped while working on this."
    assert stopped.data_safe
    conforms(app, payload)


@pytest.mark.error_code("ENGINE_UNAVAILABLE")
def test_an_engine_that_cannot_run_is_engine_unavailable_with_its_reason(scenarios: Scenarios) -> None:
    status, payload, app = answer(scenarios, "unavailable")
    assert status == 503
    error = payload["error"]
    assert error["code"] == "ENGINE_UNAVAILABLE"
    assert error["retryable"] is True
    assert error["details"] == {"reason": "incompatible"}
    conforms(app, payload)


@pytest.mark.error_code("CHANGE_OUTCOME_UNKNOWN")
def test_a_save_cut_past_its_commit_point_is_change_outcome_unknown_and_not_retryable(scenarios: Scenarios) -> None:
    """§5.3 "a save's outcome is resolved from disk (§6.6)" (BL-0151); until then the answer says the outcome is not
    known, never that the files are safe, and offers no Retry (S0058 verifier F5)."""
    status, payload, app = answer(scenarios, "outcome_unknown")
    assert status == 503
    error = payload["error"]
    assert error["code"] == "CHANGE_OUTCOME_UNKNOWN"
    assert error["retryable"] is False
    assert error["data_safe"] is False
    conforms(app, payload)


@pytest.mark.error_code("LEDGER_EXTERNALLY_CHANGED")
def test_a_save_whose_ledger_moved_is_ledger_externally_changed(scenarios: Scenarios) -> None:
    """§11 "ledger moved since the preview, or during the save's load": a conflict to review, nothing written."""
    status, payload, app = answer(scenarios, "ledger_changed")
    assert status == 409
    error = payload["error"]
    assert (error["code"], error["class"], error["retryable"], error["data_safe"]) == (
        "LEDGER_EXTERNALLY_CHANGED",
        "conflict",
        False,
        True,
    )
    assert error["message"] == "Your ledger changed after this preview, so Finzytrack did not save your change."
    conforms(app, payload)


@pytest.mark.error_code("WRITES_PAUSED")
def test_a_save_refused_for_an_owed_check_is_writes_paused_with_its_reason(scenarios: Scenarios) -> None:
    status, payload, app = answer(scenarios, "writes_paused")
    assert status == 409
    error = payload["error"]
    assert error["code"] == "WRITES_PAUSED"
    assert error["details"] == {
        "reason": "check_pending",
        "file": None,
        "record": None,
        "backup": None,
        "could_not_check": False,
    }
    assert error["data_safe"] is False
    conforms(app, payload)


@pytest.mark.error_code("LEGACY_APP_SAVING")
def test_a_save_refused_for_the_old_app_is_legacy_app_saving_and_retryable(scenarios: Scenarios) -> None:
    """§11 "legacy app saving" (ADR-0062): "The old Finzytrack app is saving. Quit the old Finzytrack first, then try
    again." with Try again."""
    status, payload, app = answer(scenarios, "legacy_app_saving")
    assert status == 409
    error = payload["error"]
    assert (error["code"], error["retryable"], error["data_safe"]) == ("LEGACY_APP_SAVING", True, True)
    assert error["message"] == "The old Finzytrack app is saving."
    conforms(app, payload)


@pytest.mark.error_code("CHANGE_NOT_SAVED")
def test_a_save_the_writer_refused_is_change_not_saved_with_its_reason_and_file(scenarios: Scenarios) -> None:
    status, payload, app = answer(scenarios, "not_saved")
    assert status == 409
    error = payload["error"]
    assert error["code"] == "CHANGE_NOT_SAVED"
    assert error["details"] == {
        "reason": "hard_linked",
        "path": "/L/main.bean",
        "folder": None,
        "acl_entry": None,
        "unchanged": True,
    }
    conforms(app, payload)
