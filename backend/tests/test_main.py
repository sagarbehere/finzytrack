"""The main sidecar entry (ARCHITECTURE.md §5, the shell interface): CLI args, loopback bind, `FT_READY` handshake,
the API served by uvicorn (S0048, BL-0105) and the stop path that always closes the engine (failure-modes M7).

The acceptance harness (S0030) starts this process with `--port 0` and data dirs inside its temp root, and reads
the port from the handshake line only. `--frozen-clock` is the test-only way to freeze the backend's clock.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import cast, override

import pytest
from fastapi import APIRouter

from app import main
from app.api import ApiContainer, create_app
from app.api.envelope import Envelope
from app.api.health import health_router
from app.api.routes import declare
from app.platform.adapters.clock import SystemClock
from app.platform.adapters.engine_client import (
    EngineClient,
    EngineCommand,
    bytecode_build_dir,
    packaged_engine_site,
    venv_site,
)
from app.platform.clock import FrozenClock
from app.platform.engine_boot import EngineBoot, EngineStarting
from app.platform.observability import Event, EventLog
from app.workspaces.access import Unknown
from tests.api.support import Counted, FakeContainer, FixedAccess


def lock_held(path: Path) -> bool:
    """Whether another process holds this `flock` (POSIX; a fresh open file description, never blocking)."""
    import fcntl  # POSIX only: the tests that call it skip elsewhere

    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def no_log(event: Event, /, **fields: object) -> None:
    """An `EventSink` that drops every event."""


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    data, cache = tmp_path / "local", tmp_path / "cache"
    data.mkdir()
    cache.mkdir()
    return data, cache


VERSION = "9.9.9-test"


def argv(data: Path, cache: Path, *extra: str) -> list[str]:
    return ["--app-local-data", str(data), "--app-cache", str(cache), *extra]


def test_args_default_to_port_0_and_the_system_clock(dirs: tuple[Path, Path]) -> None:
    args = main.parse_args(argv(*dirs))
    assert args.port == 0
    assert args.workspace is None
    assert isinstance(main.wire(args, no_log, VERSION).clock, SystemClock)


def test_frozen_clock_arg_wires_a_frozen_clock(dirs: tuple[Path, Path]) -> None:
    args = main.parse_args(argv(*dirs, "--frozen-clock", "2026-03-31T21:30:00-07:00"))
    clock = main.wire(args, no_log, VERSION).clock
    assert isinstance(clock, FrozenClock)
    assert clock.now() == datetime(2026, 4, 1, 4, 30, tzinfo=UTC)
    assert clock.now().utcoffset() == timedelta(hours=-7)
    assert clock.now().tzinfo == timezone(timedelta(hours=-7))


@pytest.mark.parametrize(
    "extra",
    [
        ("--frozen-clock", "2026-03-31T21:30:00"),  # no offset: not an instant
        ("--frozen-clock", "yesterday"),
        ("--port", "-1"),
        ("--port", "65536"),
        ("--port", "http"),
    ],
)
def test_bad_args_are_refused(dirs: tuple[Path, Path], extra: tuple[str, str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main.parse_args(argv(*dirs, *extra))
    assert exc.value.code == 2


def test_missing_or_non_folder_roots_are_refused(tmp_path: Path, dirs: tuple[Path, Path]) -> None:
    a_file = tmp_path / "file"
    a_file.write_text("x")
    for data, cache in ((tmp_path / "missing", dirs[1]), (dirs[0], a_file)):
        with pytest.raises(SystemExit):
            main.parse_args(argv(data, cache))
    with pytest.raises(SystemExit):
        main.parse_args(argv(*dirs, "--workspace", str(tmp_path / "missing")))
    with pytest.raises(SystemExit):
        main.parse_args(["--app-cache", str(dirs[1])])  # --app-local-data is required


def test_the_handshake_line_is_ft_ready_with_port_and_version() -> None:
    line = main.handshake_line(51234, "0.4.0.dev0")
    assert line.startswith("FT_READY ")
    assert "\n" not in line
    assert json.loads(line.removeprefix("FT_READY ")) == {"port": 51234, "version": "0.4.0.dev0"}


def test_the_server_binds_loopback_only_on_an_os_assigned_port_and_listens_before_the_handshake() -> None:
    sock = main.bind(0)
    try:
        host, port = main.bound_address(sock)
        assert host == "127.0.0.1"
        assert 0 < port <= 65535
        # Listening already: a client that connects right after `FT_READY`, before uvicorn's loop runs, is queued,
        # never refused (S0048).
        socket.create_connection((host, port), timeout=10).close()
    finally:
        sock.close()


def get(port: int, path: str) -> tuple[int, dict[str, object]]:
    """One request to the sidecar under test, on its own loopback port; the status and the JSON body."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, cast("dict[str, object]", json.loads(response.read()))
    finally:
        conn.close()


def test_the_process_prints_the_handshake_and_serves_health(dirs: tuple[Path, Path]) -> None:
    """BL-0105: the sidecar serves `app.api.create_app`; `/health` answers in the envelope with the handshake's
    version and a fresh uuid7 request id per request (the composition root's `Ids`)."""
    proc = subprocess.Popen(  # noqa: S603 (fixed argv: this interpreter running the entry under test)
        [sys.executable, "-m", "app.main", *argv(*dirs), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        assert line.startswith("FT_READY "), line
        handshake = json.loads(line.removeprefix("FT_READY "))
        port = handshake["port"]
        assert port != 0
        ids: list[str] = []
        for _ in range(2):
            status, answer = get(port, "/health")
            assert status == 200, answer
            assert answer["data"] == {"status": "ok", "version": handshake["version"]}
            meta = cast("dict[str, object]", answer["meta"])
            assert meta["workspace_id"] is None
            request_id = meta["request_id"]
            assert isinstance(request_id, str)
            assert uuid.UUID(request_id).version == 7
            ids.append(request_id)
        assert ids[0] != ids[1]
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_an_unknown_route_is_route_not_found_in_the_envelope_from_the_real_server(dirs: tuple[Path, Path]) -> None:
    sidecar = Sidecar(dirs)
    try:
        port = sidecar.handshake()
        status, answer = get(port, "/api/boot")  # L10-09 (M2): not served yet
        assert status == 404
        error = cast("dict[str, object]", answer["error"])
        assert error["code"] == "ROUTE_NOT_FOUND"
        # A workspace path reaches no route yet, so no gate is asked (BL-0102 adds the routes and the gate).
        status, answer = get(port, "/api/w/demo/version")
        assert status == 404
        assert cast("dict[str, object]", answer["error"])["code"] == "ROUTE_NOT_FOUND"
    finally:
        assert sidecar.stop() == 0


# -- The engine at boot (S0038; BL-0083; ADR-0054 decision 4) ---------------------------------------------------

REPO = Path(__file__).resolve().parents[2]
ENGINE_VENV = REPO / "engine" / ".venv"
DEADLINE = 30.0


def test_engine_venv_selects_the_venv_engine(dirs: tuple[Path, Path]) -> None:
    args = main.parse_args(argv(*dirs, "--engine-venv", str(ENGINE_VENV)))
    assert args.engine_venv == ENGINE_VENV
    lock = dirs[0] / "locks" / "engine.lock"
    python = ENGINE_VENV / "bin" / "python"
    bytecode = bytecode_build_dir(dirs[0], python, venv_site(ENGINE_VENV), VERSION)
    expected = EngineCommand.for_venv(ENGINE_VENV, engine_lock=lock, bytecode_dir=bytecode)
    assert main.engine_command(args, VERSION) == expected


def test_without_engine_venv_the_engine_is_the_packaged_one_beside_this_interpreter(dirs: tuple[Path, Path]) -> None:
    args = main.parse_args(argv(*dirs))
    assert args.engine_venv is None
    lock = dirs[0] / "locks" / "engine.lock"
    site = packaged_engine_site(Path(sys.prefix))
    bytecode = bytecode_build_dir(dirs[0], Path(sys.executable), site, VERSION)
    expected = EngineCommand.for_packaged(Path(sys.executable), site, engine_lock=lock, bytecode_dir=bytecode)
    assert main.engine_command(args, VERSION) == expected


def test_the_bytecode_directory_is_per_build_in_app_local_data(dirs: tuple[Path, Path]) -> None:
    """engine-jobs §5.1: "always in app-local data (`app_local_data/engine/bytecode/<build>/`, never inside the
    packaged install, which may be read-only)"; another app version is another build."""
    args = main.parse_args(argv(*dirs))
    command = main.engine_command(args, VERSION)
    assert command.bytecode is not None
    assert command.bytecode.directory.parent == dirs[0] / "engine" / "bytecode"
    other = main.engine_command(args, VERSION + ".1")
    assert other.bytecode is not None
    assert other.bytecode.directory != command.bytecode.directory


def test_engine_venv_must_be_a_folder(tmp_path: Path, dirs: tuple[Path, Path]) -> None:
    with pytest.raises(SystemExit):
        main.parse_args(argv(*dirs, "--engine-venv", str(tmp_path / "missing")))


def test_wire_builds_the_engine_client_without_starting_it(dirs: tuple[Path, Path]) -> None:
    wiring = main.wire(main.parse_args(argv(*dirs, "--engine-venv", str(ENGINE_VENV))), no_log, VERSION)
    try:
        assert isinstance(wiring.engine, EngineClient)
        assert wiring.engine.pid is None
        assert isinstance(wiring.boot.state, EngineStarting)
    finally:
        wiring.engine.close()


class Sidecar:
    """`python -m app.main` as the shell runs it: its stdout handshake, its stderr, and its events read from the log
    file (ARCHITECTURE.md §6A.1; S0039)."""

    def __init__(self, dirs: tuple[Path, Path], *extra: str) -> None:
        self.log_path = dirs[0] / "logs" / "finzytrack.jsonl"
        self.proc = subprocess.Popen(  # noqa: S603 (fixed argv: this interpreter running the entry under test)
            [sys.executable, "-m", "app.main", *argv(*dirs), "--port", "0", *extra],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.raw: list[str] = []
        self._pumping = threading.Thread(target=self._pump, daemon=True)
        self._pumping.start()

    def _pump(self) -> None:
        assert self.proc.stderr is not None
        self.raw.extend(self.proc.stderr)

    def stderr_after_exit(self) -> str:
        """All the process wrote to stderr; call once it has exited."""
        self._pumping.join(DEADLINE)
        return "".join(self.raw)

    def events(self) -> list[dict[str, object]]:
        """The whole lines of the log file so far."""
        if not self.log_path.exists():
            return []
        out: list[dict[str, object]] = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines(keepends=True):
            if line.endswith("\n"):
                out.append(cast("dict[str, object]", json.loads(line)))
        return out

    def handshake(self) -> int:
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        assert line.startswith("FT_READY "), line + "".join(self.raw)
        return int(json.loads(line.removeprefix("FT_READY "))["port"])

    def event(self, name: str, timeout: float = DEADLINE) -> dict[str, object]:
        end = time.monotonic() + timeout
        tick = threading.Event()
        while time.monotonic() < end:
            for event in self.events():
                if event.get("event") == name:
                    return event
            tick.wait(0.05)
        pytest.fail(f"no {name} event within {timeout} s; log: {self.events()}; stderr: {''.join(self.raw)}")

    def stop(self) -> int:
        """SIGTERM, as the shell's exit and the harness stop it."""
        if self.proc.poll() is None:
            self.proc.terminate()
        return self.proc.wait(timeout=DEADLINE)


def health_status(port: int) -> int:
    """The status `/health` answers with (S0048: 200 whatever the engine's state; BL-0094 adds the engine to it)."""
    return get(port, "/health")[0]


def pid_alive(pid: int) -> bool:
    """The engine runs (signal 0 only probes; the pid is the one our own sidecar reported)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.skipif(os.name != "posix", reason="SIGTERM and pid probes as on POSIX; Windows is the manual CI run")
def test_the_sidecar_starts_a_real_engine_and_closes_it_on_sigterm(dirs: tuple[Path, Path]) -> None:
    sidecar = Sidecar(dirs, "--engine-venv", str(ENGINE_VENV))
    try:
        port = sidecar.handshake()
        ready = sidecar.event("engine.ready")
        assert ready["level"] == "info"
        assert ready["source"] == "backend"
        assert ready["beancount_version"] == "3.2.0"
        assert ready["protocol"] == 2
        pid = ready["pid"]
        assert isinstance(pid, int)
        assert pid_alive(pid)
        assert health_status(port) == 200
    finally:
        code = sidecar.stop()
    # The sidecar closed and reaped its engine before it exited: no grace time (its watchdog and stdin EOF would end
    # an orphaned engine too, but only after the sidecar had gone; S0038 self-mutation B7).
    assert not pid_alive(pid), "the sidecar exited before closing its engine"
    closed = sidecar.event("engine.closed")
    assert closed["source"] == "backend"
    # One line each (S0040: the client's table logs the lifecycle; the composition root no longer logs it too).
    names = [e.get("event") for e in sidecar.events()]
    assert names.count("engine.ready") == 1
    assert names.count("engine.closed") == 1
    assert code == 0, "".join(sidecar.raw)


@pytest.mark.skipif(os.name != "posix", reason="flock is POSIX; Windows is BL-0087 (M1b)")
def test_an_earlier_sessions_engine_holding_the_engine_lock_keeps_the_first_engine_from_starting(
    dirs: tuple[Path, Path],
) -> None:
    """engine-jobs §5.1, §14 "Engine" (simplification B; ADR-0064 amendment of 2026-10-28): a process of an earlier
    session holding the engine lock keeps the new start's sidecar waiting before its first engine; the sidecar still
    answers; once that process ends, the engine starts and holds the lock itself."""
    lock = dirs[0] / "locks" / "engine.lock"
    lock.parent.mkdir()
    holder = subprocess.Popen(  # noqa: S603 (this interpreter running a fixed test helper)
        [sys.executable, "-I", str(Path(__file__).parent / "platform" / "lock_holder.py"), str(lock)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    sidecar = None
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline() == "held\n"
        sidecar = Sidecar(dirs, "--engine-venv", str(ENGINE_VENV))
        port = sidecar.handshake()
        assert health_status(port) == 200
        tick = threading.Event()
        tick.wait(1.0)  # long enough for an engine that did not wait to log engine.ready (it takes ~0.1 s)
        assert "engine.ready" not in [e.get("event") for e in sidecar.events()], "an engine started beside the lock"
        assert holder.stdin is not None
        holder.stdin.close()  # the earlier engine ends
        holder.wait(DEADLINE)
        pid = sidecar.event("engine.ready")["pid"]
        assert isinstance(pid, int)
        assert pid_alive(pid)
        end = time.monotonic() + DEADLINE
        while not lock_held(lock) and time.monotonic() < end:
            tick.wait(0.05)
        assert lock_held(lock), "the product's engine does not hold the engine lock after its handshake"
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(DEADLINE)
        if sidecar is not None:
            sidecar.stop()


@pytest.mark.skipif(os.name != "posix", reason="the parent-death watchdog is POSIX; Windows is the manual CI run")
def test_a_sidecar_ended_hard_leaves_no_engine_behind(dirs: tuple[Path, Path]) -> None:
    """@regression-guard (S0041, failure-modes rows M1, M4, E5): a sidecar that crashes or is ended by the OS (SIGKILL:
    no handler, no `finally`, no close) cannot close its engine; the engine notices on its own (its parent watchdog,
    stdin EOF) and ends, so no orphan engine keeps running. The sidecar's log ends without `engine.closed`."""
    sidecar = Sidecar(dirs, "--engine-venv", str(ENGINE_VENV))
    try:
        sidecar.handshake()
        pid = sidecar.event("engine.ready")["pid"]
        assert isinstance(pid, int)
        assert pid_alive(pid)
    finally:
        sidecar.proc.send_signal(signal.SIGKILL)
        sidecar.proc.wait(timeout=DEADLINE)
    end = time.monotonic() + DEADLINE
    tick = threading.Event()
    while pid_alive(pid) and time.monotonic() < end:
        tick.wait(0.05)
    assert not pid_alive(pid), "the engine outlived its sidecar"
    assert "engine.closed" not in [e.get("event") for e in sidecar.events()]


def test_a_missing_engine_is_a_reported_state_and_the_sidecar_keeps_answering(
    tmp_path: Path, dirs: tuple[Path, Path]
) -> None:
    empty = tmp_path / "no-engine"
    empty.mkdir()
    sidecar = Sidecar(dirs, "--engine-venv", str(empty))
    try:
        port = sidecar.handshake()
        failed = sidecar.event("engine.start_failed")
        assert failed["level"] == "error"
        assert failed["kind"] == "start_failed"
        assert sidecar.proc.poll() is None, "the sidecar crashed with its engine"
        assert health_status(port) == 200
    finally:
        assert sidecar.stop() == 0


@pytest.mark.skipif(os.name != "posix", reason="the delayed stand-in engine is a shell script")
def test_the_handshake_does_not_wait_for_the_engine(tmp_path: Path, dirs: tuple[Path, Path]) -> None:
    """The engine starts in parallel with the sidecar (ADR-0054 Consequences): a slow engine delays only itself."""
    slow = tmp_path / "slow-venv"
    (slow / "bin").mkdir(parents=True)
    script = slow / "bin" / "python"
    script.write_text(f'#!/bin/sh\necho "slow stand-in: waiting" >&2\nsleep 3\nexec "{ENGINE_VENV}/bin/python" "$@"\n')
    script.chmod(0o755)
    (slow / "lib").symlink_to(ENGINE_VENV / "lib")  # the engine runs under -S with the venv's own site (S0057)
    began = time.monotonic()
    sidecar = Sidecar(dirs, "--engine-venv", str(slow))
    try:
        sidecar.handshake()
        handshake_at = time.monotonic() - began
        stderr_line = sidecar.event("engine.stderr")
        assert stderr_line["source"] == "engine"
        # BL-0088: the engine's stderr is untrusted text, redacted outside debug mode.
        assert stderr_line["line"] == f"<untrusted len={len('slow stand-in: waiting')}>"
        assert "slow stand-in" not in sidecar.log_path.read_text(encoding="utf-8")
        sidecar.event("engine.ready")
        ready_at = time.monotonic() - began
        assert ready_at - handshake_at > 2.0, (handshake_at, ready_at)
    finally:
        sidecar.stop()


# -- The structured log (S0039; BL-0088; ARCHITECTURE.md §6A.1) -------------------------------------------------


def test_the_session_id_comes_from_the_shell_or_is_made_per_launch(dirs: tuple[Path, Path]) -> None:
    assert main.parse_args(argv(*dirs, "--session-id", "0f3c2a1b-from-shell")).session_id == "0f3c2a1b-from-shell"
    assert main.parse_args(argv(*dirs)).session_id is None


@pytest.mark.parametrize("bad", ["short", "has space here", "a" * 65, "Assets:Bank:Checking"])
def test_a_bad_session_id_is_refused(dirs: tuple[Path, Path], bad: str) -> None:
    with pytest.raises(SystemExit) as exc:
        main.parse_args(argv(*dirs, "--session-id", bad))
    assert exc.value.code == 2


def test_the_sidecars_events_go_to_the_log_file_with_its_session_and_never_to_stderr(
    tmp_path: Path, dirs: tuple[Path, Path]
) -> None:
    empty = tmp_path / "no-engine"
    empty.mkdir()
    sidecar = Sidecar(dirs, "--engine-venv", str(empty), "--session-id", "0f3c2a1b-from-shell")
    try:
        sidecar.handshake()
        failed = sidecar.event("engine.start_failed")
        assert failed["session_id"] == "0f3c2a1b-from-shell"
        assert str(failed["detail"]).startswith("<text len=")  # free text, shown only in debug mode
        assert str(empty) not in sidecar.log_path.read_text(encoding="utf-8")
    finally:
        assert sidecar.stop() == 0
    closed = sidecar.event("engine.closed")
    assert closed["session_id"] == "0f3c2a1b-from-shell"
    assert '"event"' not in sidecar.stderr_after_exit(), "an event reached stderr"


def test_a_generated_session_id_is_the_same_on_every_line_of_a_launch(tmp_path: Path, dirs: tuple[Path, Path]) -> None:
    empty = tmp_path / "no-engine"
    empty.mkdir()
    sidecar = Sidecar(dirs, "--engine-venv", str(empty))
    try:
        sidecar.handshake()
        sidecar.event("engine.start_failed")
    finally:
        sidecar.stop()
    sidecar.event("engine.closed")
    ids = {e["session_id"] for e in sidecar.events()}
    assert len(ids) == 1
    (only,) = ids
    assert isinstance(only, str)
    assert len(only) == 32


def test_the_sidecar_refuses_to_start_when_its_log_folder_leads_outside(
    tmp_path: Path, dirs: tuple[Path, Path]
) -> None:
    outside = tmp_path / "synced"
    outside.mkdir()
    (dirs[0] / "logs").symlink_to(outside, target_is_directory=True)
    r = subprocess.run(  # noqa: S603 (fixed argv: this interpreter running the entry under test)
        [sys.executable, "-m", "app.main", *argv(*dirs), "--port", "0"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=DEADLINE,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )
    assert r.returncode == 2, r.stderr
    assert "FT_READY" not in r.stdout
    assert "log" in r.stderr
    assert list(outside.iterdir()) == []


def test_an_exception_that_leaves_main_is_logged_as_its_type_and_never_printed(dirs: tuple[Path, Path]) -> None:
    """The port is taken, so the bind fails inside `main`: the traceback (which would quote source lines and the
    error's message) goes to the log as type and code locations only, and stderr stays empty."""
    taken = main.bind(0)
    try:
        port = main.bound_address(taken)[1]
        sidecar = Sidecar(dirs, "--port", str(port), "--engine-venv", str(dirs[0]))
        code = sidecar.proc.wait(timeout=DEADLINE)
    finally:
        taken.close()
    assert code == 1
    uncaught = sidecar.event("app.uncaught")
    assert uncaught["thread"] == "MainThread"
    exception = cast("dict[str, object]", uncaught["exception"])
    assert exception["type"] == "OSError"
    assert set(exception) == {"type", "at"}
    assert sidecar.stderr_after_exit() == ""


# -- The API's container (S0048, BL-0105; ARCHITECTURE.md §2.3) --------------------------------------------------


def test_the_api_container_has_the_version_uuid7_ids_no_workspaces_and_the_log(dirs: tuple[Path, Path]) -> None:
    wiring = main.wire(main.parse_args(argv(*dirs)), no_log, VERSION)
    try:
        api = wiring.api
        assert api.app_version == VERSION
        first, second = api.ids.new_id(), api.ids.new_id()
        assert uuid.UUID(first).version == 7
        assert first != second
        # Until the registry exists (BL-0102) every key is unknown: `404 WORKSPACE_UNKNOWN`, never served.
        for key in ("demo", "", "../x", "Assets:Bank"):
            assert api.workspaces.access(key) == Unknown()
        assert api.log is no_log
    finally:
        wiring.engine.close()


# -- The stop path (S0048; failure-modes M7 and decision 6) -------------------------------------------------------


@dataclass
class FakeServer:
    """The two flags of uvicorn's `Server` that the stop request sets."""

    should_exit: bool = False
    force_exit: bool = False


def test_the_first_stop_ends_the_serve_and_a_later_one_ends_the_graceful_wait() -> None:
    stop, server = main.StopRequest(), FakeServer()
    stop.attach(server)
    assert not stop.requested
    stop(signal.SIGTERM, None)
    assert stop.requested
    assert (server.should_exit, server.force_exit) == (True, False)
    stop(signal.SIGINT, None)
    assert (server.should_exit, server.force_exit) == (True, True)


@pytest.mark.parametrize(("before", "expected"), [(1, (True, False)), (2, (True, True))])
def test_a_stop_before_the_server_exists_is_kept(before: int, expected: tuple[bool, bool]) -> None:
    stop, server = main.StopRequest(), FakeServer()
    for _ in range(before):
        stop(signal.SIGTERM, None)
    stop.attach(server)
    assert (server.should_exit, server.force_exit) == expected


def test_the_stop_handler_never_raises() -> None:
    """The stand-in's handler raised `SystemExit`, so a signal at the wrong line cut the stop path before the
    engine's close (decision 6). This one only records; with or without a server, any number of times."""
    for server in (None, FakeServer()):
        stop = main.StopRequest()
        if server is not None:
            stop.attach(server)
        for signum in (*main.stop_signals(), *main.stop_signals(), *main.stop_signals()):
            stop(signum, None)
        assert stop.requested


def test_the_stop_signals_are_sigterm_and_sigint() -> None:
    assert {signal.SIGTERM, signal.SIGINT} <= set(main.stop_signals())


@pytest.fixture
def saved_handlers() -> Iterator[None]:
    saved = {signum: signal.getsignal(signum) for signum in main.stop_signals()}
    yield
    for signum, handler in saved.items():
        signal.signal(signum, handler)


class NoRequestExpected(AssertionError):
    """A request reached a server that these tests never send one to."""


def _unused_container() -> ApiContainer:
    raise NoRequestExpected


@pytest.mark.usefixtures("saved_handlers")
def test_while_uvicorn_serves_the_stop_handlers_are_the_composition_roots() -> None:
    """uvicorn's `Server.serve` puts its own handlers in while it serves and, when it ends, puts the old ones back and
    raises each caught signal again. The composition root owns the stop for the whole process instead, so one
    handler decides and a caught signal is never raised again behind it (BL-0105: uvicorn's own signal handling
    checked against M7). Runs in the main thread, where uvicorn would install its handlers."""
    seen: list[dict[int, object]] = []

    class OneTick(main.ApiServer):
        @override
        async def on_tick(self, counter: int) -> bool:
            seen.append({signum: signal.getsignal(signum) for signum in main.stop_signals()})
            return True

    stop = main.StopRequest()
    main.install_stop_handler(stop)
    sock = main.bind(0)
    server = OneTick(main.server_config(create_app(_unused_container), sock))
    stop.attach(server)
    server.run(sockets=[sock])
    assert seen == [dict.fromkeys(main.stop_signals(), stop)]
    assert {signum: signal.getsignal(signum) for signum in main.stop_signals()} == dict.fromkeys(
        main.stop_signals(), stop
    )
    assert not stop.requested


def test_the_server_config_turns_off_what_the_sidecar_does_itself() -> None:
    sock = main.bind(0)
    try:
        config = main.server_config(create_app(_unused_container), sock)
        assert (config.host, config.port) == main.bound_address(sock)
    finally:
        sock.close()
    assert config.lifespan == "off"  # the composition root starts and stops the engine, on every path
    assert config.log_config is None  # library records go to the structured log (ARCHITECTURE.md §6A.1)
    assert config.access_log is False  # request logging arrives with the logging slice (§6A.5)
    assert config.proxy_headers is False  # nothing proxies a loopback app; never trust a forwarded address
    assert config.server_header is False
    assert config.ws == "none"


def wait_until(condition: Callable[[], bool]) -> None:
    end = time.monotonic() + DEADLINE
    tick = threading.Event()
    while not condition():
        assert time.monotonic() < end, "the condition never held"
        tick.wait(0.01)


def test_the_first_stop_waits_for_a_request_in_flight_and_a_second_ends_it() -> None:
    """A request whose handler never returns: the first stop waits for it (a quit waits for work in flight); the
    second ends it, so the serve returns and the way to `engine.close()` is open. Without that, asyncio's
    `Server.wait_closed` waits for the open connection for ever, whatever uvicorn's `force_exit` says (S0048: seen
    with uvicorn 0.54.0 on Python 3.13). The server runs on a thread here; its stop request is called directly."""
    hung = APIRouter(prefix="/api/test")
    reached = threading.Event()

    @hung.get("/hang", openapi_extra=declare("app"))
    async def hang() -> Envelope[Counted]:
        reached.set()
        await asyncio.Event().wait()
        raise NoRequestExpected

    container = FakeContainer(FixedAccess(Unknown()))
    sock = main.bind(0)
    port = main.bound_address(sock)[1]
    server = main.ApiServer(main.server_config(create_app(lambda: container, routers=[health_router(), hung]), sock))
    stop = main.StopRequest()
    stop.attach(server)
    serving = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    serving.start()
    wait_until(lambda: server.started)
    client = socket.create_connection(("127.0.0.1", port), timeout=DEADLINE)
    try:
        client.sendall(b"GET /api/test/hang HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        assert reached.wait(DEADLINE)
        stop(signal.SIGTERM, None)
        serving.join(0.5)
        assert serving.is_alive(), "the first stop did not wait for the request in flight"
        client.settimeout(0.2)
        with pytest.raises(TimeoutError):  # still open, waiting for its answer (b"" would mean closed)
            client.recv(1)
        stop(signal.SIGTERM, None)
        serving.join(DEADLINE)
        assert not serving.is_alive(), "a second stop left the serve waiting for the request"
    finally:
        client.close()
        if serving.is_alive():
            server.force_exit = server.should_exit = True


# A child that serves one extra route whose sync work runs on an anyio worker thread (ARCHITECTURE.md §2, the
# execution rule for sync use cases) and blocks until its stdin closes; it touches the flag file once the work runs.
SYNC_WORK = """
import sys
from pathlib import Path

import anyio
from fastapi import APIRouter

from app import main
from app.api import create_app
from app.api.envelope import Envelope
from app.api.health import health_router
from app.api.routes import declare
from tests.api.support import Counted

FLAG = Path(sys.argv[1])
work = APIRouter(prefix="/api/test")

def blocking() -> None:
    FLAG.touch()
    sys.stdin.read()  # until the test closes our stdin

@work.get("/sync", openapi_extra=declare("app"))
async def sync() -> Envelope[Counted]:
    await anyio.to_thread.run_sync(blocking)
    raise AssertionError

main.create_app = lambda container: create_app(container, routers=[health_router(), work])
raise SystemExit(main.main(sys.argv[2:]))
"""


def child_path() -> str:
    """The child's import path: what this run has (a mutant run puts its own `src` first), then `backend/`, so the
    child can import `tests.api.support`."""
    inherited = os.environ.get("PYTHONPATH")
    backend = str(Path(__file__).resolve().parents[1])
    return os.pathsep.join([inherited, backend]) if inherited else backend


class SyncWorkSidecar(Sidecar):
    def __init__(self, dirs: tuple[Path, Path], flag: Path) -> None:
        self.log_path = dirs[0] / "logs" / "finzytrack.jsonl"
        self.proc = subprocess.Popen(  # noqa: S603 (fixed argv: this interpreter running the entry under test)
            [
                sys.executable,
                "-c",
                SYNC_WORK,
                str(flag),
                *argv(*dirs),
                "--port",
                "0",
                "--engine-venv",
                str(ENGINE_VENV),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": child_path()},
        )
        self.raw = []
        self._pumping = threading.Thread(target=self._pump, daemon=True)
        self._pumping.start()


@pytest.mark.skipif(os.name != "posix", reason="signals and pid probes as on POSIX; Windows is the manual CI run")
def test_a_second_stop_closes_the_engine_while_sync_work_is_in_flight(tmp_path: Path, dirs: tuple[Path, Path]) -> None:
    """S0048 verifier F1: work on a worker thread cannot be ended from outside it. The second stop still ends the serve,
    so `engine.close()` runs while the work is in flight; the process ends once the work returns (the thread is not
    a daemon), which is the quit design's to change (engine-jobs §9.3, BL-0109). SIGTERM then SIGINT: two different
    signals are never merged into one, as two pending SIGTERMs can be."""
    flag = tmp_path / "working"
    sidecar = SyncWorkSidecar(dirs, flag)
    assert sidecar.proc.stdin is not None
    client: socket.socket | None = None
    try:
        port = sidecar.handshake()
        sidecar.event("engine.ready")
        client = socket.create_connection((main.LOOPBACK, port), timeout=DEADLINE)
        client.sendall(b"GET /api/test/sync HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        wait_until(flag.exists)
        sidecar.proc.send_signal(signal.SIGTERM)
        sidecar.proc.send_signal(signal.SIGINT)
        sidecar.event("engine.closed")  # while the sync work is still blocked on our stdin
    finally:
        sidecar.proc.stdin.close()  # the work returns
        if client is not None:
            client.close()
        try:
            code = sidecar.proc.wait(timeout=DEADLINE)
        except subprocess.TimeoutExpired:
            sidecar.proc.kill()  # our own child: never leave it running when the test fails
            sidecar.proc.wait()
            raise
    assert code == 0, sidecar.stderr_after_exit()
    engine_gone(sidecar)


def engine_gone(sidecar: Sidecar) -> None:
    """The run's engine, if it started, is no longer running; `engine.closed` is logged exactly once."""
    names = [e.get("event") for e in sidecar.events()]
    assert names.count("engine.closed") == 1, names
    for event in sidecar.events():
        if event.get("event") == "engine.ready":
            pid = event["pid"]
            assert isinstance(pid, int)
            assert not pid_alive(pid), "the sidecar exited before closing its engine"


class EngineStartAsked(Exception):
    """Ends `main` at the engine's start in the test below: no engine, no server."""


@pytest.mark.usefixtures("saved_handlers")
def test_the_stop_handler_is_in_place_before_the_engine_can_start(
    dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure-modes M7, ARCHITECTURE.md §2.0: a stop that comes once the engine may be starting reaches its close. The
    storm tests only storm once a handler is installed, so they cannot see where it is installed (S0048 verifier F2:
    installing it after the engine's start passed them). The engine starts only through `start_in_background`
    (`wire` builds and never starts it, as tested above), so the handlers are read at that call."""
    seen: dict[int, object] = {}

    def start(self: EngineBoot) -> None:
        seen.update({s: signal.getsignal(s) for s in main.stop_signals()})
        raise EngineStartAsked

    monkeypatch.setattr(EngineBoot, "start_in_background", start)
    logs: list[EventLog] = []

    def capture_process(self: EventLog) -> Callable[[], None]:  # keep pytest's own hooks; close the log after
        logs.append(self)
        return lambda: None

    monkeypatch.setattr(EventLog, "capture_process", capture_process)
    try:
        with pytest.raises(EngineStartAsked):
            main.main(argv(*dirs))
    finally:
        for log in logs:
            log.close()
    handlers = set(map(id, seen.values()))
    assert len(handlers) == 1, seen  # one handler for every stop signal
    assert all(isinstance(h, main.StopRequest) for h in seen.values()), seen


@pytest.mark.skipif(os.name != "posix", reason="SIGINT and pid probes as on POSIX; Windows is the manual CI run")
def test_sigint_stops_the_sidecar_like_sigterm(dirs: tuple[Path, Path]) -> None:
    sidecar = Sidecar(dirs, "--engine-venv", str(ENGINE_VENV))
    try:
        port = sidecar.handshake()
        sidecar.event("engine.ready")
        assert health_status(port) == 200
    finally:
        sidecar.proc.send_signal(signal.SIGINT)
        code = sidecar.proc.wait(timeout=DEADLINE)
    assert code == 0, sidecar.stderr_after_exit()
    engine_gone(sidecar)


# The storm: a child runs `app.main` with a line tracer on its main thread. Once a stop handler is installed (the
# SIGTERM handler is neither the default nor "ignore"), it sends itself one SIGTERM at each line of `app.main` it
# reaches, the first time it reaches it, so a signal lands at every line of the stop path; the interpreter handles it
# at the next point it checks, inside that line. S0038's window was the line `signal.signal(SIGTERM, SIG_IGN)`
# itself: `signal.signal` runs the pending handler before it changes the handler, so `SystemExit` left before the
# close. In mode `after` the storm starts only once the flag file exists, which the test makes just before it sends
# its own SIGTERM, so the stop after serving is stormed and the start is not.
STORM = """
import json, os, signal, sys
from pathlib import Path
from app import main

TARGET, MODE, FLAG = main.__file__, sys.argv[1], Path(sys.argv[2])
hit, raised = set(), []

def storming():
    handler = signal.getsignal(signal.SIGTERM)
    if handler in (signal.SIG_DFL, signal.SIG_IGN, None):
        return False
    return MODE == "start" or FLAG.exists()

def local(frame, event, arg):
    where = (frame.f_code, frame.f_lineno)
    if event == "line" and where not in hit and storming():
        hit.add(where)
        try:
            os.kill(os.getpid(), signal.SIGTERM)  # handled right here, at the call's end
        except BaseException as e:  # the handler raised into the start or stop path at this line
            raised.append(f"{frame.f_code.co_name}:{frame.f_lineno}:{type(e).__name__}")
            raise
    return local

def tracer(frame, event, arg):
    return local if frame.f_code.co_filename == TARGET else None

sys.settrace(tracer)
try:
    code = main.main(sys.argv[3:])
finally:
    sys.settrace(None)
    print("STORM " + json.dumps({"lines": len(hit), "raised": raised}), flush=True)
raise SystemExit(code)
"""


class StormedSidecar(Sidecar):
    def __init__(self, dirs: tuple[Path, Path], mode: str, flag: Path) -> None:
        self.log_path = dirs[0] / "logs" / "finzytrack.jsonl"
        self.proc = subprocess.Popen(  # noqa: S603 (fixed argv: this interpreter running the entry under test)
            [
                sys.executable,
                "-c",
                STORM,
                mode,
                str(flag),
                *argv(*dirs),
                "--port",
                "0",
                "--engine-venv",
                str(ENGINE_VENV),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.raw = []
        self._pumping = threading.Thread(target=self._pump, daemon=True)
        self._pumping.start()

    def storm(self) -> tuple[int, list[str]]:
        """How many lines of `app.main` the storm hit, and where a handled signal raised; after the child exited."""
        assert self.proc.stdout is not None
        tail = self.proc.stdout.read()
        found = re.search(r"^STORM (.*)$", tail, re.MULTILINE)
        assert found is not None, tail + self.stderr_after_exit()
        report = cast("dict[str, object]", json.loads(found.group(1)))
        lines, raised = report["lines"], report["raised"]
        assert isinstance(lines, int)
        assert isinstance(raised, list)
        return lines, [str(r) for r in cast("list[object]", raised)]


@pytest.mark.skipif(os.name != "posix", reason="signals to itself as on POSIX; Windows is the manual CI run")
def test_a_signal_at_any_line_of_the_start_never_skips_the_engines_close(
    tmp_path: Path, dirs: tuple[Path, Path]
) -> None:
    sidecar = StormedSidecar(dirs, "start", tmp_path / "unused")
    code = sidecar.proc.wait(timeout=DEADLINE)
    assert code == 0, sidecar.stderr_after_exit()
    lines, raised = sidecar.storm()
    assert raised == [], "a stop signal raised into the start or stop path"
    assert lines > 5  # the storm reached the start and the stop path
    engine_gone(sidecar)


@pytest.mark.skipif(os.name != "posix", reason="signals to itself as on POSIX; Windows is the manual CI run")
def test_a_signal_at_any_line_of_the_stop_after_serving_never_skips_the_engines_close(
    tmp_path: Path, dirs: tuple[Path, Path]
) -> None:
    flag = tmp_path / "storm"
    sidecar = StormedSidecar(dirs, "after", flag)
    try:
        sidecar.handshake()
        sidecar.event("engine.ready")
    finally:
        flag.touch()
        sidecar.proc.send_signal(signal.SIGTERM)
        code = sidecar.proc.wait(timeout=DEADLINE)
    assert code == 0, sidecar.stderr_after_exit()
    lines, raised = sidecar.storm()
    assert raised == [], "a stop signal raised into the stop path"
    assert lines > 2
    engine_gone(sidecar)
