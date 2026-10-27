"""Every route declares its scope, and the declaration matches its path and its workspace gate (S0046).

Spec: ADR-0018 consequences ("a route-scope test checks that every route declares `app` or `workspace` scope
(`x-ft-scope`)"), ADR-0038 (`x-ft-scope: app | config | ledger` on every operation; `x-ft-writes: ledger | config |
none`), ARCHITECTURE.md §4.2 (workspace-scoped endpoints live under `/api/w/{ws}/…`, app-scoped ones do not), INV-28
(every workspace-scoped request names its workspace in the path and passes the open-workspace gate). The check runs
when the app is built, so a misdeclared route can never be served or exported; each rule sits beside the planted
route that must not pass (EP-29).
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from fastapi import APIRouter, Depends, Request, WebSocket
from starlette.responses import PlainTextResponse

from app.api import create_app, export_openapi
from app.api.envelope import Envelope, ok
from app.api.health import health_router
from app.api.routes import RouteScopeError, declare, product_routers, route_scope_problems
from app.api.workspace import GatedRoute, open_workspace, workspace_router
from app.workspaces.access import NotOpen, Unknown, WorkspaceKey
from tests.api.support import Counted, FakeContainer, FixedAccess, client, probe_router


def _build(*routers: APIRouter) -> Any:
    container = FakeContainer(FixedAccess(Unknown()))
    return create_app(lambda: container, routers=(health_router(), *routers))


async def _handler(request: Request) -> Envelope[Counted]:
    return ok(request, Counted(n=1))


async def _ws_handler(request: Request, ws: Annotated[WorkspaceKey, Depends(open_workspace)]) -> Envelope[Counted]:
    return ok(request, Counted(n=1))


def _router(prefix: str = "", **kw: Any) -> APIRouter:
    return APIRouter(prefix=prefix, **kw)


@pytest.mark.inv("INV-28")
def test_the_product_app_passes() -> None:
    container = FakeContainer(FixedAccess(Unknown()))
    assert route_scope_problems(create_app(lambda: container)) == []
    assert [r for router in product_routers() for r in router.routes]  # the product has routes to check


@pytest.mark.inv("INV-28")
def test_a_well_declared_workspace_route_passes() -> None:
    _build(probe_router([]))


def _app_route(path: str, extra: dict[str, Any] | None, method: str = "GET") -> APIRouter:
    r = _router()
    r.add_api_route(path, _handler, methods=[method], openapi_extra=extra)
    return r


def _ws_route(path: str, extra: dict[str, Any] | None, *, gated: bool = True, method: str = "GET") -> APIRouter:
    r = workspace_router() if gated else _router("/api/w/{ws}")
    r.add_api_route(path, _ws_handler if gated else _handler, methods=[method], openapi_extra=extra)
    return r


@pytest.mark.inv("INV-28")
@pytest.mark.parametrize(
    ("router", "problem"),
    [
        pytest.param(lambda: _app_route("/api/app/x", None), "x-ft-scope", id="no declaration"),
        pytest.param(lambda: _app_route("/api/app/x", {"x-ft-writes": "none"}), "x-ft-scope", id="no scope"),
        pytest.param(lambda: _app_route("/api/app/x", {"x-ft-scope": "app"}), "x-ft-writes", id="no writes"),
        pytest.param(
            lambda: _app_route("/api/app/x", {"x-ft-scope": "workspace", "x-ft-writes": "none"}),
            "x-ft-scope",
            id="an unknown scope",
        ),
        pytest.param(
            lambda: _app_route("/api/app/x", {"x-ft-scope": "app", "x-ft-writes": "files"}),
            "x-ft-writes",
            id="an unknown writes value",
        ),
        pytest.param(lambda: _ws_route("/x", declare("app")), "workspace path", id="a workspace path declared app"),
        pytest.param(lambda: _app_route("/api/app/x", declare("ledger")), "workspace path", id="an app path as ledger"),
        pytest.param(lambda: _app_route("/api/app/x", declare("config")), "workspace path", id="an app path as config"),
        pytest.param(
            lambda: _ws_route("/x", declare("ledger"), gated=False), "GatedRoute", id="a workspace route ungated"
        ),
        pytest.param(lambda: _app_route("/api/w/x", declare("app")), "/api/w/", id="an app path under /api/w/"),
        pytest.param(
            lambda: _app_route("/api/app/{ws}/x", declare("app")), "{ws}", id="an app path taking a workspace key"
        ),
        pytest.param(
            lambda: _app_route("/api/app/x", declare("app", writes="config"), method="GET"),
            "GET",
            id="a GET that writes",
        ),
        pytest.param(
            lambda: _ws_route("/x", declare("ledger", writes="ledger"), method="HEAD"), "HEAD", id="a HEAD that writes"
        ),
        pytest.param(
            lambda: _app_route("/api/app/x", {"x-ft-scope": "app", "x-ft-writes": "ledger"}, method="POST"),
            "ledger",
            id="an app route that writes a ledger",
        ),
    ],
)
def test_a_misdeclared_route_refuses_to_build_the_app(router: Any, problem: str) -> None:
    with pytest.raises(RouteScopeError, match=problem.replace("{", r"\{").replace("}", r"\}")):
        _build(router())


@pytest.mark.inv("INV-28")
def test_an_app_route_that_passes_the_workspace_gate_is_refused() -> None:
    r = _router("/api/app")
    r.add_api_route("/x", _ws_handler, methods=["GET"], openapi_extra=declare("app"))
    with pytest.raises(RouteScopeError, match="app route"):
        _build(r)


async def _through_the_gate(ws: Annotated[WorkspaceKey, Depends(open_workspace)]) -> str:
    return ws


async def _nested_handler(request: Request, ws: Annotated[str, Depends(_through_the_gate)]) -> Envelope[Counted]:
    return ok(request, Counted(n=1))


@pytest.mark.inv("INV-28")
def test_an_app_route_that_reaches_the_gate_through_another_dependency_is_refused() -> None:
    r = _router("/api/app")
    r.add_api_route("/x", _nested_handler, methods=["GET"], openapi_extra=declare("app"))
    with pytest.raises(RouteScopeError, match="app route"):
        _build(r)


@pytest.mark.inv("INV-28")
def test_a_workspace_route_that_is_gated_only_through_a_dependency_is_refused() -> None:
    """The gate must run before anything else of the route (verifier F1): a dependency is not enough."""
    r = _router("/api/w/{ws}")
    r.add_api_route("/x", _nested_handler, methods=["GET"], openapi_extra=declare("ledger"))
    with pytest.raises(RouteScopeError, match="GatedRoute"):
        _build(r)


@pytest.mark.inv("INV-28")
def test_a_dependency_listed_before_the_gate_cannot_be_built() -> None:
    """Verifier F1 (attack A5): a router-level dependency before `open_workspace` would run on a refused request."""
    effects: list[str] = []

    async def write_something() -> None:
        effects.append("wrote")

    r = APIRouter(prefix="/api/w/{ws}", dependencies=[Depends(write_something), Depends(open_workspace)])
    r.add_api_route("/x", _handler, methods=["POST"], openapi_extra=declare("ledger", writes="ledger"))
    with pytest.raises(RouteScopeError, match="GatedRoute"):
        _build(r)
    assert effects == []


@pytest.mark.inv("INV-28")
def test_on_a_workspace_router_no_dependency_runs_for_a_refused_request() -> None:
    """The same dependency on a `workspace_router()` route: the gate refuses first and the dependency never runs."""
    effects: list[str] = []

    async def write_something() -> None:
        effects.append("wrote")

    r = workspace_router()
    r.add_api_route(
        "/x",
        _handler,
        methods=["POST"],
        openapi_extra=declare("ledger", writes="ledger"),
        dependencies=[Depends(write_something)],
    )
    container = FakeContainer(FixedAccess(NotOpen(open_workspace=WorkspaceKey("b"), switching=False)))
    app = create_app(lambda: container, routers=(health_router(), r))
    resp = client(app).request("POST", "/api/w/a/x")
    assert resp.status_code == 409
    assert effects == []


def _mounted() -> APIRouter:
    r = APIRouter()
    r.mount("/static", app=PlainTextResponse("x"))
    return r


def _plain_route() -> APIRouter:
    async def plain(request: Request) -> PlainTextResponse:
        return PlainTextResponse("x")

    r = APIRouter()
    r.add_route("/api/plain", plain)
    return r


def _websocket() -> APIRouter:
    async def sock(websocket: WebSocket) -> None:
        await websocket.close()

    r = APIRouter()
    r.add_api_websocket_route("/api/w/{ws}/sock", sock)
    return r


@pytest.mark.inv("INV-28")
@pytest.mark.parametrize("router", [_mounted, _plain_route, _websocket], ids=["mount", "plain route", "websocket"])
def test_a_route_that_is_not_an_api_route_refuses_to_build_the_app(router: Any) -> None:
    """A mount, a plain Starlette route or a websocket is outside the envelope and the declarations."""
    with pytest.raises(RouteScopeError, match="not an API route"):
        _build(router())


def test_the_contract_carries_both_extensions_on_every_operation() -> None:
    for path, item in export_openapi()["paths"].items():
        for method, op in item.items():
            assert op.get("x-ft-scope") in {"app", "config", "ledger"}, (path, method)
            assert op.get("x-ft-writes") in {"ledger", "config", "none"}, (path, method)


def test_declare_refuses_a_write_from_an_app_scope_that_is_not_config() -> None:
    """App-scoped routes write app config at most (ARCHITECTURE.md §4.2); a ledger write needs a workspace."""
    with pytest.raises(ValueError, match="ledger"):
        declare("app", writes="ledger")


@pytest.mark.inv("INV-28")
def test_a_gated_route_outside_the_workspace_prefix_is_refused() -> None:
    """A `GatedRoute` is a workspace route; on an app path, or with another prefix in front, it is refused."""
    r = _router("/api/app", route_class=GatedRoute)
    r.add_api_route("/x", _handler, methods=["GET"], openapi_extra=declare("app"))
    with pytest.raises(RouteScopeError, match="app route"):
        _build(r)
    outer = _router("/v2")
    outer.include_router(_ws_route("/x", declare("ledger")))
    with pytest.raises(RouteScopeError):
        _build(outer)
