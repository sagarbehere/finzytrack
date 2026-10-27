"""The registry of every stateful part of the main sidecar (ARCHITECTURE.md §2.5; EP-04; INV-10, INV-11).

State that outlives a request lives only in the places listed here, each with its owner, bound and eviction.
Every entry is owned by a container: a `UserContainer` or `WorkspaceContainer` entry names the class or factory
whose instances the container holds. There is no process-wide state (ADR-0064 retired the heavy-load semaphore,
the one process-wide entry ADR-0041 allowed; the engine job queue hangs off `UserContainer`, INV-11).
`tests/architecture/test_globals.py` scans every module and fails on module-level or class-level state,
`global` writes or memoizing decorators, and on a process-wide, duplicate, incomplete or stale entry. The slices
that add the engine job queue and the stores register them here.
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
        "one engine process with three pipe threads, one request in flight, the last 200 stderr lines, one "
        "supervision state (app.platform.engine_supervision) holding at most max_crashes stop times",
        "close() when the user container closes; a stopped engine is restarted by the next request, bounded by "
        "RestartPolicy",
    ),
    StateEntry(
        "app.platform.engine_boot",
        "EngineBoot",
        "UserContainer",  # S0038: the engine's first start, with the EngineClient it reports on
        "one EngineState value (starting, ready with its handshake facts and pid, or down with its failure)",
        "replaced once when the first start ends; dropped with the user container",
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
