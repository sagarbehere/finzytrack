"""The registry of every stateful part of the main sidecar (ARCHITECTURE.md §2.5; EP-04; INV-10, INV-11).

State that outlives a request lives only in the places listed here, each with its owner, bound and eviction.
`tests/architecture/test_globals.py` scans every module and fails on module-level mutable state, `global`
writes or memoizing decorators that are not registered, and on registered entries that no longer exist.
M0 has no stateful parts; the slices that add the heavy-load semaphore, the limiters, the job queue and the
stores register them here.
"""

from dataclasses import dataclass
from typing import Literal

type Owner = Literal["process", "UserContainer", "WorkspaceContainer"]


@dataclass(frozen=True, slots=True)
class StateEntry:
    """One stateful module-level name: `module.name`, who owns it, how big it may grow and how it shrinks."""

    module: str
    name: str
    owner: Owner
    bound: str
    eviction: str


REGISTRY: tuple[StateEntry, ...] = ()
