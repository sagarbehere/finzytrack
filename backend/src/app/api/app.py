"""The app factory (ARCHITECTURE.md §2.3: `create_app(container_factory) -> FastAPI`; ADR-0038).

Builds the FastAPI app around the given routers (the product's by default): the edge middleware, the error handlers,
the error envelope as every operation's `default` answer, and the route-scope check, which refuses a misdeclared
route here. It does no I/O and serves nothing; serving it from `app.main` is BL-0105. FastAPI's own documentation
pages and `/openapi.json` route are off: the contract is exported offline (`app.api.contract`).
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.metadata import version

from fastapi import APIRouter, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.telemetry import TelemetryConfig
from starlette.exceptions import HTTPException

from app.api.container import ContainerFactory
from app.api.errors import Edge, error_envelope_model, on_domain_error, on_http_error, on_validation_error
from app.api.routes import RouteScopeError, product_routers, route_scope_problems
from app.domain.errors import DomainError

DISTRIBUTION = "finzytrack-backend"


def no_telemetry() -> TelemetryConfig:
    """FastAPI's built-in OpenTelemetry (since 0.13x) records request bodies, exception messages and stack traces, and
    `FASTAPI_OTEL_AUTO_CONFIGURE=true` in the environment would add OTLP exporters. Finzytrack sends no data anywhere
    the user did not ask for, and logs only through its own redacted log (ARCHITECTURE.md §6A), so all of it is off
    explicitly, whatever the environment says (S0046)."""
    return {"tracing": False, "metrics": False, "logs": False, "operation_spans": False, "auto_configure": False}


def create_app(container_factory: ContainerFactory, routers: Sequence[APIRouter] | None = None) -> FastAPI:
    app = FastAPI(
        title="Finzytrack",
        version=version(DISTRIBUTION),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        separate_input_output_schemas=False,  # one schema per model (ADR-0038 amendment)
        telemetry=no_telemetry(),
    )
    error_envelope = error_envelope_model()
    default = {"model": error_envelope, "description": "An error, in the envelope (ARCHITECTURE.md §6)."}
    for router in product_routers() if routers is None else routers:
        app.include_router(router, responses={"default": default})
    app.add_exception_handler(DomainError, on_domain_error)
    app.add_exception_handler(RequestValidationError, on_validation_error)
    app.add_exception_handler(HTTPException, on_http_error)
    app.add_middleware(Edge, container_factory=container_factory)
    problems = route_scope_problems(app)
    if problems:
        raise RouteScopeError("\n".join(problems))
    return app
