"""Errors translated once, at the edge, into the envelope's `error` (ARCHITECTURE.md §6.2, §6.3; EP-13).

The one class -> HTTP status table, the handlers that build `{error, meta}` from a code's registry entry, and the
edge middleware (`Edge`): it gives each request its id and container, binds the id to the log, and turns anything
unexpected into `INTERNAL`. It holds the API's one `except Exception` (§6.3), which logs the exception with the
request's id and never puts the exception's text in the answer (§6.5). A `DomainError` the registry does not
describe (an unknown code, another class, details of another type) is a bug too, so it fails closed as `INTERNAL`.

The contract carries the registry as a union of one error shape per code, keyed by `code` (§6.2), built by
`error_envelope_model` when an app is built, never at import (INV-11).
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import traceback
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, Literal

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, create_model
from starlette.exceptions import HTTPException

from app.api.container import ContainerFactory, RequestContext
from app.api.envelope import STATE_KEY, Meta, context_of, meta_of
from app.domain.errors import DomainError, ErrorClass
from app.domain.errors import Issue as DomainIssue
from app.platform.errors import ErrorCode, ErrorSpec, details_fit, spec_for, specs
from app.platform.observability import bound_context
from app.platform.observability.events import API_INTERNAL_ERROR

type Scope = MutableMapping[str, Any]
type Message = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type AsgiApp = Callable[[Scope, Receive, Send], Awaitable[None]]

UNASSIGNED = "unassigned"
"""The request id of an answer given before the request had a container (the factory failed)."""


def status_for(error_class: ErrorClass) -> int:
    """ARCHITECTURE.md §6.1's table; the only place that knows an HTTP status for an error."""
    match error_class:
        case ErrorClass.INVALID:
            return 422
        case ErrorClass.NOT_FOUND:
            return 404
        case ErrorClass.CONFLICT:
            return 409
        case ErrorClass.FORBIDDEN:
            return 403
        case ErrorClass.UNAVAILABLE:
            return 503
        case ErrorClass.INTERNAL:
            return 500


class Issue(BaseModel, frozen=True):
    """A located problem of an invalid request (`app.domain.errors.Issue` on the wire)."""

    path: str
    message: str


def error_envelope_model() -> type[BaseModel]:
    """`ErrorEnvelope`: `{error, meta}`, `error` being one shape per registry code, discriminated by `code`."""
    shapes: list[Any] = [_error_shape(s) for s in specs()]
    union: Any = functools.reduce(lambda a, b: a | b, shapes)
    return create_model(
        "ErrorEnvelope",
        __config__=ConfigDict(frozen=True),
        error=(union, Field(discriminator="code")),
        meta=(Meta, ...),
    )


def _error_shape(spec: ErrorSpec) -> type[BaseModel]:
    name = "".join(part.capitalize() for part in str(spec.code).split("_")) + "Error"
    code: Any = Literal[str(spec.code)]  # one literal per code, from the registry
    error_class: Any = Literal[spec.error_class.value]
    details: Any = spec.details if spec.details is not None else None
    return create_model(
        name,
        __config__=ConfigDict(frozen=True, populate_by_name=True),
        code=(code, ...),
        error_class=(error_class, Field(alias="class")),
        message=(str, ...),
        data_safe=(bool, ...),
        next_step=(str, ...),
        issues=(list[Issue], ...),
        retryable=(bool, ...),
        retry_after_ms=(int | None, ...),
        details=(details, ...),
        correlation_id=(str, ...),
    )


def error_body(spec: ErrorSpec, request_id: str, ctx: RequestContext | None, err: DomainError | None) -> dict[str, Any]:
    details = err.details if err is not None else None
    return {
        "error": {
            "code": str(spec.code),
            "class": spec.error_class.value,
            "message": spec.message,
            "data_safe": spec.data_safe,
            "next_step": spec.next_step,
            "issues": [dataclasses.asdict(i) for i in (err.issues if err is not None else ())],
            "retryable": spec.retryable,
            "retry_after_ms": None,
            "details": dataclasses.asdict(details) if details is not None else None,  # pyright: ignore[reportArgumentType] (a registry dataclass, checked by details_fit)
            "correlation_id": request_id,
        },
        "meta": meta_of(ctx, request_id).model_dump(mode="json"),
    }


def _registry(code: ErrorCode) -> ErrorSpec:
    spec = spec_for(code)
    if spec is None:  # ErrorCode and the registry are held equal by a test
        raise LookupError(code)
    return spec


def _response(spec: ErrorSpec, request_id: str, ctx: RequestContext | None, err: DomainError | None) -> JSONResponse:
    return JSONResponse(error_body(spec, request_id, ctx, err), status_code=status_for(spec.error_class))


def _internal(ctx: RequestContext, exc: BaseException) -> JSONResponse:
    ctx.container.log(
        API_INTERNAL_ERROR,
        exception_type=type(exc).__name__,
        exception="".join(traceback.format_exception(exc)),
    )
    return _response(_registry(ErrorCode.INTERNAL), ctx.request_id, ctx, None)


def well_formed(err: DomainError) -> ErrorSpec | None:
    """The registry entry `err` follows, or None: an unknown code, another class, details of another type."""
    spec = spec_for(err.code)
    if spec is None or err.error_class is not spec.error_class or not details_fit(spec, err.details):
        return None
    return spec


async def on_domain_error(request: Request, exc: Exception) -> JSONResponse:
    ctx = context_of(request)
    if not isinstance(exc, DomainError):  # registered for DomainError only
        return _internal(ctx, exc)
    spec = well_formed(exc)
    if spec is None:
        return _internal(ctx, exc)
    return _response(spec, ctx.request_id, ctx, exc)


async def on_validation_error(request: Request, exc: Exception) -> JSONResponse:
    ctx = context_of(request)
    if not isinstance(exc, RequestValidationError):  # registered for RequestValidationError only
        return _internal(ctx, exc)
    issues = tuple(_issue(e) for e in exc.errors())
    err = DomainError(ErrorCode.VALIDATION_FAILED, issues=issues)
    return _response(_registry(ErrorCode.VALIDATION_FAILED), ctx.request_id, ctx, err)


def _issue(e: Any) -> DomainIssue:
    """A located issue: the JSON pointer of the value (`/query/n`, `/body/postings/1/amount`) and pydantic's message
    (the message names the rule, never the value sent)."""
    loc: tuple[object, ...] = tuple(e.get("loc", ()))
    path = "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in loc)
    return DomainIssue(path=path, message=str(e.get("msg", "")))


async def on_http_error(request: Request, exc: Exception) -> JSONResponse:
    """The router's own refusals: no route for this path (404) or method (405) is `ROUTE_NOT_FOUND`; any other HTTP
    exception is not something our code raises, so it is `INTERNAL`."""
    ctx = context_of(request)
    if isinstance(exc, HTTPException) and exc.status_code in (404, 405):
        return _response(_registry(ErrorCode.ROUTE_NOT_FOUND), ctx.request_id, ctx, None)
    return _internal(ctx, exc)


@dataclasses.dataclass(slots=True)
class _Progress:
    """Whether this request's answer has started (one per request, never shared)."""

    started: bool = False


class Edge:
    """ASGI middleware around every HTTP request: its id and container, the log binding, and the catch-all."""

    def __init__(self, app: AsgiApp, container_factory: ContainerFactory) -> None:
        self.app = app
        self.container_factory = container_factory

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            container = self.container_factory()
            ctx = RequestContext(request_id=container.ids.new_id(), container=container)
        except Exception:  # §6.3's catch-all: answered in the envelope, then re-raised for the server's log
            response = _response(_registry(ErrorCode.INTERNAL), UNASSIGNED, None, None)
            await response(scope, receive, send)
            raise
        scope.setdefault("state", {})[STATE_KEY] = ctx
        progress = _Progress()

        async def tracked(message: Message) -> None:
            if message["type"] == "http.response.start":
                progress.started = True
            await send(message)

        with contextlib.ExitStack() as binding:
            try:
                binding.enter_context(bound_context(correlation_id=ctx.request_id))
                await self.app(scope, receive, tracked)
            except Exception as exc:  # §6.3's catch-all: logged with the request id, answered as INTERNAL
                response = _internal(ctx, exc)
                if progress.started:  # too late for an envelope: the answer is under way
                    raise
                await response(scope, receive, send)
