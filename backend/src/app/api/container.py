"""What the API edge needs from the composition root, per request (ARCHITECTURE.md §2.3, `create_app`).

The edge asks the factory for the caller's container once per request: on the desktop one container serves every
request; a hosted build would return the calling user's (ADR-0041, don't foreclose). `{ws}` resolves only through
this container's gate (INV-28). The `UserContainer` of M2 (BL-0102) implements it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.platform.ids import Ids
from app.platform.observability import EventSink
from app.workspaces.access import WorkspaceAccess, WorkspaceKey


class ApiContainer(Protocol):
    @property
    def app_version(self) -> str: ...

    @property
    def ids(self) -> Ids: ...

    @property
    def workspaces(self) -> WorkspaceAccess: ...

    @property
    def log(self) -> EventSink: ...


type ContainerFactory = Callable[[], ApiContainer]


@dataclass(slots=True)
class RequestContext:
    """One request's own state at the edge, kept in its ASGI scope: never shared between requests."""

    request_id: str
    container: ApiContainer
    workspace_id: WorkspaceKey | None = None  # set by the workspace gate when it serves the request
