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
from fastapi import APIRouter, Depends, Request
from starlette.routing import Mount

from app.api import create_app, export_openapi
from app.api.envelope import Envelope, ok
from app.api.health import health_router
from app.api.routes import RouteScopeError, declare, product_routers, route_scope_problems
from app.api.workspace import open_workspace, workspace_router
from app.workspaces.access import Unknown, WorkspaceKey
from tests.api.support import Counted, FakeContainer, FixedAccess, probe_router


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
            lambda: _ws_route("/x", declare("ledger"), gated=False), "open_workspace", id="a workspace route ungated"
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
def test_a_workspace_route_gated_through_another_dependency_passes() -> None:
    r = _router("/api/w/{ws}")
    r.add_api_route("/x", _nested_handler, methods=["GET"], openapi_extra=declare("ledger"))
    _build(r)


@pytest.mark.inv("INV-28")
def test_a_route_that_is_not_an_api_route_is_refused() -> None:
    """A mount, a plain Starlette route or a websocket is outside the envelope and the declarations."""
    container = FakeContainer(FixedAccess(Unknown()))
    app = create_app(lambda: container)
    app.router.routes.append(Mount("/static", routes=[]))
    assert any("not an API route" in p for p in route_scope_problems(app))


def test_the_contract_carries_both_extensions_on_every_operation() -> None:
    for path, item in export_openapi()["paths"].items():
        for method, op in item.items():
            assert op.get("x-ft-scope") in {"app", "config", "ledger"}, (path, method)
            assert op.get("x-ft-writes") in {"ledger", "config", "none"}, (path, method)


def test_declare_refuses_a_write_from_an_app_scope_that_is_not_config() -> None:
    """App-scoped routes write app config at most (ARCHITECTURE.md §4.2); a ledger write needs a workspace."""
    with pytest.raises(ValueError, match="ledger"):
        declare("app", writes="ledger")
