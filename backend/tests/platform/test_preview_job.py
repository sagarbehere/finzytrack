"""The preview job, the sidecar's side (`app.platform.engine_jobs.preview`; S0117; `design/specs/engine-jobs.md` §2's
`preview`, §5.1, §7, §11; ADR-0008 steps 1-3, ADR-0067, ADR-0068 D1; BL-0106), over the fake engine port with a real
scheduler. Written from BL-0106's list and the S0117 brief:

- "the sidecar's preview use case (its tries, `LEDGER_CHANGING`, a `stale` answer turned into
  `WORKSPACE_NOT_READY {WARMING}` and a re-plan)": a `moved` answer runs the job again in a fresh engine, at most 3
  tries in a row (§7), then `LEDGER_CHANGING`;
- the contract's refusals are `ContractRefused` codes (ARCHITECTURE §6.1): a text that is not the intended change
  (steps apply, parse, diff, spans) `WRITE_REFUSED_UNINTENDED_CHANGE`, a check that could not run (the plan, the
  load) `WRITE_REFUSED_CHECK_FAILED`, an encrypted file `ENCRYPTED_FILE_NOT_EDITABLE`;
- an unfinished save's journal (`paused`) is `WRITES_PAUSED {earlier_unfinished}`; an answer that does not hold
  together, or an engine refusal, is `INTERNAL` (INV-22: never a plan);
- BL-0255 (S0118): a `not_fresh` answer whose published generation has a readable stamp of this ledger carries it
  as a hint, and the job submits the workspace's background rebuild with it, so "wait a moment and try again" comes
  true; every other `stale` answer carries none and queues nothing (round 1, red team RT-F1: a set that is not a
  stamp would drop the ledger's include patterns; with no usable mirror, a reopen builds it); a refused submit is
  logged (`rebuild.not_queued`), never raised, and the answer stays `WORKSPACE_NOT_READY {WARMING}`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from app.platform.clock import FrozenClock
from app.platform.engine import EngineRefusal
from app.platform.engine_jobs.preview import (
    MAX_TRIES,
    NotPrepared,
    Prepared,
    PreviewOrder,
    PreviewTarget,
    PreviewValue,
    params_of,
    preview_spec,
    preview_work,
    submit_preview,
)
from app.platform.engine_jobs.queue import QueuePolicy
from app.platform.engine_jobs.scheduler import Done, EngineJobs, Failed, JobContext, JobTicket, WorkResult
from app.platform.errors import (
    ContractStep,
    ContractStepDetails,
    EncryptedFileDetails,
    ErrorCode,
    WorkspaceNotReadyDetails,
    WritesPausedDetails,
)
from app.platform.observability import Event as LogEvent
from tests.platform.fake_job_engine import BLOCK, CRASH, FakeEngine

DEADLINE = 20.0
W1 = "k1"
TARGET = PreviewTarget(workspace_key=W1, root="/L/main.bean", lineage="lin1", app_local_data=Path("/app"))
ORDER = PreviewOrder(generation=3, mutator="test.edits", request={"edits": []})
SHA = "a" * 64
FINGERPRINT = '{"document_files":[],"documents":[],"files":[],"globs":[],"modules":[],"root":"/L/main.bean","v":2}'
HINT = (
    '{"document_files":[],"documents":[],"files":[["/L/main.bean",["absent"]]],"globs":[],"modules":[],'
    '"root":"/L/main.bean","v":2}'
)
CLOCK = FrozenClock(datetime(2026, 11, 14, 10, 0, tzinfo=UTC))
BLANK: dict[str, object] = {
    "outcome": "prepared",
    "files": [{"path": "/L/main.bean", "pre": SHA, "post": "b" * 64, "text": "x\n"}],
    "fingerprint": FINGERPRINT,
    "new_errors": [],
    "errors": 0,
    "step": None,
    "reason": None,
    "path": None,
    "detail": None,
    "line": None,
    "stale": None,
}


def answer(**kw: object) -> dict[str, object]:
    return {**BLANK, **kw}


def refused(step: str, reason: str, path: str | None = None) -> dict[str, object]:
    return answer(
        outcome="refused", files=[], fingerprint=None, step=step, reason=reason, path=path, detail="d", errors=0
    )


MOVED = answer(outcome="moved", files=[], fingerprint=None)


def stale(why: str, hint: str | None = HINT) -> dict[str, object]:
    return answer(outcome="stale", files=[], fingerprint=hint, stale=why)


REBUILT: dict[str, object] = {
    "outcome": "loaded",
    "fingerprint": HINT,
    "entries": 1,
    "errors": 0,
    "publish": "published",
    "number": 4,
    "ledger_version": "4/sha256:" + "1" * 64,
    "removed": 0,
    "remove_failed": 0,
    "refusal": None,
    "refused": None,
    "line": None,
}


class Rig:
    def __init__(self, scripted: dict[str, list[object]], policy: QueuePolicy | None = None) -> None:
        self.engine = FakeEngine(scripted=scripted)
        self.logged: list[tuple[str, dict[str, object]]] = []
        self.jobs = EngineJobs(self.engine, wait_engine_lock=lambda: None, policy=policy, log=self._sink)
        self.engine.spawned = self.jobs.engine_spawned
        self.engine.ended = self.jobs.engine_ended
        boot = self.jobs.start()
        assert isinstance(boot, JobTicket)
        assert boot.wait(DEADLINE) is not None
        self.jobs.workspace_opened(W1)

    def _sink(self, event: LogEvent, /, **fields: object) -> None:
        self.logged.append((event.name, fields))

    def preview(self, ticket: JobTicket | Failed | None = None) -> PreviewValue | Failed:
        if ticket is None:
            ticket = self.jobs.submit(preview_spec(W1), preview_work(TARGET, ORDER))
        assert isinstance(ticket, JobTicket), ticket
        out = ticket.wait(DEADLINE)
        assert out is not None
        if isinstance(out, Failed):
            return out
        assert isinstance(out, Done), out
        assert isinstance(out.value, Prepared | NotPrepared), out.value
        return out.value


@pytest.fixture
def make() -> Iterator[Any]:
    rigs: list[Rig] = []

    def build(scripted: dict[str, list[object]], policy: QueuePolicy | None = None) -> Rig:
        rigs.append(Rig(scripted, policy))
        return rigs[-1]

    yield build
    for r in rigs:
        r.jobs.close()


def error_of(value: PreviewValue | Failed) -> tuple[str, object]:
    assert isinstance(value, NotPrepared | Failed), value
    return value.error.code, value.error.details


# -- the request ------------------------------------------------------------------------------------------------------


def test_the_request_names_the_generation_the_mutator_and_its_request() -> None:
    assert params_of(TARGET, ORDER) == {
        "root": "/L/main.bean",
        "app_local_data": "/app",
        "workspace_key": W1,
        "lineage": "lin1",
        "generation": 3,
        "mutator": "test.edits",
        "request": {"edits": []},
    }


def test_a_preview_is_waited_for_and_writes_nothing() -> None:
    spec = preview_spec(W1)
    assert (spec.kind, spec.workspace, spec.waited_for, spec.confirmed) == ("preview", W1, True, False)


# -- the answer -------------------------------------------------------------------------------------------------------


def test_a_prepared_plan_is_its_files_fingerprint_and_new_errors_and_its_engine_is_retired(make: Any) -> None:
    err = {"key": "c" * 64, "type": "ValidationError", "message": "m", "path": "/L/main.bean", "line": 4}
    rig = make({"preview": [answer(new_errors=[err], errors=2)]})
    value = rig.preview()
    assert isinstance(value, Prepared), value
    assert [f.path for f in value.files] == ["/L/main.bean"]
    assert value.fingerprint == FINGERPRINT
    assert [e.key for e in value.new_errors] == ["c" * 64]
    assert (value.errors, value.tries) == (2, 1)
    assert rig.engine.ends[-1][1] == "retire"  # it ran the ledger's plugins (§5.1)


@pytest.mark.parametrize("step", ["apply", "parse", "diff", "spans"])
def test_a_text_that_is_not_the_intended_change_is_refused_unintended(make: Any, step: str) -> None:
    rig = make({"preview": [refused(step, "not_intended_change")]})
    assert error_of(rig.preview()) == (
        ErrorCode.WRITE_REFUSED_UNINTENDED_CHANGE,
        ContractStepDetails(cast("ContractStep", step)),
    )


@pytest.mark.parametrize(
    ("step", "reason"),
    [
        ("plan", "unknown_mutator"),
        ("plan", "fault"),
        ("load", "plugin-refused"),
        ("load", "number-failed"),
        ("load", "undecidable"),
        ("load", "fault"),
        ("errors", "fault"),
        ("diff", "fault"),  # a fault in a step that checks the text is a check that did not run, never "not intended"
    ],
)
def test_a_check_that_could_not_run_is_refused_check_failed(make: Any, step: str, reason: str) -> None:
    rig = make({"preview": [refused(step, reason)]})
    assert error_of(rig.preview()) == (
        ErrorCode.WRITE_REFUSED_CHECK_FAILED,
        ContractStepDetails(cast("ContractStep", step)),
    )


@pytest.mark.parametrize(("step", "reason"), [("apply", "encrypted_file"), ("load", "encrypted")])
def test_an_encrypted_file_is_refused_with_its_own_code(make: Any, step: str, reason: str) -> None:
    rig = make({"preview": [refused(step, reason, "/L/secret.bean.gpg")]})
    assert error_of(rig.preview()) == (
        ErrorCode.ENCRYPTED_FILE_NOT_EDITABLE,
        EncryptedFileDetails("/L/secret.bean.gpg"),
    )


WARMING = (ErrorCode.WORKSPACE_NOT_READY, WorkspaceNotReadyDetails(phase="WARMING"))


@pytest.mark.parametrize(
    ("why", "hint"), [("unpublished", None), ("other_generation", None), ("not_fresh", HINT), ("not_fresh", None)]
)
def test_a_stale_baseline_is_workspace_not_ready_warming(make: Any, why: str, hint: str | None) -> None:
    rig = make({"preview": [stale(why, hint)]})
    assert error_of(rig.preview()) == WARMING
    assert [op for _, op in rig.engine.calls] == ["preview"]  # the caller re-plans on a new generation


# -- BL-0255 (S0118): a stale baseline queues the workspace's rebuild ------------------------------------------------


def wait_ops(rig: Rig, n: int) -> list[str]:
    """The engine's ops once it has had `n` calls (the fake notifies its condition on every call; EP-28)."""
    cond = rig.engine._cond  # pyright: ignore[reportPrivateUsage]
    with cond:
        assert cond.wait_for(lambda: len(rig.engine.calls) >= n, DEADLINE), rig.engine.calls
    return [op for _, op in rig.engine.calls]


def test_a_not_fresh_baseline_queues_the_rebuild_with_the_answers_hint(make: Any) -> None:
    rig = make({"preview": [stale("not_fresh")], "rebuild": [REBUILT]})
    assert error_of(rig.preview(submit_preview(rig.jobs, TARGET, ORDER, clock=CLOCK))) == WARMING  # unchanged
    assert rebuild_queued(rig) or "rebuild" in [op for _, op in rig.engine.calls]
    assert wait_ops(rig, 2) == ["preview", "rebuild"]
    _, _, params = rig.engine.params[-1]
    assert {k: params[k] for k in ("root", "fingerprint", "workspace_key", "lineage", "app_local_data")} == {
        "root": "/L/main.bean",
        "fingerprint": HINT,  # the engine's hint, not a plan's: no plan was made
        "workspace_key": W1,
        "lineage": "lin1",
        "app_local_data": "/app",
    }


def rebuild_queued(rig: Rig) -> bool:
    """A rebuild is queued or running: the job submits it inside its work, so before its own answer."""
    q = rig.jobs.status().queue
    return any(j.kind == "rebuild" for j in q.queued) or (q.running is not None and q.running.job.kind == "rebuild")


def test_another_generation_queues_no_rebuild(make: Any) -> None:
    """The mirror may be fresh: the caller plans again on the current generation."""
    rig = make({"preview": [stale("other_generation", None)], "rebuild": [REBUILT]})
    assert error_of(rig.preview(submit_preview(rig.jobs, TARGET, ORDER, clock=CLOCK))) == WARMING
    assert not rebuild_queued(rig)
    assert [op for _, op in rig.engine.calls] == ["preview"]


@pytest.mark.parametrize("why", ["refused", "moved", "paused", "prepared", "unpublished", "not_fresh_no_hint"])
def test_no_other_answer_queues_a_rebuild(make: Any, why: str) -> None:
    """Round 1 (RT-F1): no mirror (`unpublished`), or one whose stamp is not this ledger's or cannot be read
    (`not_fresh` with no hint), queues nothing: a reopen builds it (engine-jobs §15)."""
    first = {
        "refused": refused("load", "fault"),
        "moved": MOVED,
        "paused": answer(outcome="paused", files=[], fingerprint=None),
        "prepared": answer(),
        "unpublished": stale("unpublished", None),
        "not_fresh_no_hint": stale("not_fresh", None),
    }[why]
    rig = make({"preview": [first, answer()], "rebuild": [REBUILT]})
    rig.preview(submit_preview(rig.jobs, TARGET, ORDER, clock=CLOCK))
    assert not rebuild_queued(rig)
    assert "rebuild" not in [op for _, op in rig.engine.calls]


def _filler(_ctx: JobContext) -> WorkResult:
    return WorkResult(None, ran_user_code=False)


def test_a_refused_submit_is_logged_and_the_answer_is_unchanged(make: Any) -> None:
    """A full queue (INV-10): the rebuild is not queued, the refusal is logged content-free (INV-23), nothing raised."""
    rig = make({"preview": [BLOCK, stale("not_fresh")]}, QueuePolicy(max_queued=1))
    ticket = submit_preview(rig.jobs, TARGET, ORDER, clock=CLOCK)
    wait_ops(rig, 1)
    filler = rig.jobs.submit(preview_spec(W1), _filler)  # the queue is now full
    assert isinstance(filler, JobTicket)
    rig.engine.release("preview")
    assert error_of(rig.preview(ticket)) == WARMING
    assert isinstance(ticket, JobTicket)
    assert [(n, f) for n, f in rig.logged if n.startswith("rebuild.")] == [
        ("rebuild.not_queued", {"job": ticket.id, "workspace_key": W1, "code": "ENGINE_QUEUE_FULL"})
    ]
    assert isinstance(filler.wait(DEADLINE), Done)
    assert "rebuild" not in [op for _, op in rig.engine.calls]


def test_a_rebuild_is_never_queued_for_a_workspace_no_longer_open(make: Any) -> None:
    """INV-28, INV-31: after a switch the old workspace's rebuild is refused `WORKSPACE_NOT_OPEN`, and logged."""
    rig = make({"preview": [BLOCK, stale("not_fresh")]})
    ticket = submit_preview(rig.jobs, TARGET, ORDER, clock=CLOCK)
    wait_ops(rig, 1)
    rig.jobs.workspace_opened("k2")
    rig.engine.release("preview")
    assert error_of(rig.preview(ticket)) == WARMING
    assert [(n, f["code"]) for n, f in rig.logged if n.startswith("rebuild.")] == [
        ("rebuild.not_queued", "WORKSPACE_NOT_OPEN")
    ]


def test_an_unfinished_save_is_writes_paused_earlier_unfinished(make: Any) -> None:
    rig = make({"preview": [answer(outcome="paused", files=[], fingerprint=None)]})
    assert error_of(rig.preview()) == (ErrorCode.WRITES_PAUSED, WritesPausedDetails("earlier_unfinished", None))


def test_a_moved_ledger_is_tried_again_in_a_fresh_engine(make: Any) -> None:
    rig = make({"preview": [MOVED, answer()]})
    value = rig.preview()
    assert isinstance(value, Prepared), value
    assert value.tries == 2
    serials = [serial for serial, op in rig.engine.calls if op == "preview"]
    assert len(set(serials)) == 2  # the first try ran the ledger's plugins: a fresh engine for the second


def test_a_ledger_that_keeps_moving_is_ledger_changing_after_three_tries(make: Any) -> None:
    rig = make({"preview": [MOVED] * MAX_TRIES})
    assert error_of(rig.preview()) == (ErrorCode.LEDGER_CHANGING, None)
    assert [op for _, op in rig.engine.calls].count("preview") == MAX_TRIES == 3


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(answer(files=[]), id="prepared without files"),
        pytest.param(answer(fingerprint=None), id="prepared without a fingerprint"),
        pytest.param(answer(step="load"), id="prepared naming a step"),
        pytest.param(answer(stale="not_fresh"), id="prepared naming a stale reason"),
        pytest.param(answer(outcome="refused", files=[], fingerprint=None), id="refused without a step"),
        pytest.param(refused("load", "x") | {"files": BLANK["files"]}, id="refused with files"),
        pytest.param(answer(outcome="stale", files=[], fingerprint=None), id="stale without a reason"),
        pytest.param(
            refused("load", "x")
            | {"new_errors": [{"key": SHA, "type": "T", "message": "m", "path": None, "line": None}]},
            id="refused listing new errors",
        ),
        pytest.param(answer(outcome="moved"), id="moved with files"),
        pytest.param(MOVED | {"fingerprint": HINT}, id="moved with a fingerprint"),
        pytest.param(refused("load", "x") | {"fingerprint": HINT}, id="refused with a fingerprint"),
        pytest.param(answer(outcome="paused", files=[]), id="paused with a fingerprint"),
        pytest.param(stale("other_generation"), id="another generation with a hint"),
        pytest.param(stale("unpublished"), id="unpublished with a hint"),
        pytest.param(stale("not_fresh") | {"files": BLANK["files"]}, id="stale with files"),
        pytest.param({"outcome": "prepared"}, id="a reply of the wrong shape"),
    ],
)
def test_an_answer_that_does_not_hold_together_is_internal(make: Any, bad: dict[str, object]) -> None:
    """The job's own answer (`NotPrepared`), never an exception in its code: a check that relies on an `assert` alone
    would let the answer through under `python -O` (S0118 round 1, mutant `prepared-without-fingerprint`)."""
    rig = make({"preview": [bad]})
    value = rig.preview()
    assert isinstance(value, NotPrepared), value
    assert error_of(value) == (ErrorCode.INTERNAL, None)


def test_an_engine_refusal_is_internal(make: Any) -> None:
    rig = make({"preview": [EngineRefusal(key="protocol.invalid_params", message="x")]})
    assert error_of(rig.preview()) == (ErrorCode.INTERNAL, None)


def test_an_engine_that_dies_is_engine_stopped(make: Any) -> None:
    rig = make({"preview": [CRASH]})
    assert error_of(rig.preview())[0] == ErrorCode.ENGINE_STOPPED
