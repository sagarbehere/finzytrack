"""The create job, the sidecar's side (`app.platform.engine_jobs.create`, `create_text`; `design/specs/engine-jobs.md`
§1 "Commit point", §4, §5.3, §8.2, §11; S0080), over the fake engine port with a real scheduler. Written from the
spec's lines:

- §1 "After the commit point the job finishes or is resolved from disk"; §8.2 "The manifest in place (step 7), not
  yet registered: an engine death leaves the job to register the workspace": a create whose engine died is read from
  disk by a fresh engine (`create_outcome`): `.finzytrack` with our bytes is created, anything else is not;
- §8.2 "A failure or Cancel at steps 2-6 ends the job with 'Not created.' and, when files were written, … It adds
  'You can delete it.' only when the folder, listed when the job stops, holds nothing but the job's own files";
- §5.3 "never 'Your ledger files are safe' once the engine may have written": with no engine to read the disk past
  the commit point, the answer is `CHANGE_OUTCOME_UNKNOWN` (no Retry);
- §8.2 step 7's text for a `.finzytrack` that appeared meanwhile, and §11's for a non-empty folder.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from typing import Any, get_args, override

import pytest

from app.domain.paths import WorkspacePath
from app.platform.engine import CreateAnswer, CreateFacts, EngineFailure, EngineRefusal, RefusalKey
from app.platform.engine_jobs import create_text
from app.platform.engine_jobs.create import (
    Created,
    CreateRequest,
    CreateValue,
    NewFile,
    NotCreated,
    NotCreatedReason,
    answer_of,
    create_outcome,
    create_spec,
    create_work,
    params_of,
)
from app.platform.engine_jobs.save import NotDone
from app.platform.engine_jobs.scheduler import Done, EngineJobs, JobTicket
from app.platform.errors import ErrorCode, spec_for
from app.workspaces.creation import manifest_files
from app.workspaces.format.manifest import Inside, Manifest, read_manifest
from tests.platform.fake_job_engine import CRASH, CRASH_TO_BOUND, GATE_DIE, FakeEngine, Gated

DEADLINE = 20.0
FOLDER = "/u/Books"
MANIFEST = manifest_files(WorkspacePath.parse("main.beancount"))
REQUEST = CreateRequest(
    target=FOLDER,
    files=(NewFile("main.beancount", "2026-01-01 open Assets:Cash EUR\n"),),
    root="main.beancount",
    manifest=MANIFEST,
)
BLANK: dict[str, object] = {
    "outcome": "created",
    "reason": None,
    "folder": FOLDER,
    "path": None,
    "detail": None,
    "errno": None,
    "left": [],
}


def answer(**kw: object) -> dict[str, object]:
    return {**BLANK, **kw}


def facts(manifest: str, *, left: list[str] | None = None) -> dict[str, object]:
    return {"manifest": manifest, "left": [] if left is None else left, "errno": None}


class Rig:
    def __init__(self, scripted: dict[str, list[object]]) -> None:
        self.engine = FakeEngine(scripted=scripted)
        self.jobs = EngineJobs(self.engine, wait_engine_lock=lambda: None)
        self.engine.spawned = self.jobs.engine_spawned
        self.engine.ended = self.jobs.engine_ended
        boot = self.jobs.start()
        assert isinstance(boot, JobTicket)
        assert boot.wait(DEADLINE) is not None

    def create(self) -> CreateValue:
        ticket = self.jobs.submit(create_spec(), create_work(REQUEST))
        assert isinstance(ticket, JobTicket), ticket
        out = ticket.wait(DEADLINE)
        assert isinstance(out, Done), out
        assert isinstance(out.value, Created | NotCreated | NotDone), out.value
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


def code(value: CreateValue) -> str:
    assert isinstance(value, NotDone), value
    return value.error.code


# -- the engine's answer --------------------------------------------------------------------------------------------


def test_a_create_that_lands_is_created_and_its_engine_is_retired(make: Any) -> None:
    rig = make({"create": [Gated(answer())]})
    assert rig.create() == Created(FOLDER)
    assert rig.engine.proceeded == 1
    assert [op for _, op in rig.engine.calls] == ["create"]
    assert any(reason == "retire" for _, reason in rig.engine.ends), "its load ran user code: a fresh engine next"
    [(_, _, params)] = rig.engine.params
    assert params == params_of(REQUEST, params["job"])


def test_a_refusal_before_the_commit_point_is_not_created_naming_what_it_left(make: Any) -> None:
    rig = make({"create": [answer(outcome="not_created", reason="load_errors", detail="2", left=["main.beancount"])]})
    value = rig.create()
    assert value == NotCreated("load_errors", FOLDER, ("main.beancount",), detail="2")
    assert rig.engine.proceeded == 0
    assert create_text.text(value).endswith("Check them, and remove any you do not need.")


@pytest.mark.parametrize(
    ("on_disk", "want"),
    [
        (facts("ours"), Created(FOLDER)),
        (facts("absent"), NotCreated("stopped", FOLDER, ())),
        (facts("foreign", left=["main.beancount"]), NotCreated("manifest_taken", FOLDER, ("main.beancount",))),
    ],
)
def test_a_create_cut_past_its_commit_point_is_told_what_the_disk_says(
    make: Any, on_disk: dict[str, object], want: object
) -> None:
    rig = make({"create": [GATE_DIE], "create_outcome": [on_disk]})
    assert rig.create() == want
    calls = rig.engine.calls
    assert [op for _, op in calls] == ["create", "create_outcome"]
    assert calls[0][0] != calls[1][0], "the outcome is read by a fresh engine"
    first, second = rig.engine.params
    assert second[2] == first[2], "the read names the same job, folder and files"


def test_an_engine_death_before_the_commit_point_is_read_from_disk_naming_what_may_be_ours(make: Any) -> None:
    rig = make({"create": [CRASH], "create_outcome": [facts("absent", left=["main.beancount", "prices"])]})
    value = rig.create()
    assert value == NotCreated("stopped", FOLDER, ("main.beancount", "prices"))
    assert create_text.text(value) == (
        "Not created. Finzytrack stopped before it finished. Finzytrack left these in /u/Books because it could not "
        "be sure they are only its own: main.beancount, prices. Check them, and remove any you do not need."
    )


def test_a_foreign_manifest_before_the_commit_point_is_still_not_created(make: Any) -> None:
    rig = make({"create": [CRASH], "create_outcome": [facts("foreign")]})
    assert rig.create() == NotCreated("stopped", FOLDER, ())


def test_the_outcome_engine_dying_too_asks_for_another(make: Any) -> None:
    rig = make({"create": [GATE_DIE], "create_outcome": [CRASH, facts("ours")]})
    assert rig.create() == Created(FOLDER)
    assert [op for _, op in rig.engine.calls] == ["create", "create_outcome", "create_outcome"]


def test_no_engine_to_read_the_outcome_past_the_commit_point_is_change_outcome_unknown(make: Any) -> None:
    rig = make({"create": [GATE_DIE], "create_outcome": [CRASH_TO_BOUND]})
    assert code(rig.create()) == ErrorCode.CHANGE_OUTCOME_UNKNOWN
    unknown = spec_for(ErrorCode.CHANGE_OUTCOME_UNKNOWN)
    assert unknown is not None
    assert not unknown.data_safe
    assert not unknown.retryable


def test_no_engine_to_read_the_outcome_before_the_commit_point_is_not_created_unlisted(make: Any) -> None:
    """Nothing past the gate ran, so no `.finzytrack` was written: "Not created" is true, but the folder was not
    listed, so the text says to check it first."""
    rig = make({"create": [CRASH], "create_outcome": [CRASH_TO_BOUND]})
    value = rig.create()
    assert value == NotCreated("stopped", FOLDER, None)
    assert create_text.text(value).endswith("Check it before you delete anything.")


def test_an_outcome_that_cannot_be_read_is_unknown_past_the_commit_point(make: Any) -> None:
    rig = make({"create": [GATE_DIE], "create_outcome": [{"manifest": "unknown", "left": None, "errno": 5}]})
    assert code(rig.create()) == ErrorCode.CHANGE_OUTCOME_UNKNOWN


def test_an_engine_refusal_past_the_commit_point_reads_the_disk(make: Any) -> None:
    refusal = EngineRefusal("engine.internal", "boom")
    rig = make({"create": [Gated(refusal)], "create_outcome": [facts("ours")]})
    assert rig.create() == Created(FOLDER)


def test_a_folder_that_cannot_be_read_before_the_commit_point_says_to_check_it() -> None:
    """Verifier r1 V2, red team r1 RT3: the engine's `unknown` comes with `check`; the text asks to check the folder,
    it never drops the folder's advice."""
    value = create_outcome(CreateFacts("unknown", None, 13), gated=False, folder=FOLDER)
    assert value == NotCreated("stopped", FOLDER, None, errno=13)
    assert isinstance(value, NotCreated)
    assert create_text.text(value).endswith("Check it before you delete anything.")


@pytest.mark.parametrize("key", ["protocol.invalid_params", "engine.no_engine_lock"])
def test_a_request_the_engine_would_not_take_is_a_finzytrack_fault_and_reads_nothing(
    make: Any, key: RefusalKey
) -> None:
    """S0080 r1 question 2: a malformed plan (`BadPlanError`) or params are refused before anything is read or
    written, so the answer is `INTERNAL`, never "Finzytrack stopped before it finished"."""
    rig = make({"create": [EngineRefusal(key, "no")]})
    value = rig.create()
    assert isinstance(value, NotDone)
    assert value.error.code == ErrorCode.INTERNAL
    assert [op for _, op in rig.engine.calls] == ["create"]


def test_an_engine_refusal_before_the_commit_point_reads_the_disk_for_the_advice(make: Any) -> None:
    refusal = EngineRefusal("engine.internal", "boom")
    rig = make({"create": [refusal], "create_outcome": [facts("absent")]})
    assert rig.create() == NotCreated("stopped", FOLDER, ())


def test_a_reply_of_the_wrong_shape_reads_the_disk(make: Any) -> None:
    rig = make({"create": [Gated({"outcome": "created"})], "create_outcome": [facts("ours")]})
    assert rig.create() == Created(FOLDER)


def test_a_create_whose_first_engine_does_not_start_is_answered(make: Any) -> None:
    """The table keeps a writing job whose engine died, a failed start included, so its work must run even then."""
    rig = make({"create_outcome": [facts("absent")]})
    failures = [EngineFailure("start_failed", "the handshake was refused")]

    def first_start_fails() -> None:
        rig.engine.start_failure = failures.pop() if failures else None

    rig.engine.before_start = first_start_fails
    rig.engine.die()
    for _ in range(int(DEADLINE / 0.01)):
        if rig.jobs.status().queue.slot == "none":
            break
        threading.Event().wait(0.01)
    assert rig.create() == NotCreated("stopped", FOLDER, ())
    assert [op for _, op in rig.engine.calls] == ["create_outcome"]


def test_a_request_the_client_could_not_send_is_internal() -> None:
    class TooLarge(FakeEngine):
        @override
        def call(self, op: str, params: Any, **kw: Any) -> Any:
            return EngineFailure("too_large", "the request line is too long")

    engine = TooLarge()
    jobs = EngineJobs(engine, wait_engine_lock=lambda: None)
    engine.spawned = jobs.engine_spawned
    engine.ended = jobs.engine_ended
    try:
        boot = jobs.start()
        assert isinstance(boot, JobTicket)
        boot.wait(DEADLINE)
        ticket = jobs.submit(create_spec(), create_work(REQUEST))
        assert isinstance(ticket, JobTicket)
        out = ticket.wait(DEADLINE)
        assert isinstance(out, Done)
        assert isinstance(out.value, NotDone)
        assert out.value.error.code == ErrorCode.INTERNAL
    finally:
        jobs.close()


# -- the pure parts ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("manifest", "gated", "want"),
    [
        ("ours", True, "created"),
        ("ours", False, "created"),
        ("absent", True, "stopped"),
        ("absent", False, "stopped"),
        ("foreign", True, "manifest_taken"),
        ("foreign", False, "stopped"),
        ("unknown", True, "CHANGE_OUTCOME_UNKNOWN"),
        ("unknown", False, "stopped"),
    ],
)
def test_create_outcome_is_the_disks_answer(manifest: str, gated: bool, want: str) -> None:
    value = create_outcome(CreateFacts(manifest, None, None), gated=gated, folder=FOLDER)  # type: ignore[arg-type]
    match value:
        case Created():
            assert want == "created"
        case NotCreated():
            assert value.reason == want
        case NotDone():
            assert value.error.code == want


def test_create_outcome_never_says_created_but_for_our_manifest() -> None:
    for manifest in ("absent", "foreign", "unknown"):
        for gated in (False, True):
            value = create_outcome(CreateFacts(manifest, ("main.beancount",), None), gated=gated, folder=FOLDER)
            assert not isinstance(value, Created)


def test_answer_of_never_says_created_but_for_created() -> None:
    for reason in ("not_empty", "taken", "read_back", "load_errors", "manifest_taken", "io_error"):
        a = CreateAnswer("not_created", reason, FOLDER, None, None, None, ())
        assert isinstance(answer_of(a), NotCreated)
    assert answer_of(CreateAnswer("created", None, FOLDER, None, None, None, ())) == Created(FOLDER)


def test_the_manifest_a_create_writes_reads_back_with_a_new_lineage() -> None:
    (only,) = MANIFEST
    assert only.path == "workspace.json"
    read = read_manifest(only.text.encode("utf-8"))
    assert isinstance(read, Manifest)
    assert read.ledger == Inside(WorkspacePath.parse("main.beancount"))
    assert str(uuid.UUID(read.lineage_id)) == read.lineage_id
    (again,) = manifest_files(WorkspacePath.parse("main.beancount"))
    other = read_manifest(again.text.encode("utf-8"))
    assert isinstance(other, Manifest)
    assert other.lineage_id != read.lineage_id, "every new workspace gets its own lineage"


def test_every_create_has_its_own_job_name(make: Any) -> None:
    rig = make({"create": [Gated(answer()), Gated(answer())]})
    rig.create()
    rig.create()
    names = [p[2]["job"] for p in rig.engine.params]
    assert len(set(names)) == 2


# -- the texts -----------------------------------------------------------------------------------------------------


def test_a_non_empty_folder_says_to_choose_another() -> None:
    value = NotCreated("not_empty", FOLDER, ())
    assert create_text.text(value) == "This folder is not empty. Choose an empty folder or a new name."


def test_a_manifest_that_appeared_says_to_check_the_folder() -> None:
    value = NotCreated("manifest_taken", FOLDER, ())
    assert create_text.text(value) == (
        "Not created. Another program put a Finzytrack workspace file into /u/Books at the same moment. Check the "
        "folder before you use it."
    )


def test_the_text_names_what_the_job_left_and_never_says_to_delete_anything() -> None:
    """Owner, 2026-11-07 (the middle path): the job removes what it made; what it kept because it could not be sure
    is named with the advice to check it; nothing left, nothing said of the folder (verifier r2 F1)."""
    named = create_text.text(NotCreated("taken", FOLDER, ("main.beancount",), path="/u/Books/main.beancount"))
    unknown = create_text.text(NotCreated("stopped", FOLDER, None))
    nothing = create_text.text(NotCreated("load_failed", FOLDER, ()))
    assert named.endswith(
        "Finzytrack left these in /u/Books because it could not be sure they are only its own: main.beancount. "
        "Check them, and remove any you do not need."
    )
    assert unknown.endswith("Check it before you delete anything.")
    assert nothing == "Not created. Finzytrack could not check the new ledger."
    for text in (named, unknown, nothing):
        assert "You can delete it." not in text


def test_a_long_list_of_kept_names_shows_five_and_how_many_more() -> None:
    left = tuple(f"f{i}.beancount" for i in range(8))
    text = create_text.text(NotCreated("stopped", FOLDER, left))
    assert "f0.beancount, f1.beancount, f2.beancount, f3.beancount, f4.beancount and 3 more." in text
    assert "f5.beancount" not in text


def test_a_folder_the_job_wrote_into_but_could_not_list_says_to_check_it_and_blames_no_one() -> None:
    text = create_text.text(NotCreated("io_error", FOLDER, None, path="/u/Books/x"))
    assert "You can delete it." not in text
    assert "left these" not in text, "it was not listed: it names nothing"
    assert text.endswith("Check it before you delete anything.")


def test_a_cancelled_create_says_to_check_the_folder_first() -> None:
    text = create_text.cancelled_text(FOLDER)
    assert text.startswith("Not created.")
    assert "You can delete it." not in text
    assert text.endswith("Check it before you delete anything.")


@pytest.mark.parametrize("reason", sorted(get_args(NotCreatedReason.__value__)))
def test_every_reason_has_its_words(reason: str) -> None:
    text = create_text.text(NotCreated(reason, FOLDER, (), path="/u/Books/x"))  # type: ignore[arg-type]
    assert text
    assert "None" not in text
