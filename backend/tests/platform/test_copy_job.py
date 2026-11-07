"""The copy job, the sidecar's side (`app.platform.engine_jobs.copy`, `copy_text`; `design/specs/engine-jobs.md` §1
"Commit point", §4, §5.3, §8.2's copy, §11; S0081), over the fake engine port with a real scheduler. Written from the
spec's lines and the owner's decisions of 2026-11-07:

- "Same rule as create … `msg:create.left` style, "copy" for "new workspace". … After an engine death it removes
  nothing and names every entry" (decision 1): a copy whose engine died is read from disk by a fresh engine
  (`copy_outcome`); `.finzytrack` with our bytes is copied, anything else is not, naming what is there;
- "If anything changed, it stops with a text like "Your ledger changed while Finzytrack was copying it. Try
  again."" (decision 2);
- §8.2: a copy is a new set of books: its manifest has a new lineage id (ADR-0017), and its root is the source root's
  file name;
- §5.3: past the commit point with no engine to read the disk, `CHANGE_OUTCOME_UNKNOWN` (no Retry).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, get_args

import pytest

from app.domain.paths import WorkspacePath
from app.platform.engine import CreateAnswer, CreateFacts, EngineRefusal
from app.platform.engine_jobs import copy_text
from app.platform.engine_jobs.copy import (
    Copied,
    CopyRequest,
    CopyValue,
    NotCopied,
    NotCopiedReason,
    answer_of,
    copy_outcome,
    copy_spec,
    copy_work,
    params_of,
)
from app.platform.engine_jobs.save import NotDone
from app.platform.engine_jobs.scheduler import Done, EngineJobs, JobTicket
from app.platform.errors import ErrorCode
from app.workspaces.creation import copy_manifest_files
from app.workspaces.format.manifest import Inside, Manifest, read_manifest
from tests.platform.fake_job_engine import CRASH, CRASH_TO_BOUND, GATE_DIE, FakeEngine, Gated

DEADLINE = 20.0
FOLDER = "/u/Books copy"
SOURCE = "/data/ledgers/Main Books.beancount"
MANIFEST = copy_manifest_files(SOURCE)
REQUEST = CopyRequest(
    target=FOLDER,
    source=SOURCE,
    manifest=MANIFEST,
    app_local_data=Path("/app"),
    legacy_locks=("/legacy/data/.write.lock",),
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

    def copy(self) -> CopyValue:
        ticket = self.jobs.submit(copy_spec(), copy_work(REQUEST))
        assert isinstance(ticket, JobTicket), ticket
        out = ticket.wait(DEADLINE)
        assert isinstance(out, Done), out
        assert isinstance(out.value, Copied | NotCopied | NotDone), out.value
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


def test_a_copy_that_lands_is_copied_and_its_engine_is_retired(make: Any) -> None:
    rig = make({"copy": [Gated(answer())]})
    assert rig.copy() == Copied(FOLDER)
    assert rig.engine.proceeded == 1
    assert [op for _, op in rig.engine.calls] == ["copy"]
    assert any(reason == "retire" for _, reason in rig.engine.ends), "its loads ran user code: a fresh engine next"
    [(_, _, params)] = rig.engine.params
    assert params == params_of(REQUEST, params["job"])
    assert params["legacy_locks"] == ["/legacy/data/.write.lock"]
    assert params["app_local_data"] == "/app"


def test_a_changed_source_is_not_copied_with_the_owners_words(make: Any) -> None:
    rig = make({"copy": [answer(outcome="not_created", reason="source_changed", path=SOURCE)]})
    value = rig.copy()
    assert value == NotCopied("source_changed", FOLDER, (), path=SOURCE)
    assert isinstance(value, NotCopied)
    assert copy_text.text(value) == "Not copied. Your ledger changed while Finzytrack was copying it. Try again."


def test_a_held_legacy_lock_says_to_quit_the_old_app(make: Any) -> None:
    rig = make({"copy": [answer(outcome="not_created", reason="legacy_saving", path="/legacy/data/.write.lock")]})
    value = rig.copy()
    assert isinstance(value, NotCopied)
    assert copy_text.text(value) == (
        "Not copied. The old Finzytrack app is saving. Quit the old Finzytrack first, then try again."
    )


def test_what_a_stopped_copy_kept_is_named_in_the_creates_words(make: Any) -> None:
    rig = make({"copy": [answer(outcome="not_created", reason="load_differs", detail="12", path=SOURCE, left=["x"])]})
    value = rig.copy()
    assert isinstance(value, NotCopied)
    assert copy_text.text(value) == (
        "Not copied. The copy did not load the same as your ledger (first at Main Books.beancount, line 12). "
        "Finzytrack left these in /u/Books copy because it could not be sure they are only its own: x. Check them, "
        "and remove any you do not need."
    )


def test_a_copy_cut_past_its_commit_point_is_read_from_disk_by_a_fresh_engine(make: Any) -> None:
    rig = make({"copy": [GATE_DIE], "copy_outcome": [facts("ours")]})
    assert rig.copy() == Copied(FOLDER)
    calls = rig.engine.calls
    assert [op for _, op in calls] == ["copy", "copy_outcome"]
    assert calls[0][0] != calls[1][0], "the outcome is read by a fresh engine"
    first, second = rig.engine.params
    assert second[2] == first[2], "the read names the same job, folder and source"


def test_an_engine_death_before_the_commit_point_names_every_entry_it_found(make: Any) -> None:
    rig = make({"copy": [CRASH], "copy_outcome": [facts("absent", left=["documents", "main.beancount"])]})
    value = rig.copy()
    assert value == NotCopied("stopped", FOLDER, ("documents", "main.beancount"))
    assert isinstance(value, NotCopied)
    assert copy_text.text(value).startswith("Not copied. Finzytrack stopped before it finished. Finzytrack left these")


def test_an_engine_death_that_finds_only_the_folder_never_says_finzytrack_left_it(make: Any) -> None:
    """BL-0199: after a death the read names the folder itself when nothing is in it; the job may never have made it
    (the user's own empty folder), so the text says only that Finzytrack may have made it."""
    rig = make({"copy": [CRASH], "copy_outcome": [facts("absent", left=["."])]})
    value = rig.copy()
    assert value == NotCopied("stopped", FOLDER, (".",))
    assert isinstance(value, NotCopied)
    text = copy_text.text(value)
    assert text == (
        "Not copied. Finzytrack stopped before it finished. Finzytrack may have made the folder /u/Books copy. Check "
        "it, and remove it if you do not need it."
    )
    assert "left the folder" not in text


def test_no_engine_past_the_commit_point_is_change_outcome_unknown(make: Any) -> None:
    rig = make({"copy": [GATE_DIE], "copy_outcome": [CRASH_TO_BOUND]})
    value = rig.copy()
    assert isinstance(value, NotDone)
    assert value.error.code == ErrorCode.CHANGE_OUTCOME_UNKNOWN


def test_an_outcome_that_cannot_be_read_past_the_commit_point_is_unknown(make: Any) -> None:
    rig = make({"copy": [GATE_DIE], "copy_outcome": [{"manifest": "unknown", "left": None, "errno": 5}]})
    value = rig.copy()
    assert isinstance(value, NotDone)
    assert value.error.code == ErrorCode.CHANGE_OUTCOME_UNKNOWN


def test_no_engine_before_the_commit_point_is_not_copied_and_asks_to_check_the_folder(make: Any) -> None:
    rig = make({"copy": [CRASH], "copy_outcome": [CRASH_TO_BOUND]})
    value = rig.copy()
    assert value == NotCopied("stopped", FOLDER, None)
    assert isinstance(value, NotCopied)
    assert copy_text.text(value).endswith("may hold an unfinished copy. Check it before you delete anything.")


def test_a_request_the_engine_would_not_take_is_internal_and_reads_nothing(make: Any) -> None:
    rig = make({"copy": [EngineRefusal("protocol.invalid_params", "no")]})
    value = rig.copy()
    assert isinstance(value, NotDone)
    assert value.error.code == ErrorCode.INTERNAL
    assert [op for _, op in rig.engine.calls] == ["copy"]


def test_an_engine_refusal_past_the_commit_point_reads_the_disk(make: Any) -> None:
    rig = make({"copy": [Gated(EngineRefusal("engine.internal", "boom"))], "copy_outcome": [facts("ours")]})
    assert rig.copy() == Copied(FOLDER)


# -- the pure parts ------------------------------------------------------------------------------------------------


def test_copy_outcome_never_says_copied_but_for_our_manifest() -> None:
    for manifest in ("absent", "foreign", "unknown"):
        for gated in (False, True):
            value = copy_outcome(CreateFacts(manifest, ("x",), None), gated=gated, folder=FOLDER)
            assert not isinstance(value, Copied)
    assert copy_outcome(CreateFacts("ours", (), None), gated=True, folder=FOLDER) == Copied(FOLDER)


def test_answer_of_never_says_copied_but_for_created_and_keeps_every_engine_reason() -> None:
    for reason in get_args(NotCopiedReason.__value__):
        if reason == "stopped":
            continue
        out = answer_of(CreateAnswer("not_created", reason, FOLDER, None, None, None, ()))
        assert isinstance(out, NotCopied)
        assert out.reason == reason
    assert answer_of(CreateAnswer("created", None, FOLDER, None, None, None, ())) == Copied(FOLDER)


@pytest.mark.parametrize("reason", get_args(NotCopiedReason.__value__))
def test_every_reason_has_a_text_that_never_says_delete_it(reason: str) -> None:
    text = copy_text.text(NotCopied(reason, FOLDER, (), path=SOURCE))  # type: ignore[arg-type]
    assert text
    assert "You can delete" not in text


def test_the_manifest_of_a_copy_has_a_new_lineage_and_the_sources_root_name() -> None:
    (only,) = MANIFEST
    read = read_manifest(only.text.encode("utf-8"))
    assert isinstance(read, Manifest)
    assert read.ledger == Inside(WorkspacePath.parse("Main Books.beancount"))
    assert str(uuid.UUID(read.lineage_id)) == read.lineage_id
    (again,) = copy_manifest_files(SOURCE)
    other = read_manifest(again.text.encode("utf-8"))
    assert isinstance(other, Manifest)
    assert other.lineage_id != read.lineage_id, "every copy gets its own lineage"


def test_a_copy_is_an_app_scoped_waited_for_writing_job() -> None:
    spec = copy_spec()
    assert (spec.kind, spec.workspace, spec.waited_for, spec.confirmed) == ("copy", None, True, True)


def test_a_ledger_with_its_own_plugin_is_told_why_it_cannot_be_copied() -> None:
    """r2 (the owner's "Fresh process"), BL-0202: the plugin is named, and the reason is the version's limit."""
    value = NotCopied("plugin_modules", FOLDER, (), detail="my_fx_rates")
    assert copy_text.text(value) == (
        "Not copied. The copy did not load the same as your ledger, which uses a plugin of its own (my_fx_rates). "
        "This version of Finzytrack cannot copy a ledger's own plugins."
    )


@pytest.mark.parametrize(
    ("detail", "path", "start"),
    [
        ("reserved_name", "/s/.finzytrack/x.beancount", "Your ledger uses a folder named .finzytrack (x.beancount)"),
        ("inside_documents", "/s/docs", "The new folder is inside your ledger's documents folder."),
        ("inside_legacy", "/legacy", "The new folder is inside the old Finzytrack app's folder."),
        ("inside_target", "/u/Books copy/x.beancount", "Your ledger names a file inside the new folder"),
        ("two_sources", "/s/a.pdf", "a.pdf and another of your files would get the same name"),
        ("two_files_one_name", None, "Finzytrack could not place the files of the copy. This is a fault"),
    ],
)
def test_every_cannot_place_detail_has_its_own_true_sentence(detail: str, path: str | None, start: str) -> None:
    """r2 F8: one sentence per detail; the writer's own refusal (no path) is Finzytrack's fault, never "same name"."""
    text = copy_text.text(NotCopied("cannot_place", FOLDER, (), path=path, detail=detail))
    assert text.startswith(f"Not copied. {start}"), text
