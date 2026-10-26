"""The composition root and main sidecar entry (ARCHITECTURE.md §2.2 `main.py`, §5 the shell interface).

    python -m app.main --app-local-data DIR --app-cache DIR [--port N] [--workspace DIR] [--frozen-clock ISO]
                       [--engine-venv DIR] [--session-id ID]

It binds a free loopback port (`--port 0`) and prints one handshake line, `FT_READY {"port":…,"version":…}`, on
stdout. `--frozen-clock` (an instant with its UTC offset) is test-only: the acceptance harness freezes the backend's
clock through it (S0030; review:27 §1(d)), since the CLI is the one channel between a parent and the sidecar.

The ledger engine (ADR-0054; S0038) starts at boot on its own thread, in parallel with the bind and the handshake;
its outcome is a state (`app.platform.engine_boot`) and an event, never a crash, and the sidecar answers either way.
The engine is the packaged one beside this interpreter (`<prefix>/../engine/site-packages`, ADR-0056) unless
`--engine-venv` (development and tests) names a virtual environment that has it. Events go to the structured log,
`<app-local-data>/logs/finzytrack.jsonl` (`app.platform.observability`; ARCHITECTURE.md §6A), the engine's own
stderr lines among them, redacted; other libraries' records, warnings and uncaught exceptions too. Every line carries
the launch's `--session-id` (the shell's), or one made here. A log path that leads outside the app-local-data folder
or into the workspace is refused: the process exits with code 2 before the handshake. On exit, also on SIGTERM, the
engine is closed before the process ends.

Until the API edge exists (FastAPI and uvicorn, `/api/boot`: L1-07, L10-09, M2) the server has no routes and
answers every request with 501; that slice replaces `bind` and `serve` and keeps the args and the handshake.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path
from types import FrameType
from typing import Any, override

from app.domain.text import UntrustedText
from app.platform.adapters.clock import SystemClock
from app.platform.adapters.engine_client import EngineClient, EngineCommand, packaged_engine_site
from app.platform.clock import Clock, FrozenClock
from app.platform.engine_boot import EngineBoot
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
class Wiring:
    """What the composition root builds. Grows into `build_user_container` (L1-07)."""

    clock: Clock
    engine: EngineClient
    boot: EngineBoot


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


def wire(args: Args, log: EventSink) -> Wiring:
    """Build, never start: the engine starts in `main` (S0038)."""

    def engine_stderr(line: str) -> None:
        log(ENGINE_STDERR, line=UntrustedText(line))  # the engine's stderr can carry ledger text (BL-0088)

    engine = EngineClient(engine_command(args), on_stderr=engine_stderr, log=log)  # it logs its lifecycle (S0040)
    return Wiring(
        clock=FrozenClock(args.frozen_clock) if args.frozen_clock else SystemClock(),
        engine=engine,
        boot=EngineBoot(engine, pid=lambda: engine.pid),
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


class _NoRoutes(BaseHTTPRequestHandler):
    """No handler methods, so every request is answered 501 by the base class (no routes before L10-09)."""

    @override
    def log_message(self, format: str, *args: Any) -> None:
        """Quiet: request logging arrives with the logging slice (ARCHITECTURE.md §7)."""


def bind(port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((LOOPBACK, port), _NoRoutes)


def bound_address(server: ThreadingHTTPServer) -> tuple[str, int]:
    host, port = server.server_address[:2]
    return str(host), int(port)


def _stop(signum: int, frame: FrameType | None) -> None:
    """SIGTERM (the shell's exit, the harness's stop): leave `serve_forever` so the engine is closed on the way out."""
    raise SystemExit(0)


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
    wiring = wire(args, log)  # nothing reads the clock yet; the first use case receives it from here
    signal.signal(signal.SIGTERM, _stop)
    try:
        wiring.boot.start_in_background()
        server = bind(args.port)
        try:
            sys.stdout.write(handshake_line(bound_address(server)[1], app_version) + "\n")
            sys.stdout.flush()
            server.serve_forever()
        finally:
            server.server_close()
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)  # the close below is bounded (RestartPolicy) and must finish
        wiring.engine.close()  # logs engine.closed
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
