"""The workspace gate of every `/api/w/{ws}/…` route (ADR-0018, ADR-0063; INV-28).

`open_workspace` resolves `{ws}`, the key in the path and nothing else (no header, query, cookie or server-side
"current" workspace), through the caller's own container. The workspace is served only when the gate says it is the
open one; otherwise the request is refused before any handler runs, so it changes nothing:

- not registered: `404 WORKSPACE_UNKNOWN`;
- registered but not open, or a switch is closing it: `409 WORKSPACE_NOT_OPEN {open_workspace, switching}`, never
  redirected to the open workspace;
- open but warming or recovering: `409 WORKSPACE_NOT_READY {phase}`.

A gate that answers "open" for another key than the one named is a bug; the request fails closed as `INTERNAL`. Every
workspace route is built on `workspace_router()`, which runs the gate for each request; the route-scope check
(`app.api.routes`) refuses a workspace route without it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request

from app.api.envelope import context_of
from app.platform.errors import ErrorCode, WorkspaceNotOpenDetails, WorkspaceNotReadyDetails, error
from app.workspaces.access import NotOpen, NotReady, Open, Unknown, WorkspaceKey

PREFIX = "/api/w/{ws}"


class GateAnsweredForAnotherWorkspace(RuntimeError):
    """The gate answered "open" for a key other than the one the request named."""


async def open_workspace(request: Request, ws: Annotated[str, Path()]) -> WorkspaceKey:
    ctx = context_of(request)
    match ctx.container.workspaces.access(ws):
        case Open(key=key) if key == ws:
            ctx.workspace_id = key
            return key
        case Open():
            raise GateAnsweredForAnotherWorkspace
        case Unknown():
            raise error(ErrorCode.WORKSPACE_UNKNOWN)
        case NotOpen(open_workspace=open_key, switching=switching):
            raise error(ErrorCode.WORKSPACE_NOT_OPEN, WorkspaceNotOpenDetails(open_key, switching))
        case NotReady(phase=phase):
            raise error(ErrorCode.WORKSPACE_NOT_READY, WorkspaceNotReadyDetails(phase))


def workspace_router() -> APIRouter:
    """The router of workspace-scoped routes: under `/api/w/{ws}`, every route behind the gate."""
    return APIRouter(prefix=PREFIX, dependencies=[Depends(open_workspace)])
