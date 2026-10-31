"""What the user reads about a job (`app.platform.engine_jobs.text`; S0058; `design/specs/engine-jobs.md` §4, §5.2,
§5.4, §11; failure-modes E2, E3).

Pure: a job's text from the scheduler's published status and the time. The texts are the spec's, row by row:
§5.4 ("at once, the job's phase with Cancel where §4 allows it ...; when it is queued, 'Waiting for: <the running
job's phase>'. After a short display delay ... the text becomes 'Still working…' with the same actions"), §4 ("a
writing job past its commit point ... 'Finishing your change…'"), §5.2 and §11 (the quit advice "in the waiting job's
place, or as a notice"). The UI that shows them is BL-0092.
"""

from __future__ import annotations

import pytest

from app.platform.engine_jobs.queue import Job, QueuePolicy, QueueState, Running
from app.platform.engine_jobs.scheduler import JobsStatus, Shown
from app.platform.engine_jobs.text import (
    ADVICE,
    CANNOT_STOP_FROM_HERE,
    CHECKING_LAST_CHANGE_FIRST,
    FINISHING,
    PHASES,
    STILL_WORKING,
    cancelled_text,
    describe,
    notice,
    unavailable_text,
)
from app.platform.errors import EngineUnavailableDetails, JobCancelledDetails

POLICY = QueuePolicy()
NOW = 100.0
DELAY = 2.0

PREVIEW = Job(1, "preview", "ws-1", True, False, "user", True)
SAVE = Job(2, "save", "ws-1", True, True, "user", True)
R4 = Job(3, "r4", "ws-1", False, False, "app", True)
WAITING = Job(4, "bql", "ws-1", True, False, "user", True)


def status(
    *,
    running: Running | None = None,
    queued: tuple[Job, ...] = (),
    shown: dict[int, Shown] | None = None,
    slot: str = "busy",
    advice: bool = False,
) -> JobsStatus:
    state = QueueState(
        queued=queued,
        running=running,
        slot=slot,  # type: ignore[arg-type]
        engine_lock="unchecked",
        open_workspace="ws-1",
        advice=advice,
        engine=None if slot == "none" else 1,
        next_engine=2,
    )
    return JobsStatus(queue=state, policy=POLICY, shown=shown or {})


def test_the_phases_are_the_specs() -> None:
    assert dict(PHASES) == {
        "starting": "Starting the ledger engine…",
        "reading": "Reading your ledger…",
        "checking_change": "Checking your change…",
        "waiting_for_files": "Waiting for your ledger files…",
        "saving": "Saving…",
        "updating_views": "Updating your views…",
        "checking_last_change": "Checking your last change…",
    }


@pytest.mark.parametrize("phase", sorted(dict(PHASES)))
def test_a_running_job_shows_its_phase_at_once_then_still_working(phase: str) -> None:
    s = status(running=Running(PREVIEW), shown={1: Shown(phase, NOW)})  # type: ignore[arg-type]
    at_once = describe(s, 1, NOW + DELAY / 2, DELAY)
    assert at_once is not None
    assert at_once.text == dict(PHASES)[phase]
    assert at_once.cancel
    later = describe(s, 1, NOW + DELAY, DELAY)
    assert later is not None
    assert later.text == STILL_WORKING
    assert later.cancel, "the same actions after the delay"


def test_a_phase_change_is_shown_at_once() -> None:
    """§5.4: "a phase change is shown, never judged": the delay counts from the last change."""
    s = status(running=Running(PREVIEW), shown={1: Shown("saving", NOW + 10)})
    shown = describe(s, 1, NOW + 10.5, DELAY)
    assert shown is not None
    assert shown.text == "Saving…"


def test_before_its_first_phase_a_job_on_its_engine_shows_working() -> None:
    s = status(running=Running(PREVIEW), shown={1: Shown(None, NOW)})
    shown = describe(s, 1, NOW, DELAY)
    assert shown is not None
    assert shown.text == "Working…"


def test_a_queued_job_shows_what_it_waits_for() -> None:
    s = status(running=Running(PREVIEW), queued=(WAITING,), shown={1: Shown("reading", NOW), 4: Shown(None, NOW)})
    shown = describe(s, 4, NOW, DELAY)
    assert shown is not None
    assert shown.text == "Waiting for: Reading your ledger…"
    assert shown.cancel
    later = describe(s, 4, NOW + DELAY, DELAY)
    assert later is not None
    assert later.text == STILL_WORKING


def test_a_queued_job_with_nothing_running_waits_for_the_engine() -> None:
    s = status(queued=(WAITING,), shown={4: Shown(None, NOW)}, slot="none")
    shown = describe(s, 4, NOW, DELAY)
    assert shown is not None
    assert shown.text == "Waiting for: Starting the ledger engine…"


def test_past_the_commit_point_the_job_is_finishing_with_no_cancel() -> None:
    """§4: "a writing job past its commit point: nothing: the job finishes ... 'Finishing your change…'"."""
    s = status(running=Running(SAVE, past_commit=True), shown={2: Shown("saving", NOW)})
    shown = describe(s, 2, NOW + 10 * DELAY, DELAY)
    assert shown is not None
    assert shown.text == FINISHING
    assert not shown.cancel


def test_a_background_job_has_no_cancel() -> None:
    s = status(running=Running(R4), shown={3: Shown("checking_last_change", NOW)})
    shown = describe(s, 3, NOW, DELAY)
    assert shown is not None
    assert not shown.cancel


def test_a_save_that_checks_the_last_change_first_says_so() -> None:
    """BL-0173 (owner, 2026-10-31: "Drop digest; 2 jobs (Recommended)"); §11: "a save waits for R4 | 'Checking your
    last change first…'": the save runs the previous save's R4 before its own work. The background R4 says "Checking
    your last change…"; a job waiting behind the save reads the save's text."""
    s = status(running=Running(SAVE), queued=(WAITING,), shown={2: Shown("checking_last_change", NOW)})
    shown = describe(s, 2, NOW, DELAY)
    assert shown is not None
    assert shown.text == CHECKING_LAST_CHANGE_FIRST == "Checking your last change first…"
    assert shown.cancel, "§6.1 step 1: a Cancel during this step cancels the save"
    waiting = describe(s, 4, NOW, DELAY)
    assert waiting is not None
    assert waiting.text == "Waiting for: Checking your last change first…"
    background = describe(status(running=Running(R4), shown={3: Shown("checking_last_change", NOW)}), 3, NOW, DELAY)
    assert background is not None
    assert background.text == "Checking your last change…"


def test_the_advice_shows_in_the_waiting_jobs_place_or_else_as_a_notice() -> None:
    """§5.2: "in the waiting job's place if there is one, otherwise as a notice in the window, which stays until the
    process ends" (N3)."""
    with_job = status(queued=(WAITING,), shown={4: Shown(None, NOW)}, slot="ending", advice=True)
    shown = describe(with_job, 4, NOW, DELAY)
    assert shown is not None
    assert shown.text == ADVICE
    assert shown.cancel
    assert notice(with_job) is None
    alone = status(slot="ending", advice=True)
    assert notice(alone) == ADVICE
    assert notice(status(slot="ending")) is None


def test_a_job_the_queue_does_not_hold_has_no_text() -> None:
    assert describe(status(), 9, NOW, DELAY) is None


def test_the_answer_to_a_cancel_behind_work_that_cannot_be_stopped() -> None:
    """§4, §11: "Your request is cancelled. The earlier ledger work cannot be stopped from here; if it never
    finishes, quit and reopen Finzytrack."."""
    assert cancelled_text(JobCancelledDetails(cannot_stop_ahead=True)) == CANNOT_STOP_FROM_HERE
    assert CANNOT_STOP_FROM_HERE == (
        "Your request is cancelled. The earlier ledger work cannot be stopped from here; if it never finishes, "
        "quit and reopen Finzytrack."
    )
    assert cancelled_text(JobCancelledDetails(cannot_stop_ahead=False)) == "Cancelled."


def test_the_advice_and_still_working_are_the_specs() -> None:
    assert ADVICE == "Finzytrack cannot stop the earlier ledger work. Quit and reopen Finzytrack."
    assert STILL_WORKING == "Still working…"
    assert FINISHING == "Finishing your change…"


def test_a_waited_for_job_between_engines_carries_the_advice_so_no_notice_shows() -> None:
    """§5.2: "in the waiting job's place if there is one": a running job that waits for its next engine is waiting
    (S0058 verifier F4)."""
    between = status(
        running=Running(SAVE, wants_engine=True), shown={2: Shown("saving", NOW)}, slot="ending", advice=True
    )
    shown = describe(between, 2, NOW, DELAY)
    assert shown is not None
    assert shown.text == ADVICE
    assert notice(between) is None


def test_a_queued_job_whose_requester_left_carries_no_advice_so_the_notice_shows() -> None:
    """§5.2, N3: nobody waits on a job whose tab was closed, so the advice is a notice in the window."""
    gone = Job(4, "save", "ws-1", True, True, "user", False)
    s = status(queued=(gone,), shown={4: Shown(None, NOW)}, slot="ending", advice=True)
    assert notice(s) == ADVICE


def test_a_background_job_queued_carries_no_advice_so_the_notice_shows() -> None:
    s = status(queued=(R4,), shown={3: Shown(None, NOW)}, slot="ending", advice=True)
    assert notice(s) == ADVICE


@pytest.mark.parametrize(
    ("reason", "text"),
    [
        (
            "stopped_often",
            "The ledger engine stopped several times. Your ledger files are safe. Saving is off until it works again.",
        ),
        (
            "cannot_start",
            "The ledger engine could not start. You can still look at your ledgers; saving changes is off.",
        ),
        ("incompatible", "This installation is damaged. Please reinstall Finzytrack."),
        ("closing", "Finzytrack is closing."),
    ],
)
def test_the_engine_unavailable_answer_is_worded_by_its_reason(reason: str, text: str) -> None:
    """§11 "bound reached | failure-modes E1's text"; E6's text for an engine that cannot start (S0058 verifier F8).
    The registry's message is the code's short form; these are the words the UI shows for each reason (BL-0092)."""
    assert unavailable_text(EngineUnavailableDetails(reason=reason)) == text  # type: ignore[arg-type]
