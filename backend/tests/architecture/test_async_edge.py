"""EP-25: the async edge only awaits (L1-02; ADR-0011, ADR-0064; INV-20's "the event loop is never blocked"; S0103).

@regression-guard (S0103: `next` meets the rule; no route does blocking work yet; this pins it for the routes to come).

`loop_calls.walk` finds every call the event loop runs in the main sidecar: from every `async def` and the reviewed
sync callbacks of the loop thread (`ROOTS`), through every function of ours they call. A call it cannot resolve to
code of ours that it walks, to an awaited call or to a pure builtin is a **site**, and every site must be reviewed
here with its reason: why it does not block (in memory, or the reason it is accepted). Default-deny: a new call into
a reader, the QueryRunner, the engine job scheduler, a file or a lock from loop code fails until it is moved to a
worker thread (`await anyio.to_thread.run_sync(work, ...)`, ARCHITECTURE.md §2.5) or reviewed; a reviewed site the
walk no longer meets fails too, so the list stays the code's.

Ledger work never runs in this process (the `no-beancount` contract): it is an engine job, run by the scheduler on a
worker thread (ADR-0064). The latency half of INV-20 (`/health` under a busy sidecar) is BL-0114 and BL-0020.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.architecture.loop_calls import Walk, index, unindexed_async_defs, walk

pytestmark = pytest.mark.inv("INV-20")

ROOTS: dict[str, str] = {
    "app.main:StopRequest.__call__": (
        "the stop signals' handler: Python runs it on the main thread, which runs the event loop while uvicorn serves"
    ),
}
"""Sync code the loop thread runs that is not an `async def`."""

MEMORY = "in memory"
REVIEWED: dict[tuple[str, str], str] = {
    ("app.api.envelope:context_of", "request.scope.get"): "a dict read of the ASGI scope",
    ("app.api.envelope:context_of", "request.scope.get('state', {}).get"): "a dict read of the ASGI scope",
    ("app.api.errors:Edge.__call__", "self.container_factory"): (
        "the composition root's factory: `lambda: wiring.api` (app.main), returns the one container; a hosted build "
        "would look the caller's up in memory (ApiContainer)"
    ),
    ("app.api.errors:Edge.__call__", "container.ids.new_id"): "a uuid7 from memory (`Uuid7Ids`)",
    ("app.api.errors:Edge.__call__", "scope.setdefault"): "a dict write of the ASGI scope",
    ("app.api.errors:Edge.__call__", "contextlib.ExitStack"): MEMORY,
    ("app.api.errors:Edge.__call__", "binding.enter_context"): "enters `bound_context`: binds context variables",
    ("app.api.errors:_internal", "ctx.container.log"): (
        "one log line on an INTERNAL answer: `EventLog` formats it and appends it under the handler's lock to the "
        "local log file, then flushes it to the OS (never a sync); once per 10 MiB the handler rotates (renames up "
        "to 5 files, opens a new one) on whichever thread writes the line. Accepted on the loop as a small write of "
        "the log only (S0103 round 1 decision, verifier F3): the log is in the app's own local folder, and a thread "
        "for it could lose the line of a bug that ends the process"
    ),
    ("app.api.errors:_internal", "traceback.format_exception"): "formats an exception in memory",
    ("app.api.errors:_issue", "e.get"): "a dict read of pydantic's error",
    ("app.api.errors:_issue", "str(p).replace"): MEMORY,
    ("app.api.errors:_issue", "str(p).replace('~', '~0').replace"): MEMORY,
    ("app.api.errors:_response", "JSONResponse"): "Starlette renders the body to bytes in memory",
    ("app.api.errors:error_body", "dataclasses.asdict"): MEMORY,
    ("app.api.errors:error_body", "meta_of(ctx, request_id).model_dump"): MEMORY,
    ("app.api.errors:on_validation_error", "exc.errors"): "pydantic's list of errors, in memory",
    ("app.api.workspace:admit", "request.path_params.get"): "a dict read",
    ("app.api.workspace:admit", "ctx.container.workspaces.access"): (
        "the workspace gate: `WorkspaceAccess.access` must not do I/O or wait (its docstring; `NoWorkspaces` until "
        "BL-0102, whose registry gate reads memory)"
    ),
    ("app.domain.errors:DomainError.__init__", "super().__init__"): "Exception's constructor",
    ("app.main:ApiServer._end_requests_in_flight", "connection.transport.close"): (
        "asyncio's transport close schedules the close on the loop; it never waits"
    ),
    ("app.main:ApiServer.shutdown", "asyncio.ensure_future"): "schedules a task",
    ("app.main:ApiServer.shutdown", "super().shutdown"): "makes uvicorn's shutdown coroutine (awaited as a task)",
    ("app.main:ApiServer.shutdown", "ending.done"): "a task's state",
    ("app.platform.errors.registry:error", "_class_of(spec.error_class)"): (
        "builds the DomainError subclass `_class_of` returned (in memory)"
    ),
    ("app.platform.observability.log:bound_context", "structlog.contextvars.bound_contextvars"): (
        "binds context variables"
    ),
}


def problems(found: Walk, reviewed: dict[tuple[str, str], str]) -> list[str]:
    seen = {(s.fn, s.callee) for s in found.sites}
    out = [
        f"{s.fn} line {s.line}: {s.callee} runs on the event loop: move it to a worker thread or review it"
        for s in found.sites
        if (s.fn, s.callee) not in reviewed
    ]
    out.extend(f"stale review: {fn} {callee} is no longer met" for fn, callee in sorted(reviewed.keys() - seen))
    out.extend(f"{key}: an empty reason" for key, why in reviewed.items() if not why.strip())
    return sorted(set(out))


def test_every_call_the_event_loop_runs_is_awaited_ours_or_reviewed() -> None:
    found = walk(index(), tuple(ROOTS))
    assert problems(found, REVIEWED) == []


def test_the_walk_reaches_the_edge_and_every_route() -> None:
    """The walk is not vacuous: it reaches the middleware, the error handlers, the gate and the route handler."""
    found = walk(index(), tuple(ROOTS))
    keys = {f.key for f in found.reached}
    assert {
        "app.api.errors:Edge.__call__",
        "app.api.errors:on_domain_error",
        "app.api.errors:_internal",
        "app.api.workspace:admit",
        "app.api.health:health_router.health",
        "app.platform.errors.registry:error",
        "app.main:StopRequest._apply",
    } <= keys


def plant(tmp_path: Path, modules: dict[str, str]) -> Walk:
    root = tmp_path / "src"
    for name, text in modules.items():
        path = root.joinpath(*name.split(".")).with_suffix(".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        for parent in path.relative_to(root).parents:
            if parent != Path():
                (root / parent / "__init__.py").touch()
        path.write_text(text)
    return walk(index(root, "app"))


def site_texts(found: Walk) -> set[str]:
    return {s.callee for s in found.sites}


@pytest.mark.parametrize(
    ("modules", "expected"),
    [
        ({"app.h": "async def h():\n    open('x')\n"}, "open"),
        ({"app.h": "async def h(reader):\n    return reader.read()\n"}, "reader.read"),
        ({"app.h": "async def h(f, jobs):\n    await f(jobs.submit(1))\n"}, "jobs.submit"),
        ({"app.h": "def helper(t):\n    t.wait()\n\nasync def h(t):\n    helper(t)\n"}, "t.wait"),
        ({"app.h": "def helper(t):\n    t.wait()\n    return t\n\nasync def h(t):\n    await helper(t)\n"}, "t.wait"),
        (
            {
                "app.h": "import time\n\nclass C:\n    def m(self):\n        time.sleep(1)\n\n"
                "    async def h(self):\n        self.m()\n"
            },
            "time.sleep",
        ),
        (
            {
                "app.lib": "def run(q, sql):\n    return q.run(sql)\n",
                "app.h": "from app.lib import run\n\nasync def h(q):\n    return run(q, 'x')\n",
            },
            "q.run",
        ),
        (
            {
                "app.lib.__init__": "from app.lib.inner import run\n",
                "app.lib.inner": "def run(p):\n    return p.read_text()\n",
                "app.h": "from app.lib import run\n\nasync def h(p):\n    return run(p)\n",
            },
            "p.read_text",
        ),
        ({"app.h": "async def h(lock):\n    with lock:\n        pass\n"}, "with lock"),
        ({"app.h": "async def h(x):\n    return (lambda: x)()\n"}, "lambda: x"),
        (
            {
                "app.h": "class Store:\n    def __init__(self, p):\n        self.f = open(p)\n\n"
                "async def h(p):\n    Store(p)\n"
            },
            "open",
        ),
        ({"app.h": "def outer():\n    async def inner(c):\n        c.execute('x')\n    return inner\n"}, "c.execute"),
        ({"app.h": "async def h(x):\n    print(x)\n"}, "print"),
        # S0103 round 1 (verifier F1): callables a pure builtin calls, construction, `with`, and every block.
        ({"app.h": "async def h(ps):\n    return list(map(open, ps))\n"}, "open"),
        ({"app.h": "import os\n\nasync def h(ps):\n    return sorted(ps, key=os.stat)\n"}, "os.stat"),
        (
            {"app.h": "import sqlite3\n\nasync def h(ps):\n    return list(map(lambda p: sqlite3.connect(p), ps))\n"},
            "sqlite3.connect",
        ),
        (
            {"app.h": "import os.path\n\nasync def h(ps):\n    return list(filter(os.path.exists, ps))\n"},
            "os.path.exists",
        ),
        ({"app.h": "async def h(f):\n    return list(iter(f.read, ''))\n"}, "f.read"),
        (
            {
                "app.h": "from time import sleep\n\nclass C:\n    def sleep(self):\n        return 1\n\n"
                "    async def h(self):\n        sleep(5)\n"
            },
            "sleep",
        ),
        (
            {
                "app.h": "from dataclasses import dataclass, field\nimport subprocess\n\n"
                "def load():\n    return subprocess.run(['ls'])\n\n"
                "@dataclass\nclass S:\n    x: object = field(default_factory=load)\n\nasync def h():\n    S()\n"
            },
            "subprocess.run",
        ),
        (
            {
                "app.h": "from dataclasses import dataclass, field\n\n"
                "@dataclass\nclass S:\n    x: object = field(default_factory=lambda: open('x'))\n\n"
                "async def h():\n    S()\n"
            },
            "open",
        ),
        (
            {
                "app.h": "import time\n\nclass Lk:\n    def __enter__(self):\n        time.sleep(1)\n\n"
                "    def __exit__(self, *a):\n        pass\n\nasync def h():\n    with Lk():\n        pass\n"
            },
            "time.sleep",
        ),
        (
            {
                "app.h": "class Lk:\n    def __enter__(self):\n        return self\n\n"
                "    def __exit__(self, *a):\n        self.f.flush()\n\nasync def h():\n    with Lk():\n        pass\n"
            },
            "self.f.flush",
        ),
        ({"app.h": "try:\n    import foo\nexcept ImportError:\n    async def h():\n        open('x')\n"}, "open"),
        (
            {
                "app.h": "def make():\n    for _ in range(1):\n"
                "        async def h():\n            open('x')\n    return h\n"
            },
            "open",
        ),
        (
            {
                "app.h": "def make(x):\n    match x:\n        case 1:\n"
                "            async def h():\n                open('x')\n"
            },
            "open",
        ),
        (
            {
                "app.h": "from pydantic import BaseModel, field_validator\nimport time\n\nclass M(BaseModel):\n"
                "    x: int\n\n    @field_validator('x')\n    @classmethod\n    def v(cls, x):\n"
                "        time.sleep(1)\n        return x\n\nasync def h():\n    M(x=1)\n"
            },
            "time.sleep",
        ),
        (
            {
                "app.h": "class S:\n    def __post_init__(self):\n        self.f = open('x')\n\n"
                "async def h():\n    S()\n"
            },
            "open",
        ),
        (
            {
                "app.h": "class S:\n    def __new__(cls):\n        open('x')\n        return object.__new__(cls)\n\n"
                "async def h():\n    S()\n"
            },
            "open",
        ),
        (
            {
                "app.base": "class Base:\n    def __init__(self):\n        open('x')\n",
                "app.h": "from app.base import Base\n\nclass S(Base):\n    pass\n\nasync def h():\n    S()\n",
            },
            "open",
        ),
        (  # a nested helper is found before a module-level function of the same name (mutant L02-no-nested-scope)
            {
                "app.h": "def work():\n    return 1\n\n"
                "async def h(p):\n    def work():\n        return p.read_text()\n\n    work()\n"
            },
            "p.read_text",
        ),
    ],
)
def test_planted_blocking_calls_are_sites(tmp_path: Path, modules: dict[str, str], expected: str) -> None:
    found = plant(tmp_path, modules)
    assert expected in site_texts(found)
    assert problems(found, {}) != []


@pytest.mark.parametrize(
    "source",
    [
        "import anyio\n\ndef work(p):\n    return p.read_text()\n\n"
        "async def h(p):\n    return await anyio.to_thread.run_sync(work, p)\n",
        "async def h(p, send):\n    def work():\n        return p.read_text()\n    await send(work)\n",
        "async def h(xs):\n    return sorted(len(x) for x in xs if isinstance(x, str))\n",
        "async def h(xs):\n    if not xs:\n        raise ValueError('none')\n    return ', '.join(xs)\n",
        "async def h(lock):\n    async with lock:\n        pass\n",
    ],
)
def test_awaited_work_threads_and_pure_calls_pass(tmp_path: Path, source: str) -> None:
    assert plant(tmp_path, {"app.h": source}).sites == []


def test_a_reviewed_site_the_walk_no_longer_meets_is_stale(tmp_path: Path) -> None:
    found = plant(tmp_path, {"app.h": "async def h():\n    return 1\n"})
    assert problems(found, {("app.h:h", "reader.read"): "gone"}) == [
        "stale review: app.h:h reader.read is no longer met"
    ]


def test_a_shadowed_builtin_is_not_pure(tmp_path: Path) -> None:
    found = plant(
        tmp_path, {"app.h": "from app.io import sorted\n\nasync def h(x):\n    return sorted(x)\n", "app.io": ""}
    )
    assert site_texts(found) == {"sorted"}


def test_every_async_def_of_the_tree_is_a_root() -> None:
    """S0103 round 1 (verifier F1): an `async def` in a block the index does not enter, or two with one name, would
    never be walked."""
    assert unindexed_async_defs(index()) == []


def test_an_async_def_the_index_cannot_tell_apart_is_reported(tmp_path: Path) -> None:
    root = tmp_path / "src" / "app"
    root.mkdir(parents=True)
    (root / "__init__.py").touch()
    (root / "h.py").write_text(
        "if X:\n    async def h():\n        pass\nelse:\n    async def h():\n        open('x')\n"
    )
    assert unindexed_async_defs(index(tmp_path / "src", "app")) == ["app.h: 2 async defs, 1 indexed"]
