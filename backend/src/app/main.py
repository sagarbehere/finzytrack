"""The composition root and main sidecar entry (ARCHITECTURE.md §2.2 `main.py`, §5 the shell interface).

    python -m app.main --app-local-data DIR --app-cache DIR [--port N] [--workspace DIR] [--frozen-clock ISO]
                       [--engine-venv DIR] [--session-id ID]

It binds a free loopback port (`--port 0`), listening, and prints one handshake line,
`FT_READY {"port":…,"version":…}`, on stdout; a connection made right after it is queued until the server runs.
`--frozen-clock` (an instant with its UTC offset) is test-only: the acceptance harness freezes the backend's clock
through it (S0030; review:27 §1(d)), since the CLI is the one channel between a parent and the sidecar.

The API (`app.api.create_app`, S0046) is served by uvicorn on that socket (S0048, BL-0105), with the container the
composition root builds (`SidecarContainer`: the version, uuid7 ids, the `NoWorkspaces` gate until the registry
exists, BL-0102, and the structured log). uvicorn's own logging setup, access log, lifespan, proxy headers,
websockets and server header are off: library records reach the structured log, the composition root starts and
stops the engine itself, and nothing proxies a loopback app.

The ledger engine (ADR-0054; S0038) starts at boot on its own thread, in parallel with the bind and the handshake;
its outcome is a state (`app.platform.engine_boot`) and an event, never a crash, and the sidecar answers either way.
The engine is the packaged one beside this interpreter (`<prefix>/../engine/site-packages`, ADR-0056) unless
`--engine-venv` (development and tests) names a virtual environment that has it. Events go to the structured log,
`<app-local-data>/logs/finzytrack.jsonl` (`app.platform.observability`; ARCHITECTURE.md §6A), the engine's own
stderr lines among them, redacted; other libraries' records, warnings and uncaught exceptions too. Every line carries
the launch's `--session-id` (the shell's), or one made here. A log path that leads outside the app-local-data folder
or into the workspace is refused: the process exits with code 2 before the handshake.

**The stop** (failure-modes M7). SIGTERM and SIGINT (and SIGBREAK on Windows) have one handler for the process's
life, `StopRequest`, installed before the engine can start. It only records: the first signal ends the serve, which
waits for the requests in flight; a later one ends those requests (`ApiServer`). It never raises, so no signal at any
line can cut the way from the server's end to `engine.close()`, which every path (a stop, a server that fails, a
refused bind) reaches through one `finally`. uvicorn's own handlers are kept out (`ApiServer`): while it serves it
would replace the process's handlers, then put them back and raise each caught signal again.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import signal
import socket
import sys
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from types import FrameType
from typing import Protocol, override

import uvicorn
from fastapi import FastAPI

from app.api import ApiContainer, create_app
from app.domain.text import UntrustedText
from app.platform.adapters.clock import SystemClock
from app.platform.adapters.engine_client import EngineClient, EngineCommand, packaged_engine_site
from app.platform.adapters.ids import Uuid7Ids
from app.platform.clock import Clock, FrozenClock
from app.platform.engine_boot import EngineBoot
from app.platform.ids import Ids
from app.platform.observability import (
    EventLog,
    EventSink,
    LogContext,
    LogRefusal,
    Roots,
    log_file,
    new_session_id,
    valid_session_id,
)
from app.platform.observability.events import ENGINE_STDERR
from app.workspaces.access import NoWorkspaces, WorkspaceAccess

LOOPBACK = "127.0.0.1"
DISTRIBUTION = "finzytrack-backend"


@dataclass(frozen=True, slots=True)
class Args:
    app_local_data: Path
    app_cache: Path
    port: int
    workspace: Path | None
    frozen_clock: datetime | None
    engine_venv: Path | None
    session_id: str | None


@dataclass(frozen=True, slots=True)
class SidecarContainer:
    """The API's container (`app.api.ApiContainer`): one for every request on the desktop. Replaced by the
    `UserContainer` of M2 (BL-0102), which holds the registry and the open-workspace gate."""

    app_version: str
    ids: Ids
    workspaces: WorkspaceAccess
    log: EventSink


@dataclass(frozen=True, slots=True)
class Wiring:
    """What the composition root builds. Grows into `build_user_container` (L1-07)."""

    clock: Clock
    engine: EngineClient
    boot: EngineBoot
    api: ApiContainer


def _folder(text: str) -> Path:
    path = Path(text)
    if not path.is_dir():
        msg = f"{text} is not an existing folder"
        raise argparse.ArgumentTypeError(msg)
    return path


def _port(text: str) -> int:
    try:
        port = int(text)
    except ValueError:
        port = -1
    if not 0 <= port <= 65535:
        msg = f"{text!r} is not a port (0 to 65535; 0 lets the OS choose)"
        raise argparse.ArgumentTypeError(msg)
    return port


def _instant(text: str) -> datetime:
    try:
        at = datetime.fromisoformat(text)
    except ValueError:
        at = None
    if at is None or at.utcoffset() is None:
        msg = f"{text!r} is not an ISO instant with a UTC offset"
        raise argparse.ArgumentTypeError(msg)
    return at


def _session_id(text: str) -> str:
    if not valid_session_id(text):
        msg = f"{text!r} is not a session id (8 to 64 letters, digits or hyphens)"
        raise argparse.ArgumentTypeError(msg)
    return text


def parse_args(argv: list[str] | None) -> Args:
    parser = argparse.ArgumentParser(prog="app.main", description="Finzytrack main sidecar")
    parser.add_argument("--app-local-data", required=True, type=_folder)
    parser.add_argument("--app-cache", required=True, type=_folder)
    parser.add_argument("--port", type=_port, default=0)
    parser.add_argument("--workspace", type=_folder)
    parser.add_argument("--frozen-clock", type=_instant, help="test only: freeze the clock at this instant")
    parser.add_argument(
        "--engine-venv", type=_folder, help="development and tests: run the engine from this virtual environment"
    )
    parser.add_argument("--session-id", type=_session_id, help="this launch's id, from the shell (made here if absent)")
    ns = parser.parse_args(argv)
    return Args(ns.app_local_data, ns.app_cache, ns.port, ns.workspace, ns.frozen_clock, ns.engine_venv, ns.session_id)


def engine_command(args: Args) -> EngineCommand:
    """The venv engine when one is named, else the packaged engine beside this interpreter (ADR-0056 layout)."""
    if args.engine_venv is not None:
        return EngineCommand.for_venv(args.engine_venv)
    return EngineCommand.for_packaged(Path(sys.executable), packaged_engine_site(Path(sys.prefix)))


def wire(args: Args, log: EventSink, app_version: str) -> Wiring:
    """Build, never start: the engine starts in `main` (S0038)."""

    def engine_stderr(line: str) -> None:
        log(ENGINE_STDERR, line=UntrustedText(line))  # the engine's stderr can carry ledger text (BL-0088)

    engine = EngineClient(engine_command(args), on_stderr=engine_stderr, log=log)  # it logs its lifecycle (S0040)
    return Wiring(
        clock=FrozenClock(args.frozen_clock) if args.frozen_clock else SystemClock(),
        engine=engine,
        boot=EngineBoot(engine, pid=lambda: engine.pid),
        # TODO(BL-0102): the registry's gate replaces NoWorkspaces (every key is WORKSPACE_UNKNOWN until then).
        api=SidecarContainer(app_version=app_version, ids=Uuid7Ids(), workspaces=NoWorkspaces(), log=log),
    )


def handshake_line(port: int, ver: str) -> str:
    return "FT_READY " + json.dumps({"port": port, "version": ver}, separators=(",", ":"))


def open_log(args: Args, app_version: str) -> EventLog | LogRefusal:
    """The structured log, before anything logs. Its timestamps are real time, even under `--frozen-clock`."""
    path = log_file(args.app_local_data, args.workspace)
    if isinstance(path, LogRefusal):
        return path
    context = LogContext(session_id=args.session_id or new_session_id(), app_version=app_version)
    roots = Roots(args.app_local_data.resolve(), args.app_cache.resolve())
    try:
        return EventLog(path, context=context, clock=SystemClock(), roots=roots)
    except OSError:
        return LogRefusal("the log file cannot be opened")


def bind(port: int) -> socket.socket:
    """The loopback socket, bound and listening (`SO_REUSEADDR` on POSIX only, as the standard library decides: on
    Windows it would let another program take a port in use)."""
    return socket.create_server((LOOPBACK, port))


def bound_address(sock: socket.socket) -> tuple[str, int]:
    host, port = sock.getsockname()[:2]
    return str(host), int(port)


def stop_signals() -> tuple[signal.Signals, ...]:
    """The signals that stop the sidecar: the shell's quit and the harness's stop (SIGTERM), Ctrl-C (SIGINT) and, on
    Windows, Ctrl-Break: the ones uvicorn would handle itself."""
    if sys.platform == "win32":
        return (signal.SIGTERM, signal.SIGINT, signal.SIGBREAK)
    return (signal.SIGTERM, signal.SIGINT)


class Stoppable(Protocol):
    """The two flags of uvicorn's `Server` that end its serve: `should_exit` ends the loop and starts the graceful
    shutdown (it waits for requests in flight), `force_exit` ends that wait."""

    should_exit: bool
    force_exit: bool


class StopRequest:
    """The one handler of the stop signals for the process's life (failure-modes M7). It only records, and never
    raises: a signal handled at any line of the start or the stop cannot cut the way to `engine.close()` (S0038's
    handler raised `SystemExit`, so a signal at the line that switched it off skipped the close). The first request
    ends the serve, which waits for the requests in flight; any later one also ends those requests (`ApiServer`). A
    request made before the server exists is kept and applied when it is attached. Python runs signal handlers on
    the main thread only, between two of its steps, and `attach` runs there too, so the two never interleave inside
    a step."""

    def __init__(self) -> None:
        self._count = 0
        self._server: Stoppable | None = None

    @property
    def requested(self) -> bool:
        return self._count > 0

    def __call__(self, signum: int, frame: FrameType | None) -> None:
        self._count += 1
        self._apply()

    def attach(self, server: Stoppable) -> None:
        self._server = server
        self._apply()

    def _apply(self) -> None:
        server = self._server
        if server is None or self._count == 0:
            return
        server.should_exit = True
        if self._count > 1:
            # TODO(BL-0109): the quit of engine-jobs §9.3 (quiesce, Quit anyway) replaces this second-signal rule.
            server.force_exit = True


def install_stop_handler(stop: StopRequest) -> None:
    for signum in stop_signals():
        signal.signal(signum, stop)


class ApiServer(uvicorn.Server):
    """uvicorn's server, with two changes for the stop (failure-modes M7):

    - no signal handling of its own: the composition root's `StopRequest` keeps the signals. uvicorn's
      `capture_signals` would replace the handlers while it serves, then put the old ones back and raise each caught
      signal again (`tests/test_main.py` checks that the handlers stay the composition root's);
    - once `force_exit` is set (a second stop), the requests still in flight are ended: their connections are
      closed. uvicorn's `force_exit` only stops its own waiting, and asyncio's
      `Server.wait_closed` then waits for every open connection, so a handler that never returned would hold the
      process for ever, short of `engine.close()`. The check repeats every 0.1 s, uvicorn's own shutdown tick."""

    @override
    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None]:
        yield

    @override
    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        ending = asyncio.ensure_future(super().shutdown(sockets=sockets))
        while not ending.done():
            if self.force_exit:
                self._end_requests_in_flight()
            await asyncio.wait({ending}, timeout=0.1)
        await ending  # its own exception, if any

    def _end_requests_in_flight(self) -> None:
        """Close every open connection: `wait_closed` waits for them, nothing else. A handler still running is
        cancelled with the loop's other tasks when `asyncio.run` ends."""
        for connection in list(self.server_state.connections):
            connection.transport.close()


def server_config(app: FastAPI, sock: socket.socket) -> uvicorn.Config:
    """uvicorn on our socket; what the sidecar does itself is off. No time limit on the graceful shutdown: it waits
    for requests in flight, and a second stop signal ends them (`ApiServer`; no fixed budget, PROCESS.md §6)."""
    host, port = bound_address(sock)
    return uvicorn.Config(
        app,
        host=host,
        port=port,
        loop="asyncio",
        http="h11",
        ws="none",
        lifespan="off",
        interface="asgi3",
        log_config=None,
        access_log=False,
        proxy_headers=False,
        server_header=False,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    app_version = version(DISTRIBUTION)
    log = open_log(args, app_version)
    if isinstance(log, LogRefusal):
        sys.stderr.write(f"app.main: refused: {log.reason}\n")
        return 2
    # Kept until the process ends: an exception that leaves `main` is logged by the log's hook, never printed. The
    # handler is closed by logging's own shutdown at exit; every line is already flushed.
    log.capture_process()
    stop = StopRequest()
    install_stop_handler(stop)  # before the engine can start, so every later stop reaches the close below
    wiring = wire(args, log, app_version)  # nothing reads the clock yet; the first use case receives it from here
    try:
        wiring.boot.start_in_background()
        with bind(args.port) as sock:
            config = server_config(create_app(lambda: wiring.api), sock)
            config.load()  # a configuration uvicorn refuses fails here, before the handshake
            server = ApiServer(config)
            stop.attach(server)
            sys.stdout.write(handshake_line(bound_address(sock)[1], app_version) + "\n")
            sys.stdout.flush()
            server.run(sockets=[sock])
    finally:
        # Bounded today (RestartPolicy.longest_close after a call in flight). TODO(BL-0109): close() signals the
        # engine's group once and returns without waiting for the reap (engine-jobs §5.5, §9.3).
        wiring.engine.close()  # logs engine.closed
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
