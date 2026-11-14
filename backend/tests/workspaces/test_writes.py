"""The write contract's preview and confirm use cases (`app.workspaces.writes`; S0117; ADR-0008 steps 4-7, ADR-0014,
ADR-0063, ADR-0067; ARCHITECTURE.md §9.2 steps 3-4; `design/specs/engine-jobs.md` §6.1, §11; BL-0106), over the fake
engine port with a real scheduler. Each rule below is the owner's ownership rule of 2026-11-14 (the S0117 brief), and
each must-not-pass case shows that no `save` reached the engine (so no byte was written: the save is the only op that
writes a ledger file; the same cases on a real disk are `test_writes_end_to_end.py`):

1. a confirm writes exactly the files and bytes of one plan this app's preview prepared, held in the `PlanStore`;
2. a plan is bound to its workspace and ledger state; it expires, is used once, and a switch drops it;
3. the confirm carries the preview's hash; new errors need acknowledgements that cover exactly their keys, else
   `ACK_REQUIRED {error_keys}` and nothing is written; an accounting error never blocks;
5. never a file outside the plan or the ledger set, never an encrypted file;
6. until a mutator is promoted, its plans are confirmed only on test copies (`UNPROMOTED_MUTATOR_ON_REAL_LEDGER`),
   refusing by default.
Rule 4 (the gate's re-check) and rule 7 ("saved" only once durable) are the save's own (`app.platform.engine_jobs.
save`, S0063), which the confirm runs unchanged; a fault in each of the use case's own steps fails closed (INV-22).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.platform.engine_jobs.preview import NotPrepared
from app.platform.engine_jobs.queue import JobSpec
from app.platform.engine_jobs.save import NotDone, Saved
from app.platform.engine_jobs.scheduler import Done, EngineJobs, Failed, JobTicket, WorkResult
from app.platform.errors import (
    AckRequiredDetails,
    ContractStepDetails,
    EncryptedFileDetails,
    ErrorCode,
    UnpromotedDetails,
)
from app.platform.plans import PlannedFile, PlanPolicy, PlanStore, WritePlan
from app.workspaces import writes
from app.workspaces.writes import (
    CopyMark,
    Previewed,
    WriteServices,
    WriteTarget,
    plan_problems,
    promotion_refusal,
    submit_confirm,
    submit_preview,
)
from tests.platform.fake_job_engine import FakeEngine, Gated
from tests.platform.test_rebuild_job import loaded

DEADLINE = 20.0
W1 = "k1"
T0 = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)
MUTATOR = "test.edits"
SAVED: dict[str, object] = {
    "outcome": "saved",
    "reason": None,
    "path": None,
    "entry": None,
    "detail": None,
    "unchanged": False,
    "changed": None,
    "open_journal": False,
    "cause": None,
    "cause_path": None,
    "published": False,
}
KEY_A, KEY_B = "a" * 64, "b" * 64


class MovingClock:
    def __init__(self) -> None:
        self.at = T0

    def now(self) -> datetime:
        return self.at


class Ids:
    def __init__(self) -> None:
        self.n = 0

    def new_id(self) -> str:
        self.n += 1
        return f"plan-{self.n}"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def fingerprint(root: str, files: dict[str, str]) -> str:
    """The engine's encoding (`ledger_engine.ledger.core.ledger_set.encode`, v2) of a set of files at these hashes."""
    value = {
        "v": 2,
        "root": root,
        "files": [[p, ["sha256", h]] for p, h in files.items()],
        "globs": [],
        "documents": [],
        "document_files": [],
        "modules": [],
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class Rig:
    """A temp test copy with one ledger file, a booted scheduler over the fake engine, the services and a target."""

    def __init__(self, tmp: Path) -> None:
        self.folder = tmp / "copy"
        self.folder.mkdir()
        self.main = self.folder / "main.bean"
        self.before = "2026-01-01 open Assets:Cash\n"
        self.after = self.before + '2026-01-02 note Assets:Cash "s0117"\n'
        self.main.write_text(self.before)
        self.engine = FakeEngine(scripted={"preview": [], "save": [], "rebuild": []})
        self.jobs = EngineJobs(self.engine, wait_engine_lock=lambda: None)
        self.engine.spawned = self.jobs.engine_spawned
        self.engine.ended = self.jobs.engine_ended
        boot = self.jobs.start()
        assert isinstance(boot, JobTicket)
        assert boot.wait(DEADLINE) is not None
        self.jobs.workspace_opened(W1)
        self.clock = MovingClock()
        self.plans = PlanStore(self.clock, PlanPolicy(ttl_seconds=15 * 60, max_plans=4))
        self.generation = 3
        self.services = WriteServices(
            jobs=self.jobs,
            plans=self.plans,
            clock=self.clock,
            ids=Ids(),
            session="s1",
            published_generation=lambda _t: self.generation,
        )
        self.target = WriteTarget(
            workspace=W1,
            root=str(self.main),
            lineage="lin1",
            app_local_data=tmp / "app",
            app_cache=tmp / "cache",
            copy=CopyMark(self.folder),
        )

    def answer(self, new_errors: tuple[str, ...] = (), **kw: object) -> dict[str, object]:
        path = str(self.main)
        out: dict[str, object] = {
            "outcome": "prepared",
            "files": [{"path": path, "pre": sha(self.before), "post": sha(self.after), "text": self.after}],
            "fingerprint": fingerprint(path, {path: sha(self.before)}),
            "new_errors": [
                {"key": k, "type": "ValidationError", "message": "m", "path": path, "line": 2} for k in new_errors
            ],
            "errors": len(new_errors),
            "step": None,
            "reason": None,
            "path": None,
            "detail": None,
            "line": None,
            "stale": None,
        }
        return {**out, **kw}

    def preview(self, answer: dict[str, object] | None = None, target: WriteTarget | None = None) -> Previewed:
        self.engine.scripted["preview"].append(answer if answer is not None else self.answer())
        ticket = submit_preview(self.services, target or self.target, self.generation, MUTATOR, {"edits": []})
        assert isinstance(ticket, JobTicket), ticket
        out = ticket.wait(DEADLINE)
        assert isinstance(out, Done), out
        assert isinstance(out.value, Previewed), out.value
        return out.value

    def confirm(
        self,
        p: Previewed,
        *,
        acks: frozenset[str] = frozenset(),
        target: WriteTarget | None = None,
        preview_hash: str | None = None,
    ) -> Saved | NotDone | Failed:
        out = submit_confirm(
            self.services, target or self.target, p.plan_id, preview_hash or p.preview_hash, acknowledged=acks
        )
        if isinstance(out, NotDone | Failed):
            return out
        done = out.wait(DEADLINE)
        assert done is not None
        if isinstance(done, Failed):
            return done
        assert isinstance(done, Done), done
        assert isinstance(done.value, Saved | NotDone), done.value
        return done.value

    def saves(self) -> int:
        return [op for _, op in self.engine.calls].count("save")

    def script_saved(self) -> None:
        self.engine.scripted["save"].append(Gated(SAVED))
        self.engine.scripted["rebuild"].append(loaded())


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path)
    yield r
    r.jobs.close()


def code_of(value: object) -> str:
    assert isinstance(value, NotDone | Failed | NotPrepared), value
    return value.error.code


def details_of(value: object) -> object:
    assert isinstance(value, NotDone | Failed | NotPrepared), value
    return value.error.details


# -- the confirm that saves -------------------------------------------------------------------------------------------


def test_a_previewed_plan_is_confirmed_by_the_save_of_exactly_its_files_and_bytes(rig: Rig) -> None:
    p = rig.preview()
    rig.script_saved()
    assert rig.confirm(p) == Saved(published=False)
    sent = [params for _, op, params in rig.engine.params if op == "save"]
    assert len(sent) == 1
    assert sent[0]["changes"] == [{"path": str(rig.main), "pre": sha(rig.before), "post": rig.after}]
    assert sent[0]["fingerprint"] == fingerprint(str(rig.main), {str(rig.main): sha(rig.before)})
    assert (sent[0]["workspace_key"], sent[0]["root"], sent[0]["session"]) == (W1, str(rig.main), "s1")


def test_a_landed_confirm_queues_the_background_rebuild(rig: Rig) -> None:
    p = rig.preview()
    rig.script_saved()
    assert isinstance(rig.confirm(p), Saved)
    for _ in range(int(DEADLINE / 0.01)):
        if "rebuild" in [op for _, op in rig.engine.calls]:
            break
        rig.engine.release_ends()
        threading_pause()
    assert "rebuild" in [op for _, op in rig.engine.calls]


def test_the_preview_answers_its_plan_id_hash_files_and_new_errors(rig: Rig) -> None:
    p = rig.preview(rig.answer(new_errors=(KEY_A,)))
    assert p.plan_id == "plan-1"
    stored = rig.plans.find(W1, p.plan_id)
    assert isinstance(stored, WritePlan)
    assert stored.preview_hash == p.preview_hash
    assert stored.new_error_keys == (KEY_A,)
    assert [e.key for e in p.new_errors] == [KEY_A]
    assert stored.generation == rig.generation
    assert stored.files == (PlannedFile(str(rig.main), sha(rig.before), sha(rig.after), rig.after),)


def test_new_errors_acknowledged_exactly_are_saved_anyway(rig: Rig) -> None:
    """ADR-0067: an accounting error never blocks; the user's Save anyway covers exactly the listed keys."""
    p = rig.preview(rig.answer(new_errors=(KEY_A, KEY_B)))
    rig.script_saved()
    assert rig.confirm(p, acks=frozenset({KEY_A, KEY_B})) == Saved(published=False)


def test_a_second_copy_of_one_error_key_is_acknowledged_by_that_key(rig: Rig) -> None:
    p = rig.preview(rig.answer(new_errors=(KEY_A, KEY_A)))
    rig.script_saved()
    assert rig.confirm(p, acks=frozenset({KEY_A})) == Saved(published=False)


# -- must not pass: nothing reaches the save --------------------------------------------------------------------------


def test_an_unknown_plan_is_plan_expired(rig: Rig) -> None:
    p = rig.preview()
    gone = dataclasses.replace(p, plan_id="plan-99")
    assert code_of(rig.confirm(gone)) == ErrorCode.PLAN_EXPIRED
    assert rig.saves() == 0


def test_an_expired_plan_is_plan_expired(rig: Rig) -> None:
    p = rig.preview()
    rig.clock.at = T0 + timedelta(minutes=15)
    rig.script_saved()
    assert code_of(rig.confirm(p)) == ErrorCode.PLAN_EXPIRED
    assert rig.saves() == 0


def test_a_plan_used_twice_is_refused_the_second_time(rig: Rig) -> None:
    p = rig.preview()
    rig.script_saved()
    assert isinstance(rig.confirm(p), Saved)
    rig.script_saved()
    assert code_of(rig.confirm(p)) == ErrorCode.PLAN_EXPIRED
    assert rig.saves() == 1


def test_a_plan_whose_save_was_refused_is_used_up_too(rig: Rig) -> None:
    """A confirm that reached the save uses its plan whatever the save answered: the ledger is planned again."""
    p = rig.preview()
    rig.engine.scripted["save"].append({**SAVED, "outcome": "refused", "reason": "ledger_changed", "unchanged": True})
    assert code_of(rig.confirm(p)) == ErrorCode.LEDGER_EXTERNALLY_CHANGED
    rig.script_saved()
    assert code_of(rig.confirm(p)) == ErrorCode.PLAN_EXPIRED
    assert rig.saves() == 1


def test_two_confirms_of_one_plan_queued_together_save_once(rig: Rig) -> None:
    """Both pass the checks before the queue (the plan is still held while they wait behind other work); the first job
    to start takes the plan, the second finds it used."""
    p = rig.preview()
    busy = JobSpec(kind="bql", workspace=W1, waited_for=True, confirmed=False, owner="user")
    blocker = rig.jobs.submit(busy, lambda ctx: WorkResult(ctx.call("block", {})))
    assert isinstance(blocker, JobTicket)
    threading_wait(lambda: "block" in [op for _, op in rig.engine.calls])
    first = submit_confirm(rig.services, rig.target, p.plan_id, p.preview_hash, acknowledged=frozenset())
    second = submit_confirm(rig.services, rig.target, p.plan_id, p.preview_hash, acknowledged=frozenset())
    assert isinstance(first, JobTicket)
    assert isinstance(second, JobTicket)
    rig.script_saved()
    rig.engine.release()
    out1, out2 = first.wait(DEADLINE), second.wait(DEADLINE)
    assert isinstance(out1, Done)
    assert out1.value == Saved(published=False)
    assert isinstance(out2, Done)
    assert code_of(out2.value) == ErrorCode.PLAN_EXPIRED
    assert rig.saves() == 1


def test_a_confirm_the_queue_refuses_keeps_its_plan(rig: Rig) -> None:
    """The plan is taken only when its save job starts: a confirm the queue refuses (here the workspace is not open)
    leaves it for the user's Try again."""
    p = rig.preview()
    rig.jobs.workspace_closed()
    out = rig.confirm(p)
    assert code_of(out) == ErrorCode.WORKSPACE_NOT_OPEN
    assert rig.plans.find(W1, p.plan_id) is not None
    assert rig.saves() == 0


def test_a_plan_of_another_workspace_is_plan_expired(rig: Rig) -> None:
    p = rig.preview()
    other = dataclasses.replace(rig.target, workspace="k2")
    assert code_of(rig.confirm(p, target=other)) == ErrorCode.PLAN_EXPIRED
    assert rig.saves() == 0


def test_a_plan_of_another_root_is_plan_expired(rig: Rig) -> None:
    p = rig.preview()
    other = dataclasses.replace(rig.target, root=str(rig.folder / "other.bean"))
    assert code_of(rig.confirm(p, target=other)) == ErrorCode.PLAN_EXPIRED
    assert rig.saves() == 0


@pytest.mark.parametrize("switch", ["closed", "another_opened"])
def test_a_plan_dropped_by_a_switch_is_plan_expired(rig: Rig, switch: str) -> None:
    p = rig.preview()
    if switch == "closed":
        rig.plans.workspace_closed()
    else:
        rig.plans.workspace_opened("k2")
    assert code_of(rig.confirm(p)) == ErrorCode.PLAN_EXPIRED
    assert rig.saves() == 0


def test_a_wrong_preview_hash_is_preview_stale(rig: Rig) -> None:
    p = rig.preview()
    assert code_of(rig.confirm(p, preview_hash="0" * 64)) == ErrorCode.PREVIEW_STALE
    assert rig.saves() == 0
    rig.script_saved()
    assert isinstance(rig.confirm(p), Saved)  # a wrong hash did not use the plan up


def test_a_plan_made_on_another_generation_is_preview_stale(rig: Rig) -> None:
    """Rule 2: bound to the mirror generation it was made on, read again inside the save's job (one job at a time:
    no publish between that read and the save's gate)."""
    p = rig.preview()
    rig.generation = 4
    assert code_of(rig.confirm(p)) == ErrorCode.PREVIEW_STALE
    assert rig.saves() == 0


def no_generation(_t: WriteTarget) -> int | None:
    return None


def test_no_published_generation_at_the_confirm_is_preview_stale(rig: Rig) -> None:
    p = rig.preview()
    rig.services = dataclasses.replace(rig.services, published_generation=no_generation)
    assert code_of(rig.confirm(p)) == ErrorCode.PREVIEW_STALE
    assert rig.saves() == 0


@pytest.mark.parametrize(
    ("listed", "acks"),
    [
        pytest.param((KEY_A,), frozenset[str](), id="missing"),
        pytest.param((KEY_A, KEY_B), frozenset({KEY_A}), id="one missing"),
        pytest.param((KEY_A,), frozenset({KEY_A, KEY_B}), id="one added"),
        pytest.param((KEY_A,), frozenset({KEY_B}), id="different"),
        pytest.param((), frozenset({KEY_A}), id="added where none is listed"),
    ],
)
def test_acknowledgements_that_miss_add_or_differ_are_ack_required(
    rig: Rig, listed: tuple[str, ...], acks: frozenset[str]
) -> None:
    p = rig.preview(rig.answer(new_errors=listed))
    out = rig.confirm(p, acks=acks)
    assert code_of(out) == ErrorCode.ACK_REQUIRED
    assert details_of(out) == AckRequiredDetails(error_keys=tuple(dict.fromkeys(listed)))
    assert rig.saves() == 0
    rig.script_saved()
    assert isinstance(rig.confirm(p, acks=frozenset(listed)), Saved)  # ACK_REQUIRED keeps the plan for Save anyway


def test_a_real_workspace_with_no_promoted_mutator_is_refused_by_default(rig: Rig) -> None:
    """Rule 6, the guard's default: no test-copy mark (none exists before CAP-WS-TEST-COPY, M3) and no promoted
    mutator (`writes.PROMOTED` is empty) refuse every confirm."""
    real = dataclasses.replace(rig.target, copy=None)
    p = rig.preview(target=real)
    out = rig.confirm(p, target=real)
    assert code_of(out) == ErrorCode.UNPROMOTED_MUTATOR_ON_REAL_LEDGER
    assert details_of(out) == UnpromotedDetails(mutator=MUTATOR)
    assert rig.saves() == 0
    assert not writes.PROMOTED


def test_the_guard_comes_before_the_acknowledgements(rig: Rig) -> None:
    """Never ask the user to Save anyway for a change that would be refused anyway."""
    real = dataclasses.replace(rig.target, copy=None)
    p = rig.preview(rig.answer(new_errors=(KEY_A,)), target=real)
    assert code_of(rig.confirm(p, target=real)) == ErrorCode.UNPROMOTED_MUTATOR_ON_REAL_LEDGER


def test_a_promoted_mutator_is_confirmed_on_a_real_workspace(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(writes, "PROMOTED", frozenset({MUTATOR}))
    real = dataclasses.replace(rig.target, copy=None)
    p = rig.preview(target=real)
    rig.script_saved()
    assert isinstance(rig.confirm(p, target=real), Saved)


def test_a_test_copy_whose_file_lies_outside_its_folder_is_refused(rig: Rig, tmp_path: Path) -> None:
    elsewhere = dataclasses.replace(rig.target, copy=CopyMark(tmp_path / "another"))
    (tmp_path / "another").mkdir()
    p = rig.preview(target=elsewhere)
    assert code_of(rig.confirm(p, target=elsewhere)) == ErrorCode.UNPROMOTED_MUTATOR_ON_REAL_LEDGER
    assert rig.saves() == 0


def test_a_test_copy_file_that_links_out_of_its_folder_is_refused(rig: Rig, tmp_path: Path) -> None:
    """The save writes a linked file at its real path (ADR-0008 step 5): a link in the copy to a real ledger would
    write the real ledger."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "main.bean").write_text(rig.before)
    rig.main.unlink()
    rig.main.symlink_to(real / "main.bean")
    p = rig.preview()
    assert code_of(rig.confirm(p)) == ErrorCode.UNPROMOTED_MUTATOR_ON_REAL_LEDGER
    assert rig.saves() == 0
    assert (real / "main.bean").read_text() == rig.before


def test_a_test_copy_folder_that_is_a_link_is_judged_by_where_it_leads(rig: Rig, tmp_path: Path) -> None:
    """The mark's folder and the file are both resolved: a copy reached through a link of its own is still inside."""
    link = tmp_path / "copy-link"
    link.symlink_to(rig.folder)
    p = rig.preview()
    rig.script_saved()
    assert isinstance(rig.confirm(p, target=dataclasses.replace(rig.target, copy=CopyMark(link))), Saved)


def test_a_folder_beside_the_copy_whose_name_starts_with_it_is_outside(tmp_path: Path) -> None:
    """`/x/copy-2/main.bean` is not inside `/x/copy` (a prefix of the text is not a parent folder)."""
    (tmp_path / "copy").mkdir()
    (tmp_path / "copy-2").mkdir()
    (tmp_path / "copy-2" / "main.bean").write_text("x")  # it resolves: only the folder test can refuse it
    target = WriteTarget("k1", "/r", "lin1", tmp_path, tmp_path, CopyMark(tmp_path / "copy"))
    f = PlannedFile(str(tmp_path / "copy-2" / "main.bean"), "a" * 64, "b" * 64, "x")
    p = make_plan(files=(f,))
    assert promotion_refusal(p, target) is not None


def test_a_resolution_that_fails_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """INV-22: a guard that cannot decide refuses."""
    (tmp_path / "main.bean").write_text("x")
    target = WriteTarget("k1", "/r", "lin1", tmp_path, tmp_path, CopyMark(tmp_path))
    p = make_plan(files=(PlannedFile(str(tmp_path / "main.bean"), "a" * 64, "b" * 64, "x"),))
    assert promotion_refusal(p, target) is None

    def broken(path: object, *, strict: bool = False) -> str:
        raise OSError(5, "I/O error")

    monkeypatch.setattr(os.path, "realpath", broken)
    assert promotion_refusal(p, target) is not None


# -- rule 5: the plan's own files ------------------------------------------------------------------------------------


def make_plan(**kw: object) -> WritePlan:
    from app.platform.plans import PlanId, new_plan

    path = "/L/main.bean"
    fields: dict[str, object] = {
        "plan_id": PlanId("p1"),
        "workspace": W1,
        "root": path,
        "mutator": MUTATOR,
        "generation": 3,
        "fingerprint": fingerprint(path, {path: "a" * 64}),
        "files": (PlannedFile(path, "a" * 64, sha("x"), "x"),),
        "new_error_keys": (),
        "made_at": T0,
    }
    fields.update(kw)
    return new_plan(**fields)  # pyright: ignore[reportArgumentType]


def test_a_plan_whose_texts_are_the_checked_ones_passes() -> None:
    assert plan_problems(make_plan()) is None


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(
            {"files": (PlannedFile("/L/main.bean", "a" * 64, sha("y"), "x"),)}, id="a text not the checked one"
        ),
        pytest.param(
            {"files": (PlannedFile("/L/main.bean", "a" * 64, sha("x"), "x"), PlannedFile("/L/b", "a" * 64, "0", "y"))},
            id="the second text not the checked one",
        ),
        pytest.param({"files": ()}, id="no file"),
    ],
)
def test_a_plan_whose_texts_are_not_the_checked_ones_is_refused(change: dict[str, object]) -> None:
    out = plan_problems(make_plan(**change))
    assert out is not None
    assert (out.code, out.details) == (ErrorCode.WRITE_REFUSED_CHECK_FAILED, ContractStepDetails("files"))


@pytest.mark.parametrize("name", ["main.bean.gpg", "main.bean.GPG", "main.asc", "x.Asc"])
def test_a_plan_that_writes_an_encrypted_file_is_refused(name: str) -> None:
    path = f"/L/{name}"
    p = make_plan(fingerprint=fingerprint(path, {path: "a" * 64}), files=(PlannedFile(path, "a" * 64, sha("x"), "x"),))
    out = plan_problems(p)
    assert out is not None
    assert (out.code, out.details) == (ErrorCode.ENCRYPTED_FILE_NOT_EDITABLE, EncryptedFileDetails(path))


def test_a_plan_with_a_changed_text_in_the_store_is_refused_before_the_save(rig: Rig) -> None:
    """Defence in depth: whatever put it in the store, a confirm checks the plan's texts again (the save receives only
    the text, never the hash the preview checked)."""
    p = rig.preview()
    stored = rig.plans.take(W1, p.plan_id)
    assert stored is not None
    bad = dataclasses.replace(stored, files=(dataclasses.replace(stored.files[0], text="; other\n"),))
    rig.plans.add(bad)
    assert code_of(rig.confirm(p)) == ErrorCode.WRITE_REFUSED_CHECK_FAILED
    assert rig.saves() == 0


def test_a_preview_whose_answer_breaks_rule_5_is_not_stored(rig: Rig) -> None:
    path = str(rig.main)
    files = [{"path": path, "pre": sha(rig.before), "post": sha(rig.after), "text": rig.after + "; not checked\n"}]
    rig.engine.scripted["preview"].append(rig.answer(files=files))
    ticket = submit_preview(rig.services, rig.target, rig.generation, MUTATOR, {"edits": []})
    assert isinstance(ticket, JobTicket)
    out = ticket.wait(DEADLINE)
    assert isinstance(out, Done)
    assert code_of(out.value) == ErrorCode.WRITE_REFUSED_CHECK_FAILED
    assert len(rig.plans) == 0


# -- INV-22: a fault in each step of the use cases fails closed -------------------------------------------------------


class Broken(Exception):
    pass


def boom(*_a: object, **_k: object) -> Any:
    raise Broken


def test_a_fault_storing_the_plan_ends_the_preview_internal_with_no_plan(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.engine.scripted["preview"].append(rig.answer())
    monkeypatch.setattr(rig.plans, "add", boom)
    ticket = submit_preview(rig.services, rig.target, rig.generation, MUTATOR, {"edits": []})
    assert isinstance(ticket, JobTicket)
    out = ticket.wait(DEADLINE)
    assert isinstance(out, Failed)
    assert out.error.code == ErrorCode.INTERNAL


def test_a_fault_making_the_plan_id_ends_the_preview_internal(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.engine.scripted["preview"].append(rig.answer())
    monkeypatch.setattr(rig.services.ids, "new_id", boom)
    ticket = submit_preview(rig.services, rig.target, rig.generation, MUTATOR, {"edits": []})
    assert isinstance(ticket, JobTicket)
    out = ticket.wait(DEADLINE)
    assert isinstance(out, Failed)
    assert len(rig.plans) == 0


@pytest.mark.parametrize("step", ["find", "plan_problems", "promotion_refusal"])
def test_a_fault_before_the_queue_raises_and_queues_no_save(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    """The edge answers an exception `INTERNAL` (ARCHITECTURE §6.3); no save job was queued."""
    p = rig.preview()
    if step == "find":
        monkeypatch.setattr(rig.plans, "find", boom)
    else:
        monkeypatch.setattr(writes, step, boom)
    with pytest.raises(Broken):
        submit_confirm(rig.services, rig.target, p.plan_id, p.preview_hash, acknowledged=frozenset())
    assert rig.saves() == 0


@pytest.mark.parametrize("step", ["take", "published_generation"])
def test_a_fault_inside_the_confirms_job_before_the_save_is_internal_and_saves_nothing(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    p = rig.preview()
    if step == "take":
        monkeypatch.setattr(rig.plans, "take", boom)
    else:
        rig.services = dataclasses.replace(rig.services, published_generation=boom)
    out = rig.confirm(p)
    assert code_of(out) == ErrorCode.INTERNAL
    assert rig.saves() == 0


# -- helpers ----------------------------------------------------------------------------------------------------------


def threading_pause() -> None:
    import threading

    threading.Event().wait(0.01)


def threading_wait(cond: Any) -> None:
    for _ in range(int(DEADLINE / 0.01)):
        if cond():
            return
        threading_pause()
    assert cond()
