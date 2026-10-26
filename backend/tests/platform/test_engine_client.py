"""The engine client against the real engine process (ADR-0054 decision 4; S0035 brief; EP-07, EP-13).

The real engine runs from `engine/.venv` (sync it with `uv sync --locked` in `engine/`). A few cases need an engine
that misbehaves in one exact way; those run a small stand-in script instead. Backoff is checked with an injected
monotonic clock and pause, so no test waits for real backoff time (EP-28).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from app.platform.adapters.engine_client import EngineClient, EngineCommand, RestartPolicy
from app.platform.engine import EngineFailure, EngineInfo, EnginePort, EngineRefusal, Pong

REPO = Path(__file__).resolve().parents[3]
ENGINE_VENV = REPO / "engine" / ".venv"
DEADLINE = 20.0


def engine_command(*, test_ops: bool = True) -> EngineCommand:
    if not (ENGINE_VENV / "bin" / "python").exists() and not (ENGINE_VENV / "Scripts" / "python.exe").exists():
        pytest.fail("engine/.venv is missing: run `uv sync --locked` in engine/ (the engine tests need it)")
    return EngineCommand.for_venv(ENGINE_VENV, test_ops=test_ops)


class FakeTime:
    """An injected monotonic clock whose pause advances it, recording each pause (EP-28: no real waits)."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.pauses: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def pause(self, seconds: float) -> None:
        self.pauses.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


@pytest.fixture
def client(fake_time: FakeTime) -> Iterator[EngineClient]:
    c = EngineClient(engine_command(), RestartPolicy(), monotonic=fake_time.monotonic, pause=fake_time.pause)
    yield c
    c.close()


def wait_until(cond: Callable[[], bool], timeout: float = DEADLINE) -> bool:
    end = time.monotonic() + timeout
    step = threading.Event()
    while time.monotonic() < end:
        if cond():
            return True
        step.wait(0.02)
    return cond()


def alive(pid: int) -> bool:
    """Running: it exists and is not a zombie (a killed child stays a zombie until the client reaps it)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)  # noqa: S603, S607 (fixed argv)
    return not stat.stdout.strip().startswith("Z") and bool(stat.stdout.strip())


def stopped(pid: int) -> bool:
    """Wait until the process is no longer running."""
    return wait_until(lambda: not alive(pid))


def kill(pid: int) -> None:
    os.kill(pid, signal.SIGKILL if os.name == "posix" else signal.SIGTERM)


def in_thread[T](fn: Callable[[], T]) -> tuple[threading.Thread, list[T]]:
    out: list[T] = []
    t = threading.Thread(target=lambda: out.append(fn()), daemon=True)
    t.start()
    return t, out


def engine_version() -> str:
    return tomllib.loads((REPO / "engine" / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def test_the_client_is_an_engine_port(client: EngineClient) -> None:
    port: EnginePort = client  # pyright checks the structural match
    assert port is client


def test_start_handshakes(client: EngineClient) -> None:
    info = client.start()
    assert info == EngineInfo(protocol=1, engine_version=engine_version(), beancount_version="3.2.0")
    assert client.pid is not None
    assert alive(client.pid)


def test_ping_starts_the_engine_when_needed(client: EngineClient) -> None:
    assert client.pid is None
    assert client.ping(timeout=DEADLINE) == Pong()
    assert client.pid is not None


def test_a_refusal_comes_back_as_a_refusal(client: EngineClient) -> None:
    out = client.call("no.such.op", {}, timeout=DEADLINE)
    assert isinstance(out, EngineRefusal)
    assert out.key == "protocol.unknown_op"
    assert client.ping(timeout=DEADLINE) == Pong()  # a refusal does not end the engine


def test_an_engine_killed_mid_request_is_a_failure_not_a_hang(client: EngineClient, fake_time: FakeTime) -> None:
    client.start()
    first = client.pid
    assert first is not None
    t, out = in_thread(lambda: client.call("test.stall", {}, timeout=60.0))
    assert wait_until(lambda: "stalling" in client.stderr_tail()), "the engine never started the request"
    kill(first)
    t.join(DEADLINE)
    assert not t.is_alive(), "the caller hung after the engine died"
    assert len(out) == 1
    failure = out[0]
    assert isinstance(failure, EngineFailure)
    assert failure.kind == "crashed"
    # The next request restarts the engine, after the first backoff delay.
    assert client.ping(timeout=DEADLINE) == Pong()
    assert client.pid not in (None, first)
    assert fake_time.pauses == [0.5]


def test_a_request_is_never_retried_after_a_crash(client: EngineClient) -> None:
    """A write is not idempotent: the caller decides; the client never resends (S0035 decision)."""
    client.start()
    pid = client.pid
    assert pid is not None
    t, out = in_thread(lambda: client.call("test.stall", {}, timeout=60.0))
    assert wait_until(lambda: "stalling" in client.stderr_tail())
    kill(pid)
    t.join(DEADLINE)
    assert isinstance(out[0], EngineFailure)
    assert client.pid is None  # no new engine was started, so nothing was resent on the caller's behalf


def test_a_stalled_request_times_out_and_the_engine_is_killed(client: EngineClient) -> None:
    client.start()
    pid = client.pid
    assert pid is not None
    began = time.monotonic()
    out = client.call("test.stall", {}, timeout=0.5)
    assert time.monotonic() - began < 10.0, "the caller's timeout was not honoured"  # S0035 mutant C13
    assert isinstance(out, EngineFailure)
    assert out.kind == "timeout"
    assert stopped(pid), "the stalled engine was left running"
    assert client.ping(timeout=DEADLINE) == Pong()


def test_an_engine_that_died_between_requests_is_restarted(client: EngineClient, fake_time: FakeTime) -> None:
    client.start()
    pid = client.pid
    assert pid is not None
    kill(pid)
    assert stopped(pid)
    assert client.ping(timeout=DEADLINE) == Pong()
    assert fake_time.pauses == [0.5]


def test_restarts_are_bounded_then_allowed_again_after_the_window(client: EngineClient, fake_time: FakeTime) -> None:
    for _ in range(3):
        assert client.ping(timeout=DEADLINE) == Pong()
        pid = client.pid
        assert pid is not None
        kill(pid)
        assert stopped(pid)
    out = client.ping(timeout=DEADLINE)
    assert isinstance(out, EngineFailure)
    assert out.kind == "unavailable"
    now = client.pid
    assert now is None or not alive(now)
    assert fake_time.pauses == [0.5, 2.0]
    fake_time.now += RestartPolicy().window
    assert client.ping(timeout=DEADLINE) == Pong()


def test_reset_allows_a_restart_at_once(client: EngineClient) -> None:
    for _ in range(3):
        client.ping(timeout=DEADLINE)
        pid = client.pid
        assert pid is not None
        kill(pid)
        assert stopped(pid)
    assert isinstance(client.ping(timeout=DEADLINE), EngineFailure)
    client.reset()
    assert client.ping(timeout=DEADLINE) == Pong()


def stand_in(script: str) -> EngineCommand:
    return EngineCommand(argv=(sys.executable, "-I", "-c", script), env=None)


def test_a_start_that_fails_counts_towards_the_bound(fake_time: FakeTime) -> None:
    c = EngineClient(stand_in("import sys; sys.exit(3)"), monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        kinds = [r.kind for r in (c.start() for _ in range(4)) if isinstance(r, EngineFailure)]
        assert kinds == ["start_failed", "start_failed", "start_failed", "unavailable"]
        assert fake_time.pauses == [0.5, 2.0]
        fake_time.now += RestartPolicy().window
        again = c.start()
        assert isinstance(again, EngineFailure)
        assert again.kind == "start_failed"
        assert fake_time.pauses == [0.5, 2.0]  # the window passed: no delay before this start
    finally:
        c.close()


COUNTING = r"""
import json, sys
with open(sys.argv[1], "a") as fh:
    fh.write("x")
req = json.loads(sys.stdin.readline())
reply = {MODE}
sys.stdout.write(json.dumps(reply) + "\n")
sys.stdout.flush()
sys.stdin.read()
"""
MISMATCH = """{"v": 1, "id": req["id"], "op": "handshake", "ok": False,
         "error": {"key": "protocol.version_mismatch", "message": "this engine speaks protocol 2"}}"""
NEWER = """{"v": 1, "id": req["id"], "op": "handshake", "ok": True,
         "result": {"protocol": 2, "engine_version": "9", "beancount_version": "3.2.0"}}"""


@pytest.mark.parametrize("mode", [MISMATCH, NEWER])
def test_an_incompatible_engine_is_never_retried(tmp_path: Path, fake_time: FakeTime, mode: str) -> None:
    starts = tmp_path / "starts"
    script = COUNTING.replace("{MODE}", mode)
    command = EngineCommand(argv=(sys.executable, "-I", "-c", script, str(starts)), env=None)
    c = EngineClient(command, monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        for _ in range(3):
            out = c.start()
            assert isinstance(out, EngineFailure)
            assert out.kind == "incompatible"
        assert starts.read_text() == "x"
        assert fake_time.pauses == []
        now = c.pid
        assert now is None or not alive(now)
    finally:
        c.close()


GARBAGE = r"""
import json, sys
req = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({"v": 1, "id": req["id"], "op": "handshake", "ok": True,
    "result": {"protocol": 1, "engine_version": "0", "beancount_version": "3.2.0"}}) + "\n")
sys.stdout.flush()
sys.stdin.readline()
sys.stdout.write("this is not a reply\n")
sys.stdout.flush()
sys.stdin.read()
"""


def test_a_reply_that_breaks_the_protocol_is_a_failure_and_ends_the_engine(fake_time: FakeTime) -> None:
    c = EngineClient(stand_in(GARBAGE), monotonic=fake_time.monotonic, pause=fake_time.pause)
    try:
        assert isinstance(c.start(), EngineInfo)
        pid = c.pid
        assert pid is not None
        out = c.ping(timeout=DEADLINE)
        assert isinstance(out, EngineFailure)
        assert out.kind == "protocol_violation"
        assert stopped(pid)
    finally:
        c.close()


def test_close_ends_the_engine_and_later_calls_fail(client: EngineClient) -> None:
    client.start()
    pid = client.pid
    assert pid is not None
    client.close()
    assert stopped(pid)
    out = client.ping(timeout=DEADLINE)
    assert isinstance(out, EngineFailure)
    assert out.kind == "closed"
    client.close()  # closing twice is harmless


def test_the_engine_environment_drops_the_shell_token_and_python_overrides() -> None:
    environ = {
        "FT_SHELL_TOKEN": "secret",
        "FT_OTHER": "x",
        "PYTHONPATH": "/x",
        "PYTHONHOME": "/y",
        "HOME": "/var/empty",
        "PATH": "/bin",
        "TZ": "Asia/Kolkata",
        "LANG": "C.UTF-8",
    }
    command = EngineCommand.for_venv(ENGINE_VENV, environ=environ)
    assert command.env == {"HOME": "/var/empty", "PATH": "/bin", "TZ": "Asia/Kolkata", "LANG": "C.UTF-8"}


def test_the_engine_is_told_its_parent(client: EngineClient) -> None:
    """The engine watches the pid the client gives it, so a parent that dies during start-up is caught."""
    client.start()
    pid = client.pid
    assert pid is not None
    args = subprocess.run(["ps", "-o", "args=", "-p", str(pid)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    assert f"--parent-pid {os.getpid()}" in args.stdout


def test_the_engine_command_runs_the_venv_interpreter_isolated() -> None:
    command = EngineCommand.for_venv(ENGINE_VENV, environ={})
    assert Path(command.argv[0]).parent.parent == ENGINE_VENV
    assert command.argv[1:] == ("-I", "-m", "ledger_engine")
    assert EngineCommand.for_venv(ENGINE_VENV, environ={}, test_ops=True).argv[-1] == "--test-ops"
