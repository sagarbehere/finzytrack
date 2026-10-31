"""The save job, the sidecar's side (`app.platform.engine_jobs.save`, `save_text`; `design/specs/engine-jobs.md` §5.3,
§6.1, §6.6, §11; S0063), over the fake engine port with a real scheduler. Written from the spec's lines:

- §6.6 "When a save's engine ends without its final reply, the sidecar first `durable_sync`s the job's journal
  directory, then decides the outcome from disk by a pure function": `.closed` or later is saved; `.resolved` not
  saved; `.open` unfinished, writes paused, Reopen; no record not saved, "unchanged" only after the re-hash;
- §11 "'Your ledger is unchanged' is said only after the targets were hashed again and equal their pre bytes"; the
  rows of the save's refusals and their texts; after `created_path_taken` the Reopen text promises no undo;
- §5.3 "a save's outcome is resolved from disk (§6.6)": never "Your ledger files are safe" once the engine may have
  written (S0058 verifier F5); when no engine can read the disk, the answer says the outcome is not known.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.platform.engine import EngineFailure, EngineRefusal, OutcomeFacts, SaveAnswer, SaveOutcomeKind
from app.platform.engine_jobs import save_text
from app.platform.engine_jobs.recovery_text import REOPEN
from app.platform.engine_jobs.save import (
    ChangeText,
    NotDone,
    Saved,
    SaveRequest,
    SaveValue,
    answer_of,
    new_session_id,
    params_of,
    save_outcome,
    save_spec,
    save_work,
)
from app.platform.engine_jobs.scheduler import Done, EngineJobs, JobTicket
from app.platform.errors import (
    ChangeNotSavedDetails,
    EngineStoppedDetails,
    ErrorCode,
    WorkspaceNotReadyDetails,
    WritesPausedDetails,
)
from tests.platform.fake_job_engine import CRASH, CRASH_TO_BOUND, GATE_DIE, FakeEngine, Gated

REPO = Path(__file__).resolve().parents[3]
DEADLINE = 20.0
W1 = "ws-1"
MAIN = "/L/main.bean"
NEW = "/L/prices.bean"
REQUEST = SaveRequest(
    session="a1b2",
    workspace_key="k1",
    lineage="lin1",
    root=MAIN,
    changes=(ChangeText(MAIN, "0" * 64, "post\n"), ChangeText(NEW, None, "p\n")),
    fingerprint='{"document_files":[],"documents":[],"files":[],"globs":[],"root":"/L/main.bean","v":1}',
    mirror=None,
    legacy_locks=(),
    app_local_data=Path("/app"),
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


def facts(record: str, unchanged: bool | None = None, path: str | None = None) -> dict[str, object]:
    return {"record": record, "unchanged": unchanged, "path": path, "errno": None}


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

    def save(self) -> SaveValue:
        ticket = self.jobs.submit(save_spec(W1), save_work(REQUEST))
        assert isinstance(ticket, JobTicket), ticket
        out = ticket.wait(DEADLINE)
        assert isinstance(out, Done), out
        assert isinstance(out.value, Saved | NotDone), out.value
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


def code(value: SaveValue) -> str:
    assert isinstance(value, NotDone), value
    return value.error.code


def details(value: SaveValue) -> object:
    assert isinstance(value, NotDone), value
    return value.error.details


# -- the engine's answer --------------------------------------------------------------------------------------------


def test_a_save_that_lands_is_saved_and_its_engine_is_retired(make: Any) -> None:
    rig = make({"save": [Gated(answer())]})
    assert rig.save() == Saved(published=False)
    assert rig.engine.proceeded == 1
    assert [op for _, op in rig.engine.calls] == ["save"]
    assert (rig.engine.ends[-1][1]) == "retire"  # it ran the ledger's plugins (§5.1)


@pytest.mark.parametrize(
    ("result", "want_code", "want_details"),
    [
        (
            answer(outcome="refused", reason="unfinished_save", unchanged=True),
            "WRITES_PAUSED",
            WritesPausedDetails("unfinished_save", None),
        ),
        (
            answer(outcome="refused", reason="check_pending", unchanged=True),
            "WRITES_PAUSED",
            WritesPausedDetails("check_pending", None),
        ),
        (
            answer(outcome="refused", reason="check_failed", unchanged=True),
            "WRITES_PAUSED",
            WritesPausedDetails("check_failed", None),
        ),
        (answer(outcome="refused", reason="legacy_app_saving", unchanged=True), "LEGACY_APP_SAVING", None),
        (answer(outcome="refused", reason="ledger_changed"), "LEDGER_EXTERNALLY_CHANGED", None),
        (
            answer(outcome="refused", reason="mirror_stale", unchanged=True),
            "WORKSPACE_NOT_READY",
            WorkspaceNotReadyDetails("WARMING"),
        ),
        (answer(outcome="refused", reason="load_refused", unchanged=True), "INTERNAL", None),
        (answer(outcome="not_saved", reason="changed"), "LEDGER_EXTERNALLY_CHANGED", None),
        (answer(outcome="not_saved", reason="path_moved"), "LEDGER_EXTERNALLY_CHANGED", None),
        (
            answer(outcome="not_saved", reason="io_error", open_journal=True),
            "WRITES_PAUSED",
            WritesPausedDetails("unfinished_save", None),
        ),
        (
            answer(outcome="not_saved", reason="hard_linked", path=MAIN, unchanged=True),
            "CHANGE_NOT_SAVED",
            ChangeNotSavedDetails("hard_linked", MAIN, None, None, True),
        ),
        (
            answer(outcome="not_saved", reason="permission", entry="/L", detail="deny delete_child", unchanged=True),
            "CHANGE_NOT_SAVED",
            ChangeNotSavedDetails("permission", None, "/L", "deny delete_child", True),
        ),
        (
            answer(
                outcome="unfinished",
                reason="io_error",
                path=MAIN,
                open_journal=True,
                cause="created_path_taken",
                cause_path=NEW,
            ),
            "WRITES_PAUSED",
            WritesPausedDetails("unfinished_save", NEW),
        ),
        (
            answer(
                outcome="unfinished", reason="changed", path=MAIN, open_journal=True, cause="kept_copy", cause_path=MAIN
            ),
            "WRITES_PAUSED",
            WritesPausedDetails("unfinished_save", None),
        ),
        (answer(outcome="not_saved", reason="something_new"), "INTERNAL", None),
    ],
)
def test_each_engine_answer_is_its_code(
    make: Any, result: dict[str, object], want_code: str, want_details: object
) -> None:
    rig = make({"save": [Gated(result) if result["outcome"] in ("not_saved", "unfinished") else result]})
    value = rig.save()
    assert code(value) == want_code
    assert details(value) == want_details


def test_a_reply_of_the_wrong_shape_reads_the_outcome_from_disk(make: Any) -> None:
    """A malformed final reply is never taken for "saved": the disk decides (here: no record, so not saved)."""
    rig = make({"save": [Gated({"outcome": "saved"})], "save_outcome": [facts("none", unchanged=True)]})
    assert code(rig.save()) == ErrorCode.CHANGE_NOT_SAVED


# -- §6.6: the outcome after a death ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("on_disk", "want"),
    [
        (facts("closed"), Saved(False)),
        (facts("done"), Saved(False)),
        (facts("mismatch"), Saved(False)),
        (facts("open"), "WRITES_PAUSED"),
        (facts("resolved", unchanged=True), "CHANGE_NOT_SAVED"),
        (facts("none", unchanged=True), "CHANGE_NOT_SAVED"),
    ],
)
def test_a_save_cut_past_its_gate_is_told_what_the_disk_says(
    make: Any, on_disk: dict[str, object], want: object
) -> None:
    rig = make({"save": [GATE_DIE], "save_outcome": [on_disk]})
    value = rig.save()
    if isinstance(want, Saved):
        assert value == want
    else:
        assert code(value) == want
    calls = rig.engine.calls
    assert [op for _, op in calls] == ["save", "save_outcome"]
    assert calls[0][0] != calls[1][0], "the outcome is read by a fresh engine"
    [(_, _, params)] = [p for p in rig.engine.params if p[1] == "save_outcome"]
    assert params == {
        "app_local_data": "/app",
        "workspace_key": "k1",
        "session": "a1b2",
        "job": rig.engine.params[0][2]["job"],
        "targets": [{"path": MAIN, "pre": "0" * 64}, {"path": NEW, "pre": None}],
    }


def test_an_engine_death_before_the_gate_is_read_from_disk_too(make: Any) -> None:
    rig = make({"save": [CRASH], "save_outcome": [facts("none", unchanged=True)]})
    value = rig.save()
    assert details(value) == ChangeNotSavedDetails("engine_stopped", None, None, None, True)
    assert save_text.text(value.error) == "Your change was not saved. Your ledger is unchanged."


def test_an_unfinished_save_after_a_death_names_the_file_it_was_creating(make: Any) -> None:
    rig = make({"save": [GATE_DIE], "save_outcome": [facts("open", path=NEW)]})
    value = rig.save()
    assert details(value) == WritesPausedDetails("unfinished_save", NEW)
    assert "undo" not in save_text.text(value.error)


def test_the_outcome_engine_dying_too_asks_for_another(make: Any) -> None:
    rig = make({"save": [GATE_DIE], "save_outcome": [CRASH, facts("closed")]})
    assert rig.save() == Saved(False)
    assert [op for _, op in rig.engine.calls] == ["save", "save_outcome", "save_outcome"]


def test_no_engine_to_read_the_outcome_past_the_gate_is_change_outcome_unknown(make: Any) -> None:
    rig = make({"save": [GATE_DIE], "save_outcome": [CRASH_TO_BOUND]})
    assert code(rig.save()) == ErrorCode.CHANGE_OUTCOME_UNKNOWN


def test_no_engine_to_read_the_outcome_before_the_gate_is_engine_stopped(make: Any) -> None:
    """The engine never sent `gate`, so it never got `proceed` and wrote nothing (§4): "Your ledger files are safe"
    is true."""
    rig = make({"save": [CRASH], "save_outcome": [CRASH_TO_BOUND]})
    value = rig.save()
    assert code(value) == ErrorCode.ENGINE_STOPPED
    assert details(value) == EngineStoppedDetails(reason="crashed")


def test_an_outcome_that_cannot_be_read_is_unknown_past_the_gate(make: Any) -> None:
    rig = make({"save": [GATE_DIE], "save_outcome": [facts("unknown")]})
    assert code(rig.save()) == ErrorCode.CHANGE_OUTCOME_UNKNOWN


def test_a_save_whose_first_engine_does_not_start_is_answered(make: Any) -> None:
    """The table keeps a save whose engine died, a failed start included, so its work must run even then (else the
    job would wait for ever): here the next engine starts and reads the outcome."""
    rig = make({"save_outcome": [facts("none", unchanged=True)]})
    failures = [EngineFailure("start_failed", "the handshake was refused")]

    def first_start_fails() -> None:
        rig.engine.start_failure = failures.pop() if failures else None

    rig.engine.before_start = first_start_fails
    rig.engine.die()  # the boot's engine, idle, would be reused: the save's engine is a fresh one
    for _ in range(int(DEADLINE / 0.01)):
        if rig.jobs.status().queue.slot == "none":
            break
        threading.Event().wait(0.01)
    ticket = rig.jobs.submit(save_spec(W1), save_work(REQUEST))
    assert isinstance(ticket, JobTicket)
    out = ticket.wait(DEADLINE)
    assert isinstance(out, Done), out
    assert details(out.value) == ChangeNotSavedDetails("engine_stopped", None, None, None, True)  # type: ignore[arg-type]
    assert [op for _, op in rig.engine.calls] == ["save_outcome"]


# -- the pure parts ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("record", "gated", "want"),
    [
        ("closed", False, "saved"),
        ("done", True, "saved"),
        ("mismatch", True, "saved"),
        ("resolved", True, "CHANGE_NOT_SAVED"),
        ("part", True, "CHANGE_NOT_SAVED"),
        ("none", False, "CHANGE_NOT_SAVED"),
        ("open", True, "WRITES_PAUSED"),
        ("kept", True, "WRITES_PAUSED"),
        ("unknown", True, "CHANGE_OUTCOME_UNKNOWN"),
        ("unknown", False, "ENGINE_STOPPED"),
    ],
)
def test_save_outcome_is_the_disks_answer(record: str, gated: bool, want: str) -> None:
    value = save_outcome(OutcomeFacts(record, None, None, None), gated=gated)  # type: ignore[arg-type]
    assert (value == Saved(False)) if want == "saved" else (code(value) == want)


def test_not_saved_says_unchanged_only_after_the_rehash() -> None:
    told = save_outcome(OutcomeFacts("none", False, MAIN, None), gated=True)
    assert isinstance(told, NotDone)
    assert "unchanged" not in save_text.text(told.error)
    assert "main.bean" in save_text.text(told.error)
    unknown = save_outcome(OutcomeFacts("none", None, None, None), gated=True)
    assert isinstance(unknown, NotDone)
    assert details(unknown) == ChangeNotSavedDetails("engine_stopped", None, None, None, False)
    assert "is unchanged" not in save_text.text(unknown.error)


@pytest.mark.parametrize(
    ("d", "want"),
    [
        (
            ChangeNotSavedDetails("hard_linked", MAIN, None, None, True),
            "main.bean has a second name on disk (a hard link)",
        ),
        (ChangeNotSavedDetails("locked", MAIN, None, None, True), "main.bean is locked. Unlock it (Finder: Get Info)"),
        (ChangeNotSavedDetails("not_a_file", MAIN, None, None, True), "main.bean is not a normal file."),
        (ChangeNotSavedDetails("permission", MAIN, None, None, True), "Your Mac does not allow changes to main.bean."),
        (
            ChangeNotSavedDetails("permission", None, "/L", "deny delete_child,directory_inherit", True),
            "The folder /L has a permission entry (ACL) that stops Finzytrack from saving safely: "
            "deny delete_child,directory_inherit. Remove that entry, then try again.",
        ),
        (
            ChangeNotSavedDetails("permission", None, "/app/local/k1/journal", None, True),
            "allow changes to /app/local/k1/journal.",
        ),
        (
            ChangeNotSavedDetails("volume_no_swap", MAIN, None, None, True),
            "This drive does not support safe saving yet.",
        ),
        (
            ChangeNotSavedDetails("io_error", MAIN, None, None, True),
            "Your change was not saved. Your ledger is unchanged.",
        ),
    ],
)
def test_each_not_saved_reason_has_its_section_11_text(d: ChangeNotSavedDetails, want: str) -> None:
    assert want in save_text.not_saved_text(d)


def test_the_paused_texts() -> None:
    assert save_text.paused_text(WritesPausedDetails("unfinished_save", None)) == REOPEN
    created = save_text.paused_text(WritesPausedDetails("unfinished_save", NEW))
    assert "prices.bean" in created
    assert "undo" not in created  # S0061 red team r8 overflow 1: Reopen will ask about the file
    assert save_text.paused_text(WritesPausedDetails("check_pending", None)).startswith("Finzytrack could not check")


def test_session_ids_never_differ_only_in_case() -> None:
    ids = {new_session_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(i == i.lower() and i.isalnum() for i in ids)
    assert len({i.casefold() for i in ids}) == len(ids)


def test_the_request_is_what_the_engine_protocol_takes() -> None:
    schema = json.loads((REPO / "contracts" / "engine-protocol.schema.json").read_text(encoding="utf-8"))
    request: Any = {"v": 2, "id": 1, "op": "save", "params": params_of(REQUEST, "j3")}
    validator = Draft202012Validator({"$ref": "#/$defs/SaveRequest", "$defs": schema["$defs"]})
    assert list(validator.iter_errors(request)) == []  # pyright: ignore[reportUnknownMemberType]
    upper: Any = {**request, "params": {**params_of(REQUEST, "j3"), "session": "A1B2"}}
    assert list(validator.iter_errors(upper)) != []  # pyright: ignore[reportUnknownMemberType]


def test_answer_of_never_says_saved_but_for_saved() -> None:
    outcomes: tuple[SaveOutcomeKind, ...] = ("refused", "not_saved", "unfinished")
    for outcome in outcomes:
        a = SaveAnswer(outcome, "io_error", None, None, None, True, False, None, None, True)
        assert isinstance(answer_of(a), NotDone), outcome


def test_a_request_the_client_could_not_send_is_internal_never_saved() -> None:
    """A line over the protocol's limit is refused by the client before it is sent (`too_large`): nothing was written,
    and the answer is a value the route can raise, never "saved" (spec §15: whole files until BL-0106)."""
    from app.platform.engine_jobs.save import _Run  # pyright: ignore[reportPrivateUsage]

    class Ctx:
        def call(self, op: str, params: object, *, on_event: object = None) -> EngineFailure:
            return EngineFailure("too_large", "the request is longer than 16 MiB")

    out = _Run(Ctx(), REQUEST).run()  # type: ignore[arg-type]
    assert isinstance(out.value, NotDone)
    assert out.value.error.code == ErrorCode.INTERNAL


# -- round 1 ----------------------------------------------------------------------------------------------------------


def test_an_engine_refusal_past_the_gate_reads_the_outcome_from_disk(make: Any) -> None:
    """Verifier r1 F3, red team r1 M1: past `proceed` the engine may have replaced files, and an exception there comes
    back as an `engine.internal` refusal; §6.6 then decides from disk, never "refused before it ran"."""
    refusal = EngineRefusal("engine.internal", "Something went wrong inside the engine.")
    rig = make({"save": [Gated(refusal)], "save_outcome": [facts("open")]})
    value = rig.save()
    assert details(value) == WritesPausedDetails("unfinished_save", None)
    assert [op for _, op in rig.engine.calls] == ["save", "save_outcome"]


def test_an_engine_refusal_before_the_gate_is_internal_and_reads_nothing() -> None:
    from app.platform.engine_jobs.save import _Run  # pyright: ignore[reportPrivateUsage]

    class Ctx:
        def __init__(self) -> None:
            self.ops: list[str] = []

        def call(self, op: str, params: object, *, on_event: object = None) -> EngineRefusal:
            self.ops.append(op)
            return EngineRefusal("protocol.invalid_params", "bad save plan: name_taken")

    ctx = Ctx()
    out = _Run(ctx, REQUEST).run()  # type: ignore[arg-type]
    assert isinstance(out.value, NotDone)
    assert out.value.error.code == ErrorCode.INTERNAL
    assert ctx.ops == ["save"]


def test_every_save_has_its_own_name_and_its_outcome_is_read_by_that_name(make: Any) -> None:
    """Verifier r1 F2: §6.6 tells the outcome by the record's name `<session>-<job>` alone, so no two saves may share
    it (§6.5 "One save, one name"): the sidecar makes the job part for every save, never the caller; a second try of
    the same request gets a new name, so an earlier save's record is never taken for it."""
    rig = make({"save": [CRASH, CRASH], "save_outcome": [facts("none", True), facts("none", True)]})
    rig.save()
    rig.save()
    sent = [(op, p["session"], p["job"]) for _, op, p in rig.engine.params]
    [(_, s1, j1), (_, s1b, j1b), (_, s2, j2), (_, s2b, j2b)] = sent
    assert [op for op, _, _ in sent] == ["save", "save_outcome", "save", "save_outcome"]
    assert (s1, j1) == (s1b, j1b), "the outcome is read by the name its save sent"
    assert (s2, j2) == (s2b, j2b)
    assert j1 != j2, "two saves never share a name"
    assert all(isinstance(j, str) and re.fullmatch(r"[a-z0-9_]{1,64}", j) for j in (j1, j2))


def test_an_io_error_naming_a_file_never_says_another_program_changed_it() -> None:
    """The writer's and the save's `io_error` name the file the error was about, not one found changed: only the
    outcome after a death (`engine_stopped`) names a file that differs from its pre bytes (§11)."""
    for reason in ("io_error", "read_back", "name_not_utf8"):
        text = save_text.not_saved_text(ChangeNotSavedDetails(reason, MAIN, None, None, False))
        assert "changed by another program" not in text, reason
        assert "is unchanged" not in text, reason
    stopped = save_text.not_saved_text(ChangeNotSavedDetails("engine_stopped", MAIN, None, None, False))
    assert "main.bean was changed by another program" in stopped
