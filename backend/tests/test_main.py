"""The main sidecar entry (ARCHITECTURE.md §5, the shell interface): CLI args, loopback bind, `FT_READY` handshake.

The acceptance harness (S0030) starts this process with `--port 0` and data dirs inside its temp root, and reads
the port from the handshake line only. `--frozen-clock` is the test-only way to freeze the backend's clock.
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest

from app import main
from app.platform.adapters.clock import SystemClock
from app.platform.adapters.engine_client import EngineClient, EngineCommand, packaged_engine_site
from app.platform.clock import FrozenClock
from app.platform.engine_boot import EngineStarting
from app.platform.observability import Event


def no_log(event: Event, /, **fields: object) -> None:
    """An `EventSink` that drops every event."""


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
    # One line each (S0040: the client's table logs the lifecycle; the composition root no longer logs it too).
    names = [e.get("event") for e in sidecar.events()]
    assert names.count("engine.ready") == 1
    assert names.count("engine.closed") == 1
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
        taken.server_close()
    assert code == 1
    uncaught = sidecar.event("app.uncaught")
    assert uncaught["thread"] == "MainThread"
    exception = cast("dict[str, object]", uncaught["exception"])
    assert exception["type"] == "OSError"
    assert set(exception) == {"type", "at"}
    assert sidecar.stderr_after_exit() == ""
