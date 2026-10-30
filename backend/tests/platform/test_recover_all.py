"""Recover-all, the sidecar's side (`app.platform.engine_jobs.recovery`; `design/specs/engine-jobs.md` §4's
`recover_all` row, §8.1 and "Afterwards", §9.1 step 6, §9.2; ADR-0063 decision 2; INV-32; S0062).

Written from the spec's lines: "Every journal ends recovered or blocking; none is skipped"; "Cancel during a journal
ends the engine; that journal counts as blocking (reason `cancelled`), and recovery goes on with the next journal in a
new engine"; "An engine crash during a journal makes that journal blocking (reason `failed`) ... recovery goes on
with the next journal after the backoff"; "When the engine cannot run ... every journal not yet tried counts as
blocking (reason `not_tried`)"; Retry "runs `recover_all` again for every journal still `.open`"; "a blocking journal
blocks every open of a workspace whose ledger set uses a file it names by `path_key`, its own workspace included ...
(matched by the registry key and lineage id the journal records)"; "A journal whose file list cannot be read blocks
every open". First over a scripted job context (each step's answer chosen by the test), then through the scheduler
with the fake engine (the Cancel, the death and the bound as the table and the shell carry them out).
"""

from __future__ import annotations

import faulthandler
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from app.platform.engine import EngineFailure, EngineRefusal
from app.platform.engine_jobs.recovery import (
    ENGINE_STOPPED,
    Blocking,
    DeleteCreated,
    DeleteKept,
    Journal,
    Keep,
    NotRecovered,
    Report,
    blocking_for,
    recover_all_spec,
    recover_all_work,
)
from app.platform.engine_jobs.scheduler import Done, EngineJobs, Failed, JobContext, JobTicket
from app.platform.errors import ErrorCode
from tests.platform.fake_job_engine import BLOCK, CRASH, CRASH_TO_BOUND, FakeEngine
from tests.platform.test_engine_scheduler import DEADLINE, LockWait, wait_until

pytestmark = [pytest.mark.inv("INV-32"), pytest.mark.cap("CAP-WS-RECOVER-FIRST")]

ALD = Path("/app-data")
JD1 = "/app-data/local/k1/journal"
JD2 = "/app-data/local/k2/journal"


@pytest.fixture(autouse=True)
def hang_guard() -> Iterator[None]:
    faulthandler.dump_traceback_later(15 * DEADLINE, exit=True)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


def listed(name: str, jd: str = JD1, *, key: str = "k1", lineage: str = "lin1", files: tuple[str, ...] = ("/L/a",)):
    return {
        "journal_dir": jd,
        "name": name,
        "readable": True,
        "workspace_key": key,
        "lineage": lineage,
        "files": [{"path": f, "path_key": f.lower(), "created": False} for f in files],
        "errno": None,
    }


def unreadable(name: str, jd: str = JD1) -> dict[str, Any]:
    return {
        "journal_dir": jd,
        "name": name,
        "readable": False,
        "workspace_key": None,
        "lineage": None,
        "files": [],
        "errno": None,
    }


def listing(*journals: dict[str, Any], unlisted: tuple[tuple[str, int | None], ...] = ()) -> dict[str, Any]:
    return {
        "journals": list(journals),
        "unlisted": [{"folder": f, "errno": e} for f, e in unlisted],
        "parts_removed": 0,
    }


def tried(outcome: str, cause: str | None = None, **kw: Any) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "cause": cause,
        "path": kw.get("path"),
        "entry": kw.get("entry"),
        "detail": kw.get("detail"),
        "errno": kw.get("errno"),
        "inspection": kw.get("inspection"),
    }


def acted(outcome: str = "done", reason: str | None = None) -> dict[str, Any]:
    return {"outcome": outcome, "reason": reason, "path": None, "detail": None, "errno": None}


class Script:
    """A job context whose engine answers each call from a script: per op, a list of answers (a result mapping, an
    `EngineFailure`, an `EngineRefusal`); `fresh` answers each ask for a fresh engine (None: one came)."""

    def __init__(self, answers: Mapping[str, list[object]], fresh: list[EngineFailure | None] | None = None) -> None:
        self.answers = {op: list(a) for op, a in answers.items()}
        self.fresh = list(fresh or [])
        self.log: list[tuple[str, dict[str, object]]] = []

    def call(self, op: str, params: Mapping[str, object], **_: object) -> object:
        self.log.append((op, dict(params)))
        return self.answers[op].pop(0)

    def fresh_engine(self) -> EngineFailure | None:
        self.log.append(("fresh", {}))
        return self.fresh.pop(0) if self.fresh else None

    def listed(self) -> None:
        self.log.append(("listed", {}))

    def ops(self) -> list[str]:
        return [op for op, _ in self.log]


def run(script: Script, *actions: Any) -> Report:
    result = recover_all_work(ALD, actions)(script)  # type: ignore[arg-type] (the context's duck type)
    assert result.ran_user_code is False, "recover-all runs no user code: its engine may serve the next job"
    assert isinstance(result.value, Report)
    return result.value


def reasons(report: Report) -> dict[str, str]:
    return {b.journal.name: b.reason for b in report.blocking}


def every_journal_ends_once(report: Report, names: set[str]) -> None:
    """INV-32: every listed journal ends the run resolved, kept, or blocking, exactly once."""
    seen = [j.name for j in report.undone] + [j.name for j in report.kept] + [b.journal.name for b in report.blocking]
    assert sorted(seen) == sorted(names), seen


CRASHED = EngineFailure("crashed", "the engine's output ended")
CANCELLED = EngineFailure("cancelled", "ended by us")
UNAVAILABLE = EngineFailure("unavailable", "the engine stopped 3 times in 300 s")


# -- the work, step by step -------------------------------------------------------------------------------------------


def test_each_listed_journal_is_tried_after_the_listing_and_ends_with_its_try() -> None:
    s = Script(
        {
            "recover_list": [listing(listed("s1-j1"), listed("s1-j2", JD2, key="k2"), listed("s1-j3"))],
            "recover_try": [
                tried("resolved"),
                tried("stopped", "kept_copy", path="/L/a"),
                tried("failed", "io_error", path="/L/a", entry="/L/.a.ft-s1-j3.tmp", errno=5),
            ],
        }
    )
    report = run(s)
    assert s.ops() == ["recover_list", "listed", "recover_try", "recover_try", "recover_try"]
    assert all(p["app_local_data"] == str(ALD) for _, p in s.log if _ != "listed" and _ != "fresh")
    assert [p["name"] for op, p in s.log if op == "recover_try"] == ["s1-j1", "s1-j2", "s1-j3"]
    assert report.listed
    assert [j.name for j in report.undone] == ["s1-j1"]
    assert reasons(report) == {"s1-j2": "stopped", "s1-j3": "failed"}
    failed = next(b for b in report.blocking if b.journal.name == "s1-j3")
    assert (failed.cause, failed.path, failed.entry, failed.errno) == ("io_error", "/L/a", "/L/.a.ft-s1-j3.tmp", 5)
    every_journal_ends_once(report, {"s1-j1", "s1-j2", "s1-j3"})


def test_a_cancel_during_a_journal_marks_it_cancelled_and_the_next_is_tried_in_a_fresh_engine() -> None:
    s = Script(
        {
            "recover_list": [listing(listed("s1-j1"), listed("s1-j2"), listed("s1-j3"))],
            "recover_try": [tried("resolved"), CANCELLED, tried("resolved")],
        }
    )
    report = run(s)
    assert s.ops() == ["recover_list", "listed", "recover_try", "recover_try", "fresh", "recover_try"]
    assert reasons(report) == {"s1-j2": "cancelled"}
    assert [j.name for j in report.undone] == ["s1-j1", "s1-j3"]


def test_an_engine_death_during_a_journal_marks_it_failed_and_recovery_goes_on() -> None:
    s = Script(
        {
            "recover_list": [listing(listed("s1-j1"), listed("s1-j2"))],
            "recover_try": [CRASHED, tried("resolved")],
        }
    )
    report = run(s)
    assert s.ops() == ["recover_list", "listed", "recover_try", "fresh", "recover_try"]
    [b] = report.blocking
    assert (b.journal.name, b.reason, b.cause) == ("s1-j1", "failed", ENGINE_STOPPED)


def test_when_no_engine_can_run_every_journal_not_yet_tried_is_not_tried() -> None:
    s = Script(
        {
            "recover_list": [listing(listed("s1-j1"), listed("s1-j2"), listed("s1-j3"), listed("s1-j4"))],
            "recover_try": [tried("resolved"), CRASHED],
        },
        fresh=[UNAVAILABLE],
    )
    report = run(s)
    assert reasons(report) == {"s1-j2": "failed", "s1-j3": "not_tried", "s1-j4": "not_tried"}
    assert next(b for b in report.blocking if b.journal.name == "s1-j3").cause == "unavailable"
    every_journal_ends_once(report, {"s1-j1", "s1-j2", "s1-j3", "s1-j4"})


def test_a_death_before_the_listing_lists_again_in_a_fresh_engine() -> None:
    """Model `rc_spawn` then `rc_init`: nothing was in hand, so nothing is marked; the listing runs again."""
    s = Script({"recover_list": [CRASHED, listing(listed("s1-j1"))], "recover_try": [tried("resolved")]})
    report = run(s)
    assert s.ops() == ["recover_list", "fresh", "recover_list", "listed", "recover_try"]
    assert [j.name for j in report.undone] == ["s1-j1"]


@pytest.mark.parametrize(
    ("first", "why"),
    [
        (CANCELLED, "cancelled"),
        (EngineRefusal("engine.no_engine_lock", "This engine does not hold the engine lock."), "engine.no_engine_lock"),
        ({"journals": "not a list"}, "protocol_violation"),
    ],
)
def test_a_run_that_cannot_list_reports_nothing_listed(first: object, why: str) -> None:
    s = Script({"recover_list": [first]})
    report = run(s)
    assert not report.listed
    assert report.why_not_listed == why
    assert "listed" not in s.ops(), "the table was told of a listing that did not happen"


def test_a_run_with_no_engine_for_its_listing_reports_nothing_listed() -> None:
    s = Script({"recover_list": [CRASHED]}, fresh=[UNAVAILABLE])
    report = run(s)
    assert not report.listed
    assert report.why_not_listed == "unavailable"


def test_a_record_that_cannot_be_read_blocks_without_a_try() -> None:
    s = Script({"recover_list": [listing(unreadable("s9-j9"), listed("s1-j1"))], "recover_try": [tried("resolved")]})
    report = run(s)
    assert [p["name"] for op, p in s.log if op == "recover_try"] == ["s1-j1"]
    [b] = report.blocking
    assert (b.journal.name, b.reason, b.cause) == ("s9-j9", "stopped", "unreadable")


def test_a_try_reply_of_another_shape_marks_the_journal_failed() -> None:
    s = Script({"recover_list": [listing(listed("s1-j1"))], "recover_try": [{"outcome": "resolved"}]})
    report = run(s)
    [b] = report.blocking
    assert (b.reason, b.cause) == ("failed", ENGINE_STOPPED)


def test_keep_first_then_the_others_are_tried_and_the_kept_one_is_not() -> None:
    s = Script(
        {
            "recover_list": [listing(listed("s1-j1"), listed("s1-j2"))],
            "recover_act": [acted()],
            "recover_try": [tried("resolved")],
        }
    )
    report = run(s, Keep(JD1, "s1-j1"))
    assert s.ops() == ["recover_list", "listed", "recover_act", "recover_try"]
    act = next(p for op, p in s.log if op == "recover_act")
    assert act == {"app_local_data": str(ALD), "journal_dir": JD1, "name": "s1-j1", "action": "keep"}
    assert [j.name for j in report.kept] == ["s1-j1"]
    assert [j.name for j in report.undone] == ["s1-j2"]
    every_journal_ends_once(report, {"s1-j1", "s1-j2"})


def test_delete_the_kept_copy_then_the_journal_is_tried() -> None:
    s = Script(
        {
            "recover_list": [listing(listed("s1-j1"))],
            "recover_act": [acted()],
            "recover_try": [tried("resolved")],
        }
    )
    report = run(s, DeleteKept(JD1, "s1-j1", "/L/a", "a.kept-s1-j1", "0" * 64))
    act = next(p for op, p in s.log if op == "recover_act")
    assert act["action"] == "delete_kept"
    assert (act["file"], act["kept"], act["sha256"]) == ("/L/a", "a.kept-s1-j1", "0" * 64)
    assert [j.name for j in report.undone] == ["s1-j1"]
    assert [a.outcome.outcome for a in report.actions] == ["done"]


def test_an_action_on_a_journal_the_listing_did_not_find_open_is_not_sent() -> None:
    """The blocked screen showed a journal that has since resolved (or a Retry undid it first): its action is refused
    `not_open` without a call, so nothing acts on what the user did not see."""
    s = Script({"recover_list": [listing(listed("s1-j1"))], "recover_try": [tried("resolved")]})
    report = run(s, DeleteCreated(JD1, "s7-j7", "/L/p", "0" * 64))
    assert "recover_act" not in s.ops()
    assert [(a.outcome.outcome, a.outcome.reason) for a in report.actions] == [("refused", "not_open")]


def test_a_refused_delete_leaves_the_journal_to_its_try() -> None:
    s = Script(
        {
            "recover_list": [listing(listed("s1-j1"))],
            "recover_act": [acted("refused", "changed")],
            "recover_try": [tried("stopped", "kept_copy", path="/L/a")],
        }
    )
    report = run(s, DeleteKept(JD1, "s1-j1", "/L/a", "a.kept-s1-j1", "0" * 64))
    assert reasons(report) == {"s1-j1": "stopped"}
    assert [(a.outcome.outcome, a.outcome.reason) for a in report.actions] == [("refused", "changed")]


def test_a_cancel_during_an_action_marks_its_journal_cancelled_and_goes_on() -> None:
    s = Script(
        {
            "recover_list": [listing(listed("s1-j1"), listed("s1-j2"))],
            "recover_act": [CANCELLED],
            "recover_try": [tried("resolved")],
        }
    )
    report = run(s, Keep(JD1, "s1-j1"))
    assert reasons(report) == {"s1-j1": "cancelled"}
    assert [j.name for j in report.undone] == ["s1-j2"]
    assert s.ops() == ["recover_list", "listed", "recover_act", "fresh", "recover_try"]


def test_retry_runs_the_work_again_from_a_fresh_listing() -> None:
    """S0061 red team r1 L7: a try whose last sync failed left the record `.resolved`; Retry lists again (after the
    folder's sync, in the engine) and so neither tries nor reports it."""
    first = run(Script({"recover_list": [listing(listed("s1-j1"))], "recover_try": [tried("failed", "io_error")]}))
    assert reasons(first) == {"s1-j1": "failed"}
    again = Script({"recover_list": [listing()]})
    retry = run(again)
    assert again.ops() == ["recover_list", "listed"]
    assert retry.blocking == ()
    assert blocking_for(retry, workspace_key="k1", lineage="lin1", ledger_keys={"/l/a"}) == ()


# -- which journal blocks which open (§8.1 "Afterwards", ADR-0063) ---------------------------------------------------


def report_with(*blocking: Blocking, unlisted: tuple[tuple[str, int | None], ...] = ()) -> Report:
    from app.platform.engine import UnlistedFolder

    return Report(listed=True, blocking=blocking, unlisted=tuple(UnlistedFolder(f, e) for f, e in unlisted))


def journal(key: str | None = "k1", lineage: str | None = "lin1", keys: tuple[str, ...] = ("/l/prices",)) -> Journal:
    from app.platform.engine import JournalFile

    files = tuple(JournalFile(k, k, False) for k in keys)
    return Journal(JD1, "s1-j1", key is not None, key, lineage, files)


@pytest.mark.parametrize(
    ("key", "lineage", "ledger", "blocked"),
    [
        ("k1", None, set[str](), True),  # its own workspace, by registry key
        ("k9", "lin1", set[str](), True),  # its folder opened by Locate or "Open folder…": by lineage
        ("k2", "lin2", {"/l/prices"}, True),  # another workspace that uses one of its files
        ("k2", "lin2", {"/l/other"}, False),  # a workspace that uses none of its files
        (None, None, {"/l/prices"}, True),  # a copy that would read one of its files
        (None, None, {"/l/other"}, False),
    ],
)
def test_a_blocking_journal_blocks_the_workspaces_that_use_its_files(
    key: str | None, lineage: str | None, ledger: set[str], blocked: bool
) -> None:
    b = Blocking(journal(), "stopped", "foreign")
    out = blocking_for(report_with(b), workspace_key=key, lineage=lineage, ledger_keys=ledger)
    assert out == ((b,) if blocked else ())


def test_a_journal_whose_file_list_cannot_be_read_blocks_every_open() -> None:
    b = Blocking(journal(key=None, lineage=None, keys=()), "stopped", "unreadable")
    assert blocking_for(report_with(b), workspace_key="k7", lineage="lin7", ledger_keys={"/x"}) == (b,)


def test_a_journal_folder_that_could_not_be_listed_blocks_every_open() -> None:
    out = blocking_for(report_with(unlisted=((JD2, 5),)), workspace_key="k7", lineage="lin7", ledger_keys={"/x"})
    assert out == (NotRecovered(JD2, 5),)


@pytest.mark.parametrize("report", [None, Report(listed=False, why_not_listed="unavailable")])
def test_a_recover_all_that_never_listed_blocks_every_open(report: Report | None) -> None:
    out = blocking_for(report, workspace_key="k7", lineage="lin7", ledger_keys=set())
    assert len(out) == 1
    assert isinstance(out[0], NotRecovered)


def test_an_undone_or_kept_journal_blocks_nothing() -> None:
    j = journal()
    report = Report(listed=True, undone=(j,), kept=(j,))
    assert blocking_for(report, workspace_key="k1", lineage="lin1", ledger_keys={"/l/prices"}) == ()


# -- through the scheduler (the table's Cancel per journal, a death, the bound; S0062) -------------------------------


class Rig:
    def __init__(self, scripted: dict[str, list[object]]) -> None:
        self.engine = FakeEngine(scripted=scripted)
        self.jobs = EngineJobs(self.engine, wait_engine_lock=LockWait(), recover_all=recover_all_work(ALD))
        self.engine.spawned = self.jobs.engine_spawned
        self.engine.ended = self.jobs.engine_ended

    def boot(self) -> JobTicket:
        ticket = self.jobs.start()
        assert isinstance(ticket, JobTicket)
        return ticket


@pytest.fixture
def make() -> Iterator[Callable[[dict[str, list[object]]], Rig]]:
    rigs: list[Rig] = []

    def build(scripted: dict[str, list[object]]) -> Rig:
        rigs.append(Rig(scripted))
        return rigs[-1]

    yield build
    for r in rigs:
        r.jobs.close()


def report_of(ticket: JobTicket) -> Report:
    out = ticket.wait(DEADLINE)
    assert isinstance(out, Done), out
    assert isinstance(out.value, Report)
    return out.value


def test_the_boots_recover_all_runs_the_real_work(make: Callable[[dict[str, list[object]]], Rig]) -> None:
    rig = make({"recover_list": [listing(listed("s1-j1"))], "recover_try": [tried("resolved")]})
    report = report_of(rig.boot())
    assert [j.name for j in report.undone] == ["s1-j1"]
    assert [op for _, op, _ in rig.engine.params] == ["recover_list", "recover_try"]


def test_a_cancel_per_journal_through_the_table(make: Callable[[dict[str, list[object]]], Rig]) -> None:
    rig = make(
        {
            "recover_list": [listing(listed("s1-j1"), listed("s1-j2"))],
            "recover_try": [BLOCK, tried("resolved"), tried("resolved")],
        }
    )
    ticket = rig.boot()
    wait_until(lambda: len(rig.engine.params) == 2)  # the first journal's try is in flight
    wait_until(lambda: (r := rig.jobs.status().queue.running) is not None and r.listed)
    assert rig.jobs.cancel(ticket.id) == "cancelled"
    report = report_of(ticket)
    assert reasons(report) == {"s1-j1": "cancelled"}
    assert [j.name for j in report.undone] == ["s1-j2"]
    serials = [serial for serial, _, _ in rig.engine.params]
    assert serials == [1, 1, 2], "the next journal was not tried in a fresh engine"
    assert (1, "cancel") in rig.engine.ends


def test_a_cancel_before_the_listing_ends_the_boot_on_its_cancelled_screen(
    make: Callable[[dict[str, list[object]]], Rig],
) -> None:
    rig = make({"recover_list": [BLOCK, listing()]})
    ticket = rig.boot()
    wait_until(lambda: len(rig.engine.params) == 1)
    assert rig.jobs.cancel(ticket.id) == "cancelled"
    out = ticket.wait(DEADLINE)
    assert isinstance(out, Failed)
    assert out.error.code == ErrorCode.JOB_CANCELLED


def test_an_engine_death_per_journal_through_the_table(make: Callable[[dict[str, list[object]]], Rig]) -> None:
    rig = make({"recover_list": [listing(listed("s1-j1"), listed("s1-j2"))], "recover_try": [CRASH, tried("resolved")]})
    report = report_of(rig.boot())
    assert reasons(report) == {"s1-j1": "failed"}
    assert [j.name for j in report.undone] == ["s1-j2"]
    assert [serial for serial, op, _ in rig.engine.params if op == "recover_try"] == [1, 2]


def test_the_stop_bound_through_the_table_marks_the_rest_not_tried(
    make: Callable[[dict[str, list[object]]], Rig],
) -> None:
    rig = make(
        {
            "recover_list": [listing(listed("s1-j1"), listed("s1-j2"), listed("s1-j3"))],
            "recover_try": [CRASH],
        }
    )
    rig.engine.before_start = lambda: rig.engine.unavailable((1.0, 2.0, 3.0)) if rig.engine.starts >= 1 else None
    report = report_of(rig.boot())
    assert reasons(report) == {"s1-j1": "failed", "s1-j2": "not_tried", "s1-j3": "not_tried"}


def test_the_bound_known_before_the_next_engine_refuses_it_and_the_report_still_comes(
    make: Callable[[dict[str, list[object]]], Rig],
) -> None:
    """The stop that reaches the bound is the journal's own death: the table is told the engine is unavailable before
    the job asks for its next engine, refuses it (`EngineRefused`) and lets the work report."""
    rig = make(
        {"recover_list": [listing(listed("s1-j1"), listed("s1-j2"))], "recover_try": [CRASH_TO_BOUND]},
    )
    report = report_of(rig.boot())
    assert reasons(report) == {"s1-j1": "failed", "s1-j2": "not_tried"}
    assert next(b for b in report.blocking if b.journal.name == "s1-j2").cause == "unavailable"
    assert rig.engine.starts == 1, "an engine was started while the engine was unavailable"


def test_the_spec_of_a_recover_all_job_is_the_switchs_own() -> None:
    spec = recover_all_spec()
    assert (spec.kind, spec.workspace, spec.waited_for, spec.owner) == ("recover_all", None, True, "switch")


def test_a_retry_through_the_scheduler_in_a_switchs_window(make: Callable[[dict[str, list[object]]], Rig]) -> None:
    """§9.1 step 6, §9.2: Retry is recover-all again, then the open; here its job in a window (the switch's
    machinery that opens the window is BL-0154)."""
    rig = make({"recover_list": [listing(listed("s1-j1")), listing()], "recover_try": [tried("failed", "io_error")]})
    assert reasons(report_of(rig.boot())) == {"s1-j1": "failed"}
    wait_until(lambda: not rig.jobs.status().queue.exclusive)
    rig.jobs.exclusive_began()
    ticket = rig.jobs.submit(recover_all_spec(), recover_all_work(ALD))
    assert isinstance(ticket, JobTicket)
    retry = report_of(ticket)
    assert retry.listed
    assert retry.blocking == ()


def test_the_scripts_context_is_the_jobs(make: Callable[[dict[str, list[object]]], Rig]) -> None:
    """The scripted context above has the job context's methods (a guard on the duck type)."""
    for name in ("call", "fresh_engine", "listed"):
        assert callable(getattr(JobContext, name))
