"""The engine-job codes through the API (S0058; `design/specs/engine-jobs.md` §11; ARCHITECTURE.md §6.1, §6.7).

No product route runs an engine job yet (the first ones come with BL-0151 and BL-0152), so a test-only route runs a
real scheduler (`app.platform.engine_jobs.scheduler.EngineJobs`) over the fake engine port and raises the job's
`Failed` error, as a product route will: the edge must answer each code with its class, status, plain message and
typed details, in the envelope the contract declares.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from jsonschema import Draft202012Validator

from app.api.envelope import Envelope
from app.api.routes import declare
from app.platform.engine_jobs.queue import JobSpec, QueuePolicy
from app.platform.engine_jobs.scheduler import EngineJobs, Failed, JobContext, JobTicket, Work, WorkResult
from app.platform.errors import ErrorCode, spec_for
from app.workspaces.access import Unknown
from tests.api.support import Counted, body, client, make_app
from tests.platform.fake_job_engine import FakeEngine

DEADLINE = 20.0
W1 = "ws-1"


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

    def run(self, name: str) -> Failed:
        out = getattr(self, name)()
        assert isinstance(out, Failed), out
        return out

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
    assert error["details"] == {"reason": "cannot_start"}
    conforms(app, payload)
