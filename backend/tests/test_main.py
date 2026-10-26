"""The main sidecar entry (ARCHITECTURE.md §5, the shell interface): CLI args, loopback bind, `FT_READY` handshake.

The acceptance harness (S0030) starts this process with `--port 0` and data dirs inside its temp root, and reads
the port from the handshake line only. `--frozen-clock` is the test-only way to freeze the backend's clock.
"""

from __future__ import annotations

import http.client
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest

from app import main
from app.platform.adapters.clock import SystemClock
from app.platform.adapters.engine_client import EngineClient, EngineCommand, packaged_engine_site
from app.platform.clock import FrozenClock
from app.platform.engine_boot import EngineStarting, Level


def no_log(level: Level, event: str, fields: Mapping[str, object]) -> None:
    """An `EventLog` that drops every event."""


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    data, cache = tmp_path / "local", tmp_path / "cache"
    data.mkdir()
    cache.mkdir()
    return data, cache


def argv(data: Path, cache: Path, *extra: str) -> list[str]:
    return ["--app-local-data", str(data), "--app-cache", str(cache), *extra]


def test_args_default_to_port_0_and_the_system_clock(dirs: tuple[Path, Path]) -> None:
    args = main.parse_args(argv(*dirs))
    assert args.port == 0
    assert args.workspace is None
    assert isinstance(main.wire(args, no_log).clock, SystemClock)


def test_frozen_clock_arg_wires_a_frozen_clock(dirs: tuple[Path, Path]) -> None:
    args = main.parse_args(argv(*dirs, "--frozen-clock", "2026-03-31T21:30:00-07:00"))
    clock = main.wire(args, no_log).clock
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


def test_the_server_binds_loopback_only_on_an_os_assigned_port() -> None:
    server = main.bind(0)
    try:
        host, port = main.bound_address(server)
        assert host == "127.0.0.1"
        assert 0 < port <= 65535
    finally:
        server.server_close()


def test_the_process_prints_the_handshake_and_answers_on_its_port(dirs: tuple[Path, Path]) -> None:
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
        port = json.loads(line.removeprefix("FT_READY "))["port"]
        assert port != 0
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/api/boot")
        # No routes until the API edge exists (L1-07, L10-09): any request is answered, with 501.
        assert conn.getresponse().status == 501
        conn.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# -- The engine at boot (S0038; BL-0083; ADR-0054 decision 4) ---------------------------------------------------

REPO = Path(__file__).resolve().parents[2]
ENGINE_VENV = REPO / "engine" / ".venv"
DEADLINE = 30.0


def test_engine_venv_selects_the_venv_engine(dirs: tuple[Path, Path]) -> None:
    args = main.parse_args(argv(*dirs, "--engine-venv", str(ENGINE_VENV)))
    assert args.engine_venv == ENGINE_VENV
    assert main.engine_command(args) == EngineCommand.for_venv(ENGINE_VENV)


def test_without_engine_venv_the_engine_is_the_packaged_one_beside_this_interpreter(dirs: tuple[Path, Path]) -> None:
    args = main.parse_args(argv(*dirs))
    assert args.engine_venv is None
    expected = EngineCommand.for_packaged(Path(sys.executable), packaged_engine_site(Path(sys.prefix)))
    assert main.engine_command(args) == expected


def test_engine_venv_must_be_a_folder(tmp_path: Path, dirs: tuple[Path, Path]) -> None:
    with pytest.raises(SystemExit):
        main.parse_args(argv(*dirs, "--engine-venv", str(tmp_path / "missing")))


def test_wire_builds_the_engine_client_without_starting_it(dirs: tuple[Path, Path]) -> None:
    wiring = main.wire(main.parse_args(argv(*dirs, "--engine-venv", str(ENGINE_VENV))), no_log)
    try:
        assert isinstance(wiring.engine, EngineClient)
        assert wiring.engine.pid is None
        assert isinstance(wiring.boot.state, EngineStarting)
    finally:
        wiring.engine.close()


class Sidecar:
    """`python -m app.main` as the shell runs it, with its stdout handshake and its stderr events read apart."""

    def __init__(self, dirs: tuple[Path, Path], *extra: str) -> None:
        self.proc = subprocess.Popen(  # noqa: S603 (fixed argv: this interpreter running the entry under test)
            [sys.executable, "-m", "app.main", *argv(*dirs), "--port", "0", *extra],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.events: queue.Queue[dict[str, object]] = queue.Queue()
        self.raw: list[str] = []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.raw.append(line)
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                self.events.put(cast("dict[str, object]", event))

    def handshake(self) -> int:
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        assert line.startswith("FT_READY "), line + "".join(self.raw)
        return int(json.loads(line.removeprefix("FT_READY "))["port"])

    def event(self, name: str, timeout: float = DEADLINE) -> dict[str, object]:
        end = time.monotonic() + timeout
        while (left := end - time.monotonic()) > 0:
            try:
                event = self.events.get(timeout=left)
            except queue.Empty:
                break
            if event.get("event") == name:
                return event
        pytest.fail(f"no {name} event within {timeout} s; stderr: {''.join(self.raw)}")

    def stop(self) -> int:
        """SIGTERM, as the shell's exit and the harness stop it."""
        if self.proc.poll() is None:
            self.proc.terminate()
        return self.proc.wait(timeout=DEADLINE)


def answers_http(port: int) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", "/api/boot")
        return conn.getresponse().status
    finally:
        conn.close()


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
        assert ready["protocol"] == 1
        pid = ready["pid"]
        assert isinstance(pid, int)
        assert pid_alive(pid)
        assert answers_http(port) == 501
    finally:
        code = sidecar.stop()
    # The sidecar closed and reaped its engine before it exited: no grace time (its watchdog and stdin EOF would end
    # an orphaned engine too, but only after the sidecar had gone; S0038 self-mutation B7).
    assert not pid_alive(pid), "the sidecar exited before closing its engine"
    closed = sidecar.event("engine.closed")
    assert closed["source"] == "backend"
    assert code == 0, "".join(sidecar.raw)


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
        assert answers_http(port) == 501
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
    began = time.monotonic()
    sidecar = Sidecar(dirs, "--engine-venv", str(slow))
    try:
        sidecar.handshake()
        handshake_at = time.monotonic() - began
        stderr_line = sidecar.event("engine.stderr")
        assert stderr_line["source"] == "engine"
        assert stderr_line["line"] == "slow stand-in: waiting"
        sidecar.event("engine.ready")
        ready_at = time.monotonic() - began
        assert ready_at - handshake_at > 2.0, (handshake_at, ready_at)
    finally:
        sidecar.stop()
