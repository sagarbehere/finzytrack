"""Workspace-addressed requests: `{ws}` resolves only in the caller's container; only the open one is served (S0046).

Spec: INV-28 ("every workspace-scoped request names its workspace in the path, and is served only when that
workspace is the one open … a request naming any other registered workspace is refused with `WORKSPACE_NOT_OPEN`
and changes nothing. No request finds its target from server-side state, and a request, response or write can never
act on a workspace other than the one named. `{ws}` resolves only in the caller's registry"), ADR-0018 (unknown ->
`404 WORKSPACE_UNKNOWN`; not ready -> `409 WORKSPACE_NOT_READY {phase}`; the response's `meta.workspace_id`),
ADR-0063 (registered but not open, also while a switch is closing it -> `409 WORKSPACE_NOT_OPEN {open_workspace,
switching}`, changes nothing, never redirected). INV-28's planned "middleware test", for the skeleton: the real gate
(the registry and the open-workspace record) arrives with BL-0102 in M2; here the container's port answers.
"""

from __future__ import annotations

from typing import Any, cast

import httpx
import pytest

from app.api import create_app
from app.api.health import health_router
from app.platform.errors import NotReadyPhase
from app.workspaces.access import (
    Access,
    NotOpen,
    NotReady,
    NoWorkspaces,
    Open,
    Unknown,
    WorkspaceKey,
)
from tests.api.support import FakeContainer, FixedAccess, body, client, make_app, probe_router

PERSONAL = WorkspaceKey("personal")
BUSINESS = WorkspaceKey("business")


def _ask(
    outcome: Access, method: str = "GET", path: str = "/api/w/personal/probe", **kw: Any
) -> tuple[httpx.Response, list[str], FakeContainer]:
    calls: list[str] = []
    app, container = make_app(outcome, calls=calls)
    cookies = kw.pop("cookies", None)
    return client(app, cookies=cookies).request(method, path, **kw), calls, container


@pytest.mark.inv("INV-28")
def test_the_open_workspace_named_in_the_path_is_served() -> None:
    r, calls, container = _ask(Open(PERSONAL))
    assert r.status_code == 200
    assert body(r)["data"] == {"ws": "personal"}
    assert body(r)["meta"]["workspace_id"] == "personal"
    assert calls == ["probe personal"]
    assert container.workspaces.asked == ["personal"]


@pytest.mark.inv("INV-28")
@pytest.mark.error_code("WORKSPACE_UNKNOWN")
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_an_unknown_workspace_is_404_and_reaches_no_handler(method: str) -> None:
    path = "/api/w/personal/probe" if method == "GET" else "/api/w/personal/save"
    r, calls, _ = _ask(Unknown(), method, path)
    assert r.status_code == 404
    error = body(r)["error"]
    assert (error["code"], error["class"], error["details"]) == ("WORKSPACE_UNKNOWN", "not_found", None)
    assert calls == []


@pytest.mark.inv("INV-28")
@pytest.mark.error_code("WORKSPACE_NOT_OPEN")
@pytest.mark.parametrize(
    ("outcome", "details"),
    [
        (NotOpen(open_workspace=BUSINESS, switching=False), {"open_workspace": "business", "switching": False}),
        (NotOpen(open_workspace=BUSINESS, switching=True), {"open_workspace": "business", "switching": True}),
        (NotOpen(open_workspace=None, switching=True), {"open_workspace": None, "switching": True}),
        (NotOpen(open_workspace=None, switching=False), {"open_workspace": None, "switching": False}),
    ],
)
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_a_registered_workspace_that_is_not_open_is_409_and_changes_nothing(
    outcome: NotOpen, details: dict[str, object], method: str
) -> None:
    path = "/api/w/personal/probe" if method == "GET" else "/api/w/personal/save"
    r, calls, _ = _ask(outcome, method, path)
    assert r.status_code == 409
    error = body(r)["error"]
    assert (error["code"], error["class"], error["details"]) == ("WORKSPACE_NOT_OPEN", "conflict", details)
    assert calls == []  # never served, never redirected to the open workspace


@pytest.mark.inv("INV-28")
@pytest.mark.error_code("WORKSPACE_NOT_READY")
@pytest.mark.parametrize("phase", ["WARMING", "RECOVERING"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_the_open_workspace_while_it_is_not_ready_is_409_with_its_phase(phase: str, method: str) -> None:
    path = "/api/w/personal/probe" if method == "GET" else "/api/w/personal/save"
    r, calls, _ = _ask(NotReady(phase=cast("NotReadyPhase", phase)), method, path)
    assert r.status_code == 409
    error = body(r)["error"]
    assert (error["code"], error["class"], error["details"]) == ("WORKSPACE_NOT_READY", "conflict", {"phase": phase})
    assert error["retryable"] is True
    assert calls == []


@pytest.mark.inv("INV-28")
@pytest.mark.parametrize("outcome", [Unknown(), NotOpen(open_workspace=BUSINESS, switching=False), NotReady("WARMING")])
def test_a_refusal_names_no_workspace_in_its_meta(outcome: Access) -> None:
    r, _, _ = _ask(outcome)
    assert body(r)["meta"]["workspace_id"] is None


@pytest.mark.inv("INV-28")
def test_only_the_key_in_the_path_is_asked_for() -> None:
    """No other part of the request can choose the workspace: a header, a query or a cookie naming another one."""
    r, calls, container = _ask(
        Open(PERSONAL),
        path="/api/w/personal/probe?ws=business&workspace=business",
        headers={"X-Workspace": "business", "X-Ft-Workspace": "business"},
        cookies={"ws": "business"},
    )
    assert r.status_code == 200
    assert container.workspaces.asked == ["personal"]
    assert calls == ["probe personal"]


@pytest.mark.inv("INV-28")
def test_an_answer_for_another_workspace_is_never_served() -> None:
    """A gate that answers "open" for a key other than the one named is a bug; the edge refuses, fails closed."""
    r, calls, container = _ask(Open(BUSINESS))
    assert r.status_code == 500
    assert body(r)["error"]["code"] == "INTERNAL"
    assert body(r)["meta"]["workspace_id"] is None
    assert calls == []
    assert len(container.log.lines) == 1


@pytest.mark.inv("INV-28")
@pytest.mark.parametrize("key", ["%E2%9C%93", "a%20b", "...", "PERSONAL", "x" * 300])
def test_an_odd_key_is_only_ever_looked_up(key: str) -> None:
    """A key is looked up as given; whatever it looks like, it is unknown or served, never a validation error."""
    r, calls, container = _ask(Unknown(), path=f"/api/w/{key}/probe")
    assert r.status_code == 404
    assert body(r)["error"]["code"] == "WORKSPACE_UNKNOWN"
    assert len(container.workspaces.asked) == 1
    assert calls == []


@pytest.mark.inv("INV-28")
def test_each_request_resolves_in_its_own_container() -> None:
    """`{ws}` resolves only in the caller's registry: the container the factory gives this request (ADR-0041)."""
    first, second = FakeContainer(FixedAccess(Open(PERSONAL))), FakeContainer(FixedAccess(Unknown()))
    order = iter([first, second])
    calls: list[str] = []
    c = client(create_app(lambda: next(order), routers=(health_router(), probe_router(calls))))
    assert c.get("/api/w/personal/probe").status_code == 200
    assert c.get("/api/w/personal/probe").status_code == 404
    assert (first.workspaces.asked, second.workspaces.asked) == (["personal"], ["personal"])
    assert calls == ["probe personal"]


@pytest.mark.inv("INV-28")
def test_no_workspace_is_known_until_the_registry_exists() -> None:
    """The product's only gate before M2 (BL-0102): no workspace is registered, so every key is unknown."""
    assert NoWorkspaces().access("personal") == Unknown()
    assert NoWorkspaces().access("") == Unknown()


@pytest.mark.inv("INV-28")
@pytest.mark.parametrize("outcome", [Unknown(), NotOpen(open_workspace=BUSINESS, switching=False), NotReady("WARMING")])
def test_a_workspace_route_that_takes_no_workspace_parameter_is_still_gated(outcome: Access) -> None:
    """The router runs the gate for every route under it, not only for handlers that ask for the key."""
    r, calls, _ = _ask(outcome, "POST", "/api/w/personal/plain")
    assert r.status_code in (404, 409)
    assert calls == []


@pytest.mark.inv("INV-28")
def test_a_workspace_route_that_takes_no_workspace_parameter_is_served_when_open() -> None:
    r, calls, _ = _ask(Open(PERSONAL), "POST", "/api/w/personal/plain")
    assert r.status_code == 200
    assert body(r)["meta"]["workspace_id"] == "personal"
    assert calls == ["plain"]
