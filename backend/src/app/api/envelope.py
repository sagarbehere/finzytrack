"""The one envelope of every answer (ADR-0038; ARCHITECTURE.md §4.1).

Success: `{data, meta}`; error: `{error, meta}` (`app.api.errors`). `meta` always has its four fields, null where the
server has no value: `workspace_id` is the workspace that served the request (null for app-scoped routes and for any
refusal before a workspace was served); `ledger_version` and `config_version` arrive with the mirror and the settings
stores. Response models carry no defaults, so every field is required in the generated types (ADR-0038 amendment).
"""

from __future__ import annotations

from fastapi import Request
from pydantic import BaseModel

from app.api.container import RequestContext


class Meta(BaseModel, frozen=True):
    request_id: str
    workspace_id: str | None
    ledger_version: str | None
    config_version: str | None


class Envelope[T](BaseModel, frozen=True):
    data: T
    meta: Meta


STATE_KEY = "ft"
"""The key of the request's `RequestContext` in its ASGI scope's `state`."""


class NoRequestContext(RuntimeError):
    """A handler ran without the edge's middleware: a wiring bug."""


def context_of(request: Request) -> RequestContext:
    ctx: object = request.scope.get("state", {}).get(STATE_KEY)
    if not isinstance(ctx, RequestContext):
        raise NoRequestContext
    return ctx


def meta_of(ctx: RequestContext | None, request_id: str) -> Meta:
    return Meta(
        request_id=request_id,
        workspace_id=ctx.workspace_id if ctx else None,
        ledger_version=None,
        config_version=None,
    )


def ok[T](request: Request, data: T) -> Envelope[T]:
    """The success envelope of `data` for this request."""
    ctx = context_of(request)
    return Envelope(data=data, meta=meta_of(ctx, ctx.request_id))
