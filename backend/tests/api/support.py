"""Fakes and test-only routes for the API skeleton's tests (S0046).

The product has no workspace route yet, so these tests mount their own: a probe under `/api/w/{ws}/` that records
every call that reaches it, and app-scoped routes that fail on purpose. The container is the edge's port
(`app.api.ApiContainer`); each fake records what the edge asked of it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api import create_app
from app.api.envelope import Envelope, ok
from app.api.health import health_router
from app.api.routes import declare
from app.api.workspace import open_workspace, workspace_router
from app.platform.errors import ErrorCode, error
from app.platform.observability import Event
from app.workspaces.access import Access, WorkspaceKey

VERSION = "9.9.9-test"


class SequentialIds:
    """Request ids `req-1`, `req-2`, … (EP-10: ids are injected)."""

    def __init__(self) -> None:
        self._next = iter(range(1, 1_000_000))

    def new_id(self) -> str:
        return f"req-{next(self._next)}"


class FixedAccess:
    """Answers every key with one outcome, and records the keys it was asked for."""

    def __init__(self, outcome: Access) -> None:
        self.outcome = outcome
        self.asked: list[str] = []

    def access(self, key: str) -> Access:
        self.asked.append(key)
        return self.outcome


@dataclass
class RecordingLog:
    lines: list[tuple[Event, dict[str, object]]] = field(default_factory=lambda: [])

    def __call__(self, event: Event, /, **fields: object) -> None:
        self.lines.append((event, fields))


@dataclass
class FakeContainer:
    workspaces: FixedAccess
    app_version: str = VERSION
    ids: SequentialIds = field(default_factory=SequentialIds)
    log: RecordingLog = field(default_factory=RecordingLog)


class Probe(BaseModel):
    ws: str


class Counted(BaseModel):
    n: int


def probe_router(calls: list[str]) -> APIRouter:
    """`GET /api/w/{ws}/probe` and `POST /api/w/{ws}/save`: each records that it ran."""
    router = workspace_router()

    @router.get("/probe", openapi_extra=declare("ledger"))
    async def probe(request: Request, ws: Annotated[WorkspaceKey, Depends(open_workspace)]) -> Envelope[Probe]:
        calls.append(f"probe {ws}")
        return ok(request, Probe(ws=ws))

    @router.post("/save", openapi_extra=declare("ledger", writes="ledger"))
    async def save(request: Request, ws: Annotated[WorkspaceKey, Depends(open_workspace)]) -> Envelope[Probe]:
        calls.append(f"save {ws}")
        return ok(request, Probe(ws=ws))

    @router.post("/plain", openapi_extra=declare("ledger", writes="ledger"))
    async def plain(request: Request) -> Envelope[Counted]:
        """Takes no workspace parameter: the router's gate must still run."""
        calls.append("plain")
        return ok(request, Counted(n=1))

    return router


class Boom(RuntimeError):
    pass


LEDGER_TEXT = "Expenses:Groceries 12.30 USD"


def failing_router(raise_this: Callable[[], BaseException] | None = None) -> APIRouter:
    """App-scoped routes that fail on purpose: an unexpected exception, a registry error, a validation error."""
    router = APIRouter(prefix="/api/test")

    @router.get("/boom", openapi_extra=declare("app"))
    async def boom() -> Envelope[Counted]:
        raise (raise_this or (lambda: Boom(LEDGER_TEXT)))()

    @router.get("/count", openapi_extra=declare("app"))
    async def count(request: Request, n: Annotated[int, Query()]) -> Envelope[Counted]:
        return ok(request, Counted(n=n))

    @router.get("/stream", openapi_extra=declare("app"), response_class=StreamingResponse)
    async def stream() -> StreamingResponse:
        async def chunks() -> AsyncIterator[bytes]:
            yield b"partial answer"
            raise Boom(LEDGER_TEXT)

        return StreamingResponse(chunks())

    @router.get("/refuse/{code}", openapi_extra=declare("app"))
    async def refuse(code: ErrorCode) -> Envelope[Counted]:
        raise error(code)

    return router


def make_app(
    outcome: Access, *, routers: Sequence[APIRouter] | None = None, calls: list[str] | None = None
) -> tuple[FastAPI, FakeContainer]:
    container = FakeContainer(FixedAccess(outcome))
    chosen = routers if routers is not None else (health_router(), probe_router(calls if calls is not None else []))
    return create_app(lambda: container, routers=chosen), container


class Client:
    """A synchronous client over the app in process (httpx's ASGI transport; no server, no socket).

    By default an exception that leaves the app reaches the test: the edge itself answers every failure, so one that
    escapes is a bug in the edge, or (the container factory failing) the edge re-raising after its answer."""

    def __init__(self, app: FastAPI, *, raise_app_exceptions: bool = True, cookies: dict[str, str] | None = None):
        self.app = app
        self.raise_app_exceptions = raise_app_exceptions
        self.cookies = cookies

    def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        async def go() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=self.raise_app_exceptions)
            async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=self.cookies) as c:
                return await c.request(method, url, **kw)

        return asyncio.run(go())

    def get(self, url: str, **kw: Any) -> httpx.Response:
        return self.request("GET", url, **kw)


def client(app: FastAPI, **kw: Any) -> Client:
    return Client(app, **kw)


def body(response: httpx.Response) -> dict[str, Any]:
    return json.loads(response.text)
