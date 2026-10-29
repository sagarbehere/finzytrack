"""The engine lock, held by every engine for its life (S0053; `design/specs/engine-jobs.md` §5.1, §5.5; ADR-0064
amendment of 2026-10-28).

Written from the spec: "Every engine, after its handshake and before any ledger work, takes it with a whole-file
`flock`, waiting, and holds it for its whole life." So: after the handshake the engine holds it (no other process can
take it) until it ends; a request after the handshake is not answered while another process holds the lock (an engine
of an earlier session), and is answered once that process lets it go; an engine started without `--engine-lock` (the
packaged self-check, stand-ins) takes none. Waits are on a condition with a deadline (EP-28).
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="flock is POSIX; Windows is BL-0087 (M1b)")

DEADLINE = 15.0


def engine(*extra: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 (this interpreter running the engine under test)
        [sys.executable, "-I", "-m", "ledger_engine", *extra],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def send(proc: subprocess.Popen[bytes], op: str, rid: int) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps({"v": 2, "id": rid, "op": op, "params": {}}).encode() + b"\n")
    proc.stdin.flush()


class Replies:
    """The engine's reply lines, read on a thread so a test can wait for one with a deadline."""

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self._lines: list[dict[str, Any]] = []
        self._got = threading.Condition()
        assert proc.stdout is not None
        out = proc.stdout
        threading.Thread(target=self._pump, args=(out,), daemon=True).start()

    def _pump(self, out: Any) -> None:
        for line in out:
            with self._got:
                self._lines.append(json.loads(line))
                self._got.notify_all()

    def wait(self, n: int, timeout: float = DEADLINE) -> bool:
        with self._got:
            return self._got.wait_for(lambda: len(self._lines) >= n, timeout)

    def __getitem__(self, i: int) -> dict[str, Any]:
        return self._lines[i]


def held_by_another(path: Path) -> bool:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


@pytest.fixture
def lock(tmp_path: Path) -> Path:
    path = tmp_path / "locks" / "engine.lock"
    path.parent.mkdir()
    return path


@pytest.fixture
def procs() -> Iterator[list[subprocess.Popen[bytes]]]:
    started: list[subprocess.Popen[bytes]] = []
    yield started
    for proc in started:
        if proc.poll() is None:
            proc.kill()
        proc.wait(DEADLINE)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()


def test_after_its_handshake_the_engine_holds_the_lock_until_it_ends(
    lock: Path, procs: list[subprocess.Popen[bytes]]
) -> None:
    proc = engine("--engine-lock", str(lock))
    procs.append(proc)
    replies = Replies(proc)
    send(proc, "handshake", 1)
    send(proc, "ping", 2)
    assert replies.wait(2)
    assert replies[1]["ok"] is True
    assert held_by_another(lock), "the engine answered a request after its handshake without holding the lock"
    assert proc.stdin is not None
    proc.stdin.close()  # the engine ends at the end of its input
    proc.wait(DEADLINE)
    assert not held_by_another(lock)


def test_a_request_after_the_handshake_waits_while_an_earlier_engine_holds_the_lock(
    lock: Path, procs: list[subprocess.Popen[bytes]]
) -> None:
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)  # an engine of an earlier session (this test process holds it)
    try:
        proc = engine("--engine-lock", str(lock))
        procs.append(proc)
        replies = Replies(proc)
        send(proc, "handshake", 1)
        assert replies.wait(1), "the handshake is answered: the lock is taken after it"
        send(proc, "ping", 2)
        assert not replies.wait(2, timeout=0.5), "a request was answered while another process held the engine lock"
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert replies.wait(2), "the request was not answered once the lock was free"
    assert held_by_another(lock)


def test_an_engine_started_without_the_option_takes_no_lock(lock: Path, procs: list[subprocess.Popen[bytes]]) -> None:
    """@regression-guard: the packaged self-check and stand-in engines run without the lock (it passes on the base,
    where no engine takes one; it keeps the lock tied to the option)."""
    proc = engine()
    procs.append(proc)
    replies = Replies(proc)
    send(proc, "handshake", 1)
    send(proc, "ping", 2)
    assert replies.wait(2)
    assert not held_by_another(lock)


def test_the_lock_file_is_never_deleted(lock: Path, procs: list[subprocess.Popen[bytes]]) -> None:
    proc = engine("--engine-lock", str(lock))
    procs.append(proc)
    replies = Replies(proc)
    send(proc, "handshake", 1)
    send(proc, "ping", 2)
    assert replies.wait(2)
    assert proc.stdin is not None
    proc.stdin.close()
    proc.wait(DEADLINE)
    assert lock.is_file()


def test_an_engine_whose_lock_folder_is_missing_creates_it_and_holds_the_lock(
    tmp_path: Path, procs: list[subprocess.Popen[bytes]]
) -> None:
    """§5.1: every engine holds the lock for its life; nothing in the spec makes the engine depend on the sidecar
    having made `locks/` first (S0053 verifier F7: an engine spawned before the sidecar's wait, as piece 2's lazy
    spawn could be, answered its handshake and then died)."""
    lock = tmp_path / "locks" / "engine.lock"
    proc = engine("--engine-lock", str(lock))
    procs.append(proc)
    replies = Replies(proc)
    send(proc, "handshake", 1)
    send(proc, "ping", 2)
    assert replies.wait(2), "the engine did not answer after its handshake"
    assert replies[1]["ok"] is True
    assert held_by_another(lock)
