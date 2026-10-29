"""The registry of every stateful part of the main sidecar (ARCHITECTURE.md §2.5; EP-04; INV-10, INV-11).

State that outlives a request lives only in the places listed here, each with its owner, bound and eviction.
Every entry is owned by a container: a `UserContainer` or `WorkspaceContainer` entry names the class or factory
whose instances the container holds. There is no process-wide state (ADR-0064 retired the heavy-load semaphore,
the one process-wide entry ADR-0041 allowed; the engine job queue hangs off `UserContainer`, INV-11, since S0058).
`tests/architecture/test_globals.py` scans every module and fails on module-level or class-level state,
`global` writes or memoizing decorators, and on a process-wide, duplicate, incomplete or stale entry. The slices
that add the stores register them here.
"""

from dataclasses import dataclass
from typing import Literal

type Owner = Literal["UserContainer", "WorkspaceContainer"]


@dataclass(frozen=True, slots=True)
class StateEntry:
    """One stateful part: `module.name` (the class or factory a container holds), its owner, how big it may grow
    and how it shrinks."""

    module: str
    name: str
    owner: Owner
    bound: str
    eviction: str


REGISTRY: tuple[StateEntry, ...] = (
    StateEntry(
        "app.platform.adapters.engine_client",
        "EngineClient",
        "UserContainer",  # ADR-0054 decision 4: one engine serves the user container
        "one engine process with three pipe threads and one reaper thread, the previous engine until its group is "
        "drained, one request in flight, the last 200 stderr lines, one bytecode fill at a time, one supervision "
        "state (app.platform.engine_supervision) holding at most max_crashes stop times",
        "close() when the user container closes (it signals the engine's group and does not wait for the reap); a "
        "stopped engine is restarted by the next request, bounded by RestartPolicy",
    ),
    StateEntry(
        "app.platform.engine_jobs.scheduler",
        "EngineJobs",
        "UserContainer",  # ADR-0064, INV-11: one queue, one scheduler thread and one engine per user container
        "at most max_queued queued jobs (QueuePolicy) and one running, one QueueState, one worker thread per job "
        "(a cancelled job's worker ends once its engine has ended), one engine-lock wait at a time, at most two "
        "engines in its books (the previous one until its end report, the current one), two timers",
        "close() when the user container closes: every waiting job is answered and the scheduler thread stops; a "
        "job's record goes once it has ended and been answered",
    ),
    StateEntry(
        "app.platform.observability.log",
        "EventLog",
        "UserContainer",  # S0039: built by the composition root before anything logs, one per launch, closed last
        "one open log file and its handler lock; no buffered lines; on disk max_bytes x (backups + 1) (LogSettings)",
        "kept until the process ends (logging closes the handler at exit), after the engine is closed; the file "
        "rotates by size",
    ),
)
