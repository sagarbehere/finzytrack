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
  together, or an engine refusal, is `INTERNAL` (INV-22: never a plan).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

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
)
from app.platform.engine_jobs.scheduler import Done, EngineJobs, Failed, JobTicket
from app.platform.errors import (
    ContractStep,
    ContractStepDetails,
    EncryptedFileDetails,
    ErrorCode,
    WorkspaceNotReadyDetails,
    WritesPausedDetails,
)
from tests.platform.fake_job_engine import CRASH, FakeEngine

DEADLINE = 20.0
W1 = "k1"
TARGET = PreviewTarget(workspace_key=W1, root="/L/main.bean", lineage="lin1", app_local_data=Path("/app"))
ORDER = PreviewOrder(generation=3, mutator="test.edits", request={"edits": []})
SHA = "a" * 64
FINGERPRINT = '{"document_files":[],"documents":[],"files":[],"globs":[],"modules":[],"root":"/L/main.bean","v":2}'
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

    def preview(self) -> PreviewValue | Failed:
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

    def build(scripted: dict[str, list[object]]) -> Rig:
        rigs.append(Rig(scripted))
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


@pytest.mark.parametrize("why", ["unpublished", "other_generation", "not_fresh"])
def test_a_stale_baseline_is_workspace_not_ready_warming(make: Any, why: str) -> None:
    rig = make({"preview": [answer(outcome="stale", files=[], fingerprint=None, stale=why)]})
    assert error_of(rig.preview()) == (ErrorCode.WORKSPACE_NOT_READY, WorkspaceNotReadyDetails(phase="WARMING"))
    assert [op for _, op in rig.engine.calls] == ["preview"]  # the caller re-plans on a new generation


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
        pytest.param({"outcome": "prepared"}, id="a reply of the wrong shape"),
    ],
)
def test_an_answer_that_does_not_hold_together_is_internal(make: Any, bad: dict[str, object]) -> None:
    rig = make({"preview": [bad]})
    assert error_of(rig.preview()) == (ErrorCode.INTERNAL, None)


def test_an_engine_refusal_is_internal(make: Any) -> None:
    rig = make({"preview": [EngineRefusal(key="protocol.invalid_params", message="x")]})
    assert error_of(rig.preview()) == (ErrorCode.INTERNAL, None)


def test_an_engine_that_dies_is_engine_stopped(make: Any) -> None:
    rig = make({"preview": [CRASH]})
    assert error_of(rig.preview())[0] == ErrorCode.ENGINE_STOPPED
