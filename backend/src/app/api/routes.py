"""The product's routes and the route-scope check (ADR-0018 consequences, ADR-0038; INV-28).

`route_scope_problems` holds every route of an app to its declaration (`app.api.scope.declare`):

- every route is an API route (a mount or a plain route would bypass the envelope and the declarations);
- it declares `x-ft-scope` (`app`, `config` or `ledger`) and `x-ft-writes` (`ledger`, `config` or `none`);
- a path under `/api/w/{ws}/` is a workspace route: scope `config` or `ledger`, a `GatedRoute` (from
  `workspace_router()`), so the gate runs before the body is read and before any dependency;
- any other path is an app route: scope `app`, not under `/api/w/`, no `{ws}`, neither a `GatedRoute` nor depending on
  `open_workspace`, never writing a ledger;
- a GET or HEAD writes nothing.

`create_app` runs it and refuses to build an app that breaks it, so a misdeclared route is never served or exported.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts

from app.api.health import health_router
from app.api.scope import SCOPES, WRITES, declare
from app.api.workspace import PREFIX, GatedRoute, open_workspace

__all__ = ["RouteScopeError", "declare", "product_routers", "route_scope_problems"]


class RouteScopeError(ValueError):
    """An app with a misdeclared route; the message lists every problem."""


def product_routers() -> tuple[APIRouter, ...]:
    """Every router the product serves (S0046: `/health`; the verticals add theirs here)."""
    return (health_router(),)


def _gated(dependant: Dependant) -> bool:
    return any(d.call is open_workspace or _gated(d) for d in dependant.dependencies)


def route_scope_problems(app: FastAPI) -> list[str]:
    """Every route as the app serves it: an included router's routes with its prefix and dependencies (FastAPI
    includes routers lazily, so `app.routes` alone does not show them; `iter_route_contexts` is how its own OpenAPI
    generator walks them)."""
    out: list[str] = []
    for ctx in iter_route_contexts(app.routes):
        if not isinstance(ctx.original_route, APIRoute):
            out.append(f"{ctx.path!r}: not an API route (outside the envelope and the declarations)")
            continue
        out.extend(_problems(ctx))
    return out


def _problems(ctx: RouteContext) -> list[str]:
    path = ctx.path or ""
    methods = ctx.methods or set()
    where = f"{' '.join(sorted(methods))} {path}"
    extra: dict[str, object] = ctx.openapi_extra or {}
    scope, writes = extra.get("x-ft-scope"), extra.get("x-ft-writes")
    out: list[str] = []
    if scope not in SCOPES:
        out.append(f"{where}: x-ft-scope must be app, config or ledger, not {scope!r}")
    if writes not in WRITES:
        out.append(f"{where}: x-ft-writes must be ledger, config or none, not {writes!r}")
    workspace_path = path == PREFIX or path.startswith(PREFIX + "/")
    dependant: Dependant | None = ctx.dependant
    gate_first = isinstance(ctx.original_route, GatedRoute)
    gated = gate_first or (dependant is not None and _gated(dependant))
    if workspace_path:
        if scope == "app":
            out.append(f"{where}: a workspace path must be declared config or ledger")
        if not gate_first:
            out.append(f"{where}: a workspace route must be a GatedRoute (workspace_router()): the gate runs first")
    else:
        if scope in ("config", "ledger"):
            out.append(f"{where}: only a workspace path ({PREFIX}/…) may be declared {scope}")
        if path.startswith("/api/w/"):
            out.append(f"{where}: only workspace routes live under /api/w/")
        if "{ws}" in path:
            out.append(f"{where}: an app route must not take a workspace key ({{ws}})")
        if gated:
            out.append(f"{where}: an app route must not pass the workspace gate")
        if writes == "ledger":
            out.append(f"{where}: an app route cannot write a ledger")
    out.extend(
        f"{where}: {method} must not write (x-ft-writes {writes})"
        for method in sorted(methods & {"GET", "HEAD"})
        if writes not in (None, "none")
    )
    return out
