"""The workspace gate of every `/api/w/{ws}/…` route (ADR-0018, ADR-0063; INV-28).

The gate (`admit`) resolves `{ws}`, the key in the path and nothing else (no header, query, cookie or server-side
"current" workspace), through the caller's own container. The workspace is served only when the gate says it is the
open one; otherwise the request is refused before anything of the route runs, so it changes nothing:

- not registered: `404 WORKSPACE_UNKNOWN`;
- registered but not open, or a switch is closing it: `409 WORKSPACE_NOT_OPEN {open_workspace, switching}`, never
  redirected to the open workspace;
- open but still warming: `409 WORKSPACE_NOT_READY {phase}` (never recovering: ADR-0063 decision 2).

A gate that answers "open" for another key than the one named is a bug; the request fails closed as `INTERNAL`.

Every workspace route is a `GatedRoute`, made by `workspace_router()`: the gate runs as soon as the route matched,
before the body is read and before any dependency, so a refused request runs nothing of the route (S0046 verifier F1,
F4: a dependency listed before the gate, or a malformed body, could otherwise act or answer first). The dependency
`open_workspace` only hands the admitted key to the handler. The route-scope check (`app.api.routes`) refuses a
workspace path whose route is not a `GatedRoute`, and an app route that is one.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any, override

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.routing import APIRoute

from app.api.envelope import context_of
from app.platform.errors import ErrorCode, WorkspaceNotOpenDetails, WorkspaceNotReadyDetails, error
from app.workspaces.access import NotOpen, NotReady, Open, Unknown, WorkspaceKey

PREFIX = "/api/w/{ws}"


class GateAnsweredForAnotherWorkspace(RuntimeError):
    """The gate answered "open" for a key other than the one the request named."""


class NotAdmitted(RuntimeError):
    """`open_workspace` ran on a request the gate did not admit: the route is not a `GatedRoute` (a wiring bug)."""


def admit(request: Request) -> WorkspaceKey:
    """The gate: the key in the path, answered by the caller's container; raises the refusal."""
    ctx = context_of(request)
    ws = request.path_params.get("ws")
    if not isinstance(ws, str):
        raise NotAdmitted
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


class GatedRoute(APIRoute):
    """A workspace route: the gate runs first, before the body is read and before any dependency."""

    @override
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def gated(request: Request) -> Response:
            admit(request)
            return await handler(request)

        return gated


async def open_workspace(request: Request, ws: Annotated[str, Path()]) -> WorkspaceKey:
    """The workspace the gate admitted for this request (and the `ws` path parameter in the contract)."""
    admitted = context_of(request).workspace_id
    if admitted is None or admitted != ws:
        raise NotAdmitted
    return admitted


def workspace_router() -> APIRouter:
    """The router of workspace-scoped routes: under `/api/w/{ws}`, every route a `GatedRoute`."""
    return APIRouter(prefix=PREFIX, route_class=GatedRoute, dependencies=[Depends(open_workspace)])
