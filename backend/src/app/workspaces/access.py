"""Whether a request naming a workspace may be served: the open-workspace gate's answer (ADR-0018, ADR-0063; INV-28).

An instance has at most one open workspace (INV-31). A request names its workspace by registry key in the path; the
gate answers for that key alone, from the caller's own registry, and the API edge (`app.api.workspace`) turns the
answer into a served request or a refusal. No request finds its target from this state: the gate only says yes or
why not, it never names another workspace to serve instead.

`access` must not do I/O or wait: it reads the registry and the open-workspace record, both in memory. Until the
registry exists (BL-0102, M2) the only gate is `NoWorkspaces`: every key is unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NewType, Protocol

from app.platform.errors import NotReadyPhase

WorkspaceKey = NewType("WorkspaceKey", str)
"""A workspace's key in this instance's registry (ARCHITECTURE.md §2.6; not its lineage id, ADR-0017)."""


@dataclass(frozen=True, slots=True)
class Open:
    """The workspace is the open one and can serve: the request is served in it."""

    key: WorkspaceKey


@dataclass(frozen=True, slots=True)
class Unknown:
    """No workspace with this key is registered: `404 WORKSPACE_UNKNOWN`."""


@dataclass(frozen=True, slots=True)
class NotOpen:
    """Registered, but not the open one, or a switch is closing it: `409 WORKSPACE_NOT_OPEN` (ADR-0063)."""

    open_workspace: WorkspaceKey | None
    switching: bool


@dataclass(frozen=True, slots=True)
class NotReady:
    """The open workspace, not ready to serve yet: `409 WORKSPACE_NOT_READY {phase}` (ADR-0018)."""

    phase: NotReadyPhase


type Access = Open | Unknown | NotOpen | NotReady


class WorkspaceAccess(Protocol):
    def access(self, key: str) -> Access: ...


class NoWorkspaces:
    """The gate of an instance with no registered workspace; the only one until the registry exists (BL-0102)."""

    def access(self, key: str) -> Access:
        return Unknown()
