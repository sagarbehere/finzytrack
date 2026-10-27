"""One envelope for every answer, and errors translated once at the edge (S0046).

Spec: ADR-0038 ("one envelope for every endpoint": `{data, meta}` or `{error, meta}`), ARCHITECTURE.md §4.1 (the
envelope's fields), §6.1 (class -> HTTP status), §6.2 (translation once, at the edge, from the registry entry), §6.3
(an unexpected exception becomes `INTERNAL`, logged with the correlation id; nothing swallowed), §6.5 (the message is
the registry's plain text, never an exception's), §6.6 (every error carries the correlation id of its log lines),
§6.7 (every registry code has a test tagged with it, through the API). Every body is also checked against the schema
the contract declares for it, so the generated TypeScript types describe what the server sends.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from jsonschema import Draft202012Validator

from app.api import create_app, export_openapi
from app.api.errors import status_for
from app.api.health import health_router
from app.domain.errors import Conflict, DomainError, ErrorClass, NotFound
from app.platform.errors import ErrorCode, WorkspaceNotReadyDetails, spec_for
from app.platform.observability.events import API_INTERNAL_ERROR
from app.workspaces.access import Unknown
from tests.api.support import (
    LEDGER_TEXT,
    VERSION,
    Boom,
    FakeContainer,
    FixedAccess,
    body,
    client,
    failing_router,
    make_app,
)


def _app(raise_this: Any = None) -> tuple[FastAPI, Any]:
    return make_app(Unknown(), routers=(health_router(), failing_router(raise_this)))


def _conforms(app: FastAPI, path: str, method: str, status: int, payload: dict[str, Any]) -> None:
    """The body validates against the schema the app's own contract declares for this answer."""
    doc = app.openapi()
    responses = doc["paths"][path][method]["responses"]
    declared = responses.get(str(status), responses["default"])
    schema = declared["content"]["application/json"]["schema"]
    Draft202012Validator({**schema, "components": doc["components"]}).validate(payload)  # pyright: ignore[reportUnknownMemberType] (jsonschema's stubs)


def _error_schema_conforms(payload: dict[str, Any]) -> None:
    doc = export_openapi()
    validator = Draft202012Validator({"$ref": "#/components/schemas/ErrorEnvelope", "components": doc["components"]})
    validator.validate(payload)  # pyright: ignore[reportUnknownMemberType] (jsonschema's stubs)


def test_health_answers_in_the_envelope_with_the_request_id() -> None:
    app, _ = _app()
    r = client(app).get("/health")
    assert r.status_code == 200
    assert body(r) == {
        "data": {"status": "ok", "version": VERSION},
        "meta": {"request_id": "req-1", "workspace_id": None, "ledger_version": None, "config_version": None},
    }
    _conforms(app, "/health", "get", 200, body(r))


def test_each_request_gets_its_own_request_id() -> None:
    app, _ = _app()
    c = client(app)
    assert [body(c.get("/health"))["meta"]["request_id"] for _ in range(3)] == ["req-1", "req-2", "req-3"]


@pytest.mark.error_code("ROUTE_NOT_FOUND")
@pytest.mark.parametrize(("method", "path"), [("get", "/nope"), ("get", "/api/w"), ("post", "/health")])
def test_an_unknown_route_or_method_is_route_not_found_in_the_envelope(method: str, path: str) -> None:
    app, _ = _app()
    r = client(app).request(method.upper(), path)
    assert r.status_code == 404
    payload = body(r)
    assert payload["error"]["code"] == "ROUTE_NOT_FOUND"
    assert payload["error"]["class"] == "not_found"
    assert payload["error"]["correlation_id"] == payload["meta"]["request_id"] == "req-1"
    _error_schema_conforms(payload)


@pytest.mark.error_code("VALIDATION_FAILED")
@pytest.mark.parametrize("query", ["?n=abc", ""])
def test_a_request_that_fails_validation_is_validation_failed_with_located_issues(query: str) -> None:
    app, _ = _app()
    r = client(app).get(f"/api/test/count{query}")
    assert r.status_code == 422
    payload = body(r)
    assert payload["error"]["code"] == "VALIDATION_FAILED"
    assert payload["error"]["class"] == "invalid"
    assert [i["path"] for i in payload["error"]["issues"]] == ["/query/n"]
    assert all(i["message"] for i in payload["error"]["issues"])
    _conforms(app, "/api/test/count", "get", 422, payload)


def test_a_valid_request_is_served() -> None:
    app, _ = _app()
    r = client(app).get("/api/test/count?n=3")
    assert r.status_code == 200
    assert body(r)["data"] == {"n": 3}


@pytest.mark.error_code("INTERNAL")
def test_an_unexpected_exception_is_internal_logged_once_and_never_leaks_its_text() -> None:
    app, container = _app()
    r = client(app).get("/api/test/boom")
    assert r.status_code == 500
    payload = body(r)
    internal = spec_for(ErrorCode.INTERNAL)
    assert internal is not None
    assert payload["error"]["code"] == "INTERNAL"
    assert payload["error"]["class"] == "internal"
    assert payload["error"]["message"] == internal.message
    assert payload["error"]["correlation_id"] == payload["meta"]["request_id"] == "req-1"
    assert LEDGER_TEXT not in r.text
    assert "Boom" not in r.text
    assert [event for event, _ in container.log.lines] == [API_INTERNAL_ERROR]
    _conforms(app, "/api/test/boom", "get", 500, payload)


def test_the_internal_error_log_line_carries_the_exception_only_as_debug_detail() -> None:
    app, container = _app()
    client(app).get("/api/test/boom")
    [(_, fields)] = container.log.lines
    assert fields["exception_type"] == "Boom"
    kinds = {f.name: f.kind for f in API_INTERNAL_ERROR.fields}
    assert kinds["exception"] == "debug_detail"  # free text that may hold ledger data (§6A.3)


def test_an_exception_after_the_answer_started_is_logged_and_re_raised_not_answered_twice() -> None:
    """Too late for an envelope: the status line is sent. The edge logs it with the request id and lets it go."""
    app, container = _app()
    with pytest.raises(Boom):
        client(app).get("/api/test/stream")
    assert [event for event, _ in container.log.lines] == [API_INTERNAL_ERROR]


@pytest.mark.parametrize(
    ("code", "status", "error_class"),
    [
        ("WORKSPACE_UNKNOWN", 404, "not_found"),
        ("ROUTE_NOT_FOUND", 404, "not_found"),
        ("VALIDATION_FAILED", 422, "invalid"),
        ("INTERNAL", 500, "internal"),
    ],
)
def test_a_domain_error_takes_its_status_and_class_from_the_registry(code: str, status: int, error_class: str) -> None:
    app, _ = _app()
    r = client(app).get(f"/api/test/refuse/{code}")
    assert r.status_code == status
    assert body(r)["error"]["class"] == error_class
    assert body(r)["error"]["code"] == code


@pytest.mark.parametrize(
    ("error_class", "status"),
    [
        (ErrorClass.INVALID, 422),
        (ErrorClass.NOT_FOUND, 404),
        (ErrorClass.CONFLICT, 409),
        (ErrorClass.FORBIDDEN, 403),
        (ErrorClass.UNAVAILABLE, 503),
        (ErrorClass.INTERNAL, 500),
    ],
)
def test_the_status_of_each_class_is_the_table_of_architecture_6_1(error_class: ErrorClass, status: int) -> None:
    assert status_for(error_class) == status


def test_every_error_class_has_a_status() -> None:
    assert {c: status_for(c) for c in ErrorClass}.keys() == set(ErrorClass)


@pytest.mark.parametrize(
    "raised",
    [
        pytest.param(lambda: NotFound("NO_SUCH_CODE"), id="a code the registry does not know"),
        pytest.param(lambda: Conflict("WORKSPACE_NOT_OPEN", details=None), id="a code whose details are missing"),
        pytest.param(
            lambda: Conflict("WORKSPACE_NOT_OPEN", details=WorkspaceNotReadyDetails(phase="WARMING")),
            id="details of another code",
        ),
        pytest.param(lambda: NotFound("WORKSPACE_NOT_OPEN"), id="a class the registry does not give the code"),
        pytest.param(
            lambda: NotFound("WORKSPACE_UNKNOWN", details=WorkspaceNotReadyDetails(phase="WARMING")),
            id="details on a code that has none",
        ),
        pytest.param(lambda: DomainError("WORKSPACE_UNKNOWN"), id="the bare base class"),
    ],
)
def test_a_malformed_domain_error_fails_closed_as_internal(raised: Any) -> None:
    app, container = _app(raised)
    r = client(app).get("/api/test/boom")
    assert r.status_code == 500
    assert body(r)["error"]["code"] == "INTERNAL"
    assert [event for event, _ in container.log.lines] == [API_INTERNAL_ERROR]


def test_a_well_formed_domain_error_is_not_logged_as_internal() -> None:
    app, container = _app(lambda: NotFound("WORKSPACE_UNKNOWN"))
    r = client(app).get("/api/test/boom")
    assert r.status_code == 404
    assert container.log.lines == []


def test_the_container_is_asked_for_each_request() -> None:
    built: list[FakeContainer] = []

    def factory() -> FakeContainer:
        built.append(FakeContainer(FixedAccess(Unknown())))
        return built[-1]

    c = client(create_app(factory, routers=(health_router(),)))
    c.get("/health")
    c.get("/health")
    assert len(built) == 2


def test_a_container_factory_that_fails_is_internal_and_is_not_swallowed() -> None:
    """Answered in the envelope, then re-raised so the server logs it: nothing else could log it (§6.3)."""

    def factory() -> Any:
        raise RuntimeError(LEDGER_TEXT)

    app = create_app(factory, routers=(health_router(),))
    r = client(app, raise_app_exceptions=False).get("/health")
    assert r.status_code == 500
    assert body(r)["error"]["code"] == "INTERNAL"
    assert body(r)["meta"]["request_id"] == "unassigned"
    assert LEDGER_TEXT not in r.text
    with pytest.raises(RuntimeError):
        client(app).get("/health")


def test_fastapi_telemetry_is_off_whatever_the_environment_says(monkeypatch: pytest.MonkeyPatch) -> None:
    """No data leaves except where the user asked (ARCHITECTURE.md §6A; security posture): FastAPI's built-in
    OpenTelemetry records request bodies and stack traces, and its environment switch would add OTLP exporters."""
    monkeypatch.setenv("FASTAPI_OTEL_AUTO_CONFIGURE", "true")
    app, _ = _app()
    config = app._telemetry  # pyright: ignore[reportPrivateUsage] (FastAPI keeps the resolved config private)
    off = ("tracing", "metrics", "logs", "operation_spans", "auto_configure")
    assert {k: config.get(k) for k in off} == dict.fromkeys(off, False)
