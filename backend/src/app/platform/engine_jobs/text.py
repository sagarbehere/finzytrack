"""What the user reads about engine jobs (S0058; `design/specs/engine-jobs.md` §4, §5.2, §5.4, §11; failure-modes
E2, E3).

Pure: a job's text and whether Cancel is offered, from the scheduler's published status (`EngineJobs.status()`) and
the time. At once a job shows its phase; queued, "Waiting for: <the running job's phase>"; after the display delay,
counted from the last change of what it shows, "Still working…" with the same actions (§5.4: the delay only changes
the text). Cancel is offered exactly where the table's Cancel would end the job or its wait (`queue.cancel_offered`).
Past its commit point a writing job is "Finishing your change…", with no Cancel (§4). While the slot is held by a
process we signalled, or the engine lock is waited for, the quit advice shows after its delay "in the waiting job's
place if there is one, otherwise as a notice" (§5.2, N3); the table decides when (`QueueState.advice`). The UI that
shows these is BL-0092.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.platform.engine_jobs.queue import cancel_offered
from app.platform.engine_jobs.scheduler import DISPLAY_DELAY, JobsStatus, ShownPhase
from app.platform.errors import EngineUnavailableDetails, JobCancelledDetails

PHASES: Final[tuple[tuple[str, str], ...]] = (
    ("starting", "Starting the ledger engine…"),
    ("reading", "Reading your ledger…"),
    ("checking_change", "Checking your change…"),
    ("waiting_for_files", "Waiting for your ledger files…"),
    ("saving", "Saving…"),
    ("updating_views", "Updating your views…"),
    ("checking_last_change", "Checking your last change…"),
)
"""§5.4's phases ("starting" is the shell's own, while it starts the job's engine)."""
WORKING: Final = "Working…"
"""A job on its engine before the engine reports its first phase (agent's text; §5.4 names no phase for it)."""
STILL_WORKING: Final = "Still working…"
FINISHING: Final = "Finishing your change…"
ADVICE: Final = "Finzytrack cannot stop the earlier ledger work. Quit and reopen Finzytrack."
CANNOT_STOP_FROM_HERE: Final = (
    "Your request is cancelled. The earlier ledger work cannot be stopped from here; if it never finishes, quit and "
    "reopen Finzytrack."
)
CANCELLED: Final = "Cancelled."


@dataclass(frozen=True, slots=True)
class JobText:
    text: str
    cancel: bool


def phase_text(phase: ShownPhase) -> str:
    """A phase's words (§5.4); before the first phase, `WORKING`."""
    return WORKING if phase is None else next(text for name, text in PHASES if name == phase)


def describe(status: JobsStatus, job_id: int, now: float, delay: float = DISPLAY_DELAY) -> JobText | None:
    """The job's text and whether Cancel is offered; None for a job the queue no longer holds."""
    state = status.queue
    running = state.running
    queued = next((j for j in state.queued if j.id == job_id), None)
    mine = running is not None and running.job.id == job_id
    if not mine and queued is None:
        return None
    cancel = cancel_offered(status.policy, state, job_id)
    shown = status.shown.get(job_id)
    if mine and running is not None and running.past_commit:
        return JobText(FINISHING, cancel=False)
    waiting = queued is not None or (mine and state.slot != "busy")
    if waiting and state.advice:
        return JobText(ADVICE, cancel)
    if shown is not None and now - shown.since >= delay:
        return JobText(STILL_WORKING, cancel)
    if mine:
        return JobText(phase_text(shown.phase if shown is not None else None), cancel)
    if running is not None:
        ahead = status.shown.get(running.job.id)
        return JobText(f"Waiting for: {phase_text(ahead.phase if ahead is not None else None)}", cancel)
    return JobText(f"Waiting for: {phase_text('starting')}", cancel)


def notice(status: JobsStatus) -> str | None:
    """The advice as a notice in the window, when it shows and no waiting job carries it (§5.2, N3)."""
    state = status.queue
    if not state.advice:
        return None
    waiting = any(j.waited_for and j.requester_present for j in state.queued)
    if state.running is not None and state.slot != "busy" and state.running.job.waited_for:
        waiting = True
    return None if waiting else ADVICE


def cancelled_text(details: JobCancelledDetails) -> str:
    """§4, §11: the answer to a Cancel."""
    return CANNOT_STOP_FROM_HERE if details.cannot_stop_ahead else CANCELLED


UNAVAILABLE: Final[tuple[tuple[str, str], ...]] = (
    (
        "stopped_often",
        "The ledger engine stopped several times. Your ledger files are safe. Saving is off until it works again.",
    ),
    ("cannot_start", "The ledger engine could not start. You can still look at your ledgers; saving changes is off."),
    ("incompatible", "This installation is damaged. Please reinstall Finzytrack."),
    ("closing", "Finzytrack is closing."),
)
"""§11 "bound reached | failure-modes E1's text"; E6's two texts (an engine that cannot start; an incompatible one,
a damaged install); the app closing."""


def unavailable_text(details: EngineUnavailableDetails) -> str:
    """`ENGINE_UNAVAILABLE` worded by its reason (§11; the registry's message is the code's short form)."""
    return next(text for reason, text in UNAVAILABLE if reason == details.reason)
