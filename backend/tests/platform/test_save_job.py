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
    EncryptedFileDetails,
    EngineStoppedDetails,
    EngineUnavailableDetails,
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
    fingerprint='{"document_files":[],"documents":[],"files":[],"globs":[],"modules":[],"root":"/L/main.bean","v":2}',
    app_local_data=Path("/app"),
    confine=None,
)
BLANK: dict[str, object] = {
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
            WritesPausedDetails("earlier_unfinished", None),  # S0100 (BL-0183): its own reason
        ),
        # S0068 (ADR-0067): nothing after a save refuses one; a reason that came back would be a bug, never a pause
        (answer(outcome="refused", reason="check_pending", unchanged=True), "INTERNAL", None),
        (answer(outcome="refused", reason="check_failed", unchanged=True), "INTERNAL", None),
        # S0071 (ADR-0068 D4): a save tries no legacy lock; that reason coming back would be a bug
        (answer(outcome="refused", reason="legacy_app_saving", unchanged=True), "INTERNAL", None),
        (answer(outcome="refused", reason="ledger_changed"), "LEDGER_EXTERNALLY_CHANGED", None),
        (
            answer(outcome="refused", reason="mirror_stale", unchanged=True),
            "WORKSPACE_NOT_READY",
            WorkspaceNotReadyDetails("WARMING"),
        ),
        (answer(outcome="refused", reason="load_refused", unchanged=True), "INTERNAL", None),  # an older engine's
        # S0117 (BL-0106): the confirm refuses an encrypted file first; the save's own step 0 refusal (a link to one
        # made after the preview) has the contract's code, never "saved" and never INTERNAL
        (
            answer(outcome="refused", reason="encrypted_file", path=NEW, unchanged=True),
            "ENCRYPTED_FILE_NOT_EDITABLE",
            EncryptedFileDetails(NEW),
        ),
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
            ChangeNotSavedDetails("hard_linked", MAIN, None, None, True, None),
        ),
        (
            answer(outcome="not_saved", reason="permission", entry="/L", detail="deny delete_child", unchanged=True),
            "CHANGE_NOT_SAVED",
            ChangeNotSavedDetails("permission", None, "/L", "deny delete_child", True, None),
        ),
        (  # S0077 (BL-0193): a link in place of the workspace's own journal, backups or key folder
            answer(outcome="not_saved", reason="linked_folder", entry="/app/local/k1/journal", unchanged=True),
            "CHANGE_NOT_SAVED",
            ChangeNotSavedDetails("linked_folder", None, "/app/local/k1/journal", None, True, None),
        ),
        (  # S0117 round 1 (red team RT-F1): a file of the test copy led out of it after the confirm
            answer(outcome="not_saved", reason="outside_folder", path="/real/main.bean", entry="/copy", unchanged=True),
            "CHANGE_NOT_SAVED",
            ChangeNotSavedDetails("outside_folder", "/real/main.bean", "/copy", None, True, None),
        ),
        (  # S0117 round 3 (owner, 2026-11-14: "2"): a test copy's save follows no link; its folder's own refusals
            answer(outcome="not_saved", reason="link_in_copy", path="/copy/main.bean", entry="/copy"),
            "CHANGE_NOT_SAVED",
            ChangeNotSavedDetails("link_in_copy", "/copy/main.bean", "/copy", None, False, None),
        ),
        (
            answer(outcome="not_saved", reason="copy_folder_moved", entry="/copy"),
            "CHANGE_NOT_SAVED",
            ChangeNotSavedDetails("copy_folder_moved", None, "/copy", None, False, None),
        ),
        (
            answer(outcome="not_saved", reason="copy_folder_missing", entry="/copy"),
            "CHANGE_NOT_SAVED",
            ChangeNotSavedDetails("copy_folder_missing", None, "/copy", None, False, None),
        ),
        (  # S0089 (BL-0188): a drive with no volume UUID; its journal could never be undone
            answer(outcome="not_saved", reason="no_drive_id", path=MAIN, unchanged=True),
            "CHANGE_NOT_SAVED",
            ChangeNotSavedDetails("no_drive_id", MAIN, None, None, True, None),
        ),
        (  # S0089 round 1: the top level of a FAT or exFAT drive
            answer(outcome="not_saved", reason="no_folder_id", path=MAIN, unchanged=True),
            "CHANGE_NOT_SAVED",
            ChangeNotSavedDetails("no_folder_id", MAIN, None, None, True, None),
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
        # S0065: a drive without the swap is saved by the fallback; the engine no longer refuses it, and the reason
        # it used to send is not a writer reason any more (a bug if it came back, never worded as a refusal)
        (answer(outcome="not_saved", reason="volume_no_swap"), "INTERNAL", None),
    ],
)
def test_each_engine_answer_is_its_code(
    make: Any, result: dict[str, object], want_code: str, want_details: object
) -> None:
    """S0117: the save's own `encrypted_file` refusal is the contract's `ENCRYPTED_FILE_NOT_EDITABLE` naming the
    file (BL-0106's TODO), never "saved", a pause or `INTERNAL`."""
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
        # S0068: no `.done` or `.mismatch` state exists; an engine that answered one breaks the protocol, and a save
        # cut past its gate is then unknown, never "saved" from a state nothing writes
        (facts("done"), "CHANGE_OUTCOME_UNKNOWN"),
        (facts("mismatch"), "CHANGE_OUTCOME_UNKNOWN"),
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
    assert details(value) == ChangeNotSavedDetails("engine_stopped", None, None, None, True, None)
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
    assert details(out.value) == ChangeNotSavedDetails("engine_stopped", None, None, None, True, None)  # type: ignore[arg-type]
    assert [op for _, op in rig.engine.calls] == ["save_outcome"]


# -- the pure parts ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("record", "gated", "want"),
    [
        ("closed", False, "saved"),
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
    assert details(unknown) == ChangeNotSavedDetails("engine_stopped", None, None, None, False, None)
    assert "is unchanged" not in save_text.text(unknown.error)


@pytest.mark.parametrize(
    ("d", "want"),
    [
        (
            ChangeNotSavedDetails("hard_linked", MAIN, None, None, True, None),
            "main.bean has a second name on disk (a hard link)",
        ),
        (
            ChangeNotSavedDetails("locked", MAIN, None, None, True, None),
            "main.bean is locked. Unlock it (Finder: Get Info)",
        ),
        (ChangeNotSavedDetails("not_a_file", MAIN, None, None, True, None), "main.bean is not a normal file."),
        (
            ChangeNotSavedDetails("permission", MAIN, None, None, True, None),
            "Your Mac does not allow changes to main.bean.",
        ),
        (
            ChangeNotSavedDetails("permission", None, "/L", "deny delete_child,directory_inherit", True, None),
            "The folder /L has a permission entry (ACL) that stops Finzytrack from saving safely: "
            "deny delete_child,directory_inherit. Remove that entry, then try again.",
        ),
        (
            ChangeNotSavedDetails("permission", None, "/app/local/k1/journal", None, True, None),
            "allow changes to /app/local/k1/journal.",
        ),
        (  # S0100 (BL-0163 (b)): the file named
            ChangeNotSavedDetails("io_error", MAIN, None, None, True, None),
            "Your change was not saved. Finzytrack could not read or write main.bean. Your ledger is unchanged.",
        ),
        (
            ChangeNotSavedDetails("linked_folder", None, "/app/local/k1/journal", None, True, None),
            "The folder /app/local/k1/journal is a link to another place. Finzytrack keeps its save records and "
            "backups only in a real folder. Put the real folder back in its place, or remove the link, then try again.",
        ),
        (
            ChangeNotSavedDetails("no_folder_id", MAIN, None, None, True, None),
            f"The folder that holds {MAIN.rpartition('/')[2]} has no creation time that Finzytrack can read, so "
            "Finzytrack cannot tell it from a folder at the same place on another drive.",
        ),
        (  # S0090 (BL-0220 V2-1): a drive that keeps no creation time at all gets a way out that works there too
            ChangeNotSavedDetails("no_folder_id", MAIN, None, None, True, None),
            f"If {MAIN.rpartition('/')[2]} is at the top level of a USB drive, make a folder on the drive, move your "
            "ledger into it, and open it from there. Otherwise keep your ledger on another drive, such as your "
            "Mac's own disk.",
        ),
        (
            ChangeNotSavedDetails("no_drive_id", MAIN, None, None, True, None),
            f"Finzytrack cannot identify the drive that holds {MAIN.rpartition('/')[2]}, so it could not undo the "
            "change if saving were interrupted.",
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
    """The writer's and the save's `io_error` name the file the error was about, not one found changed: a file that
    differs from its pre bytes is named only from `changed` (S0100, BL-0163 (b): the re-hash's own field)."""
    for reason in ("io_error", "read_back", "name_not_utf8"):
        text = save_text.not_saved_text(ChangeNotSavedDetails(reason, MAIN, None, None, False, None))
        assert "changed by another program" not in text, reason
        assert "is unchanged" not in text, reason
    stopped = save_text.not_saved_text(ChangeNotSavedDetails("engine_stopped", None, None, None, False, MAIN))
    assert "main.bean was changed by another program" in stopped


def test_the_best_effort_drive_notice_is_the_owners_words_in_the_spec() -> None:
    """ADR-0068 D2: the notice a workspace on a drive without the swap shows at open (BL-0166 shows it) is §11's row
    and lives in one place (`save_text.BEST_EFFORT_DRIVE`): since S0079 the row cites its key, and the generated
    reference renders that key from the code, word for word."""
    spec = (REPO / "design" / "specs" / "engine-jobs.md").read_text(encoding="utf-8")
    row = next(ln for ln in spec.splitlines() if ln.startswith("| a workspace whose ledger is on a drive without"))
    assert "| `msg:save.best_effort_drive` |" in row
    texts = (REPO / "design" / "generated" / "engine-job-texts.md").read_text(encoding="utf-8")
    rendered = next(ln for ln in texts.splitlines() if ln.startswith("| `msg:save.best_effort_drive` |"))
    assert rendered.endswith(f"| {save_text.BEST_EFFORT_DRIVE} |")


# -- S0100: the write-path leftovers (BL-0162, BL-0163, BL-0182, BL-0183, BL-0187) ------------------------------------

A_BEAN = "/L/a.bean"


@pytest.mark.parametrize(
    ("result", "want"),
    [
        (  # BL-0162
            answer(outcome="not_saved", reason="case_twins", path="/L/A.bean", unchanged=True),
            ChangeNotSavedDetails("case_twins", "/L/A.bean", None, None, True, None),
        ),
        (  # BL-0187: never LEDGER_EXTERNALLY_CHANGED
            answer(outcome="not_saved", reason="folder_appeared", path=NEW, entry="/L/y2026", unchanged=True),
            ChangeNotSavedDetails("folder_appeared", NEW, "/L/y2026", None, True, None),
        ),
        (  # BL-0163 (a)
            answer(outcome="not_saved", reason="app_folder_missing", entry="/app", unchanged=True),
            ChangeNotSavedDetails("app_folder_missing", None, "/app", None, True, None),
        ),
        (  # BL-0163 (b): the changed file apart from the failed one
            answer(outcome="not_saved", reason="io_error", path=MAIN, unchanged=False, changed=A_BEAN),
            ChangeNotSavedDetails("io_error", MAIN, None, None, False, A_BEAN),
        ),
    ],
)
def test_S0100_each_new_engine_answer_is_its_code(make: Any, result: dict[str, object], want: object) -> None:
    rig = make({"save": [Gated(result)]})
    value = rig.save()
    assert code(value) == "CHANGE_NOT_SAVED"
    assert details(value) == want


def test_S0100_BL0183_an_earlier_unfinished_save_has_its_own_reason_and_says_this_change_was_not_saved() -> None:
    """BL-0183 (S0069 red team overflow 1): the step-1 refusal is about an earlier save, so this change was certainly
    not written: not the Reopen text of "could not finish your change"."""
    value = answer_of(
        SaveAnswer(
            "refused", "unfinished_save", "/app/local/k1/journal/x.open", None, None, True, False, None, None, False
        )
    )
    assert details(value) == WritesPausedDetails("earlier_unfinished", None)
    assert isinstance(value, NotDone)
    text = save_text.text(value.error)
    assert text.startswith("Your change was not saved.")
    assert "earlier change" in text
    assert "could not finish your change" not in text


def test_S0100_BL0183_this_saves_own_open_journal_still_reads_the_reopen_text() -> None:
    """@regression-guard (its logic holds on 437d6298). Must pass: a journal this save left open (`not_saved` with
    `open_journal`, `unfinished`) keeps its Reopen text."""
    for a in (
        SaveAnswer("not_saved", "io_error", MAIN, None, None, False, True, None, None, False),
        SaveAnswer("unfinished", "io_error", MAIN, None, None, False, True, None, None, False),
    ):
        value = answer_of(a)
        assert details(value) == WritesPausedDetails("unfinished_save", None)
        assert isinstance(value, NotDone)
        assert save_text.text(value.error) == REOPEN


def test_S0100_BL0163b_the_texts_name_the_failed_file_and_say_which_file_changed() -> None:
    """BL-0163 (b): `io_error`, `read_back` and `name_not_utf8` name their file; with the re-hash's changed file they
    say it changed instead of "could not check" (S0063 verifier r2 F-C)."""
    for reason in ("io_error", "read_back", "name_not_utf8"):
        changed = save_text.not_saved_text(ChangeNotSavedDetails(reason, MAIN, None, None, False, A_BEAN))
        assert "main.bean" in changed, reason
        assert "a.bean was changed by another program" in changed, reason
        assert "could not check" not in changed, reason
        unknown = save_text.not_saved_text(ChangeNotSavedDetails(reason, MAIN, None, None, False, None))
        assert "could not check whether your ledger changed" in unknown, reason
        assert "main.bean" in unknown, reason
        same = save_text.not_saved_text(ChangeNotSavedDetails(reason, MAIN, None, None, True, None))
        assert same.endswith("Your ledger is unchanged."), reason
        assert "main.bean" in same, reason


def test_S0100_the_outcome_after_a_death_carries_the_changed_file_in_its_own_field() -> None:
    """§6.6 (BL-0182's text): the re-hash's first changed file is `changed`, not `path`."""
    told = save_outcome(OutcomeFacts("resolved", False, MAIN, None), gated=True)
    assert details(told) == ChangeNotSavedDetails("engine_stopped", None, None, None, False, MAIN)
    assert isinstance(told, NotDone)
    assert save_text.text(told.error) == "Your change was not saved, and main.bean was changed by another program."


@pytest.mark.parametrize(
    ("d", "want"),
    [
        (
            ChangeNotSavedDetails("case_twins", "/L/A.bean", None, None, True, None),
            "Finzytrack did not save your change. A.bean and another file in this change have names this drive may "
            "treat as the same (for example, they differ only in capital letters). Finzytrack cannot save both in "
            "one change. Rename one of them, then try again. Your ledger is unchanged.",
        ),
        (
            ChangeNotSavedDetails("folder_appeared", NEW, "/L/y2026", None, True, None),
            "Finzytrack did not save your change. A folder /L/y2026 appeared where Finzytrack was about to make one. "
            "Try again. Your ledger is unchanged.",
        ),
        (
            ChangeNotSavedDetails("app_folder_missing", None, "/app", None, True, None),
            "Finzytrack did not save your change. Its data folder /app is missing or was replaced. Quit Finzytrack and "
            "open it again, then try again. Your ledger is unchanged.",
        ),
    ],
)
def test_S0100_the_new_reasons_texts(d: ChangeNotSavedDetails, want: str) -> None:
    assert save_text.not_saved_text(d) == want


# -- S0102 round 1 (verifier F1): the outcome in an app folder that is not the one of the start -------------------

APP_FOLDER_REFUSAL = EngineRefusal("engine.app_folder_missing", "The app's data folder is missing or was replaced.")


def test_S0102_r1_an_outcome_refused_for_the_app_folder_past_the_gate_is_unknown_never_not_saved(make: Any) -> None:
    """The save may have landed; its record is in a folder the engine no longer reads: never "not saved"."""
    rig = make({"save": [GATE_DIE], "save_outcome": [APP_FOLDER_REFUSAL]})
    assert code(rig.save()) == ErrorCode.CHANGE_OUTCOME_UNKNOWN


def test_S0102_r1_an_outcome_refused_for_the_app_folder_before_the_gate_says_quit_and_reopen(make: Any) -> None:
    """Nothing was written before the gate; the next step is the app folder's (quit and reopen)."""
    rig = make({"save": [CRASH], "save_outcome": [APP_FOLDER_REFUSAL]})
    value = rig.save()
    assert code(value) == ErrorCode.ENGINE_UNAVAILABLE
    assert details(value) == EngineUnavailableDetails(reason="app_folder_missing")
