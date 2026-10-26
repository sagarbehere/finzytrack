"""The engine as a real process (S0035 brief; ADR-0054 decision 4; spike S1's watchdog, S0001 journal).

The engine is spawned the way the main sidecar spawns it: `python -I -m ledger_engine`, pipes on stdin, stdout and
stderr. Waits are explicit waits on a condition with a deadline (EP-28), never fixed sleeps.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from typing import IO, Any

import pytest

from ledger_engine.protocol.messages import MAX_LINE_BYTES
from ledger_engine.protocol.server import PARENT_POLL_SECONDS

ENGINE = [sys.executable, "-I", "-m", "ledger_engine", "--test-ops"]
DEADLINE = 15.0
POSIX = os.name == "posix"


def wait_until(cond: Callable[[], bool], timeout: float = DEADLINE) -> bool:
    end = time.monotonic() + timeout
    pause = threading.Event()
    while time.monotonic() < end:
        if cond():
            return True
        pause.wait(0.02)
    return cond()


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:  # the pid was reused by a process we cannot signal: ours is gone
        return True
    return False


@pytest.fixture
def engine() -> Iterator[subprocess.Popen[bytes]]:
    proc = subprocess.Popen(ENGINE, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)  # noqa: S603
    yield proc
    if proc.poll() is None:
        proc.kill()
    proc.wait(DEADLINE)
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        if pipe is not None:
            pipe.close()


def send(proc: subprocess.Popen[bytes], obj: object) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(obj).encode() + b"\n")
    proc.stdin.flush()


def receive(proc: subprocess.Popen[bytes]) -> dict[str, Any]:
    assert proc.stdout is not None
    got: list[bytes] = []
    reader = threading.Thread(target=lambda: got.append(_readline(proc.stdout)), daemon=True)
    reader.start()
    reader.join(DEADLINE)
    assert got, "no reply within the deadline"
    return json.loads(got[0])


def _readline(pipe: IO[bytes] | None) -> bytes:
    assert pipe is not None
    return pipe.readline()


def call(proc: subprocess.Popen[bytes], op: str, rid: int, params: dict[str, Any] | None = None) -> dict[str, Any]:
    send(proc, {"v": 1, "id": rid, "op": op, "params": params or {}})
    return receive(proc)


def test_handshake_and_ping_over_pipes(engine: subprocess.Popen[bytes]) -> None:
    assert call(engine, "handshake", 1)["result"]["protocol"] == 1
    assert call(engine, "ping", 2) == {"v": 1, "id": 2, "op": "ping", "ok": True, "result": {"status": "ok"}}


def test_stdout_noise_inside_the_engine_never_reaches_the_channel(engine: subprocess.Popen[bytes]) -> None:
    """A print (or a raw write to fd 1) from code running in the engine goes to stderr, not into the replies."""
    call(engine, "handshake", 1)
    assert call(engine, "test.noise", 2)["ok"] is True
    assert call(engine, "ping", 3)["id"] == 3
    assert engine.stdin is not None
    engine.stdin.close()
    engine.wait(DEADLINE)
    assert engine.stderr is not None
    err = engine.stderr.read()
    assert b"noise via print" in err
    assert b"noise via fd 1" in err


def test_code_inside_the_engine_cannot_read_the_request_stream(engine: subprocess.Popen[bytes]) -> None:
    """fd 0 inside the engine is the null device: a read gets end of file and steals no request."""
    call(engine, "handshake", 1)
    assert call(engine, "test.read_stdin", 2)["ok"] is True
    assert call(engine, "ping", 3)["id"] == 3


def test_a_malformed_line_does_not_end_the_engine(engine: subprocess.Popen[bytes]) -> None:
    call(engine, "handshake", 1)
    assert engine.stdin is not None
    engine.stdin.write(b"{garbage\n")
    engine.stdin.flush()
    assert receive(engine)["error"]["key"] == "protocol.malformed"
    assert call(engine, "ping", 2)["ok"] is True


def test_an_over_long_line_is_refused_and_skipped(engine: subprocess.Popen[bytes]) -> None:
    call(engine, "handshake", 1)
    assert engine.stdin is not None
    payload = b'{"v":1,"id":5,"op":"ping","params":{"pad":"' + b"x" * (MAX_LINE_BYTES + 10) + b'"}}\n'
    writer = threading.Thread(target=lambda: (engine.stdin.write(payload), engine.stdin.flush()), daemon=True)  # type: ignore[union-attr]
    writer.start()
    assert receive(engine)["error"]["key"] == "protocol.malformed"
    writer.join(DEADLINE)
    # The rest of the long line is discarded, so the next request is read as a request of its own.
    assert call(engine, "ping", 6) == {"v": 1, "id": 6, "op": "ping", "ok": True, "result": {"status": "ok"}}


def test_the_engine_exits_when_its_stdin_closes(engine: subprocess.Popen[bytes]) -> None:
    call(engine, "handshake", 1)
    assert engine.stdin is not None
    engine.stdin.close()
    assert engine.wait(DEADLINE) == 0


def test_the_engine_exits_on_stdin_eof_before_any_request(engine: subprocess.Popen[bytes]) -> None:
    assert engine.stdin is not None
    engine.stdin.close()
    assert engine.wait(DEADLINE) == 0


# A middle process stands in for the main sidecar: it spawns the engine, reports the engine's pid, and is then
# killed with SIGKILL, so it cannot close anything on its way out.
MIDDLE = r"""
import json, os, subprocess, sys
engine_argv = json.loads(sys.argv[1]) + ["--parent-pid", str(os.getpid())]
mode = sys.argv[2]
if mode == "held-stdin":
    # The engine's stdin is a pipe whose write end the test (the grandparent) also holds: no EOF on parent death.
    fd = int(sys.argv[3])
    proc = subprocess.Popen(engine_argv, stdin=fd, stdout=subprocess.DEVNULL, pass_fds=(fd,))
else:
    proc = subprocess.Popen(engine_argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    for rid, op in ((1, "handshake"), (2, "test.stall")):
        proc.stdin.write(json.dumps({"v": 1, "id": rid, "op": op, "params": {}}).encode() + b"\n")
        proc.stdin.flush()
    proc.stdout.readline()  # the handshake reply; the stall never replies
print(proc.pid, flush=True)
sys.stdin.read()  # block until killed
"""


def spawn_middle(mode: str, *extra: str, pass_fds: tuple[int, ...] = ()) -> tuple[subprocess.Popen[str], int]:
    middle = subprocess.Popen(  # noqa: S603
        [sys.executable, "-I", "-c", MIDDLE, json.dumps(ENGINE), mode, *extra],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        pass_fds=pass_fds,
    )
    assert middle.stdout is not None
    pid = int(middle.stdout.readline())
    assert not gone(pid)
    return middle, pid


@pytest.mark.skipif(not POSIX, reason="the parent-pid poll is POSIX; Windows relies on stdin EOF and the Job Object")
def test_the_engine_exits_when_its_parent_dies_though_its_stdin_stays_open() -> None:
    read_end, write_end = os.pipe()
    middle, pid = spawn_middle("held-stdin", str(read_end), pass_fds=(read_end,))
    os.close(read_end)
    try:
        middle.send_signal(signal.SIGKILL)
        middle.wait(DEADLINE)
        assert wait_until(lambda: gone(pid)), "the engine outlived its parent"
    finally:
        os.close(write_end)
        if not gone(pid):
            os.kill(pid, signal.SIGKILL)


@pytest.mark.skipif(not POSIX, reason="the parent-pid poll is POSIX; Windows relies on stdin EOF and the Job Object")
def test_a_parent_that_died_while_the_engine_was_starting_is_caught() -> None:
    """The spawner names itself with --parent-pid; if the engine's parent is already someone else, it exits."""
    with subprocess.Popen([*ENGINE, "--parent-pid", "999999999"], stdin=subprocess.PIPE) as proc:  # noqa: S603
        assert proc.wait(DEADLINE) == 3  # EXIT_PARENT_GONE, while its stdin is still open


@pytest.mark.parametrize("pid", ["0", "-5", "x"])
def test_a_parent_pid_that_cannot_be_a_spawner_is_refused(pid: str) -> None:
    r = subprocess.run([*ENGINE, "--parent-pid", pid], stdin=subprocess.DEVNULL, capture_output=True, timeout=DEADLINE)  # noqa: S603
    assert r.returncode == 2  # argparse's usage error


def test_a_parent_that_is_pid_1_starts_the_engine_without_the_parent_watchdog() -> None:
    """S0035 independent verifier F6: a main sidecar running as pid 1 (a container without an init) must be able
    to start the engine. Its own parent is not pid 1, so a watchdog would end it at the first poll; stdin EOF still
    ends it."""
    with subprocess.Popen(  # noqa: S603
        [*ENGINE, "--parent-pid", "1"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as proc:
        assert call(proc, "handshake", 1)["ok"] is True
        polls = threading.Event()
        polls.wait(4 * PARENT_POLL_SECONDS)  # several watchdog polls would have passed
        assert proc.poll() is None
        assert call(proc, "ping", 2)["ok"] is True
        assert proc.stdin is not None
        proc.stdin.close()
        assert proc.wait(DEADLINE) == 0


@pytest.mark.skipif(not POSIX, reason="the parent-pid poll is POSIX; Windows relies on stdin EOF and the Job Object")
def test_a_busy_engine_exits_when_its_parent_dies() -> None:
    """Mid-request the read loop cannot see stdin EOF; the parent-pid watchdog still ends the engine."""
    middle, pid = spawn_middle("busy")
    try:
        middle.send_signal(signal.SIGKILL)
        middle.wait(DEADLINE)
        assert wait_until(lambda: gone(pid)), "a busy engine outlived its parent"
    finally:
        if not gone(pid):
            os.kill(pid, signal.SIGKILL)
