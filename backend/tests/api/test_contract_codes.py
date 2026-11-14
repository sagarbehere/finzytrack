"""The write contract's codes through the API (S0117; BL-0106; ARCHITECTURE.md §6.1's `ContractRefused` row, §6.2,
§6.7; `design/specs/engine-jobs.md` §11).

No product route previews or confirms yet (each mutator's slice adds its routes), so a test-only route runs the use
cases (`app.workspaces.writes`) on the fake engine with a real scheduler (the rig of `tests/workspaces/
test_writes.py`) and raises the refusal it ends with, as a product route will. BL-0106: "`ContractRefused` in the
error taxonomy with its codes (`ACK_REQUIRED {error_keys}` at 409, the refusals at 422), so the class -> status table
of `app.api.errors` gains a per-code status where the class has two". The other codes the confirm adds keep the
classes ARCHITECTURE §6.1 gives them: `PLAN_EXPIRED` `NotFound` (404), `PREVIEW_STALE` `Conflict` (409),
`UNPROMOTED_MUTATOR_ON_REAL_LEDGER` `Forbidden` (403). The preview's `stale` is `WORKSPACE_NOT_READY {WARMING}`.
"""

from __future__ import annotations

import dataclasses
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from jsonschema import Draft202012Validator

from app.api.envelope import Envelope
from app.api.errors import status_for
from app.api.routes import declare
from app.domain.errors import DomainError, ErrorClass
from app.platform.engine_jobs.preview import NotPrepared
from app.platform.engine_jobs.save import NotDone, Saved
from app.platform.engine_jobs.scheduler import Done, Failed, JobTicket
from app.platform.errors import ErrorCode, spec_for
from app.workspaces.access import Unknown
from app.workspaces.writes import submit_preview
from tests.api.support import Counted, body, client, make_app
from tests.workspaces.test_writes import KEY_A, MUTATOR, Rig

DEADLINE = 20.0


class Scenarios:
    def __init__(self, tmp: Path) -> None:
        self.rig = Rig(tmp)

    def close(self) -> None:
        self.rig.jobs.close()

    def run(self, name: str) -> DomainError:
        out = getattr(self, name)()
        assert isinstance(out, NotDone | Failed | NotPrepared), out
        return out.error

    def _preview_error(self, answer: dict[str, object]) -> object:
        self.rig.engine.scripted["preview"].append(answer)
        ticket = submit_preview(self.rig.services, self.rig.target, 3, MUTATOR, {"edits": []})
        assert isinstance(ticket, JobTicket)
        out = ticket.wait(DEADLINE)
        assert isinstance(out, Done)
        return out.value

    def _refused(self, step: str, reason: str) -> dict[str, object]:
        return self.rig.answer(
            outcome="refused", files=[], fingerprint=None, step=step, reason=reason, path="/L/a.gpg", detail="d"
        )

    def ack_required(self) -> object:
        p = self.rig.preview(self.rig.answer(new_errors=(KEY_A,)))
        return self.rig.confirm(p)

    def unintended(self) -> object:
        return self._preview_error(self._refused("diff", "not_intended_change"))

    def check_failed(self) -> object:
        return self._preview_error(self._refused("load", "plugin-refused"))

    def encrypted(self) -> object:
        return self._preview_error(self._refused("apply", "encrypted_file"))

    def stale(self) -> object:
        return self._preview_error(self.rig.answer(outcome="stale", files=[], fingerprint=None, stale="not_fresh"))

    def already_confirmed(self) -> object:
        p = self.rig.preview()
        self.rig.script_saved()
        assert self.rig.confirm(p) == Saved(published=False)
        return self.rig.confirm(p)

    def ack_unexpected(self) -> object:
        p = self.rig.preview()
        return self.rig.confirm(p, acks=frozenset({KEY_A}))

    def plan_unknown(self) -> object:
        p = self.rig.preview()
        return self.rig.confirm(dataclasses.replace(p, plan_id="plan-from-an-earlier-run"))

    def plan_expired(self) -> object:
        p = self.rig.preview()
        self.rig.plans.workspace_closed()
        return self.rig.confirm(p)

    def preview_stale(self) -> object:
        p = self.rig.preview()
        return self.rig.confirm(p, preview_hash="0" * 64)

    def unpromoted(self) -> object:
        real = dataclasses.replace(self.rig.target, copy=None)
        p = self.rig.preview(target=real)
        return self.rig.confirm(p, target=real)


@pytest.fixture
def scenarios() -> Iterator[Scenarios]:
    with tempfile.TemporaryDirectory() as tmp:
        s = Scenarios(Path(tmp))
        yield s
        s.close()


def app_for(scenarios: Scenarios) -> FastAPI:
    router = APIRouter(prefix="/api/test")

    @router.get("/contract/{name}", openapi_extra=declare("app"))
    async def contract(name: str) -> Envelope[Counted]:
        raise scenarios.run(name)

    app, _ = make_app(Unknown(), routers=(router,))
    return app


def answer(scenarios: Scenarios, name: str) -> tuple[int, dict[str, Any]]:
    app = app_for(scenarios)
    r = client(app).get(f"/api/test/contract/{name}")
    payload = body(r)
    doc = app.openapi()
    Draft202012Validator({"$ref": "#/components/schemas/ErrorEnvelope", "components": doc["components"]}).validate(  # pyright: ignore[reportUnknownMemberType] (jsonschema's stubs)
        payload
    )
    return r.status_code, payload["error"]


@pytest.mark.error_code("ACK_REQUIRED")
def test_new_errors_not_acknowledged_are_ack_required_at_409_with_their_keys(scenarios: Scenarios) -> None:
    status, error = answer(scenarios, "ack_required")
    assert status == 409
    assert (error["code"], error["class"]) == ("ACK_REQUIRED", "contract_refused")
    assert error["details"] == {"error_keys": [KEY_A]}
    assert error["data_safe"] is True
    assert error["retryable"] is False


@pytest.mark.error_code("WRITE_REFUSED_UNINTENDED_CHANGE")
def test_a_text_that_is_not_the_intended_change_is_refused_at_422(scenarios: Scenarios) -> None:
    status, error = answer(scenarios, "unintended")
    assert status == 422
    assert (error["code"], error["class"]) == ("WRITE_REFUSED_UNINTENDED_CHANGE", "contract_refused")
    assert error["details"] == {"step": "diff"}
    assert error["data_safe"] is True


@pytest.mark.error_code("WRITE_REFUSED_CHECK_FAILED")
def test_a_check_that_could_not_run_is_refused_at_422(scenarios: Scenarios) -> None:
    status, error = answer(scenarios, "check_failed")
    assert status == 422
    assert (error["code"], error["class"]) == ("WRITE_REFUSED_CHECK_FAILED", "contract_refused")
    assert error["details"] == {"step": "load"}


@pytest.mark.error_code("ENCRYPTED_FILE_NOT_EDITABLE")
def test_an_encrypted_file_is_refused_at_422_naming_it(scenarios: Scenarios) -> None:
    status, error = answer(scenarios, "encrypted")
    assert status == 422
    assert (error["code"], error["class"]) == ("ENCRYPTED_FILE_NOT_EDITABLE", "contract_refused")
    assert error["details"] == {"path": "/L/a.gpg"}


@pytest.mark.error_code("PLAN_EXPIRED")
def test_a_plan_that_is_gone_is_plan_expired_at_404(scenarios: Scenarios) -> None:
    status, error = answer(scenarios, "plan_expired")
    assert status == 404
    assert (error["code"], error["class"]) == ("PLAN_EXPIRED", "not_found")
    assert error["details"] is None


@pytest.mark.error_code("PLAN_ALREADY_CONFIRMED")
def test_a_second_confirm_of_a_confirmed_plan_is_already_confirmed_at_409(scenarios: Scenarios) -> None:
    """S0117 round 1 (red team RT-F2): never "did not save" for a change that may be saved."""
    status, error = answer(scenarios, "already_confirmed")
    assert status == 409
    assert (error["code"], error["class"]) == ("PLAN_ALREADY_CONFIRMED", "conflict")
    assert "not save" not in f"{error['message']} {error['next_step']}"


@pytest.mark.error_code("ACK_UNEXPECTED")
def test_acknowledgements_where_none_is_listed_are_ack_unexpected_at_422(scenarios: Scenarios) -> None:
    """S0117 round 1 (verifier V2): refused, and the words do not say the change adds errors."""
    status, error = answer(scenarios, "ack_unexpected")
    assert status == 422
    assert (error["code"], error["class"]) == ("ACK_UNEXPECTED", "contract_refused")
    assert "adds no new errors" in error["message"]


@pytest.mark.error_code("PLAN_UNKNOWN")
def test_a_plan_id_this_run_never_knew_is_plan_unknown_and_asks_to_check_the_ledger(scenarios: Scenarios) -> None:
    """S0117 round 3 (verifier V-R3-5; red team RT2-F2): only here may an earlier confirm have saved the change."""
    status, error = answer(scenarios, "plan_unknown")
    assert status == 404
    assert error["code"] == "PLAN_UNKNOWN"
    assert "Check your ledger first" in error["next_step"]


def test_a_plan_let_go_unconfirmed_is_plan_expired_and_asks_for_a_new_preview(scenarios: Scenarios) -> None:
    status, error = answer(scenarios, "plan_expired")
    assert (status, error["code"], error["next_step"]) == (404, "PLAN_EXPIRED", "Preview the change again.")


@pytest.mark.error_code("PREVIEW_STALE")
def test_a_confirm_of_another_preview_is_preview_stale_at_409(scenarios: Scenarios) -> None:
    status, error = answer(scenarios, "preview_stale")
    assert status == 409
    assert (error["code"], error["class"]) == ("PREVIEW_STALE", "conflict")


@pytest.mark.error_code("UNPROMOTED_MUTATOR_ON_REAL_LEDGER")
def test_an_unpromoted_mutator_on_a_real_workspace_is_refused_at_403(scenarios: Scenarios) -> None:
    status, error = answer(scenarios, "unpromoted")
    assert status == 403
    assert (error["code"], error["class"]) == ("UNPROMOTED_MUTATOR_ON_REAL_LEDGER", "forbidden")
    assert error["details"] == {"mutator": MUTATOR}


def test_a_stale_preview_is_workspace_not_ready_warming_through_the_api(scenarios: Scenarios) -> None:
    status, error = answer(scenarios, "stale")
    assert status == 409
    assert error["code"] == "WORKSPACE_NOT_READY"
    assert error["details"] == {"phase": "WARMING"}


def test_contract_refused_has_two_statuses_by_code_and_every_other_class_one() -> None:
    """The one place that knows a status (`app.api.errors.status_for`): 409 for the code that needs the user's Save
    anyway, 422 for every refusal of the class."""
    contract = [s for s in all_specs() if s.error_class is ErrorClass.CONTRACT_REFUSED]
    assert {s.code: status_for(s.error_class, s.code) for s in contract} == {
        "ACK_REQUIRED": 409,
        "ACK_UNEXPECTED": 422,
        "WRITE_REFUSED_UNINTENDED_CHANGE": 422,
        "WRITE_REFUSED_CHECK_FAILED": 422,
        "ENCRYPTED_FILE_NOT_EDITABLE": 422,
    }
    for cls in ErrorClass:
        if cls is not ErrorClass.CONTRACT_REFUSED:
            assert len({status_for(s.error_class, s.code) for s in all_specs() if s.error_class is cls}) <= 1


def test_every_contract_refusal_says_nothing_was_written_without_saying_unchanged() -> None:
    """§11: "Your ledger is unchanged" only after a re-hash; these refusals happen before any write, so their words say
    Finzytrack did not save the change, never that the ledger is unchanged (another program may have changed it)."""
    for code in (
        ErrorCode.ACK_REQUIRED,
        ErrorCode.ACK_UNEXPECTED,
        ErrorCode.PLAN_ALREADY_CONFIRMED,
        ErrorCode.PLAN_UNKNOWN,
        ErrorCode.WRITE_REFUSED_UNINTENDED_CHANGE,
        ErrorCode.WRITE_REFUSED_CHECK_FAILED,
        ErrorCode.ENCRYPTED_FILE_NOT_EDITABLE,
        ErrorCode.PLAN_EXPIRED,
        ErrorCode.PREVIEW_STALE,
        ErrorCode.UNPROMOTED_MUTATOR_ON_REAL_LEDGER,
    ):
        spec = spec_for(code)
        assert spec is not None
        words = f"{spec.message} {spec.next_step}".lower()
        assert "unchanged" not in words, code
        assert spec.data_safe is True, code


def all_specs() -> list[Any]:
    from app.platform.errors import specs

    return list(specs())
